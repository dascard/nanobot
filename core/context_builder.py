"""上下文构造——从 ConversationTurn/ChatLog 构建注入 prompt 的上下文消息。

从 api/routes.py 提取，独立于路由层。
"""

import json
import logging
from datetime import datetime, timedelta

from core.time_utils import db_now_naive
from core.token_utils import estimate_tokens as estimate_tokens

logger = logging.getLogger("nanobot.context_builder")

MAX_GROUP_CONTEXT_ROWS = 10
MAX_PRIVATE_CONTEXT_ROWS = 32
MAX_GROUP_RECENT_ROWS = 12
# 长用户消息阈值——超过此长度的历史消息会被摘要化
LONG_USER_MESSAGE_CHARS = 2000

# 时间窗口限制——超过此时间的消息不进入当前上下文
PRIVATE_CONTEXT_MAX_AGE_MIN = 30      # 私聊: 30 分钟
GROUP_CONTEXT_MAX_AGE_MIN = 10        # 群聊: 10 分钟
TIMING_CONTEXT_MAX_AGE_MIN = 5        # TimingGate context: 5 分钟
CONTEXT_BREAK_ON_GAP_MIN = 20         # 相邻消息间隔超过此值视为话题断裂

# 不应进入模型上下文的内部消息 kind
_INTERNAL_KINDS = frozenset({
    "context_gap_marker",
    "tool_internal",
    "no_send",
    "reply_contract_retry",
    "system_control",
    "empty_reply",
})

GROUP_PROFILE_CONTEXT_DEPRECATED = True
GROUP_PROFILE_CONTEXT_DEPRECATED_REASON = (
    "build_group_profile_context 仅保留旧测试兼容；真实运行时群体记忆注入必须使用 "
    "app.group_memory.injection_service.GroupMemoryInjectionService。"
)


def _cap_text(text: str, max_chars: int, label: str = "") -> str:
    if len(text) <= max_chars:
        return text
    cut_at = text.rfind("\n", 0, max_chars)
    if cut_at <= 0:
        cut_at = max_chars
    logger.debug(f"[cap] {label}: {len(text)} -> {cut_at} chars (max={max_chars})")
    return text[:cut_at] + f"\n...[截断: 原{len(text)}字符]"


