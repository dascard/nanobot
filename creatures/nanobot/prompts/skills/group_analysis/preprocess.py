"""消息清洗、去重、统计——全部纯函数，不依赖 ORM 或 LLM。"""

import json
import logging
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger("nanobot.tool.group_analysis.preprocess")

# ── 消息过滤 ──

GAME_COMMAND_PATTERNS = [
    r"^\d+连?钓鱼", r"^宠物", r"^扭蛋", r"^鱼[饵竿钩]", r"^背包",
    r"^签到", r"^打工", r"^我的宠物", r"^宠物背包", r"^宠物事件",
    r"^股票", r"^买入", r"^卖出", r"^万连", r"^千连", r"^百连",
    r"^概率公示", r"已自动购买", r"鱼累了", r"鱼竿累了", r"鱼钩累了",
    r"^✅\s*卖出成功", r"^卖出成功", r"^\d+\*", r"你的.*钓鱼结果",
    r"^技能药水", r"^遗忘药水", r"^豪华蛋糕", r"^奶油蛋糕", r"^玩具球",
    r"^普通扭蛋", r"^总价值", r"^总花费", r"^今日已钓鱼",
]


def _is_game_command(text: str) -> bool:
    for pat in GAME_COMMAND_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def clean_message(content: str) -> str | None:
    text = _strip_sender_prefix(content).strip()
    if not text or len(text) <= 2:
        return None
    if re.match(r"^\s*(?:<@!?\d+>|@\S+)?\s*/\S+", text):
        return None
    if re.match(r"^(?:<@!?\d+>|@\S+)\s*$", text):
        return None
    if re.match(r"^[\d\s\-+*/=.~`!@#$%^&*()\[\]{{}}|\\:;,.<>?/]+$", text):
        return None
    if re.match(r"^https?://\S+$", text):
        return None
    if _is_game_command(text):
        return None
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    return text


# ── 时间窗口 ──

def parse_instruction_window_hours(instructions: str) -> int | None:
    text = str(instructions or "").strip()
    if not text:
        return None
    if re.search(r"(全部|全量|所有|不限|不限制|完整历史|全部历史)", text):
        return 0
    m = re.search(r"最近\s*(\d+)\s*小时", text)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"最近\s*(\d+)\s*天", text)
    if m:
        return max(1, int(m.group(1))) * 24
    return None


def resolve_analysis_window_hours(
    window_hours: Any = None,
    instructions: str = "",
    *,
    default_hours: int = 24,
    max_hours: int = 24 * 30,
) -> int | None:
    """解析最终分析窗口；None 表示不加时间过滤。"""
    parsed = parse_instruction_window_hours(instructions)
    if parsed is not None:
        return None if parsed <= 0 else min(parsed, max_hours)

    if window_hours is None or str(window_hours).strip() == "":
        return default_hours
    try:
        value = int(float(str(window_hours).strip()))
    except (TypeError, ValueError):
        return default_hours
    if value <= 0:
        return None
    return min(value, max_hours)


# ── 可分析日志过滤 ──

_HTML_ARTIFACT_MARKERS = (
    "<!doctype html",
    "<html",
    "<article",
    "<style",
    "group-analysis-report",
    "news-brief",
    "nanobot_reply_output",
)

_INTERNAL_TEXT_PREFIXES = (
    "[NO_SEND]",
    "[系统内部错误]",
    "[工具错误]",
    "Traceback",
)


def _safe_meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _meta_blocks_analysis(meta: dict) -> bool:
    if meta.get("no_send") or meta.get("internal") or meta.get("control") or meta.get("no_context"):
        return True
    moderation = meta.get("moderation")
    if isinstance(moderation, dict) and moderation.get("no_context"):
        return True
    kind = str(meta.get("kind") or "").strip()
    if kind in {"artifact_summary", "tool_result", "internal", "control"}:
        return True
    return False


def is_analyzable_log(log: Any) -> bool:
    """判断 ChatLog 是否可作为群聊分析语料。"""
    role = str(getattr(log, "role", "") or "").strip()
    if role not in {"ambient", "user", "assistant"}:
        return False

    meta = _safe_meta(getattr(log, "meta_json", "") or "")
    if _meta_blocks_analysis(meta):
        return False

    content = str(getattr(log, "content", "") or "").strip()
    if not content:
        return False
    lowered = content[:2000].lower()
    if any(marker in lowered for marker in _HTML_ARTIFACT_MARKERS):
        return False
    if any(content.startswith(prefix) for prefix in _INTERNAL_TEXT_PREFIXES):
        return False
    return True


def filter_analyzable_logs(logs: list[Any]) -> list[Any]:
    """过滤内部记录、HTML 报告和非聊天角色，保留真实群聊语料。"""
    return [log for log in logs if is_analyzable_log(log)]


