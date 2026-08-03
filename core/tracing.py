import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from foundation.llm.cache_usage import (
    CACHE_STATUS_MISS,
    CACHE_STATUS_PENDING,
    normalize_llm_cache_usage,
)
from foundation.llm.cache_shape import (
    build_llm_cache_shape,
    infer_cache_miss_reason,
)

from core.prompt_v2.template_resolution import serialize_template_resolutions_json
from core.safe_diagnostics import safe_response_summary, safe_url_for_logging
from core.time_utils import db_now_naive, to_db_naive
from core.tool_registry import get_tool_descriptor

logger = logging.getLogger("nanobot.tracing")

SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")
SAFE_NUMERIC_TOKEN_KEYS = frozenset({
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_write_tokens",
    "cached_tokens",
    "completion_tokens",
    "input_tokens",
    "max_tokens",
    "message_token_estimate",
    "output_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "prompt_tokens",
    "tokens_cached",
    "tool_schema_token_estimate",
    "token_estimate",
    "total_cached_tokens",
    "total_tokens",
})
MAX_PREVIEW_CHARS = 2000
MAX_WEB_SEARCH_PREVIEW_CHARS = 40000
MAX_LLM_REQUEST_JSON_CHARS = 256_000
MAX_LLM_RESPONSE_JSON_CHARS = 64_000
MAX_LLM_FAILURE_SUMMARY_CHARS = 4_000
MAX_SANDBOX_TRACE_COMMAND_BYTES = 16 * 1024


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
            key_lower = key_str.lower()
            is_safe_numeric_metric = (
                key_lower in SAFE_NUMERIC_TOKEN_KEYS
                and isinstance(item, int)
                and not isinstance(item, bool)
            )
            if (
                not is_safe_numeric_metric
                and any(part in key_lower for part in SENSITIVE_KEY_PARTS)
            ):
                redacted[key_str] = "[REDACTED]"
            else:
                redacted[key_str] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _json_dumps(value: Any, *, max_chars: int = 0) -> str:
    try:
        text = json.dumps(_redact(value), ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps(str(value), ensure_ascii=False)
    if max_chars > 0 and len(text) > max_chars:
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


def _text_audit(value: Any) -> dict[str, Any]:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    return {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _safe_sandbox_path(value: Any, *, allow_empty: bool = False) -> str:
    try:
        from core.sandbox.paths import validate_relative_path

        components = validate_relative_path(str(value or ""), allow_empty=allow_empty)
        return "/".join(components)
    except Exception:
        return "[INVALID_PATH]"


def _safe_sandbox_ref(value: Any) -> str | dict[str, Any]:
    ref = str(value or "")
    if ref.startswith("asset://sha256/"):
        digest = ref.removeprefix("asset://sha256/")
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
            return ref
    if ref.startswith("workspace://current/"):
        path = _safe_sandbox_path(ref.removeprefix("workspace://current/"))
        if path != "[INVALID_PATH]":
            return f"workspace://current/{path}"
    return {"ref_omitted": True, **_text_audit(ref)}


def _safe_sandbox_command(value: Any) -> str:
    command = str(value or "")
    safe_command = safe_response_summary(
        command,
        max_chars=MAX_SANDBOX_TRACE_COMMAND_BYTES,
    )
    encoded = safe_command.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_SANDBOX_TRACE_COMMAND_BYTES:
        return safe_command
    suffix = b"...[TRUNCATED]"
    head = encoded[:MAX_SANDBOX_TRACE_COMMAND_BYTES - len(suffix)]
    return head.decode("utf-8", errors="ignore") + suffix.decode("ascii")


def _uses_metadata_only_trace(tool_name: str) -> bool:
    """从类型化工具描述符读取 Trace 策略，避免维护第二份工具名集合。"""

    descriptor = get_tool_descriptor(str(tool_name or ""))
    return descriptor is not None and descriptor.trace_policy == "metadata_only"


def sanitize_tool_trace_args(tool_name: str, args: Any) -> Any:
    """Sandbox 工具参数进入持久 Trace 前执行有界脱敏。"""

    name = str(tool_name or "")
    if not _uses_metadata_only_trace(name):
        return args
    if not isinstance(args, Mapping):
        return {"args_omitted": True, "args_type": type(args).__name__}
    unknown_count = len(set(str(key) for key in args) - {
        "command", "cwd", "timeout_seconds", "path", "cursor", "limit",
        "offset", "query", "mode", "pattern", "glob", "ignore_case",
        "max_depth", "content", "overwrite", "operations", "source_ref",
        "logical_name", "media_type", "yield_time_ms", "process_id", "chars",
        "patch",
    })
    result: dict[str, Any] = {}
    if unknown_count:
        result["rejected_field_count"] = unknown_count
    if name == "sandbox_exec":
        command = str(args.get("command") or "")
        safe_command = _safe_sandbox_command(command)
        result.update({
            "command": safe_command,
            "command_sanitized": safe_command != command,
            "command_lines": command.count("\n") + (1 if command else 0),
            **{f"command_{key}": value for key, value in _text_audit(command).items()},
            "cwd": _safe_sandbox_path(args.get("cwd"), allow_empty=True),
        })
        if args.get("timeout_seconds") is not None:
            result["timeout_seconds"] = args.get("timeout_seconds")
        if args.get("yield_time_ms") is not None:
            result["yield_time_ms"] = args.get("yield_time_ms")
        return result
    if name in {"sandbox_poll", "sandbox_terminate"}:
        result["process_id"] = str(args.get("process_id") or "")[:64]
        if name == "sandbox_poll" and args.get("cursor") is not None:
            result["cursor"] = str(args.get("cursor") or "")[:96]
        return result
    if name == "sandbox_write_stdin":
        chars = str(args.get("chars") or "")
        return {
            **result,
            "process_id": str(args.get("process_id") or "")[:64],
            "chars_omitted": True,
            **{
                f"chars_{key}": value
                for key, value in _text_audit(chars).items()
            },
        }
    if name == "workspace_write":
        content = str(args.get("content") or "")
        return {
            **result,
            "path": _safe_sandbox_path(args.get("path")),
            "cwd": _safe_sandbox_path(args.get("cwd"), allow_empty=True),
            "overwrite": bool(args.get("overwrite", False)),
            "content_omitted": True,
            **{f"content_{key}": value for key, value in _text_audit(content).items()},
        }
    if name == "workspace_edit":
        operations = (
            args.get("operations")
            if isinstance(args.get("operations"), list)
            else []
        )
        safe_operations: list[dict[str, Any]] = []
        for operation in operations[:50]:
            if not isinstance(operation, Mapping):
                safe_operations.append({
                    "operation_omitted": True,
                    "operation_type": type(operation).__name__,
                })
                continue
            if "diff" in operation:
                diff = str(operation.get("diff") or "")
                safe_operations.append({
                    "kind": "diff",
                    "diff_omitted": True,
                    "diff_lines": diff.count("\n") + (1 if diff else 0),
                    **{
                        f"diff_{key}": value
                        for key, value in _text_audit(diff).items()
                    },
                })
                continue
            old = str(operation.get("old") or "")
            new = str(operation.get("new") or "")
            safe_operations.append({
                "kind": "exact",
                "path": _safe_sandbox_path(operation.get("path")),
                "replace_all": operation.get("replace_all") is True,
                "old_omitted": True,
                "new_omitted": True,
                **{
                    f"old_{key}": value
                    for key, value in _text_audit(old).items()
                },
                **{
                    f"new_{key}": value
                    for key, value in _text_audit(new).items()
                },
            })
        return {
            **result,
            "cwd": _safe_sandbox_path(args.get("cwd"), allow_empty=True),
            "operations": safe_operations,
            "operation_count": len(operations),
            "operations_truncated": len(operations) > len(safe_operations),
        }
    if name == "workspace_apply_patch":
        patch = str(args.get("patch") or "")
        return {
            **result,
            "path": _safe_sandbox_path(args.get("path")),
            "patch_omitted": True,
            "patch_lines": patch.count("\n") + (1 if patch else 0),
            **{
                f"patch_{key}": value
                for key, value in _text_audit(patch).items()
            },
        }
    if name == "workspace_search":
        pattern = str(args.get("pattern") or args.get("query") or "")
        return {
            **result,
            "mode": str(args.get("mode") or "content")[:16],
            "path": _safe_sandbox_path(args.get("path"), allow_empty=True),
            "cwd": _safe_sandbox_path(args.get("cwd"), allow_empty=True),
            "glob_omitted": bool(args.get("glob")),
            **{f"glob_{key}": value for key, value in _text_audit(args.get("glob")).items()},
            "pattern_omitted": True,
            **{
                f"pattern_{key}": value
                for key, value in _text_audit(pattern).items()
            },
            "ignore_case": args.get("ignore_case") is True,
            "max_depth": args.get("max_depth"),
            "limit": args.get("limit"),
            "cursor": str(args.get("cursor") or "")[:2048],
        }
    if name in {"workspace_list", "workspace_read", "asset_publish"}:
        result["path"] = _safe_sandbox_path(
            args.get("path"),
            allow_empty=name == "workspace_list",
        )
        if name in {"workspace_list", "workspace_read"}:
            result["cwd"] = _safe_sandbox_path(
                args.get("cwd"),
                allow_empty=True,
            )
        for key in ("cursor", "limit", "offset", "media_type"):
            if args.get(key) is not None:
                result[key] = args.get(key)
        return result
    if name == "asset_import":
        result["source_ref"] = _safe_sandbox_ref(args.get("source_ref"))
        if args.get("logical_name") is not None:
            result["logical_name"] = _safe_sandbox_path(args.get("logical_name"))
        return result
    return {"args_omitted": True}


def _sandbox_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    artifacts: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, Any] = {
            "type": str(item.get("type") or "")[:64],
            "ref": _safe_sandbox_ref(item.get("ref")),
        }
        if item.get("path") is not None:
            safe["path"] = _safe_sandbox_path(item.get("path"))
        if item.get("logical_name") is not None:
            safe["logical_name"] = _safe_sandbox_path(item.get("logical_name"))
        if item.get("size_bytes") is not None:
            safe["size_bytes"] = item.get("size_bytes")
        artifacts.append(safe)
    return artifacts


def _sandbox_result_data(tool_name: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    if tool_name in {
        "sandbox_exec",
        "sandbox_poll",
        "sandbox_write_stdin",
        "sandbox_terminate",
    }:
        safe = {
            key: data.get(key)
            for key in (
                "run_id", "process_id", "lease_id", "profile_id",
                "execution_status", "process_state", "exit_code",
                "termination_reason", "oom_killed",
                "cpu_time_ms", "peak_memory_bytes", "stdout_bytes",
                "stderr_bytes", "stdout_truncated", "stderr_truncated",
                "workspace_used_bytes", "next_cursor", "written_bytes",
                "lease_recycled", "termination_scope",
                "workspace_preserved", "runtime_preserved",
            )
            if key in data
        }
        affected = data.get("affected_process_ids")
        if isinstance(affected, list):
            safe["affected_process_ids"] = [
                str(item or "")[:64]
                for item in affected[:100]
            ]
        active = data.get("active_processes")
        if isinstance(active, list):
            safe["active_processes"] = [
                {
                    "process_id": str(item.get("process_id") or "")[:64],
                    "state": str(item.get("state") or "")[:32],
                }
                for item in active[:100]
                if isinstance(item, Mapping)
            ]
        for stream_name in (
            "stdout",
            "stderr",
            "stdout_delta",
            "stderr_delta",
        ):
            if stream_name not in data:
                continue
            audit = _text_audit(data.get(stream_name))
            safe[f"{stream_name}_omitted"] = True
            safe[f"{stream_name}_sha256"] = audit["sha256"]
            safe.setdefault(f"{stream_name}_bytes", audit["bytes"])
        return safe
    if tool_name == "workspace_read":
        safe = {
            key: data.get(key)
            for key in (
                "protocol_version",
                "start_offset",
                "offset",
                "returned_lines",
                "next_offset",
                "total_lines",
                "returned_bytes",
                "size_bytes",
                "eof",
                "binary",
                "line_truncated",
                "output_truncated",
                "truncation_reason",
            )
            if key in data
        }
        safe["path"] = _safe_sandbox_path(data.get("path"))
        content_audit = _text_audit(data.get("content"))
        safe.update({
            "content_omitted": True,
            "content_bytes": content_audit["bytes"],
            "content_sha256": content_audit["sha256"],
        })
        return safe
    if tool_name == "workspace_search":
        matches = data.get("matches") if isinstance(data.get("matches"), list) else []
        safe_matches = []
        texts = []
        for item in matches[:200]:
            if not isinstance(item, Mapping):
                continue
            safe_matches.append({
                "path": _safe_sandbox_path(item.get("path")),
                "line": item.get("line"),
                "text_omitted": True,
            })
            texts.append(str(item.get("text") or ""))
        text_audit = _text_audit("\n".join(texts))
        return {
            "matches": safe_matches,
            "match_count": len(matches),
            "matched_text_bytes": text_audit["bytes"],
            "matched_text_sha256": text_audit["sha256"],
            "scanned_files": data.get("scanned_files"),
            "scanned_bytes": data.get("scanned_bytes"),
            "skipped_binary_files": data.get("skipped_binary_files"),
            "skipped_ignored_files": data.get("skipped_ignored_files"),
            "truncated": data.get("truncated"),
            "truncation_reason": data.get("truncation_reason"),
            "next_cursor_present": bool(data.get("next_cursor")),
        }
    if tool_name == "workspace_list":
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        return {
            "entries": [
                {
                    "path": _safe_sandbox_path(item.get("path")),
                    "type": item.get("type"),
                    "size_bytes": item.get("size_bytes"),
                    "modified_at_ns": item.get("modified_at_ns"),
                }
                for item in entries[:200]
                if isinstance(item, Mapping)
            ],
            "next_cursor": data.get("next_cursor"),
            "total_visible": data.get("total_visible"),
        }
    if tool_name == "workspace_edit":
        files = data.get("files") if isinstance(data.get("files"), list) else []
        return {
            "protocol_version": data.get("protocol_version"),
            "file_count": data.get("file_count"),
            "recovery_status": str(data.get("recovery_status") or "")[:32],
            "files": [
                {
                    "path": _safe_sandbox_path(item.get("path")),
                    **{
                        key: item.get(key)
                        for key in (
                            "size_bytes",
                            "previous_size_bytes",
                            "used_bytes",
                            "usage_delta_bytes",
                            "replacement_count",
                            "hunks_applied",
                            "added_lines",
                            "removed_lines",
                            "old_sha256",
                            "new_sha256",
                        )
                        if key in item
                    },
                }
                for item in files[:50]
                if isinstance(item, Mapping)
            ],
        }
    if tool_name in {"workspace_write", "workspace_apply_patch"}:
        return {
            "path": _safe_sandbox_path(data.get("path")),
            **{
                key: data.get(key)
                for key in (
                    "size_bytes",
                    "previous_size_bytes",
                    "used_bytes",
                    "usage_delta_bytes",
                    "hunks_applied",
                    "added_lines",
                    "removed_lines",
                )
                if key in data
            },
        }
    if tool_name in {"asset_import", "asset_publish"}:
        return {
            key: (
                _safe_sandbox_ref(value)
                if key == "ref"
                else _safe_sandbox_path(value)
                if key == "logical_name"
                else value
            )
            for key, value in data.items()
            if key in {"ref", "logical_name", "size_bytes", "media_type"}
        }
    return {}


def sanitize_tool_trace_result(tool_name: str, result: Any) -> Any:
    """Sandbox 结果正文和进程输出不得进入 ToolCall.result_preview。"""

    name = str(tool_name or "")
    if not _uses_metadata_only_trace(name):
        return result
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError):
            return {"result_omitted": True, **_text_audit(result)}
    if not isinstance(parsed, Mapping):
        return {"result_omitted": True, **_text_audit(parsed)}
    error = parsed.get("error") if isinstance(parsed.get("error"), Mapping) else None
    safe: dict[str, Any] = {
        "status": str(parsed.get("status") or "")[:32],
        "summary": str(parsed.get("summary") or "")[:500],
        "artifacts": _sandbox_artifacts(parsed.get("artifacts")),
        "data": _sandbox_result_data(name, parsed.get("data")),
    }
    if error is not None:
        safe["error"] = {
            key: error.get(key)
            for key in ("code", "retryable", "stop")
            if key in error
        }
    return safe


def sanitize_tool_trace_error(tool_name: str, error: Any) -> str:
    if not _uses_metadata_only_trace(tool_name) or not error:
        return str(error or "")
    return _json_dumps({"error_omitted": True, **_text_audit(error)})


def _prompt_preview(content: str, *, max_chars: int = 1000) -> str:
    if not content:
        return ""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    head_limit = max(1, max_chars - 80)
    head = content[:head_limit]
    return f"{head}\n...[prompt_sha256:{digest} chars:{len(content)}]"


def _serialized_payload(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _response_body_audit(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and {
        "response_body_omitted",
        "response_body_chars",
        "response_body_sha256",
    }.issubset(value):
        audit = {
            "response_body_omitted": bool(value.get("response_body_omitted")),
            "response_body_chars": max(0, int(value.get("response_body_chars") or 0)),
            "response_body_sha256": str(value.get("response_body_sha256") or "")[:64],
        }
        if value.get("response_body_truncated") is not None:
            audit["response_body_truncated"] = bool(
                value.get("response_body_truncated")
            )
        return audit
    raw = _serialized_payload(value)
    return {
        "response_body_omitted": bool(raw and raw not in {"{}", "null", ""}),
        "response_body_chars": len(raw),
        "response_body_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "safe_summary": safe_response_summary(
            value,
            max_chars=MAX_LLM_FAILURE_SUMMARY_CHARS,
        ),
    }


def _bounded_payload_json(value: Any, *, max_chars: int) -> str:
    text = _json_dumps(value, max_chars=0)
    if len(text) <= max_chars:
        return text
    raw = _serialized_payload(value)
    audit = {
        "response_body_omitted": True,
        "response_body_chars": len(raw),
        "response_body_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "safe_summary": safe_response_summary(value, max_chars=4_000),
    }
    return json.dumps(audit, ensure_ascii=False, separators=(",", ":"))


def sanitize_llm_log_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """统一保护持久 Trace 与旧记录的 Admin 输出。"""

    result = dict(data)
    result["url"] = safe_url_for_logging(result.get("url", ""))
    limits = {
        "headers_json": 12_000,
        "request_json": MAX_LLM_REQUEST_JSON_CHARS,
        "request_preview": 4_000,
        "response_json": MAX_LLM_RESPONSE_JSON_CHARS,
        "response_preview": 4_000,
        "error": 2_000,
    }
    for key, max_chars in limits.items():
        if key in result:
            result[key] = safe_response_summary(
                result.get(key, ""),
                max_chars=max_chars,
            )
    return result


def _session():
    from core import database

    return database.SessionLocal()


def _run_db_write(db: Any, operation: Any, *, label: str) -> Any:
    from core.sqlite_retry import run_sqlite_locked_retry

    return run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label=label,
        logger=logger,
    )


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
        prompt_source: str = "",
        prompt_runtime_path: str = "",
        prompt_default_path: str = "",
        prompt_sha256: str = "",
        prompt_template_resolutions: dict[str, Any] | None = None,
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
                def operation() -> None:
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
                        prompt_source=str(prompt_source or "")[:96],
                        prompt_runtime_path=str(prompt_runtime_path or ""),
                        prompt_default_path=str(prompt_default_path or ""),
                        prompt_sha256=str(prompt_sha256 or "")[:64],
                        prompt_template_resolutions_json=(
                            serialize_template_resolutions_json(
                                prompt_template_resolutions
                            )
                        ),
                        model=str(model or "")[:160],
                        status="running",
                        input_preview=_preview(input_preview, max_chars=1000),
                        meta_json=_json_dumps(meta or {}, max_chars=3000),
                        started_at=db_now_naive(),
                    ))
                    db.commit()

                _run_db_write(db, operation, label="agent_run_start")
            finally:
                db.close()
        except Exception as e:
            logger.warning("agent_run start failed: %s", e)
        return RunHandle(run_id=run_id, trace_id=trace_id)

    @staticmethod
    def update_prompt_source(
        run_id: str,
        *,
        prompt_source: str = "",
        prompt_runtime_path: str = "",
        prompt_default_path: str = "",
        prompt_sha256: str = "",
        prompt_template_resolutions: dict[str, Any] | None = None,
    ) -> None:
        if not run_id:
            return
        try:
            from core.database import AgentRun

            db = _session()
            try:
                def operation() -> None:
                    row = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                    if not row:
                        return
                    row.prompt_source = str(prompt_source or "")[:96]
                    row.prompt_runtime_path = str(prompt_runtime_path or "")
                    row.prompt_default_path = str(prompt_default_path or "")
                    row.prompt_sha256 = str(prompt_sha256 or "")[:64]
                    row.prompt_template_resolutions_json = (
                        serialize_template_resolutions_json(
                            prompt_template_resolutions
                        )
                    )
                    db.commit()

                _run_db_write(db, operation, label="agent_run_prompt_source")
            finally:
                db.close()
        except Exception as e:
            logger.warning("agent_run prompt source update failed: %s", e)

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
                def operation() -> None:
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
                    row.finished_at = to_db_naive(finished_at) or db_now_naive()
                    db.commit()

                _run_db_write(db, operation, label="agent_run_finish")
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
                def operation() -> None:
                    db.add(ToolCall(
                        tool_call_id=tool_call_id,
                        trace_id=str(trace_id or "")[:64],
                        run_id=str(run_id or "")[:80],
                        tool_name=str(tool_name or "")[:128],
                        args_json=_json_dumps(
                            sanitize_tool_trace_args(tool_name, args),
                        ),
                        status="running",
                        started_at=db_now_naive(),
                    ))
                    db.commit()

                _run_db_write(db, operation, label="tool_call_start")
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
                def operation() -> None:
                    row = db.query(ToolCall).filter(ToolCall.tool_call_id == tool_call_id).first()
                    if not row:
                        return
                    row.status = str(status or "success")[:32]
                    preview_limit = (
                        MAX_WEB_SEARCH_PREVIEW_CHARS
                        if row.tool_name == "web_search"
                        else MAX_PREVIEW_CHARS
                    )
                    row.result_preview = _preview(
                        sanitize_tool_trace_result(row.tool_name, result),
                        max_chars=preview_limit,
                    )
                    row.error = _preview(
                        sanitize_tool_trace_error(row.tool_name, error),
                        max_chars=1000,
                    )
                    if latency_ms is not None:
                        row.latency_ms = int(latency_ms)
                    row.finished_at = to_db_naive(finished_at) or db_now_naive()
                    db.commit()

                _run_db_write(db, operation, label="tool_call_finish")
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
        prompt_source: str = "",
        prompt_runtime_path: str = "",
        prompt_default_path: str = "",
        prompt_sha256: str = "",
        prompt_template_resolutions: dict[str, Any] | None = None,
    ) -> None:
        try:
            from core.database import PromptRenderLog

            db = _session()
            try:
                def operation() -> None:
                    db.add(PromptRenderLog(
                        trace_id=str(trace_id or "")[:64],
                        run_id=str(run_id or "")[:80],
                        prompt_key=str(prompt_key or "")[:96],
                        mode=str(mode or "preview")[:32],
                        prompt_source=str(prompt_source or "")[:96],
                        prompt_runtime_path=str(prompt_runtime_path or ""),
                        prompt_default_path=str(prompt_default_path or ""),
                        prompt_sha256=str(prompt_sha256 or "")[:64],
                        prompt_template_resolutions_json=(
                            serialize_template_resolutions_json(
                                prompt_template_resolutions
                            )
                        ),
                        variables_json=_json_dumps(variables or {}, max_chars=6000),
                        rendered_preview=_prompt_preview(rendered_content, max_chars=1000),
                        token_estimate=int(token_estimate or 0),
                        warnings_json=_json_dumps(warnings or [], max_chars=2000),
                        error=_preview(error, max_chars=1000),
                        created_at=db_now_naive(),
                    ))
                    db.commit()

                _run_db_write(db, operation, label="prompt_render_log")
            finally:
                db.close()
        except Exception as e:
            logger.warning("prompt_render_log failed: %s", e)