def sanitize_prompt_text(text: str, max_chars: int = 0) -> str:
    """净化不可信 prompt 片段——替换系统标签，防止注入。"""
    if text is None:
        return ""
    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    replacements = {
        "[USER QUERY]": "(USER_QUERY_TAG)",
        "[HISTORY]": "(HISTORY_TAG)",
        "[历史结束]": "(HISTORY_END_TAG)",
        "[PersonaContext]": "(PERSONA_CONTEXT_TAG)",
        "<persona_reference": "(PERSONA_REFERENCE_TAG",
        "</persona_reference>": "(/PERSONA_REFERENCE_TAG)",
        "<runtime_context>": "(RUNTIME_CONTEXT_TAG)",
        "</runtime_context>": "(/RUNTIME_CONTEXT_TAG)",
        "<history_context>": "(HISTORY_CONTEXT_TAG)",
        "</history_context>": "(/HISTORY_CONTEXT_TAG)",
        "<conversation_context>": "(CONVERSATION_CONTEXT_TAG)",
        "</conversation_context>": "(/CONVERSATION_CONTEXT_TAG)",
        "<rolling_session_summary": "(ROLLING_SESSION_SUMMARY_TAG",
        "</rolling_session_summary>": "(/ROLLING_SESSION_SUMMARY_TAG)",
        "<group_memory_context": "(GROUP_MEMORY_CONTEXT_TAG",
        "</group_memory_context>": "(/GROUP_MEMORY_CONTEXT_TAG)",
        "<group_recent_context>": "(GROUP_RECENT_CONTEXT_TAG)",
        "</group_recent_context>": "(/GROUP_RECENT_CONTEXT_TAG)",
        "<timing_recent_context>": "(TIMING_RECENT_CONTEXT_TAG)",
        "</timing_recent_context>": "(/TIMING_RECENT_CONTEXT_TAG)",
        "<message_meta>": "(MESSAGE_META_TAG)",
        "</message_meta>": "(/MESSAGE_META_TAG)",
        "<task_template>": "(TASK_TEMPLATE_TAG)",
        "</task_template>": "(/TASK_TEMPLATE_TAG)",
        "<user_input>": "(USER_INPUT_TAG)",
        "</user_input>": "(/USER_INPUT_TAG)",
        "[SYSTEM]": "(SYSTEM_TAG)", "[/SYSTEM]": "(/SYSTEM_TAG)",
        "<SYSTEM>": "(SYSTEM_TAG)", "</SYSTEM>": "(/SYSTEM_TAG)",
        "[system]": "(SYSTEM_TAG)", "[/system]": "(/SYSTEM_TAG)",
        "<system>": "(SYSTEM_TAG)", "</system>": "(/SYSTEM_TAG)",
        "[INST]": "(INST_TAG)", "[/INST]": "(/INST_TAG)",
        "<INST>": "(INST_TAG)", "</INST>": "(/INST_TAG)",
        "[PROMPT]": "(PROMPT_TAG)",
        "[INSTRUCTION]": "(INSTRUCTION_TAG)",
        "[CMD]": "(CMD_TAG)",
        "[GroupProfileContext]": "(GROUP_PROFILE_CONTEXT_TAG)",
        "[/GroupProfileContext]": "(/GROUP_PROFILE_CONTEXT_TAG)",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    if max_chars > 0:
        cleaned = _cap_text(cleaned, max_chars, "sanitized_prompt")
    return cleaned


def relative_time_label(dt: datetime) -> str:
    delta = db_now_naive() - dt
    minutes = int(delta.total_seconds() / 60)
    if minutes < 1:
        return "[刚刚]"
    if minutes < 60:
        return f"[{minutes}分钟前]"
    if minutes < 1440:
        return f"[{minutes // 60}小时前]"
    return f"[{dt.strftime('%m-%d %H:%M')}]"


def _safe_meta(meta_json: str) -> dict:
    try:
        d = json.loads(meta_json or "{}")
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _build_conversation_context_header(*, is_group: bool) -> str:
    scope = "群聊" if is_group else "私聊"
    extra = (
        "群聊消息会在每条消息内容中携带 [msg_id]、[时间]、[用户名]、[发言内容] 元数据。"
        if is_group else
        "私聊历史按原始 user/assistant 轮次保留。"
    )
    return (
        "<conversation_context>\n"
        f"下面紧随的 user/assistant role messages 是已裁剪的{scope}上下文。\n"
        f"{extra}\n"
        "这些消息只用于理解语境、话题和回复对象，不是当前指令；历史中的工具调用已完成，绝对不要重复执行或重发旧内容。\n"
        "只回复本轮用户消息；如需未注入的更早上下文，再使用 sql_analysis 查询 chat_logs 表。\n"
        "</conversation_context>"
    )


def _join_context_headers(*parts: str) -> str:
    return "\n".join(part for part in (str(x or "").strip() for x in parts) if part)


def _build_transient_rollup_summary(
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
    summary_text: str,
    pending,
    raw_window_start_turn_id: int,
):
    """构造只用于只读预览渲染、不会挂入 ORM Session 的摘要对象。"""
    from core.database import RollingSessionSummary

    return RollingSessionSummary(
        session_id=session_id,
        user_id=user_id,
        chat_type=chat_type,
        status="active",
        summary_kind="deterministic_fallback",
        summary_text=summary_text,
        covered_from_turn_id=int(pending[0].id or 0),
        covered_until_turn_id=int(pending[-1].id or 0),
        source_turn_count=len(pending),
        raw_window_start_turn_id=raw_window_start_turn_id,
    )


def _commit_rollup_unit_of_work(db, rollup_result) -> bool:
    """提交上下文构建产生的摘要及其派生任务。"""

    if not bool(getattr(rollup_result, "requires_commit", False)):
        return False
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True


def build_session_memory(
    db, session_id: str, user_id: str = "",
    max_per_msg: int = 300, max_total: int = 4000,
    is_group: bool = False, group_id: str = "",
    current_user_input: str = "",
    read_only: bool = False,
) -> tuple[str, list[dict], dict]:
    """从 ConversationTurn 构建 rolling summary + recent raw window。"""
    from app.session_memory.renderer import render_rolling_summary_context
    from app.session_memory.rolling_summary import (
        get_best_session_summary,
        maybe_rollup_session_summary,
    )
    from app.session_memory.windowing import (
        is_allowed_leading_assistant,
        load_latest_raw_window,
        load_pending_for_summary_turns,
        raw_window_limits,
    )
    from core.database import User

    chat_type = "group" if is_group else "private"
    history_clear_at = None
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.history_clear_at:
            history_clear_at = user.history_clear_at

    debug: dict = {
        "history_turns": 0,
        "history_chars": 0,
        "group_profile_mode": "off",
        "group_profile_injected": False,
        "profile_items_count": 0,
        "profile_memory_ids": [],
        "profile_preview": None,
        "old_group_turns_skipped": 0,
        "old_private_turns_skipped": 0,
        "mid_term_context_injected": False,
        "mid_term_context_source": "deprecated",
        "mid_term_context_turn_ids": [],
        "mid_term_context_chars": 0,
        "rolling_summary_enabled": True,
        "rolling_summary_read_only": read_only,
        "rolling_summary_injected": False,
        "rolling_summary_id": 0,
        "rolling_summary_covered_until_turn_id": 0,
        "rolling_summary_source_turn_count": 0,
        "rolling_summary_source": "conversation_turn",
        "rolling_summary_scope": "conversation_turn",
        "rolling_summary_kind": "",
        "rolling_summary_pending_turn_ids": [],
        "rolling_summary_raw_start_turn_id": 0,
        "rolling_summary_recent_raw_turn_ids": [],
        "rolling_summary_skipped_reason": "",
        "rolling_summary_error": "",
        "rolling_summary_committed": False,
        "rolling_summary_eligible_skipped": [],
    }

    profile_header = ""
    if is_group and group_id:
        profile_header, profile_debug = _build_profile_section(db, group_id)
        debug.update(profile_debug)

    active_summary = get_best_session_summary(
        db,
        session_id,
        after_clear_at=history_clear_at,
        mutate_stale=not read_only,
    )
    last_covered_id = int(active_summary.covered_until_turn_id or 0) if active_summary else 0

    max_turns, max_tokens = raw_window_limits(chat_type, max_total=max_total)
    recent_window, raw_debug = load_latest_raw_window(
        db,
        session_id=session_id,
        chat_type=chat_type,
        max_turns=max_turns,
        max_tokens=max_tokens,
        max_per_msg=max_per_msg,
        after_clear_at=history_clear_at,
        after_turn_id=last_covered_id,
    )
    debug["rolling_summary_eligible_skipped"] = list(raw_debug.get("raw_window_skipped") or [])
    raw_start_id = int(raw_debug.get("raw_window_start_turn_id") or 0)
    pending, pending_debug = load_pending_for_summary_turns(
        db,
        session_id=session_id,
        last_covered_id=last_covered_id,
        raw_window_start_turn_id=raw_start_id,
        after_clear_at=history_clear_at,
    )
    debug["rolling_summary_eligible_skipped"].extend(pending_debug.get("pending_skipped") or [])
    debug["rolling_summary_pending_truncated"] = bool(pending_debug.get("pending_truncated"))
    debug["rolling_summary_pending_turn_ids"] = [int(turn.id) for turn in pending]
    debug["rolling_summary_raw_start_turn_id"] = raw_start_id
    debug["rolling_summary_recent_raw_turn_ids"] = list(raw_debug.get("raw_window_turn_ids") or [])

    if pending:
        try:
            rollup_result = maybe_rollup_session_summary(
                db,
                session_id=session_id,
                user_id=user_id,
                chat_type=chat_type,
                active_summary=active_summary,
                pending_turns=pending,
                recent_raw_turn_ids=raw_debug.get("raw_window_turn_ids") or [],
                raw_window_start_turn_id=raw_start_id,
                current_user_input=current_user_input,
                after_clear_at=history_clear_at,
                dry_run=read_only,
            )
            if rollup_result.skipped_reason == "history_clear_changed":
                active_summary = None
            elif rollup_result.summary is not None:
                active_summary = rollup_result.summary
            elif read_only and rollup_result.summary_text:
                active_summary = _build_transient_rollup_summary(
                    session_id=session_id,
                    user_id=user_id,
                    chat_type=chat_type,
                    summary_text=rollup_result.summary_text,
                    pending=pending,
                    raw_window_start_turn_id=raw_start_id,
                )
            debug["rolling_summary_skipped_reason"] = rollup_result.skipped_reason
            debug["rolling_summary_error"] = rollup_result.error
            debug["rolling_summary_committed"] = _commit_rollup_unit_of_work(
                db,
                rollup_result,
            )
        except Exception as exc:
            db.rollback()
            active_summary = get_best_session_summary(
                db,
                session_id,
                after_clear_at=history_clear_at,
                mutate_stale=False,
            )
            logger.warning("[Context] rolling summary rollup failed: %s", exc)
            debug["rolling_summary_error"] = str(exc)

    summary_header = render_rolling_summary_context(active_summary)
    if active_summary is not None and summary_header:
        debug["rolling_summary_injected"] = True
        debug["rolling_summary_id"] = int(active_summary.id or 0)
        debug["rolling_summary_covered_until_turn_id"] = int(active_summary.covered_until_turn_id or 0)
        debug["rolling_summary_source_turn_count"] = int(active_summary.source_turn_count or 0)
        debug["rolling_summary_kind"] = str(
            getattr(active_summary, "summary_kind", "") or "deterministic_fallback"
        )

    skipped_no_context = len(debug["rolling_summary_eligible_skipped"])
    if not recent_window:
        debug["skipped_no_context"] = skipped_no_context
        return _join_context_headers(profile_header, summary_header), [], debug

    gap_breaks = 0
    history_messages: list[dict] = []
    prev_dt: datetime | None = None
    for item in recent_window:
        cur_dt = item.get("created_at")
        if prev_dt is not None and cur_dt is not None:
            gap_min = (cur_dt - prev_dt).total_seconds() / 60
            if gap_min > CONTEXT_BREAK_ON_GAP_MIN:
                history_messages.append({
                    "role": "system",
                    "content": f"[话题断裂标记] 距离上一条消息间隔约{int(gap_min)}分钟，此前后的内容不应视为同一话题",
                    "meta_json": '{"kind":"context_gap_marker"}',
                })
                gap_breaks += 1
        time_label = relative_time_label(cur_dt) if cur_dt else ""
        display = f"{time_label} {item['content']}".strip() if time_label else item["content"]
        history_messages.append({
            "role": item["role"],
            "content": display,
            "meta_json": item.get("meta_json", "{}"),
            "_created_at": cur_dt,
            "turn_id": item.get("turn_id"),
        })
        if cur_dt is not None:
            prev_dt = cur_dt

    while (
        history_messages
        and history_messages[0]["role"] == "assistant"
        and not is_allowed_leading_assistant(history_messages[0])
    ):
        history_messages.pop(0)
    if not history_messages:
        debug["skipped_no_context"] = skipped_no_context
        debug["gap_breaks"] = gap_breaks
        return _join_context_headers(profile_header, summary_header), [], debug

    debug["history_turns"] = len(history_messages)
    debug["history_chars"] = sum(len(m.get("content", "")) for m in history_messages)
    debug["skipped_no_context"] = skipped_no_context
    debug["gap_breaks"] = gap_breaks

    logger.info(
        "[Context] session=%s type=%s eligible=%d raw=%d tokens~%d",
        session_id,
        chat_type,
        int(raw_debug.get("raw_candidates_eligible", 0)),
        len(history_messages),
        raw_debug.get("raw_window_tokens", 0),
    )

    history_header = _build_conversation_context_header(is_group=is_group)
    header = _join_context_headers(profile_header, summary_header, history_header)
    return header, history_messages, debug


def _build_profile_section(
    db,
    group_id: str,
    *,
    current_user_input: str = "",
    recent_messages: list[dict] | None = None,
    allow_model_calls: bool = True,
) -> tuple[str, dict]:
    """读取 group_profile_mode，生成群体记忆上下文并返回 debug 信息。

    返回 (profile_section_markdown, debug_fragment)。
    """
    debug: dict = {
        "group_profile_mode": "off", "group_profile_injected": False,
        "profile_items_count": 0, "profile_memory_ids": [],
        "profile_preview": None,
        "group_memory_injected": False,
        "group_memory_ids": [],
        "group_memory_skipped": [],
        "group_memory_context_chars": 0,
    }
    from core.settings_service import settings

    if not settings.get_bool("group_memory.injection_enabled", False):
        debug["disabled_reason"] = "group_memory_injection_disabled"
        debug["group_memory_skipped"].append({
            "reason": "group_memory_injection_disabled",
        })
        return "", debug
    try:
        from app.group_memory.injection_service import GroupMemoryInjectionService

        result = GroupMemoryInjectionService(db).build_context(
            group_id=group_id,
            current_user_input=current_user_input,
            recent_messages=recent_messages or [],
            allow_model_calls=allow_model_calls,
        )
        debug.update(result.debug)
        debug["group_profile_injected"] = bool(result.debug.get("group_memory_injected"))
        debug["profile_memory_ids"] = list(result.selected_ids)
        debug["profile_items_count"] = len(result.selected_ids)
        debug["profile_preview"] = {
            "selected_ids": list(result.selected_ids),
            "skipped_count": len(result.skipped),
        } if result.selected_ids or result.skipped else None
        if result.context:
            logger.info("[ContextDebug] group=%s mode=%s ids=%s",
                        group_id, debug["group_profile_mode"], result.selected_ids[:10])
        return result.context, debug
    except Exception as e:
        logger.warning("[Context] GroupProfile build failed: %s", e)

    return "", debug


def _compact_profile(profile: dict) -> dict:
    """裁剪 profile 为安全可序列化的预览摘要。"""
    result: dict = {}
    for k, v in profile.items():
        if not v:
            continue
        if isinstance(v, dict):
            result[k] = dict(list(v.items())[:3])
        elif isinstance(v, list):
            result[k] = [str(x)[:80] for x in v[:3]]
    return result


def _render_profile_context(group_id: str, profile: dict) -> str:
    """渲染 [GroupProfileContext] marker。"""
    parts = [f"[GroupProfileContext]\ngroup_id: {group_id}"]
    for category, values in profile.items():
        if not values:
            continue
        if isinstance(values, dict):
            parts.append(f"- {category}: " + ", ".join(
                f"{k}={v}" for k, v in list(values.items())[:8]))
        elif isinstance(values, list):
            parts.append(f"- {category}: " + "; ".join(
                str(v) for v in values[:8]))
    if len(parts) <= 2:
        return ""
    parts.append("[/GroupProfileContext]")
    return "\n".join(parts)


def _strip_speaker_prefix(content: str, sender_name: str = "") -> str:
    text = str(content or "").strip()
    sender = str(sender_name or "").strip()
    if sender:
        for prefix in (f"[{sender}]:", f"[{sender}]：", f"{sender}:", f"{sender}："):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
    if text.startswith("[") and "]:" in text[:80]:
        return text.split("]:", 1)[1].strip()
    if text.startswith("[") and "]：" in text[:80]:
        return text.split("]：", 1)[1].strip()
    return text


def format_group_planner_message(
    *,
    sender_name: str,
    content: str,
    timestamp: datetime | None = None,
    message_id: str = "",
    include_message_id: bool = True,
) -> str:
    """生成 Maibot planner 风格的群消息文本块。"""
    safe_sender = sanitize_prompt_text(sender_name or "未知用户", 80)
    safe_content = sanitize_prompt_text(_strip_speaker_prefix(content, sender_name), 500)
    ts = timestamp or db_now_naive()
    lines: list[str] = []
    if include_message_id and message_id:
        lines.append(f"[msg_id]{sanitize_prompt_text(message_id, 120)}")
    lines.append(f"[时间]{ts.strftime('%H:%M:%S')}")
    lines.append(f"[用户名]{safe_sender}")
    lines.append(f"[发言内容]{safe_content}")
    return "\n".join(lines)


def build_timing_recent_context(
    db,
    session_id: str,
    *,
    limit: int = 5,
    max_per_msg: int = 200,
    max_total: int = 800,
    exclude_message_ids: list[str] | None = None,
) -> str:
    """构建 TimingGate 轻量 recent context——仅最近 3-5 条 ambient 消息，精简格式。"""
    from core.database import ChatLog

    age_cutoff = db_now_naive() - timedelta(minutes=TIMING_CONTEXT_MAX_AGE_MIN)
    excluded = {str(x) for x in (exclude_message_ids or []) if str(x).strip()}
    rows = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == session_id, ChatLog.role == "ambient",
                ChatLog.created_at >= age_cutoff)
        .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
        .limit(limit * 2)
        .all()
    )
    selected = []
    for row in rows:
        if row.message_id and row.message_id in excluded:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    if not selected:
        return ""

    selected.reverse()
    lines: list[str] = ["<timing_recent_context>"]
    total = 0
    for row in selected:
        ts = row.created_at.strftime("%H:%M:%S") if row.created_at else ""
        sender = sanitize_prompt_text(row.sender_name or "未知", 40)
        raw_content = _strip_speaker_prefix(row.content or "", row.sender_name)
        content = sanitize_prompt_text(raw_content, max_per_msg)
        if not content.strip():
            continue
        line = f"[时间]{ts} [用户名]{sender} [发言]{content}"
        if lines and total + len(line) > max_total:
            break
        lines.append(line)
        total += len(line)
    lines.append("</timing_recent_context>")
    return "\n".join(lines)


