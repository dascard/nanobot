"""
FastAPI 路由模块。
定义所有 HTTP 端点，含 Bearer Token 认证中间件。
"""
import os
import logging
import json
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Header, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from config import (
    NANOBOT_API_TOKEN, EVOLUTION_THRESHOLD, API_KEY_01_CHAT, ADMIN_USER_ID,
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_PROVIDER, NEW_API_KEY, NEW_API_BASE_URL, NEW_API_TIMEOUT,
    LLM_MODEL_SMART, LLM_MODEL_FAST, LLM_MODEL_REASONING
)
from core.database import get_db, User, Persona, SystemPrompt, ChatLog, MemoryDigest
from core.evolution import evolution_task
from core.legacy_adapter import SQLiteMemory  # Keep for evolution; UnifiedProvider/Controller replaced by KT
from nanobot_kt.bridge import get_bridge
from core.compaction import run_autocompact_circuit_breaker
from core.daily_digest import generate_daily_digest_for_date
from clients.model_registry import registry
from clients.new_api_client import NewAPIClient

logger = logging.getLogger("nanobot.routes")
router = APIRouter(prefix="/api/v1")

# --- Legacy Memory (for evolution endpoints) ---
memory = None

def init_legacy_memory():
    """Initialize SQLiteMemory for evolution endpoints. Called from server.py lifespan."""
    global memory
    memory = SQLiteMemory()
    logger.info("Legacy SQLiteMemory initialized for evolution endpoints")


def _cap_text(text: str, max_chars: int, label: str = "") -> str:
    """Truncate text at last newline boundary, appending a truncation marker."""
    if len(text) <= max_chars:
        return text
    cut_at = text.rfind("\n", 0, max_chars)
    if cut_at <= 0:
        cut_at = max_chars
    logger.debug(f"[cap] {label}: {len(text)} -> {cut_at} chars (max={max_chars})")
    return text[:cut_at] + f"\n...[截断: 原{len(text)}字符]"


def _build_session_memory(db: Session, session_id: str, max_messages: int = 30,
                          max_per_msg: int = 1000, max_total: int = 15000) -> str:
    """Build a capped conversation history context string for a given session."""
    recent_logs = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == session_id)
        .order_by(ChatLog.id.desc())
        .limit(max_messages)
        .all()
    )
    recent_logs.reverse()

    if not recent_logs:
        return ""

    lines = []
    for log in recent_logs:
        content = _cap_text(log.content.strip(), max_per_msg, f"session_msg")
        if not content:
            continue
        if log.role == "user":
            lines.append(f"用户: {content}")
        elif log.role == "assistant":
            lines.append(f"助手: {content}")

    if not lines:
        return ""

    header = "[以下为近期对话历史，供你理解上下文]"
    body = "\n".join(lines)
    footer = "[历史结束]"
    full = f"{header}\n{body}\n{footer}"
    return _cap_text(full, max_total, "session_memory")


# ── 认证中间件 ──

def verify_token(authorization: str = Header(default="")):
    """
    简单 Bearer Token 校验。
    若环境变量 NANOBOT_API_TOKEN 为空，则不启用认证（开发模式）。
    """
    if not NANOBOT_API_TOKEN:
        return  # 未配置 Token 则跳过认证
    token = authorization.replace("Bearer ", "").strip()
    if token != NANOBOT_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


# ── 请求模型 ──

class LogRequest(BaseModel):
    user_id: str = "default_user"
    role: str  # 'user' | 'model'
    content: str


class ChatProxyRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str = "default_session"
    query: str = ""
    files: Optional[List[str]] = None
    sender_name: Optional[str] = None
    session_name: Optional[str] = None

class EvolutionTriggerRequest(BaseModel):
    user_id: str

class AmbientLogRequest(BaseModel):
    group_id: str = "unknown"
    session_name: str | None = None  # 场景名 (如群名)
    sender_name: str = "unknown"    # 发送者名
    content: str = ""


class MemoryDigestRunRequest(BaseModel):
    target_date: Optional[str] = None  # YYYY-MM-DD
    user_id: Optional[str] = None