def _previous_comparable_cache_shape(
    db: Any,
    *,
    log: Any,
    current_shape: Mapping[str, Any],
) -> dict[str, Any] | None:
    """查找同一匿名 scope 最近一次请求的缓存形状。"""

    from core.database import LLMApiRequestLog

    scope_sha256 = str(current_shape.get("scope_sha256") or "")
    rows = (
        db.query(LLMApiRequestLog.cache_details_json)
        .filter(
            LLMApiRequestLog.id < int(log.id or 0),
            LLMApiRequestLog.source == str(log.source or ""),
            LLMApiRequestLog.provider == str(log.provider or ""),
            LLMApiRequestLog.model == str(log.model or ""),
            LLMApiRequestLog.status.in_(("success", "stream_success")),
        )
        .order_by(LLMApiRequestLog.id.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        try:
            details = json.loads(str(row[0] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        shape = details.get("cache_shape") if isinstance(details, dict) else None
        if not isinstance(shape, dict):
            continue
        if scope_sha256 and str(shape.get("scope_sha256") or "") != scope_sha256:
            continue
        return shape
    return None


class LLMRequestTracer:
    @staticmethod
    def record_request(
        *,
        trace_id: str = "",
        run_id: str = "",
        source: str = "",
        phase: str = "",
        round_index: int = 0,
        route_attempt_index: int = 0,
        provider: str = "",
        model: str = "",
        url: str = "",
        method: str = "POST",
        headers: Any = None,
        request: Any = None,
        status: str = "created",
        response_status: int = 0,
        error: str = "",
    ) -> int:
        try:
            from core.database import LLMApiRequestLog
            from core.llm_request_linter import lint_llm_request

            request_payload = request or {}
            try:
                from core.llm_trace_context import get_llm_cache_context

                cache_context = get_llm_cache_context()
            except Exception:
                cache_context = {}
            cache_context.setdefault(
                "scope_key",
                f"{source or 'unknown'}:{model or ''}",
            )
            cache_shape = build_llm_cache_shape(
                request_payload,
                cache_context=cache_context,
            )
            try:
                lint_result = lint_llm_request(request_payload)
            except Exception as e:
                logger.warning("llm request lint failed: %s", e)
                lint_result = {
                    "ok": False,
                    "severity_counts": {"P0": 0, "P1": 0, "P2": 1},
                    "issues": [{
                        "severity": "P2",
                        "code": "lint_error",
                        "message": str(e),
                    }],
                    "message_sources": [],
                    "actual_sent_tools": [],
                    "runtime_enabled_tools": [],
                    "runtime_disabled_tools": [],
                    "framework_injected_tools": [],
                }

            db = _session()
            try:
                def operation() -> int:
                    log = LLMApiRequestLog(
                        trace_id=str(trace_id or "")[:64],
                        run_id=str(run_id or "")[:80],
                        source=str(source or "")[:64],
                        phase=str(phase or "")[:64],
                        round_index=max(0, int(round_index or 0)),
                        route_attempt_index=max(
                            0,
                            int(route_attempt_index or 0),
                        ),
                        provider=str(provider or "")[:64],
                        model=str(model or "")[:160],
                        url=safe_url_for_logging(url),
                        method=str(method or "POST")[:16],
                        headers_json=_json_dumps(headers or {}, max_chars=12000),
                        request_json=_bounded_payload_json(
                            request_payload,
                            max_chars=MAX_LLM_REQUEST_JSON_CHARS,
                        ),
                        request_preview=_preview(request_payload, max_chars=4000),
                        status=str(status or "created")[:32],
                        cache_status=CACHE_STATUS_PENDING,
                        cache_hit=None,
                        cache_hit_tokens=0,
                        cache_miss_tokens=0,
                        cache_write_tokens=0,
                        cache_details_json=json.dumps(
                            {"cache_shape": cache_shape},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        response_status=int(response_status or 0),
                        error=safe_response_summary(error, max_chars=2000),
                        message_sources_json=_json_dumps(lint_result.get("message_sources") or [], max_chars=0),
                        request_lint_json=_json_dumps(lint_result, max_chars=0),
                        actual_sent_tools_json=_json_dumps(lint_result.get("actual_sent_tools") or [], max_chars=0),
                        runtime_enabled_tools_json=_json_dumps(lint_result.get("runtime_enabled_tools") or [], max_chars=0),
                        runtime_disabled_tools_json=_json_dumps(lint_result.get("runtime_disabled_tools") or [], max_chars=0),
                        framework_injected_tools_json=_json_dumps(lint_result.get("framework_injected_tools") or [], max_chars=0),
                        created_at=db_now_naive(),
                    )
                    db.add(log)
                    db.flush()
                    log_id = int(log.id or 0)
                    db.commit()
                    return log_id

                return int(_run_db_write(db, operation, label="llm_api_request_log") or 0)
            finally:
                db.close()
        except Exception as e:
            logger.warning("llm api request log failed: %s", e)
            return 0

    @staticmethod
    def finish_request(
        *,
        log_id: int = 0,
        response: Any = None,
        response_status: int = 0,
        status: str = "success",
        error: str = "",
        latency_ms: int = 0,
        phase: str | None = None,
    ) -> None:
        if not log_id:
            return
        try:
            from core.database import LLMApiRequestLog

            db = _session()
            try:
                def operation() -> None:
                    log = db.query(LLMApiRequestLog).filter(LLMApiRequestLog.id == int(log_id)).first()
                    if log is None:
                        return
                    normalized_status = str(status or "success")[:32]
                    successful = normalized_status in {"success", "stream_success"}
                    cache_usage = normalize_llm_cache_usage(
                        response or {},
                        successful=successful,
                    )
                    if successful:
                        log.response_json = _bounded_payload_json(
                            response or {},
                            max_chars=MAX_LLM_RESPONSE_JSON_CHARS,
                        )
                        log.response_preview = safe_response_summary(
                            response or {},
                            max_chars=4000,
                        )
                    else:
                        audit = _response_body_audit(response or {})
                        log.response_json = json.dumps(
                            audit,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        log.response_preview = safe_response_summary(
                            audit,
                            max_chars=4000,
                        )
                    log.cache_status = cache_usage.status
                    log.cache_hit = cache_usage.hit
                    log.cache_hit_tokens = cache_usage.hit_tokens
                    log.cache_miss_tokens = cache_usage.miss_tokens
                    log.cache_write_tokens = cache_usage.write_tokens
                    try:
                        cache_details = json.loads(log.cache_details_json or "{}")
                        if not isinstance(cache_details, dict):
                            cache_details = {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        cache_details = {}
                    cache_details.update(cache_usage.details)
                    cache_shape = cache_details.get("cache_shape")
                    cache_shape = (
                        cache_shape if isinstance(cache_shape, dict) else {}
                    )
                    total_cache_tokens = (
                        cache_usage.hit_tokens + cache_usage.miss_tokens
                    )
                    if total_cache_tokens > 0:
                        cache_details["cache_hit_ratio"] = round(
                            cache_usage.hit_tokens / total_cache_tokens,
                            6,
                        )
                    if cache_usage.status == CACHE_STATUS_MISS:
                        previous_shape = _previous_comparable_cache_shape(
                            db,
                            log=log,
                            current_shape=cache_shape,
                        )
                        cache_details["miss_reason"] = infer_cache_miss_reason(
                            cache_shape,
                            previous_shape,
                        )
                    log.cache_details_json = json.dumps(
                        cache_details,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    log.response_status = int(response_status or 0)
                    log.status = normalized_status
                    if phase is not None:
                        log.phase = str(phase or "")[:64]
                    log.error = safe_response_summary(error, max_chars=2000)
                    log.latency_ms = int(latency_ms or 0)
                    log.finished_at = db_now_naive()
                    db.commit()

                _run_db_write(db, operation, label="llm_api_request_finish")
            finally:
                db.close()
        except Exception as e:
            logger.warning("llm api request finish failed: %s", e)


class ReplyContractTracer:
    @staticmethod
    def record_check(
        *,
        trace_id: str = "",
        run_id: str = "",
        session_id: str = "",
        attempt: int = 0,
        raw_output: str = "",
        has_reply_tool: bool = False,
        has_no_reply_tool: bool = False,
        has_structured_fallback: bool = False,
        reply_tool_call_count: int | None = None,
        no_reply_tool_call_count: int | None = None,
        structured_fallback_count: int | None = None,
        total_final_action_count: int | None = None,
        result: str = "",
    ) -> None:
        try:
            from core.database import ReplyContractCheckLog
            from core.sqlite_retry import run_sqlite_locked_retry

            db = _session()
            try:
                reply_count = int(reply_tool_call_count if reply_tool_call_count is not None else (1 if has_reply_tool else 0))
                no_reply_count = int(no_reply_tool_call_count if no_reply_tool_call_count is not None else (1 if has_no_reply_tool else 0))
                fallback_count = int(structured_fallback_count if structured_fallback_count is not None else (1 if has_structured_fallback else 0))
                total_count = int(
                    total_final_action_count
                    if total_final_action_count is not None
                    else reply_count + no_reply_count + fallback_count
                )

                def operation() -> None:
                    db.add(ReplyContractCheckLog(
                        trace_id=str(trace_id or "")[:64],
                        run_id=str(run_id or "")[:80],
                        session_id=str(session_id or "")[:128],
                        attempt=int(attempt or 0),
                        raw_output_preview=_preview(raw_output or "", max_chars=3000),
                        has_reply_tool=1 if has_reply_tool else 0,
                        has_no_reply_tool=1 if has_no_reply_tool else 0,
                        has_structured_fallback=1 if has_structured_fallback else 0,
                        reply_tool_call_count=reply_count,
                        no_reply_tool_call_count=no_reply_count,
                        structured_fallback_count=fallback_count,
                        total_final_action_count=total_count,
                        result=str(result or "")[:64],
                        created_at=db_now_naive(),
                    ))
                    db.commit()

                run_sqlite_locked_retry(
                    operation,
                    rollback=db.rollback,
                    label="reply_contract_check_log",
                    logger=logger,
                )
            finally:
                db.close()
        except Exception as e:
            logger.warning("reply contract check log failed: %s", e)


def row_to_dict(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[col.name] = value
    if getattr(row, "__tablename__", "") == "llm_api_request_logs":
        return sanitize_llm_log_payload(data)
    return data