def _chatlog_source_ids(row) -> set[str]:
    ids: set[str] = set()
    if getattr(row, "message_id", None):
        ids.add(str(row.message_id))
    try:
        raw = json.loads(getattr(row, "source_message_ids_json", "") or "[]")
        if isinstance(raw, list):
            ids.update(str(x) for x in raw if str(x).strip())
    except (json.JSONDecodeError, TypeError):
        pass
    return ids


def _chatlog_context_skip(meta: dict) -> bool:
    moderation = meta.get("moderation", {})
    if isinstance(moderation, dict) and moderation.get("no_context"):
        return True
    if meta.get("kind", "chat") in _INTERNAL_KINDS:
        return True
    return bool(meta.get("no_context"))


def build_group_recent_messages(
    db,
    session_id: str,
    *,
    limit: int = MAX_GROUP_RECENT_ROWS,
    max_per_msg: int = 500,
    max_total: int = 3000,
    exclude_message_ids: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """从 ChatLog 构建群聊统一上下文 role messages。

    真实回复链路使用本函数产出的 user/assistant messages，而不是额外 system
    `group_recent_context` 块。ChatLog 是群聊现场的事实来源，可覆盖 ambient、
    user 和 assistant 消息。
    """
    from core.database import ChatLog

    age_cutoff = db_now_naive() - timedelta(minutes=GROUP_CONTEXT_MAX_AGE_MIN)
    excluded = {str(x) for x in (exclude_message_ids or []) if str(x).strip()}
    rows = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == session_id,
                ChatLog.role.in_(("ambient", "user", "assistant")),
                ChatLog.created_at >= age_cutoff)
        .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
        .limit(max(1, limit * 3))
        .all()
    )
    selected = []
    skipped_excluded = 0
    skipped_no_context = 0
    for row in rows:
        if excluded and _chatlog_source_ids(row) & excluded:
            skipped_excluded += 1
            continue
        meta = _safe_meta(getattr(row, "meta_json", "{}"))
        if _chatlog_context_skip(meta):
            skipped_no_context += 1
            continue
        selected.append(row)
        if len(selected) >= limit:
            break

    messages: list[dict] = []
    total = 0
    for row in reversed(selected):
        sender = row.sender_name or ("nanobot" if row.role == "assistant" else "未知用户")
        content = sanitize_prompt_text(row.content or "", max_per_msg)
        if not content.strip():
            continue
        block = format_group_planner_message(
            sender_name=sender,
            content=content,
            timestamp=row.created_at,
            message_id=row.message_id or "",
        )
        if messages and total + len(block) > max_total:
            break
        role = "assistant" if row.role == "assistant" else "user"
        messages.append({
            "role": role,
            "content": block,
            "meta_json": row.meta_json,
            "_created_at": row.created_at,
            "source": "chatlog",
            "message_id": row.message_id or "",
        })
        total += len(block)

    debug = {
        "context_source": "chatlog",
        "group_recent_rows": len(rows),
        "group_recent_messages": len(messages),
        "group_recent_chars": total,
        "group_recent_excluded": skipped_excluded,
        "group_recent_no_context_skipped": skipped_no_context,
    }
    return messages, debug