# ── 去重 ──

def _source_ids_for_log(log: Any) -> set[str]:
    ids: set[str] = set()
    message_id = str(getattr(log, "message_id", "") or "").strip()
    if message_id:
        ids.add(message_id)
    raw = str(getattr(log, "source_message_ids_json", "") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                ids.update(str(x).strip() for x in data if str(x).strip())
        except json.JSONDecodeError:
            logger.debug("[group_analysis] invalid source_message_ids_json: %.80s", raw)
    return ids


def _strip_sender_prefix(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    m = re.match(r"^\[[^\]]{1,80}\]\s*[:：]\s*(.*)$", text)
    return m.group(1).strip() if m else text


def _content_contains_source(user_content: str, ambient_content: str) -> bool:
    source = _strip_sender_prefix(ambient_content)
    if not source:
        return False
    target = str(user_content or "")
    if source in target:
        return True
    compact_source = re.sub(r"\s+", "", source)
    compact_target = re.sub(r"\s+", "", target)
    return bool(compact_source) and compact_source in compact_target


def dedupe_group_logs(logs: list[Any]) -> list[Any]:
    """按原始入站消息去重。user 仅在确实覆盖原文时替换 ambient。"""
    direct_user_ids: set[str] = set()
    source_to_user_logs: dict[str, list[Any]] = defaultdict(list)
    for log in logs:
        if getattr(log, "role", "") != "user":
            continue
        message_id = str(getattr(log, "message_id", "") or "").strip()
        if message_id:
            direct_user_ids.add(message_id)
        for source_id in _source_ids_for_log(log):
            source_to_user_logs[source_id].append(log)

    seen_ambient_ids: set[str] = set()
    seen_user_ids: set[str] = set()
    deduped: list[Any] = []
    for log in logs:
        role = getattr(log, "role", "")
        if role not in ("user", "ambient"):
            deduped.append(log)
            continue

        ids = _source_ids_for_log(log)
        message_id = str(getattr(log, "message_id", "") or "").strip()
        if role == "user":
            if message_id and message_id in seen_user_ids:
                continue
            if message_id:
                seen_user_ids.add(message_id)
            deduped.append(log)
            continue

        if ids and ids & direct_user_ids:
            continue
        if ids:
            covered = False
            for source_id in ids:
                for user_log in source_to_user_logs.get(source_id, []):
                    if _content_contains_source(
                        getattr(user_log, "content", ""),
                        getattr(log, "content", ""),
                    ):
                        covered = True
                        break
                if covered:
                    break
            if covered:
                continue
        if ids and ids & seen_ambient_ids:
            continue
        seen_ambient_ids.update(ids)
        deduped.append(log)
    return deduped


# ── 消息清洗入口 ──

def build_clean_messages(logs: list[Any]) -> list[dict]:
    messages = []
    for log in logs:
        raw_content = (
            log.content if hasattr(log, "content")
            else log.get("content", "")
        )
        c = clean_message(raw_content)
        if not c:
            continue
        sender = (
            getattr(log, "sender_name", None) or
            getattr(log, "user_id", "") or "?"
        )
        created_at = (
            log.created_at if hasattr(log, "created_at")
            else log.get("created_at")
        )
        hour = created_at.hour if created_at else 12
        messages.append({
            "log_id": getattr(log, "id", 0) if hasattr(log, "id") else log.get("id", 0),
            "user_id": str(sender),
            "content": c,
            "time": created_at.strftime("%H:%M") if created_at else "??:??",
            "hour": hour,
            "is_reply": "回复" in (raw_content or ""),
        })
    return messages


# ── 统计 ──

def compute_user_stats(messages: list[dict]) -> dict:
    stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "total_chars": 0, "night_count": 0, "reply_count": 0}
    )
    for m in messages:
        uid = m["user_id"]
        s = stats[uid]
        s["count"] += 1
        s["total_chars"] += len(m["content"])
        hour = m.get("hour", 12)
        if 0 <= hour < 6:
            s["night_count"] += 1
        if m.get("is_reply"):
            s["reply_count"] += 1
    result = {}
    for uid, s in stats.items():
        if s["count"] < 2:
            continue
        result[uid] = {
            "count": s["count"],
            "avg_chars": round(s["total_chars"] / s["count"], 1),
            "night_ratio": round(s["night_count"] / s["count"], 2),
            "reply_ratio": round(s["reply_count"] / s["count"], 2),
        }
    return result


