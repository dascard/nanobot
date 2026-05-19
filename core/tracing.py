import json
import logging
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("nanobot.tracing")

SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")
MAX_JSON_CHARS = 8000
MAX_PREVIEW_CHARS = 2000


@dataclass
class RunHandle:
    run_id: str
    trace_id: str


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def new_tool_call_id() -> str:
    return f"tool_{uuid.uuid4().hex}"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_str = str(key)
            if any(part in key_str.lower() for part in SENSITIVE_KEY_PARTS):
                redacted[key_str] = "[REDACTED]"
            else:
                redacted[key_str] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _json_dumps(value: Any, *, max_chars: int = MAX_JSON_CHARS) -> str:
    try:
        text = json.dumps(_redact(value), ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps(str(value), ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _preview(value: Any, *, max_chars: int = MAX_PREVIEW_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = _json_dumps(value, max_chars=max_chars)
    text = text.replace("\x00", "")
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _prompt_preview(content: str, *, max_chars: int = 1000) -> str:
    if not content:
        return ""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    head_limit = max(1, max_chars - 80)
    head = content[:head_limit]
    return f"{head}\n...[prompt_sha256:{digest} chars:{len(content)}]"


def _session():
    from core import database

    return database.SessionLocal()


class RunTracer:
    @staticmethod
    def start_run(
        *,
        trace_id: str = "",
        session_id: str = "",
        user_id: str = "",
        chat_type: str = "",
        group_id: str = "",
        run_type: str = "chat",
        prompt_mode: str = "legacy",
        prompt_key: str = "",
        model: str = "",
        input_preview: str = "",
        meta: dict[str, Any] | None = None,
    ) -> RunHandle:
        run_id = new_run_id()
        trace_id = trace_id or new_trace_id()
        try:
            from core.database import AgentRun

            db = _session()
            try:
                db.add(AgentRun(
                    run_id=run_id,
                    trace_id=trace_id,
                    session_id=str(session_id or "")[:128],
                    user_id=str(user_id or "")[:128],
                    chat_type=str(chat_type or "")[:32],
                    group_id=str(group_id or "")[:128],
                    run_type=str(run_type or "chat")[:32],
                    prompt_mode=str(prompt_mode or "legacy")[:32],
                    prompt_key=str(prompt_key or "")[:96],
                    model=str(model or "")[:160],
                    status="running",
                    input_preview=_preview(input_preview, max_chars=1000),
                    meta_json=_json_dumps(meta or {}, max_chars=3000),
                    started_at=datetime.now(),
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("agent_run start failed: %s", e)
        return RunHandle(run_id=run_id, trace_id=trace_id)

    @staticmethod
    def finish_run(
        run_id: str,
        *,
        status: str = "success",
        output_preview: Any = "",
        error: str = "",
        latency_ms: int | None = None,
        model: str = "",
        finished_at: datetime | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not run_id:
            return
        try:
            from core.database import AgentRun

            db = _session()
            try:
                row = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                if not row:
                    return
                row.status = str(status or "success")[:32]
                row.output_preview = _preview(output_preview, max_chars=1000)
                row.error = _preview(error, max_chars=1000)
                if latency_ms is not None:
                    row.latency_ms = int(latency_ms)
                if model:
                    row.model = str(model)[:160]
                if meta is not None:
                    row.meta_json = _json_dumps(meta, max_chars=3000)
                row.finished_at = finished_at or datetime.now()
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("agent_run finish failed: %s", e)


class ToolTracer:
    @staticmethod
    def start_tool_call(
        trace_id: str,
        run_id: str,
        tool_name: str,
        args: Any,
    ) -> str:
        if not trace_id and not run_id:
            return ""
        tool_call_id = new_tool_call_id()
        try:
            from core.database import ToolCall

            db = _session()
            try:
                db.add(ToolCall(
                    tool_call_id=tool_call_id,
                    trace_id=str(trace_id or "")[:64],
                    run_id=str(run_id or "")[:80],
                    tool_name=str(tool_name or "")[:128],
                    args_json=_json_dumps(args),
                    status="running",
                    started_at=datetime.now(),
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("tool_call start failed: %s", e)
            return ""
        return tool_call_id

    @staticmethod
    def finish_tool_call(
        tool_call_id: str,
        *,
        status: str = "success",
        result: Any = None,
        error: str = "",
        latency_ms: int | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        if not tool_call_id:
            return
        try:
            from core.database import ToolCall

            db = _session()
            try:
                row = db.query(ToolCall).filter(ToolCall.tool_call_id == tool_call_id).first()
                if not row:
                    return
                row.status = str(status or "success")[:32]
                row.result_preview = _preview(result)
                row.error = _preview(error, max_chars=1000)
                if latency_ms is not None:
                    row.latency_ms = int(latency_ms)
                row.finished_at = finished_at or datetime.now()
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("tool_call finish failed: %s", e)


class PromptTracer:
    @staticmethod
    def record_render(
        *,
        trace_id: str = "",
        run_id: str = "",
        prompt_key: str = "",
        mode: str = "preview",
        variables: Any = None,
        rendered_content: str = "",
        token_estimate: int = 0,
        warnings: list[str] | None = None,
        error: str = "",
    ) -> None:
        try:
            from core.database import PromptRenderLog

            db = _session()
            try:
                db.add(PromptRenderLog(
                    trace_id=str(trace_id or "")[:64],
                    run_id=str(run_id or "")[:80],
                    prompt_key=str(prompt_key or "")[:96],
                    mode=str(mode or "preview")[:32],
                    variables_json=_json_dumps(variables or {}, max_chars=6000),
                    rendered_preview=_prompt_preview(rendered_content, max_chars=1000),
                    token_estimate=int(token_estimate or 0),
                    warnings_json=_json_dumps(warnings or [], max_chars=2000),
                    error=_preview(error, max_chars=1000),
                    created_at=datetime.now(),
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("prompt_render_log failed: %s", e)


class LLMRequestTracer:
    @staticmethod
    def record_request(
        *,
        trace_id: str = "",
        run_id: str = "",
        source: str = "",
        provider: str = "",
        model: str = "",
        url: str = "",
        method: str = "POST",
        headers: Any = None,
        request: Any = None,
        status: str = "created",
        response_status: int = 0,
        error: str = "",
    ) -> None:
        try:
            from core.database import LLMApiRequestLog

            db = _session()
            try:
                db.add(LLMApiRequestLog(
                    trace_id=str(trace_id or "")[:64],
                    run_id=str(run_id or "")[:80],
                    source=str(source or "")[:64],
                    provider=str(provider or "")[:64],
                    model=str(model or "")[:160],
                    url=str(url or ""),
                    method=str(method or "POST")[:16],
                    headers_json=_json_dumps(headers or {}, max_chars=12000),
                    request_json=_json_dumps(request or {}, max_chars=200000),
                    request_preview=_preview(request or {}, max_chars=4000),
                    status=str(status or "created")[:32],
                    response_status=int(response_status or 0),
                    error=_preview(error, max_chars=2000),
                    created_at=datetime.now(),
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("llm api request log failed: %s", e)


def row_to_dict(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[col.name] = value
    return data