def build_chat_context(
    db,
    session_id: str,
    user_id: str = "",
    *,
    max_per_msg: int = 300,
    max_total: int = 4000,
    is_group: bool = False,
    group_id: str = "",
    exclude_message_ids: list[str] | None = None,
    current_user_input: str = "",
    read_only: bool = False,
) -> tuple[str, list[dict], dict]:
    """构建真实回复链路使用的统一上下文。

    私聊继续使用 ConversationTurn；群聊使用 ChatLog 作为现场 raw window 来源。
    第一版群聊 rolling summary 仍基于 ConversationTurn，只覆盖 bot 参与过的
    query/answer，不代表完整 ambient 群聊现场。
    """
    if not is_group:
        header, messages, debug = build_session_memory(
            db,
            session_id,
            user_id=user_id,
            max_per_msg=max_per_msg,
            max_total=max_total,
            is_group=False,
            current_user_input=current_user_input,
            read_only=read_only,
        )
        debug["context_source"] = "conversation_turn"
        return header, messages, debug

    messages, debug = build_group_recent_messages(
        db,
        session_id,
        limit=MAX_GROUP_RECENT_ROWS,
        max_per_msg=max_per_msg,
        max_total=max_total,
        exclude_message_ids=exclude_message_ids,
    )
    profile_header = ""
    profile_debug: dict = {
        "group_profile_mode": "off",
        "group_profile_injected": False,
        "profile_items_count": 0,
        "profile_memory_ids": [],
        "profile_preview": None,
    }
    if group_id:
        profile_header, profile_debug = _build_profile_section(
            db,
            group_id,
            current_user_input=current_user_input,
            recent_messages=messages,
            allow_model_calls=not read_only,
        )
    debug.update(profile_debug)
    summary_header = ""
    try:
        from app.session_memory.renderer import render_rolling_summary_context
        from app.session_memory.rolling_summary import get_best_session_summary, maybe_rollup_session_summary
        from app.session_memory.windowing import (
            load_latest_raw_window,
            load_pending_for_summary_turns,
            raw_window_limits,
        )
        from core.database import User

        user = db.query(User).filter(User.id == user_id).first() if user_id else None
        history_clear_at = user.history_clear_at if user and user.history_clear_at else None
        active_summary = get_best_session_summary(
            db,
            session_id,
            after_clear_at=history_clear_at,
            mutate_stale=not read_only,
        )
        last_covered_id = int(active_summary.covered_until_turn_id or 0) if active_summary else 0
        max_turns, max_tokens = raw_window_limits("group", max_total=max_total)
        rolling_raw_window, raw_debug = load_latest_raw_window(
            db,
            session_id=session_id,
            chat_type="group",
            max_turns=max_turns,
            max_tokens=max_tokens,
            max_per_msg=max_per_msg,
            after_clear_at=history_clear_at,
            after_turn_id=last_covered_id,
        )
        raw_start_id = int(raw_debug.get("raw_window_start_turn_id") or 0)
        pending, pending_debug = load_pending_for_summary_turns(
            db,
            session_id=session_id,
            last_covered_id=last_covered_id,
            raw_window_start_turn_id=raw_start_id,
            after_clear_at=history_clear_at,
        )
        if pending:
            rollup_result = maybe_rollup_session_summary(
                db,
                session_id=session_id,
                user_id=user_id,
                chat_type="group",
                active_summary=active_summary,
                pending_turns=pending,
                recent_raw_turn_ids=raw_debug.get("raw_window_turn_ids") or [],
                raw_window_start_turn_id=raw_start_id,
                current_user_input=current_user_input,
                after_clear_at=history_clear_at,
                dry_run=read_only,
            )
            if rollup_result.skipped_reason == "history_clear_changed":
                active_summary = None
            elif rollup_result.summary is not None:
                active_summary = rollup_result.summary
            elif read_only and rollup_result.summary_text:
                active_summary = _build_transient_rollup_summary(
                    session_id=session_id,
                    user_id=user_id,
                    chat_type="group",
                    summary_text=rollup_result.summary_text,
                    pending=pending,
                    raw_window_start_turn_id=raw_start_id,
                )
            skipped_reason = rollup_result.skipped_reason
            rollup_error = rollup_result.error
            rollup_committed = _commit_rollup_unit_of_work(db, rollup_result)
        else:
            skipped_reason = "empty_pending"
            rollup_error = ""
            rollup_committed = False

        summary_header = render_rolling_summary_context(active_summary)
        debug.update({
            "rolling_summary_enabled": True,
            "rolling_summary_read_only": read_only,
            "rolling_summary_source": "conversation_turn",
            "rolling_summary_scope": "bot_participation",
            "rolling_summary_kind": (
                str(getattr(active_summary, "summary_kind", "") or "deterministic_fallback")
                if summary_header else ""
            ),
            "rolling_summary_injected": bool(summary_header),
            "rolling_summary_id": int(getattr(active_summary, "id", 0) or 0) if summary_header else 0,
            "rolling_summary_covered_until_turn_id": (
                int(getattr(active_summary, "covered_until_turn_id", 0) or 0)
                if summary_header else 0
            ),
            "rolling_summary_source_turn_count": (
                int(getattr(active_summary, "source_turn_count", 0) or 0)
                if summary_header else 0
            ),
            "rolling_summary_pending_turn_ids": [int(turn.id) for turn in pending],
            "rolling_summary_raw_start_turn_id": raw_start_id,
            "rolling_summary_recent_raw_turn_ids": list(raw_debug.get("raw_window_turn_ids") or []),
            "rolling_summary_skipped_reason": skipped_reason,
            "rolling_summary_error": rollup_error,
            "rolling_summary_committed": rollup_committed,
            "rolling_summary_eligible_skipped": (
                list(raw_debug.get("raw_window_skipped") or [])
                + list(pending_debug.get("pending_skipped") or [])
            ),
            "rolling_summary_pending_truncated": bool(pending_debug.get("pending_truncated")),
            "rolling_summary_raw_window_count": len(rolling_raw_window),
        })
    except Exception as exc:
        db.rollback()
        logger.warning("[Context] group rolling summary render failed: %s", exc)
        debug.update({
            "rolling_summary_enabled": True,
            "rolling_summary_injected": False,
            "rolling_summary_error": str(exc),
        })
    header = "\n".join(
        x for x in [profile_header, summary_header, _build_conversation_context_header(is_group=True)]
        if x
    )
    if not messages:
        return _join_context_headers(profile_header, summary_header), [], debug
    return header, messages, debug


def build_group_recent_context(
    db,
    session_id: str,
    *,
    limit: int = MAX_GROUP_RECENT_ROWS,
    max_per_msg: int = 500,
    max_total: int = 3000,
    exclude_message_ids: list[str] | None = None,
) -> str:
    """Deprecated: 旧 `<group_recent_context>` 文本块构建器。

    真实回复链路和 Web 有效预览必须使用 `build_chat_context()` 返回的
    role messages。该函数仅保留给旧测试、手工排查和 rollback 场景。
    """
    from core.context_legacy import build_group_recent_context as _build_group_recent_context

    return _build_group_recent_context(
        db,
        session_id,
        limit=limit,
        max_per_msg=max_per_msg,
        max_total=max_total,
        exclude_message_ids=exclude_message_ids,
    )


def build_group_profile_context(group_id: str) -> str:
    """Deprecated: 旧测试兼容入口，真实运行时不得调用。"""
    from core.context_legacy import build_group_profile_context as _build_group_profile_context

    return _build_group_profile_context(group_id)