class ModelSyncRequest(BaseModel):
    force: bool = True


def _safe_meta(meta_json: str) -> dict:
    try:
        return json.loads(meta_json or "{}")
    except Exception:
        return {}


def _calc_recall_confidence(keyword: str, content: str, meta: dict) -> float:
    if not keyword.strip():
        return 0.5
    content_l = (content or "").lower()
    key_l = keyword.lower()
    hits = content_l.count(key_l)

    score = min(0.95, 0.3 + min(0.45, hits * 0.08))
    tags = (meta.get("tags") or {}) if isinstance(meta, dict) else {}
    value_signal = float((tags.get("value_signal_score") or 0))
    if value_signal > 0:
        score = min(0.98, score + min(0.2, value_signal * 0.03))
    return round(max(0.05, score), 3)


def _build_expand_chain(db: Session, base: MemoryDigest, reveal_to_level: int) -> list[MemoryDigest]:
    reveal_to_level = max(0, min(2, reveal_to_level))
    chain = [base]
    current = base

    while current.parent_id is not None and current.level > reveal_to_level:
        parent = db.query(MemoryDigest).filter(MemoryDigest.id == current.parent_id).first()
        if not parent:
            break
        chain.append(parent)
        current = parent

    return chain



# ── 端点 ──

@router.get("/context")
def get_context(
    user_id: str = "default_user",
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """返回拼合后的系统设定 + 用户画像 + 近期上下文，供前端注入脚本使用。"""
    persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
    sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()
    
    # 提取最近上下文 (Stateless Sliding Window)
    recent_logs = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == user_id)
        .order_by(ChatLog.id.desc())
        .limit(20)
        .all()
    )
    recent_logs.reverse()
    context_lines = []
    for lg in recent_logs:
        speaker = "User" if lg.role == "user" else "Assistant"
        context_lines.append(f"{speaker}: {lg.content}")
        
    recent_context_summary = run_autocompact_circuit_breaker(context_lines, max_length=4000)

    return {
        "user_id": user_id,
        "persona_json": persona_obj.persona_json if persona_obj else "{}",
        "system_prompt": sys_obj.prompt_text if sys_obj else "你是一个具备自进化能力的智能助手。",
        "recent_context_summary": recent_context_summary
    }


@router.post("/log")
def submit_log(
    log_req: LogRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """接收聊天记录，累积到阈值后触发后台自进化。"""
    # 1. 自动注册用户
    if not db.query(User).filter(User.id == log_req.user_id).first():
        db.add(User(id=log_req.user_id))
        db.commit()

    # 2. 写入日志
    db.add(ChatLog(
        user_id=log_req.user_id,
        role=log_req.role,
        content=log_req.content,
        processed=0,
    ))
    db.commit()

    # 3. 检查阈值
    pending = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == log_req.user_id, ChatLog.processed == 0)
        .count()
    )
    if pending >= EVOLUTION_THRESHOLD:
        background_tasks.add_task(evolution_task, log_req.user_id)

    return {"status": "ok", "unprocessed_logs": pending}

