"""Admin Reply Eval 路由。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import get_db
from core.tracing import row_to_dict

router = APIRouter(tags=["admin-reply"])


class ReplyTestRunRequest(BaseModel):
    chat_type: Literal["group", "private"] = "group"
    session_id: str = "reply-test"
    sender_id: str = "admin"
    sender_name: str = "admin"
    character_name: str = ""
    message: str
    recent_context: str = ""
    persona_text: str = ""
    prompt_engine: Literal["v1", "v2", "prompt"] = "prompt"
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "v2_code_retry"
    enable_reply_contract_retry: bool = True
    dry_run: bool = True


def _loads_json_list(raw: str) -> list:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _reply_case_to_dict(row) -> dict:
    data = row_to_dict(row)
    data["context"] = json.loads(row.context_json or "{}")
    data["expected_keywords"] = _loads_json_list(row.expected_keywords_json)
    data["forbidden_keywords"] = _loads_json_list(row.forbidden_keywords_json)
    data["tags"] = _loads_json_list(row.tags_json)
    return data


def _reply_eval_run_to_dict(row) -> dict:
    data = row_to_dict(row)
    try:
        data["metrics"] = json.loads(row.summary_json or "{}")
    except Exception:
        data["metrics"] = {}
    return data


def _reply_log_attempt(log) -> dict:
    if not log:
        return {
            "raw_output": "",
            "called_reply": False,
            "called_no_reply": False,
            "structured_fallback": False,
            "reply_tool_call_count": 0,
            "no_reply_tool_call_count": 0,
            "structured_fallback_count": 0,
            "total_final_action_count": 0,
            "result": "",
        }
    return {
        "raw_output": log.raw_output_preview or "",
        "called_reply": bool(log.has_reply_tool),
        "called_no_reply": bool(log.has_no_reply_tool),
        "structured_fallback": bool(log.has_structured_fallback),
        "reply_tool_call_count": int(getattr(log, "reply_tool_call_count", 0) or 0),
        "no_reply_tool_call_count": int(getattr(log, "no_reply_tool_call_count", 0) or 0),
        "structured_fallback_count": int(getattr(log, "structured_fallback_count", 0) or 0),
        "total_final_action_count": int(getattr(log, "total_final_action_count", 0) or 0),
        "result": log.result or "",
    }


def _reply_contract_has_final_action(log) -> bool:
    return bool(
        getattr(log, "has_reply_tool", 0)
        or getattr(log, "has_no_reply_tool", 0)
        or getattr(log, "has_structured_fallback", 0)
    )


def _reply_contract_run_key(log) -> str:
    return (
        str(getattr(log, "run_id", "") or "").strip()
        or str(getattr(log, "trace_id", "") or "").strip()
        or f"log:{getattr(log, 'id', '')}"
    )


def _is_reply_eval_test_session(session_id: str) -> bool:
    sid = str(session_id or "").strip().lower()
    if not sid:
        return False
    prefixes = (
        "reply-test",
        "reply_test",
        "reply-eval",
        "reply_eval",
        "test-",
        "private_smoke",
    )
    return sid.startswith(prefixes) or "smoke" in sid


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _resolve_reply_test_prompt_settings(body: ReplyTestRunRequest) -> tuple[str, str, bool]:
    variant = str(body.variant or "v2_code_retry")
    engine = "prompt"
    prompt_mode = "prompt"
    enable_retry = bool(body.enable_reply_contract_retry)

    if variant == "prompt_only":
        enable_retry = False
    elif variant == "code_retry":
        enable_retry = enable_retry
    elif variant == "baseline":
        enable_retry = False
    elif variant == "v1_baseline":
        enable_retry = False
    elif variant == "v2_prompt_only":
        enable_retry = False
    elif variant == "v2_code_retry":
        enable_retry = enable_retry
    return engine, prompt_mode, enable_retry


async def _run_reply_test_once(body: ReplyTestRunRequest, db: Session) -> dict:
    from core.database import AgentRun, LLMApiRequestLog, ReplyContractCheckLog
    from core.tracing import new_trace_id
    from nanobot_kt.bridge import get_bridge

    trace_id = new_trace_id()
    session_id = (body.session_id or f"reply-test-{uuid.uuid4().hex[:8]}").strip()
    sender_id = (body.sender_id or "admin").strip()
    prompt_engine, prompt_mode, enable_retry = _resolve_reply_test_prompt_settings(body)
    metadata = {
        "trace_id": trace_id,
        "chat_type": body.chat_type,
        "is_group": body.chat_type == "group",
        "group_id": session_id if body.chat_type == "group" else "",
        "sender_id": sender_id,
        "sender_name": body.sender_name,
        "character_name": body.character_name,
        "persona_text": body.persona_text,
        "history_header": body.recent_context,
        "variant": body.variant,
        "prompt_runtime_engine_override": prompt_engine,
        "enable_reply_contract_retry": enable_retry,
        "dry_run": bool(body.dry_run),
    }
    bridge = get_bridge()
    content = await bridge.handle_message(
        body.message,
        user_id=sender_id,
        session_id=session_id,
        sender_name=body.sender_name,
        metadata=metadata,
    )

    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    run_id = run.run_id if run else ""
    prompt_sha256 = run.prompt_sha256 if run else ""
    prompt_source = run.prompt_source if run else ""
    prompt_mode_actual = run.prompt_mode if run else prompt_mode
    reply_logs = []
    llm_logs = []
    if run_id:
        reply_logs = (
            db.query(ReplyContractCheckLog)
            .filter(ReplyContractCheckLog.run_id == run_id)
            .order_by(ReplyContractCheckLog.attempt.asc(), ReplyContractCheckLog.created_at.asc())
            .all()
        )
        llm_logs = (
            db.query(LLMApiRequestLog)
            .filter(LLMApiRequestLog.run_id == run_id)
            .order_by(LLMApiRequestLog.created_at.asc())
            .all()
        )
    first_log = reply_logs[0] if reply_logs else None
    retry_log = next((log for log in reply_logs if int(log.attempt or 0) == 1), None)
    action = "reply" if str(content or "").strip() else "no_reply"
    called_reply_or_no_reply = any(
        bool(log.has_reply_tool) or bool(log.has_no_reply_tool) or bool(log.has_structured_fallback)
        for log in reply_logs
    )
    prompt_miss_count = sum(
        1 for log in reply_logs
        if int(log.attempt or 0) == 0
        and not (bool(log.has_reply_tool) or bool(log.has_no_reply_tool) or bool(log.has_structured_fallback))
    )
    total_final_action_count = sum(int(getattr(log, "total_final_action_count", 0) or 0) for log in reply_logs)
    retry_used = retry_log is not None
    return {
        "ok": True,
        "trace_id": trace_id,
        "run_id": run_id,
        "prompt_engine": prompt_engine,
        "prompt_mode": prompt_mode_actual,
        "prompt_source": prompt_source,
        "prompt_sha256": prompt_sha256,
        "first_attempt": _reply_log_attempt(first_log),
        "retry_attempt": {
            "enabled": enable_retry,
            **_reply_log_attempt(retry_log),
        },
        "final": {"action": action, "content": str(content or "")},
        "metrics": {
            "reply_contract_ok": bool(called_reply_or_no_reply),
            "retry_used": bool(retry_used),
            "retry_success": bool(retry_log and retry_log.result == "retry_success"),
            "prompt_miss_count": int(prompt_miss_count),
            "total_final_action_count": int(total_final_action_count),
        },
        "llm_api_request_logs": [row_to_dict(row) for row in llm_logs],
        "reply_contract_check_logs": [row_to_dict(row) for row in reply_logs],
    }


@router.post("/reply-test/run")
async def reply_test_run(
    body: ReplyTestRunRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return await _run_reply_test_once(body, db)


class ReplyEvalCaseIn(BaseModel):
    case_id: str = ""
    title: str = ""
    chat_type: Literal["group", "private"] = "group"
    input_text: str
    context: dict = Field(default_factory=dict)
    expected_action: Literal["reply", "no_reply", "any"] = "any"
    expected_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)
    enabled: int = 1


class ReplyEvalCasePatch(BaseModel):
    title: str | None = None
    chat_type: Literal["group", "private"] | None = None
    input_text: str | None = None
    context: dict | None = None
    expected_action: Literal["reply", "no_reply", "any"] | None = None
    expected_keywords: list[str] | None = None
    forbidden_keywords: list[str] | None = None
    source: str | None = None
    tags: list[str] | None = None
    enabled: int | None = None


class ReplyEvalSaveGeneratedIn(BaseModel):
    items: list[ReplyEvalCaseIn]


class ReplyEvalRunIn(BaseModel):
    name: str = ""
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "v2_code_retry"
    case_ids: list[str] = Field(default_factory=list)
    limit: int = 50


def _upsert_reply_eval_case(db: Session, item: ReplyEvalCaseIn):
    from core.database import ReplyEvalCase

    case_id = (item.case_id or f"reply_case_{uuid.uuid4().hex[:12]}").strip()
    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        row = ReplyEvalCase(case_id=case_id)
        db.add(row)
    row.title = item.title or item.input_text[:40]
    row.chat_type = item.chat_type
    row.input_text = item.input_text
    row.context_json = json.dumps(item.context or {}, ensure_ascii=False)
    row.expected_action = item.expected_action
    row.expected_keywords_json = json.dumps(item.expected_keywords or [], ensure_ascii=False)
    row.forbidden_keywords_json = json.dumps(item.forbidden_keywords or [], ensure_ascii=False)
    row.source = item.source or "manual"
    row.tags_json = json.dumps(item.tags or [], ensure_ascii=False)
    row.enabled = 1 if item.enabled else 0
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


@router.get("/reply-eval/cases")
def reply_eval_list_cases(
    enabled: int | None = Query(None),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    q = db.query(ReplyEvalCase)
    if enabled is not None:
        q = q.filter(ReplyEvalCase.enabled == int(enabled))
    rows = q.order_by(ReplyEvalCase.created_at.desc()).all()
    return {"items": [_reply_case_to_dict(row) for row in rows], "total": len(rows)}


@router.post("/reply-eval/cases")
def reply_eval_create_case(
    body: ReplyEvalCaseIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return _reply_case_to_dict(_upsert_reply_eval_case(db, body))


@router.put("/reply-eval/cases/{case_id}")
def reply_eval_update_case(
    case_id: str,
    body: ReplyEvalCasePatch,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        raise HTTPException(404, "Reply eval case not found")
    updates = body.model_dump(exclude_unset=True)
    if "title" in updates:
        row.title = updates["title"] or row.title
    if "chat_type" in updates:
        row.chat_type = updates["chat_type"] or row.chat_type
    if "input_text" in updates:
        row.input_text = updates["input_text"] or row.input_text
    if "context" in updates:
        row.context_json = json.dumps(updates["context"] or {}, ensure_ascii=False)
    if "expected_action" in updates:
        row.expected_action = updates["expected_action"] or row.expected_action
    if "expected_keywords" in updates:
        row.expected_keywords_json = json.dumps(updates["expected_keywords"] or [], ensure_ascii=False)
    if "forbidden_keywords" in updates:
        row.forbidden_keywords_json = json.dumps(updates["forbidden_keywords"] or [], ensure_ascii=False)
    if "source" in updates:
        row.source = updates["source"] or row.source
    if "tags" in updates:
        row.tags_json = json.dumps(updates["tags"] or [], ensure_ascii=False)
    if "enabled" in updates:
        row.enabled = 1 if updates["enabled"] else 0
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _reply_case_to_dict(row)


@router.delete("/reply-eval/cases/{case_id}")
def reply_eval_delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        raise HTTPException(404, "Reply eval case not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/reply-eval/generate-preview")
def reply_eval_generate_preview(_body: dict = {}, _auth=Depends(verify_admin)):
    categories = {
        "被叫到": [("有人叫我", "凛音在吗"), ("明确点名", "凛音帮我看下"), ("喊名字", "凛音")],
        "直接问题": [("是否在线", "你在吗"), ("简单求助", "这个怎么弄"), ("确认问题", "能帮我解释下吗")],
        "普通闲聊": [("随口感叹", "今天好困"), ("日常吐槽", "天气又变冷了"), ("闲聊接话", "这也太离谱了")],
        "情绪低落": [("低落", "有点撑不住了"), ("烦躁", "今天真的很烦"), ("自我怀疑", "是不是我太菜了")],
        "技术求助": [("报错", "pytest 这里为什么炸了"), ("配置", "docker 端口怎么映射"), ("代码", "这个函数怎么改")],
        "信息不足": [("半截问题", "那个东西咋办"), ("缺上下文", "然后呢"), ("不清楚对象", "它又坏了")],
        "纯哈哈哈": [("笑", "哈哈哈哈"), ("表情", "笑死"), ("附和", "草")],
        "别人在和别人说话": [("转述", "你问小王吧"), ("对别人", "老张你看看"), ("旁观", "他们说的有道理")],
        "半句话": [("断句", "我刚才那个"), ("等待", "等一下我"), ("未完成", "就是那个")],
        "身份试探": [("问身份", "你是谁"), ("问机器人", "你是 bot 吗"), ("问模型", "你用的什么模型")],
        "生活场景邀请": [("吃饭", "晚上吃啥"), ("游戏", "要不要开一把"), ("出门", "周末出去吗")],
    }
    items = []
    for category, examples in categories.items():
        for idx, (title, text) in enumerate(examples + examples[:1], start=1):
            expected = "reply" if category in {"被叫到", "直接问题", "情绪低落", "技术求助", "身份试探"} else "any"
            items.append({
                "case_id": f"reply_gen_{category}_{idx}_{uuid.uuid4().hex[:6]}",
                "title": f"{category}-{title}",
                "chat_type": "group",
                "input_text": text,
                "context": {},
                "expected_action": expected,
                "expected_keywords": [],
                "forbidden_keywords": [],
                "source": "generated",
                "tags": [category],
                "enabled": 1,
            })
    return {"items": items, "total": len(items)}


@router.post("/reply-eval/save-generated")
def reply_eval_save_generated(
    body: ReplyEvalSaveGeneratedIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    saved = 0
    for item in body.items:
        item.source = item.source or "generated"
        _upsert_reply_eval_case(db, item)
        saved += 1
    return {"ok": True, "saved": saved}


@router.post("/reply-eval/run")
async def reply_eval_run(
    body: ReplyEvalRunIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase, ReplyEvalResult, ReplyEvalRun

    q = db.query(ReplyEvalCase).filter(ReplyEvalCase.enabled == 1)
    if body.case_ids:
        q = q.filter(ReplyEvalCase.case_id.in_(body.case_ids))
    cases = q.order_by(ReplyEvalCase.created_at.asc()).limit(max(1, min(body.limit or 50, 200))).all()
    run = ReplyEvalRun(name=body.name or f"{body.variant} {datetime.now().strftime('%m-%d %H:%M')}", variant=body.variant)
    db.add(run)
    db.commit()
    db.refresh(run)

    results = []
    totals = {
        "reply_contract_ok": 0,
        "retry_used": 0,
        "retry_success": 0,
        "no_tool_call": 0,
        "fake_tool_claim": 0,
        "empty_output": 0,
        "passed": 0,
    }
    for case in cases:
        context = json.loads(case.context_json or "{}")
        test_body = ReplyTestRunRequest(
            chat_type=case.chat_type or "group",
            session_id=str(context.get("session_id") or f"reply-eval-{case.case_id}"),
            sender_id=str(context.get("sender_id") or "eval"),
            sender_name=str(context.get("sender_name") or "eval"),
            message=case.input_text,
            recent_context=str(context.get("recent_context") or ""),
            persona_text=str(context.get("persona_text") or ""),
            variant=body.variant,
            enable_reply_contract_retry=body.variant in {"code_retry", "v2_code_retry"},
            dry_run=True,
        )
        try:
            outcome = await _run_reply_test_once(test_body, db)
            actual_action = outcome["final"]["action"]
            final_content = outcome["final"].get("content", "")
            expected_action = case.expected_action or "any"
            expected_ok = expected_action == "any" or actual_action == expected_action
            expected_keywords = _loads_json_list(case.expected_keywords_json)
            forbidden_keywords = _loads_json_list(case.forbidden_keywords_json)
            keyword_ok = all(k in final_content for k in expected_keywords)
            forbidden_ok = not any(k in final_content for k in forbidden_keywords)
            passed = bool(expected_ok and keyword_ok and forbidden_ok)
            first = outcome.get("first_attempt", {})
            called = bool(outcome["metrics"].get("reply_contract_ok"))
            retry_used = bool(outcome["metrics"].get("retry_used"))
            retry_success = bool(outcome["metrics"].get("retry_success"))
            totals["reply_contract_ok"] += 1 if called else 0
            totals["retry_used"] += 1 if retry_used else 0
            totals["retry_success"] += 1 if retry_success else 0
            totals["no_tool_call"] += 1 if first.get("result") == "no_tool_call" else 0
            totals["fake_tool_claim"] += 1 if first.get("result") == "fake_tool_call_claim" else 0
            totals["empty_output"] += 1 if not final_content else 0
            totals["passed"] += 1 if passed else 0
            row = ReplyEvalResult(
                run_id=run.id,
                agent_run_id=str(outcome.get("run_id") or ""),
                trace_id=str(outcome.get("trace_id") or ""),
                prompt_sha256=str(outcome.get("prompt_sha256") or ""),
                case_id=case.case_id,
                variant=body.variant,
                expected_action=expected_action,
                actual_action=actual_action,
                called_reply_or_no_reply=1 if called else 0,
                retry_used=1 if retry_used else 0,
                passed=1 if passed else 0,
                raw_output_preview=str(first.get("raw_output") or "")[:2000],
                final_content_preview=str(final_content or "")[:2000],
                error="",
            )
        except Exception as e:
            passed = False
            row = ReplyEvalResult(
                run_id=run.id,
                agent_run_id="",
                trace_id="",
                prompt_sha256="",
                case_id=case.case_id,
                variant=body.variant,
                expected_action=case.expected_action or "any",
                actual_action="error",
                passed=0,
                error=str(e)[:1000],
            )
        db.add(row)
        results.append(row)

    total = len(cases)
    failed = total - totals["passed"]
    metrics = {
        "reply_call_rate": round(totals["reply_contract_ok"] / total, 4) if total else 0,
        "valid_action_rate": round(totals["reply_contract_ok"] / total, 4) if total else 0,
        "expected_action_accuracy": round(totals["passed"] / total, 4) if total else 0,
        "retry_used_rate": round(totals["retry_used"] / total, 4) if total else 0,
        "retry_success_rate": round(totals["retry_success"] / total, 4) if total else 0,
        "no_tool_call_rate": round(totals["no_tool_call"] / total, 4) if total else 0,
        "fake_tool_claim_rate": round(totals["fake_tool_claim"] / total, 4) if total else 0,
        "empty_output_rate": round(totals["empty_output"] / total, 4) if total else 0,
    }
    run.total = total
    run.reply_contract_ok = totals["reply_contract_ok"]
    run.retry_used = totals["retry_used"]
    run.passed = totals["passed"]
    run.failed = failed
    run.summary_json = json.dumps(metrics, ensure_ascii=False)
    db.commit()
    db.refresh(run)
    return {**_reply_eval_run_to_dict(run), "results": [row_to_dict(row) for row in results]}


@router.get("/reply-eval/traffic")
def reply_eval_real_traffic(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
    session_id: str = "",
    include_test_sessions: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyContractCheckLog

    window_hours = max(1, min(int(hours or 24), 168))
    sample_limit = max(1, min(int(limit or 50), 200))
    since = datetime.now() - timedelta(hours=window_hours)
    query = db.query(ReplyContractCheckLog).filter(ReplyContractCheckLog.created_at >= since)
    if session_id:
        query = query.filter(ReplyContractCheckLog.session_id == session_id)
    rows = query.order_by(ReplyContractCheckLog.created_at.desc(), ReplyContractCheckLog.id.desc()).limit(5000).all()
    if not include_test_sessions:
        rows = [row for row in rows if not _is_reply_eval_test_session(row.session_id)]

    runs: dict[str, list[Any]] = {}
    for row in rows:
        runs.setdefault(_reply_contract_run_key(row), []).append(row)

    total_runs = len(runs)
    contract_ok_runs = 0
    first_attempt_ok_runs = 0
    prompt_miss_count = 0
    retry_used_runs = 0
    retry_success_runs = 0
    retry_failed_after_prompt_count = 0
    first_overcall_count = 0
    retry_overcall_count = 0
    total_final_action_count = 0
    reply_tool_call_count = 0
    no_reply_tool_call_count = 0
    structured_fallback_count = 0
    result_counts: dict[str, int] = {}
    session_stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        result = str(row.result or "")
        result_counts[result or "-"] = result_counts.get(result or "-", 0) + 1
        total_final_action_count += int(getattr(row, "total_final_action_count", 0) or 0)
        reply_tool_call_count += int(getattr(row, "reply_tool_call_count", 0) or 0)
        no_reply_tool_call_count += int(getattr(row, "no_reply_tool_call_count", 0) or 0)
        structured_fallback_count += int(getattr(row, "structured_fallback_count", 0) or 0)

    for run_id, run_rows in runs.items():
        ordered = sorted(run_rows, key=lambda row: (int(row.attempt or 0), row.created_at or datetime.min, int(row.id or 0)))
        first = next((row for row in ordered if int(row.attempt or 0) == 0), ordered[0] if ordered else None)
        retry_rows = [row for row in ordered if int(row.attempt or 0) > 0]
        has_ok = any(_reply_contract_has_final_action(row) for row in ordered)
        first_ok = bool(first and _reply_contract_has_final_action(first))
        first_miss = bool(first and not _reply_contract_has_final_action(first))
        retry_success = any(
            str(row.result or "") == "retry_success" or _reply_contract_has_final_action(row)
            for row in retry_rows
        )
        retry_failed_after_prompt = bool(retry_rows and not retry_success)

        contract_ok_runs += 1 if has_ok else 0
        first_attempt_ok_runs += 1 if first_ok else 0
        prompt_miss_count += 1 if first_miss else 0
        retry_used_runs += 1 if retry_rows else 0
        retry_success_runs += 1 if retry_success else 0
        retry_failed_after_prompt_count += 1 if retry_failed_after_prompt else 0
        first_overcall_count += 1 if first and int(getattr(first, "total_final_action_count", 0) or 0) > 1 else 0
        retry_overcall_count += sum(
            1 for row in retry_rows
            if int(getattr(row, "total_final_action_count", 0) or 0) != 1
        )

        sid = str(getattr(first or ordered[0], "session_id", "") or "")
        stat = session_stats.setdefault(sid, {
            "session_id": sid,
            "total_runs": 0,
            "contract_ok_runs": 0,
            "prompt_miss_count": 0,
            "retry_used_runs": 0,
            "retry_success_runs": 0,
            "latest_at": "",
        })
        stat["total_runs"] += 1
        stat["contract_ok_runs"] += 1 if has_ok else 0
        stat["prompt_miss_count"] += 1 if first_miss else 0
        stat["retry_used_runs"] += 1 if retry_rows else 0
        stat["retry_success_runs"] += 1 if retry_success else 0
        latest = max((row.created_at for row in ordered if row.created_at), default=None)
        if latest and str(latest.isoformat()) > str(stat.get("latest_at") or ""):
            stat["latest_at"] = latest.isoformat()

    for stat in session_stats.values():
        stat["contract_ok_rate"] = _safe_rate(int(stat["contract_ok_runs"]), int(stat["total_runs"]))

    failure_rows = [
        row for row in rows
        if not _reply_contract_has_final_action(row)
        or int(getattr(row, "total_final_action_count", 0) or 0) > 1
    ][:sample_limit]

    return {
        "window_hours": window_hours,
        "since": since.isoformat(),
        "total_logs": len(rows),
        "total_runs": total_runs,
        "contract_ok_runs": contract_ok_runs,
        "contract_ok_rate": _safe_rate(contract_ok_runs, total_runs),
        "first_attempt_ok_runs": first_attempt_ok_runs,
        "first_attempt_ok_rate": _safe_rate(first_attempt_ok_runs, total_runs),
        "prompt_miss_count": prompt_miss_count,
        "prompt_miss_rate": _safe_rate(prompt_miss_count, total_runs),
        "retry_used_runs": retry_used_runs,
        "retry_used_rate": _safe_rate(retry_used_runs, total_runs),
        "retry_success_runs": retry_success_runs,
        "retry_success_rate": _safe_rate(retry_success_runs, total_runs),
        "retry_failed_after_prompt_count": retry_failed_after_prompt_count,
        "retry_failed_after_prompt_rate": _safe_rate(retry_failed_after_prompt_count, total_runs),
        "first_overcall_count": first_overcall_count,
        "retry_overcall_count": retry_overcall_count,
        "total_final_action_count": total_final_action_count,
        "reply_tool_call_count": reply_tool_call_count,
        "no_reply_tool_call_count": no_reply_tool_call_count,
        "structured_fallback_count": structured_fallback_count,
        "result_counts": result_counts,
        "session_breakdown": sorted(
            session_stats.values(),
            key=lambda item: (-int(item["total_runs"]), str(item["session_id"])),
        )[:20],
        "recent_failures": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "run_id": row.run_id,
                "session_id": row.session_id,
                "attempt": int(row.attempt or 0),
                "result": row.result or "",
                "total_final_action_count": int(getattr(row, "total_final_action_count", 0) or 0),
                "reply_tool_call_count": int(getattr(row, "reply_tool_call_count", 0) or 0),
                "no_reply_tool_call_count": int(getattr(row, "no_reply_tool_call_count", 0) or 0),
                "raw_output_preview": row.raw_output_preview or "",
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in failure_rows
        ],
        "include_test_sessions": bool(include_test_sessions),
        "truncated": len(rows) >= 5000,
    }


@router.get("/reply-eval/runs")
def reply_eval_list_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalRun

    rows = db.query(ReplyEvalRun).order_by(ReplyEvalRun.created_at.desc()).limit(limit).all()
    return {"items": [_reply_eval_run_to_dict(row) for row in rows], "total": len(rows)}


@router.get("/reply-eval/runs/{run_id}")
def reply_eval_get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalResult, ReplyEvalRun

    run = db.query(ReplyEvalRun).filter(ReplyEvalRun.id == int(run_id)).first()
    if run is None:
        raise HTTPException(404, "Reply eval run not found")
    results = (
        db.query(ReplyEvalResult)
        .filter(ReplyEvalResult.run_id == run.id)
        .order_by(ReplyEvalResult.id.asc())
        .all()
    )
    return {**_reply_eval_run_to_dict(run), "results": [row_to_dict(row) for row in results]}