def compute_group_statistics(messages: list[dict]) -> dict[str, Any]:
    hour_counts: dict[int, int] = defaultdict(int)
    participants: set[str] = set()
    total_characters = 0
    emoji_count = 0
    for m in messages:
        participants.add(str(m.get("user_id") or "?"))
        content = str(m.get("content") or "")
        total_characters += len(content)
        emoji_count += _count_emojis(content)
        hour_counts[int(m.get("hour", 0))] += 1
    mc = len(messages)
    pc = len(participants)
    peak = max(hour_counts.items(), key=lambda x: x[1])[0] if hour_counts else 0
    return {
        "message_count": mc,
        "participant_count": pc,
        "total_characters": total_characters,
        "average_message_length": round(total_characters / mc, 1) if mc else 0,
        "most_active_period": f"{peak:02d}:00-{(peak + 1) % 24:02d}:00",
        "hourly_counts": dict(hour_counts),
        "emoji_count": emoji_count,
    }


def _count_emojis(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text))


# ── Prompt 构造 ──

def _format_message_line(message: dict[str, Any]) -> str:
    content = re.sub(r"\s+", " ", str(message.get("content") or "")).strip()
    if len(content) > 500:
        content = content[:500] + "..."
    return f"[{message.get('time', '??:??')}] [{message.get('user_id', '?')}]: {content}"


def _pack_lines_forward(lines: list[str], max_chars: int) -> str:
    picked: list[str] = []
    used = 0
    for line in lines:
        extra = len(line) + (1 if picked else 0)
        if used + extra > max_chars:
            break
        picked.append(line)
        used += extra
    return "\n".join(picked)


def _pack_lines_backward(lines: list[str], max_chars: int) -> str:
    picked: list[str] = []
    used = 0
    for line in reversed(lines):
        extra = len(line) + (1 if picked else 0)
        if used + extra > max_chars:
            break
        picked.append(line)
        used += extra
    picked.reverse()
    return "\n".join(picked)


def build_message_prompt_text(messages: list[dict[str, Any]], max_chars: int) -> str:
    header = f"原始可分析消息总数: {len(messages)}\n"
    lines = [_format_message_line(m) for m in messages]
    full = header + "\n".join(lines)
    if len(full) <= max_chars:
        return full

    marker = "\n...（中间消息已按预算压缩，保留开头与最新消息；统计信息仍基于全部消息）...\n"
    remaining = max(0, max_chars - len(header) - len(marker))
    if remaining <= 0:
        return (header + marker).strip()[:max_chars]

    head_budget = remaining // 4
    tail_budget = remaining - head_budget
    head = _pack_lines_forward(lines, head_budget)
    tail = _pack_lines_backward(lines, tail_budget)
    compact = header + head + marker + tail
    return compact[:max_chars]


# ── 组装入口 ──

def build_analysis_payload(
    logs: list[Any],
    *,
    prompt_budget: int = 60000,
    style_budget: int = 24000,
    local_rag_query: str = "",
    enable_local_rag: bool = False,
    embedding_provider: Any = None,
    reranker_provider: Any = None,
) -> dict:
    """从 raw logs 一次性产出 analyzer 所需全部数据。"""
    messages = build_clean_messages(logs)
    group_stats = compute_group_statistics(messages)
    user_stats = compute_user_stats(messages)
    prompt_messages = messages
    local_rag = {
        "stats_logs": {
            "total_messages": len(messages),
            "bundle_count": 0,
            "lexical_candidates": 0,
            "temporary_embedding_scored": 0,
            "reranker_candidates": 0,
            "selected_bundles": 0,
            "selected_messages": len(messages),
        },
        "prompt_logs": {"hit_bundles": [], "selected_bundles": [], "selected_message_ids": []},
    }
    if enable_local_rag and local_rag_query and messages:
        from .local_rag import select_group_analysis_context

        local_rag = select_group_analysis_context(
            messages,
            query=local_rag_query,
            budget_chars=prompt_budget,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
        )
        prompt_messages = local_rag.get("messages") or messages

    msg_text = build_message_prompt_text(prompt_messages, prompt_budget)
    style_msg_text = build_message_prompt_text(prompt_messages, style_budget)

    top = sorted(user_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
    users_text = "\n".join(
        f"- {uid} | {s['count']}条 | 均{s['avg_chars']}字 | "
        f"夜间{s['night_ratio']:.0%} | 回复{s['reply_ratio']:.0%}"
        for uid, s in top
    )

    source_log_ids = [getattr(log, "id", 0) or 0 for log in logs if getattr(log, "id", 0)]

    return {
        "messages": messages,
        "group_stats": group_stats,
        "user_stats": user_stats,
        "msg_text": msg_text,
        "style_msg_text": style_msg_text,
        "users_text": users_text,
        "source_log_ids": source_log_ids,
        "local_rag": local_rag,
    }