@router.post("/log_ambient")
def submit_ambient_log(
    req: AmbientLogRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """专门接收前台悄无声息收集的环境窥屏包，设为已处理，不消耗高级分析算力，只做持久化备份"""
    actual_user_id = f"group_{req.group_id}"
    
    # BUG-15 FIX: ensure User is committed before ChatLog
    if not db.query(User).filter(User.id == actual_user_id).first():
        db.add(User(id=actual_user_id))
        db.commit()
        
    formatted_content = f"[{req.sender_name}]: {req.content}"
    
    db.add(ChatLog(
        user_id=actual_user_id,
        session_id=str(req.group_id),
        sender_name=req.sender_name,
        session_name=req.session_name,
        role="ambient",
        content=formatted_content,
        processed=1,
    ))
    db.commit()
    return {"status": "ok", "message": "ambient log saved"}

@router.get("/search_logs")
def search_history_logs(
    user_id: str,
    keyword: str = "",
    limit: int = 50,
    context_size: int = 0,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    提供给 Dify Agent 作为 Custom Tool 调用的数据库本地精确检索 API。
    实现无需全量 RAG 的按需、极速精准回忆。带有上下文支持。
    """
    base_query = db.query(ChatLog)
    if user_id != "all":
        # 【弹性搜索核心】：允许 user_id 匹配 ID 或者 模糊匹配人名/场景名
        # 【优先精确匹配】
        exact_match = base_query.filter(
            or_(
                ChatLog.user_id == user_id,
                ChatLog.session_id == user_id
            )
        )
        if exact_match.count() > 0:
            base_query = exact_match
        else:
            # 环境模糊匹配兜底
            base_query = base_query.filter(
                or_(
                    ChatLog.sender_name.like(f"%{user_id}%"),
                    ChatLog.session_name.like(f"%{user_id}%")
                )
            )
    
    if not keyword:
        # 无关键词：直接返回最新记录
        results = base_query.order_by(ChatLog.id.desc()).limit(limit).all()
        results.reverse()
        final_logs = results
    else:
        # 有关键词：查找匹配及其上下文
        matches = base_query.filter(ChatLog.content.like(f"%{keyword}%")).order_by(ChatLog.id.desc()).limit(limit).all()
        
        log_dict = {}
        for match in matches:
            log_dict[match.id] = match
            
            if context_size > 0:
                # 查找上下文，必须限制在同一个 session_id（对话场）中，确保逻辑连贯
                # 向上查找
                before_logs = db.query(ChatLog).filter(
                    ChatLog.session_id == match.session_id,
                    ChatLog.id < match.id
                ).order_by(ChatLog.id.desc()).limit(context_size).all()
                
                # 向下查找
                after_logs = db.query(ChatLog).filter(
                    ChatLog.session_id == match.session_id,
                    ChatLog.id > match.id
                ).order_by(ChatLog.id.asc()).limit(context_size).all()
                
                for log in before_logs + after_logs:
                    log_dict[log.id] = log
                
        # 按照 ID 升序重排，确保呈现给 AI 的是正确的时间顺序
        final_logs = [log_dict[log_id] for log_id in sorted(log_dict.keys())]
        
    filtered_output = []
    for row in final_logs:
        t = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # 来源标识：跨群搜索时很有用
        source = f"[{row.session_id}]"
        filtered_output.append(f"[{t}]{source} {row.role.upper()}: {row.content}")
        
    return {
        "status": "ok",
        "results_found": len(filtered_output),
        "logs": "\n".join(filtered_output) if filtered_output else "未检索到匹配结果。"
    }

@router.get("/render")
async def render_markdown(text: str):
    """遗留端点，已弃用。目前直接内嵌 base64 返回"""
    return {"status": "deprecated"}



@router.post("/chat")
async def proxy_chat(
    req: ChatProxyRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    统一网关：接收客户端的发问，通过 KT Agent 处理，返回结果并双向落库。
    """
    logger.info(f"[/chat] Request START: user={req.user_id}, session={req.session_id}, query={req.query[:100]}, sender={req.sender_name}, files={req.files}, session_name={req.session_name}")

    # 1. 自动注册用户 & 场 (前置校验)
    for target_id in [req.user_id, req.session_id]:
        if not db.query(User).filter(User.id == target_id).first():
            db.add(User(id=target_id))
    db.commit()

    # 2. 加载用户画像 (PersonaArchitectAgent 实际输出的键: identity, communication_style, domain_profiles, persona_summary)
    # 兼容性：bot 端 user_id 格式可能变化（"12345" vs "private_12345" vs "group_xxx"）
    # 逐一尝试所有可能的 ID 变体
    def _find_persona(db: Session, uid: str) -> Persona | None:
        candidates: list[str] = [uid]
        # 添加前缀变体
        for prefix in ("private_", "group_"):
            if not uid.startswith(prefix):
                candidates.append(f"{prefix}{uid}")
        # 剥离前缀变体
        for prefix in ("private_", "group_"):
            if uid.startswith(prefix):
                candidates.append(uid[len(prefix):])
        # 去重后依次尝试
        for c in dict.fromkeys(candidates):
            p = db.query(Persona).filter(Persona.user_id == c).first()
            if p:
                if c != candidates[0]:
                    logger.info(f"[/chat] Persona found via fallback: tried={candidates[0]}, matched={c}")
                return p
        logger.debug(f"[/chat] No persona for user_id={uid} (tried {len(candidates)} variants)")
        return None
    persona_obj = _find_persona(db, req.user_id)
    persona_json_str = persona_obj.persona_json if persona_obj else "{}"
    try:
        persona_data = json.loads(persona_json_str)
    except (json.JSONDecodeError, TypeError):
        persona_data = {}
    persona_text = ""
    if isinstance(persona_data, dict) and persona_data:
        parts = []
        # persona_summary: overall self-description
        summary = str(persona_data.get("persona_summary", "")).strip()
        if summary:
            parts.append(summary)
        # identity: core user identity dict (role, name, etc.)
        identity = persona_data.get("identity")
        if isinstance(identity, dict) and identity:
            ident_parts = []
            for k, v in identity.items():
                if v and str(v).strip():
                    ident_parts.append(f"{k}: {v}")
            if ident_parts:
                parts.append("身份: " + "; ".join(ident_parts))
        # communication_style: how user prefers to communicate
        comm_style = str(persona_data.get("communication_style", "")).strip()
        if comm_style:
            parts.append(f"沟通风格: {comm_style}")
        # domain_profiles: per-domain expertise/interests
        domains = persona_data.get("domain_profiles", {})
        if isinstance(domains, dict) and domains:
            domain_lines = []
            for domain, info in domains.items():
                if isinstance(info, dict):
                    desc = str(info.get("summary", info.get("description", ""))).strip()
                    if desc:
                        domain_lines.append(f"{domain}: {desc}")
            if domain_lines:
                parts.append("领域画像:\n" + "\n".join(f"  - {line}" for line in domain_lines))
        # Fallback: unrecognized structure
        if not parts:
            raw = json.dumps(persona_data, ensure_ascii=False)
            if len(raw) > 10:
                parts.append(f"画像数据: {raw[:500]}")
        persona_text = _cap_text("\n".join(parts), 5000, "persona")

    logger.info(
        f"[/chat] Persona lookup: user_id={req.user_id}, "
        f"found={persona_obj is not None}, persona_raw_len={len(persona_json_str)}, "
        f"persona_text_len={len(persona_text)}, "
        f"keys={list(persona_data.keys()) if isinstance(persona_data, dict) else 'N/A'}"
    )

    # 3. 构建会话记忆上下文
    memory_context = _build_session_memory(db, req.session_id, max_messages=20)

    # 4. 组装 enriched query — 只注入会话记忆（用户画像通过 metadata 传递，由 bridge 注入为 system 消息；
    #    系统提示词已由 KT evolution 写入 SystemPrompt 表，再由 PromptAuditorAgent 合并到 KT system prompt）
    enriched_query = req.query
    if memory_context:
        enriched_query = _cap_text(
            f"{memory_context}\n\n[当前消息]\n用户: {req.query}", 20000, "enriched_query"
        )
        logger.info(
            f"[/chat] Enriched query: memory={len(memory_context)}chars, "
            f"total={len(enriched_query)}chars, est_tokens={len(enriched_query)//2}"
        )

    # 5. 通过 KT Bridge 调用 Agent (KT 自动处理工具循环、session 管理等)
    bridge = get_bridge()
    try:
        answer = await bridge.handle_message(
            enriched_query,
            user_id=req.user_id,
            session_id=req.session_id,
            sender_name=req.sender_name or "",
            metadata={
                "session_name": req.session_name,
                "files": req.files,
                "persona_text": persona_text,
                "raw_query": req.query,  # original user message for routing (not enriched)
            }
        )
    except Exception as e:
        logger.error(f"[/chat] KT Agent failed: {e}")
        raise HTTPException(status_code=502, detail=f"KT Error: {str(e)}")

    logger.info(f"[/chat] Bridge returned: answer_len={len(answer)}, answer_stripped_empty={not answer.strip()}")
    if answer:
        logger.debug(f"[/chat] Answer preview: {answer[:300]}")
    else:
        logger.warning(f"[/chat] EMPTY ANSWER returned from bridge!")

    # 3. 落库 (KT 的 session 管理是独立的, nanobot 原有日志需手动写入)
    db.add(ChatLog(
        user_id=req.user_id,
        session_id=req.session_id,
        role="user",
        content=req.query,
        sender_name=req.sender_name or "",
        session_name=req.session_name or "",
        processed=0,
    ))
    db.add(ChatLog(
        user_id=req.user_id,
        session_id=req.session_id,
        role="assistant",
        content=answer,
        sender_name="nanobot",
        session_name=req.session_name or "",
        processed=0,
    ))
    db.commit()

    # 4. 检查进化触发阈值 (按 user_id 计数，而非 session_id，确保个人消息足够才触发进化)
    pending = db.query(ChatLog).filter(ChatLog.user_id == req.user_id, ChatLog.processed == 0).count()
    if pending >= EVOLUTION_THRESHOLD:
        logger.info(f"[/chat] Evolution triggered: user={req.user_id}, pending={pending}, threshold={EVOLUTION_THRESHOLD}")
        background_tasks.add_task(evolution_task, req.user_id)

    # 5. 模拟短对话：内容自动拆分逻辑
    if not answer.strip():
        answer_chunks = []
    elif "\n\n" in answer:
        answer_chunks = [c.strip() for c in answer.split("\n\n") if c.strip()]
    elif len(answer) > 300:
        answer_chunks = [answer]
    else:
        answer_chunks = [answer]

    logger.info(f"[/chat] Response: answer_chunks_count={len(answer_chunks)}, status=ok")
    return {
        "status": "ok",
        "user_id": req.user_id,
        "answer": answer,
        "answer_chunks": answer_chunks,
        "unprocessed_logs": pending
    }

@router.post("/evolution/trigger")
def trigger_evolution(
    req: EvolutionTriggerRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_token),
):
    """
    手动触发自进化：通过 API 强制开启画像提炼与同步，不再依赖日志计数阈值。
    """
    logger.info(f"Manual evolution triggered for user [{req.user_id}]")
    background_tasks.add_task(evolution_task, req.user_id)
    return {"status": "ok", "message": f"Evolution task queued for {req.user_id}"}


@router.get("/memory/digests")
def get_memory_digests(
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    level: int = -1,
    limit: int = 50,
    include_content: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """按条件查询每日记忆摘要（支持渐进式披露层级）。"""
    query = db.query(MemoryDigest)
    if user_id:
        query = query.filter(MemoryDigest.user_id == user_id)
    if session_id:
        query = query.filter(MemoryDigest.session_id == session_id)
    if digest_date:
        query = query.filter(MemoryDigest.digest_date == digest_date)
    if level >= 0:
        query = query.filter(MemoryDigest.level == level)

    rows = query.order_by(MemoryDigest.id.desc()).limit(max(1, min(limit, 500))).all()

    items = []
    for r in rows:
        try:
            meta = json.loads(r.meta_json or "{}")
        except Exception:
            meta = {}

        item = {
            "id": r.id,
            "user_id": r.user_id,
            "session_id": r.session_id,
            "digest_date": r.digest_date,
            "level": r.level,
            "parent_id": r.parent_id,
            "source_start_log_id": r.source_start_log_id,
            "source_end_log_id": r.source_end_log_id,
            "meta": meta,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        if include_content:
            item["content"] = r.content
        items.append(item)

    return {
        "status": "ok",
        "count": len(items),
        "digests": items,
    }


@router.post("/memory/digests/run")
def run_memory_digests(
    req: MemoryDigestRunRequest,
    _auth=Depends(verify_token),
):
    """手动触发指定日期的每日记忆摘要任务。"""
    from datetime import datetime, timedelta

    target_date = req.target_date
    if not target_date:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    created = generate_daily_digest_for_date(target_date=target_date, user_id=req.user_id)
    return {
        "status": "ok",
        "target_date": target_date,
        "created_sessions": created,
    }


@router.get("/models/list")
def list_models(
    provider: str = "new-api",
    tier: str = "",
    _auth=Depends(verify_token),
):
    """查看本地模型注册表中的模型列表。"""
    items = registry.get_models_by_provider(provider)
    if tier:
        items = [m for m in items if (m.get("tier") or "") == tier]
    return {
        "status": "ok",
        "provider": provider,
        "count": len(items),
        "last_updated": registry.data.get("last_updated", "never"),
        "models": items,
    }


@router.post("/models/sync")
async def sync_models(
    req: ModelSyncRequest,
    _auth=Depends(verify_token),
):
    """从 new-api 拉取模型列表并同步至本地 registry。"""
    from config import NEW_API_KEY, NEW_API_BASE_URL

    if not NEW_API_KEY:
        raise HTTPException(status_code=400, detail="NEW_API_KEY is missing")

    client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
    updated = await client.sync_models_to_registry(force=req.force)

    return {
        "status": "ok",
        "updated": updated,
        "last_updated": registry.data.get("last_updated", "never"),
    }


@router.get("/memory/recall")
def recall_memory(
    keyword: str,
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    limit: int = 20,
    reveal_to_level: int = 2,
    include_content: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    记忆召回：优先命中 level=2（紧凑层），再按需向 level=1/0 展开。
    返回每条结果的置信度和来源日志范围。
    """
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")

    reveal_to_level = max(0, min(2, reveal_to_level))
    query = db.query(MemoryDigest).filter(MemoryDigest.level == 2)
    if user_id:
        query = query.filter(MemoryDigest.user_id == user_id)
    if session_id:
        query = query.filter(MemoryDigest.session_id == session_id)
    if digest_date:
        query = query.filter(MemoryDigest.digest_date == digest_date)

    # First-pass: compact digest hit.
    hits = (
        query.filter(MemoryDigest.content.like(f"%{keyword}%"))
        .order_by(MemoryDigest.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )

    # Fallback: allow metadata hit if compact content has no direct match.
    if not hits:
        hits = (
            query.filter(MemoryDigest.meta_json.like(f"%{keyword}%"))
            .order_by(MemoryDigest.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )

    results = []
    for item in hits:
        meta = _safe_meta(item.meta_json)
        confidence = _calc_recall_confidence(keyword, item.content or "", meta)
        chain = _build_expand_chain(db, item, reveal_to_level=reveal_to_level)

        expanded = []
        for d in sorted(chain, key=lambda x: x.level, reverse=True):
            node = {
                "id": d.id,
                "level": d.level,
                "parent_id": d.parent_id,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            if include_content:
                node["content"] = d.content
            expanded.append(node)

        results.append(
            {
                "digest_id": item.id,
                "user_id": item.user_id,
                "session_id": item.session_id,
                "digest_date": item.digest_date,
                "confidence": confidence,
                "source_range": {
                    "start_log_id": item.source_start_log_id,
                    "end_log_id": item.source_end_log_id,
                },
                "meta": meta,
                "revealed_chain": expanded,
            }
        )

    # Also recall news tool artifacts from SQL tool logs, as a unified memory lane.
    news_hits = (
        db.query(ChatLog)
        .filter(ChatLog.role == "tool", ChatLog.content.like("%[news_search]%"), ChatLog.content.like(f"%{keyword}%"))
        .order_by(ChatLog.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    news_items = []
    for row in news_hits:
        news_items.append(
            {
                "log_id": row.id,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "confidence": _calc_recall_confidence(keyword, row.content or "", {}),
                "source_range": {
                    "start_log_id": row.id,
                    "end_log_id": row.id,
                },
                "content": row.content if include_content else None,
            }
        )

    return {
        "status": "ok",
        "keyword": keyword,
        "digest_hits": len(results),
        "news_hits": len(news_items),
        "items": results,
        "news_items": news_items,
    }


@router.get("/health")
def health_check():
    """健康检查端点。"""
    return {"status": "healthy", "version": "0.2.0"}
