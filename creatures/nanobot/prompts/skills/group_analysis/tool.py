"""
Group Analysis tool — 群聊消息分析，生成手账风格 HTML 日报。

基于 astrbot_plugin_qq_group_daily_analysis 架构复刻：
- 消息过滤（去 game bot 命令/纯符号/超短消息）
- 用户统计（发言数/平均字数/夜间比例）→ LLM 称号分析
- 四路并发 LLM（话题/称号/金句/质量），各自 JSON→正则→降温重试
- Scrapbook 风格 HTML 报告（参考原版 templates/scrapbook/html_template.html）
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from sqlalchemy import or_

logger = logging.getLogger("nanobot.tool.group_analysis")

GROUP_ANALYSIS_MAX_LOGS = int(os.environ.get("GROUP_ANALYSIS_MAX_LOGS", "5000"))
GROUP_ANALYSIS_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_PROMPT_CHAR_BUDGET", "60000"))
GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET", "24000"))
_LAST_GROUP_ANALYSIS_REPORT: tuple[float, str] = (0.0, "")

# ── Prompt 模板 ──

TOPIC_PROMPT = """分析以下群聊记录，提取核心讨论话题。

## 消息格式: [HH:MM] [user_id]: 内容

## 输出 JSON
{{
  "topics": [
    {{"topic": "话题名(≤15字)", "contributors": ["user_id"], "detail": "一句话总结讨论内容"}}
  ]
}}

要求: 2-5个话题，按讨论热度排序。只输出JSON。

## 群聊消息
{messages_text}"""

USER_TITLE_PROMPT = """根据群聊发言统计和消息内容，给活跃用户生成有趣的称号。

## 用户发言统计 (格式: user_id | 发言数 | 平均字数 | 夜间比例 | 回复比例)
{users_text}

## 近期消息 (用于了解发言风格)
{messages_text}

## 输出 JSON
{{
  "users": [
    {{"user_id": "user_id", "title": "称号(≤8字)", "mbti": "可选，4位MBTI或空字符串", "reason": "一句话理由"}}
  ]
}}

要求: 3-8个用户。称号要贴合发言风格，有趣但不冒犯。MBTI 仅在把握较高时给出，否则留空。只输出JSON。"""

GOLDEN_QUOTE_PROMPT = """从群聊记录中提取最有趣的发言。

## 消息格式: [HH:MM] [user_id]: 内容

## 输出 JSON
{{
  "quotes": [
    {{"user_id": "user_id", "content": "发言原文(≤80字)"}}
  ]
}}

要求: 0-3条。优先提取幽默/有深度/有梗的发言。只输出JSON。

## 群聊消息
{messages_text}"""

CHAT_QUALITY_PROMPT = """请根据以下群聊记录，给出结构化的聊天质量锐评。

## 消息格式: [HH:MM] [user_id]: 内容

## 输出 JSON
{{
  "title": "一句话总评(≤12字)",
  "subtitle": "简短副标题(≤20字)",
  "dimensions": [
    {{"name": "维度名", "percentage": 0-100, "comment": "一句话点评"}}
  ],
  "summary": "2-3句话的整体总结"
}}

要求:
- 维度控制在 2-4 个
- 百分比反映相对表现，不要全部给满分
- 只输出 JSON

## 群聊消息
{messages_text}"""

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
        if re.search(pat, text): return True
    return False

def _clean_message(content: str) -> str | None:
    text = content.strip()
    if not text or len(text) <= 2: return None
    if re.match(r"^\s*(?:<@!?\d+>|@\S+)?\s*/\S+", text): return None
    if re.match(r"^(?:<@!?\d+>|@\S+)\s*$", text): return None
    if re.match(r"^[\d\s\-+*/=.~`!@#$%^&*()\[\]{{}}|\\:;,.<>?/]+$", text): return None
    if re.match(r"^https?://\S+$", text): return None
    if _is_game_command(text): return None
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    return text

def _escape_html(text: Any) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def _safe_percentage(value: Any, default: int = 50) -> int:
    try:
        pct = int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        pct = default
    return max(0, min(100, pct))

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

def _build_message_prompt_text(messages: list[dict[str, Any]], max_chars: int) -> str:
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

def _remember_group_analysis_report(report: str) -> None:
    global _LAST_GROUP_ANALYSIS_REPORT
    if report and "group-analysis-report" in report:
        _LAST_GROUP_ANALYSIS_REPORT = (time.monotonic(), report)

def get_recent_group_analysis_report(max_age_seconds: float = 300.0) -> str:
    created_at, report = _LAST_GROUP_ANALYSIS_REPORT
    if not report:
        return ""
    if time.monotonic() - created_at > max_age_seconds:
        return ""
    return report

def _parse_instruction_window_hours(instructions: str) -> int | None:
    text = str(instructions or "").strip()
    if not text: return None
    m = re.search(r"最近\s*(\d+)\s*小时", text)
    if m: return max(1, int(m.group(1)))
    m = re.search(r"最近\s*(\d+)\s*天", text)
    if m: return max(1, int(m.group(1))) * 24
    return None

def _filter_messages_by_hours(logs: list, hours: int | None, *, now: datetime | None = None) -> list:
    if not hours or hours <= 0: return list(logs)
    now = now or datetime.now()
    cutoff = now - timedelta(hours=hours)
    return [l for l in logs if (l.get("created_at") if isinstance(l, dict) else getattr(l, "created_at", None)) and
            (l.get("created_at") if isinstance(l, dict) else getattr(l, "created_at", None)) >= cutoff]

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
    """判断合并后的 user 行是否确实包含某条 ambient 原文。"""
    source = _strip_sender_prefix(ambient_content)
    if not source:
        return False
    target = str(user_content or "")
    if source in target:
        return True
    compact_source = re.sub(r"\s+", "", source)
    compact_target = re.sub(r"\s+", "", target)
    return bool(compact_source) and compact_source in compact_target

def _dedupe_group_logs(logs: list[Any]) -> list[Any]:
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

        # 同一个 message_id 的 user 行是 ambient 的正式处理副本，直接去重。
        if ids and ids & direct_user_ids:
            continue
        # 批量 source ids 只有在 user 内容确实包含该 ambient 原文时才去重。
        if ids:
            covered = False
            for source_id in ids:
                for user_log in source_to_user_logs.get(source_id, []):
                    if _content_contains_source(getattr(user_log, "content", ""), getattr(log, "content", "")):
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

# ── 统计 ──

def _compute_user_stats(messages: list[dict]) -> dict:
    stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_chars": 0, "night_count": 0, "reply_count": 0})
    for m in messages:
        uid = m["user_id"]
        s = stats[uid]; s["count"] += 1; s["total_chars"] += len(m["content"])
        hour = m.get("hour", 12)
        if 0 <= hour < 6: s["night_count"] += 1
        if m.get("is_reply"): s["reply_count"] += 1
    result = {}
    for uid, s in stats.items():
        if s["count"] < 2: continue
        result[uid] = {"count": s["count"], "avg_chars": round(s["total_chars"] / s["count"], 1),
                        "night_ratio": round(s["night_count"] / s["count"], 2),
                        "reply_ratio": round(s["reply_count"] / s["count"], 2)}
    return result

def _compute_group_statistics(messages: list[dict]) -> dict[str, Any]:
    hour_counts: dict[int, int] = defaultdict(int)
    participants: set[str] = set()
    total_characters = 0; emoji_count = 0
    for m in messages:
        participants.add(str(m.get("user_id") or "?"))
        content = str(m.get("content") or ""); total_characters += len(content)
        emoji_count += _count_emojis(content)
        hour_counts[int(m.get("hour", 0))] += 1
    mc = len(messages); pc = len(participants)
    peak = max(hour_counts.items(), key=lambda x: x[1])[0] if hour_counts else 0
    return {"message_count": mc, "participant_count": pc, "total_characters": total_characters,
            "average_message_length": round(total_characters / mc, 1) if mc else 0,
            "most_active_period": f"{peak:02d}:00-{(peak+1)%24:02d}:00",
            "hourly_counts": dict(hour_counts), "emoji_count": emoji_count}

def _count_emojis(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text))

# ── LLM 调用 ──

async def _call_llm_with_retry(client, sys_prompt: str, prompt: str, max_retries: int = 2) -> str:
    last_raw = ""
    for attempt in range(max_retries + 1):
        t = max(0.05, 0.3 * (0.5 ** attempt))
        resp = await client.chat_completion(
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}],
            model_tier="smart", manual_model="deepseek-v4-flash", temperature=t)
        if "error" in resp:
            if attempt < max_retries: await asyncio.sleep(1.5 * (attempt + 1)); continue
            raise RuntimeError(f"LLM failed: {resp['error']}")
        raw = resp["choices"][0]["message"]["content"]
        last_raw = raw
        from core.legacy_adapter import EvolutionUtils
        d = EvolutionUtils.json_repair(raw)
        if isinstance(d, dict) and not d.get("parse_error"): return raw
        if attempt == max_retries: return raw
    return last_raw


class GroupAnalysisTool(BaseTool):
    """分析群聊消息，生成手账风格 HTML 日报。"""

    @property
    def tool_name(self) -> str: return "group_analysis"

    @property
    def description(self) -> str:
        return ("分析群聊消息生成日报。提取话题总结、活跃用户称号、金句和氛围。"
                "当用户要求总结群聊、分析群消息、看群日报时使用。group_id 可以是群号或群名。")

    @property
    def execution_mode(self) -> ExecutionMode: return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "group_id": {"type": "string", "description": "要分析的群号或 session_id"},
            "instructions": {"type": "string", "description": "可选的分析指引"}},
            "required": ["group_id"]}

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        group_id = str(args.get("group_id", "")).strip()
        instructions = str(args.get("instructions", "")).strip()
        if not group_id: return ToolResult(error="Missing 'group_id' argument")

        try:
            from core.database import SessionLocal, ChatLog, User
            from clients.new_api_client import NewAPIClient
            from config import NEW_API_KEY, NEW_API_BASE_URL

            db = SessionLocal()
            try:
                # 支持群名模糊匹配（非纯数字且非 group_ 前缀 → 按 name 搜索）
                if group_id.isdigit():
                    ngid = f"group_{group_id}"
                elif group_id.startswith("group_"):
                    ngid = group_id
                else:
                    ngid = None
                    matched = db.query(User).filter(
                        User.id.like("group_%"), User.name.like(f"%{group_id}%")
                    ).first()
                    if matched:
                        ngid = matched.id
                if not ngid:
                    return ToolResult(output=f"未找到群 \"{group_id}\"——群号或群名不匹配", exit_code=0)
                lgid = ngid.removeprefix("group_")
                u = db.query(User).filter(User.id == ngid).first()
                group_name = (u.name or lgid) if u else lgid

                logs = (db.query(ChatLog).filter(
                    or_(ChatLog.session_id == ngid, ChatLog.session_id == lgid))
                    .order_by(ChatLog.id.desc()).limit(GROUP_ANALYSIS_MAX_LOGS).all())
                logs.reverse()
                logs = _filter_messages_by_hours(logs, _parse_instruction_window_hours(instructions))
                logs = _dedupe_group_logs(logs)
                if not logs: return ToolResult(output=f"未找到群 {group_id} 的消息记录", exit_code=0)

                messages = []
                for log in logs:
                    c = _clean_message(log.content or "")
                    if not c: continue
                    sender = log.sender_name or log.user_id or "?"
                    hour = log.created_at.hour if log.created_at else 12
                    messages.append({"user_id": str(sender), "content": c,
                                     "time": log.created_at.strftime("%H:%M") if log.created_at else "??:??",
                                     "hour": hour, "is_reply": "回复" in (log.content or "")})
                if len(messages) < 3:
                    return ToolResult(output=f"群 {group_id} 可分析的消息不足（需≥3条）", exit_code=0)
                logger.info(f"[group_analysis] {len(logs)} raw → {len(messages)} cleaned for {group_name}")

                group_stats = _compute_group_statistics(messages)
                msg_text = _build_message_prompt_text(messages, GROUP_ANALYSIS_PROMPT_CHAR_BUDGET)
                style_msg_text = _build_message_prompt_text(messages, GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET)
                user_stats = _compute_user_stats(messages)
                top = sorted(user_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
                users_text = "\n".join(f"- {uid} | {s['count']}条 | 均{s['avg_chars']}字 | 夜间{s['night_ratio']:.0%} | 回复{s['reply_ratio']:.0%}" for uid, s in top)

                client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL, timeout=180)
                SYS = "你是群聊分析助手。只输出JSON，不要markdown或额外说明。"

                topic_raw, title_raw, quote_raw, quality_raw = await asyncio.gather(
                    _call_llm_with_retry(client, SYS, TOPIC_PROMPT.format(messages_text=msg_text)),
                    _call_llm_with_retry(client, SYS, USER_TITLE_PROMPT.format(users_text=users_text, messages_text=style_msg_text)),
                    _call_llm_with_retry(client, SYS, GOLDEN_QUOTE_PROMPT.format(messages_text=msg_text)),
                    _call_llm_with_retry(client, SYS, CHAT_QUALITY_PROMPT.format(messages_text=msg_text)))

                from core.legacy_adapter import EvolutionUtils
                def _p(raw): d = EvolutionUtils.json_repair(raw); return d if isinstance(d, dict) else {}
                report = _format_scrapbook_html(
                    group_name, group_stats,
                    _p(topic_raw), _p(title_raw), _p(quote_raw), _p(quality_raw))
                _remember_group_analysis_report(report)
                return ToolResult(output=report, exit_code=0)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[group_analysis] Failed: {e}", exc_info=True)
            return ToolResult(error=f"群聊分析失败: {str(e)}")


# ── Scrapbook HTML 模板（参考原版 templates/scrapbook/html_template.html） ──

SCRAPBOOK_CSS = """
:root{--ink-primary:#5d4037;--ink-secondary:#8d6e63;--accent-orange:#ff7043;--bg-paper:#fdfbf7;
  --color-yellow:#fff9c4;--color-pink:#ffccbc;--color-blue:#b3e5fc;--color-green:#c8e6c9;--color-purple:#e1bee7;
  --font-title:'WenQuanYi Zen Hei','Microsoft YaHei',cursive;--font-hand:'KaiTi','STKaiti',serif;
  --font-body:'WenQuanYi Micro Hei','Noto Sans SC','Microsoft YaHei',sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font-body);color:var(--ink-primary);background-color:var(--bg-paper);
  background-image:radial-gradient(#ddd 2px,transparent 2px);background-size:20px 20px;
  min-height:100vh;padding:30px 15px;line-height:1.6}
.container{max-width:960px;margin:0 auto;background:#fff;border:2px solid var(--ink-primary);
  border-radius:20px;padding:35px;position:relative;
  box-shadow:8px 8px 0 var(--color-blue),16px 16px 0 var(--color-pink),0 18px 36px rgba(0,0,0,.1)}
.container::before{content:"";position:absolute;top:10px;bottom:10px;left:10px;right:10px;
  border:2px dashed var(--ink-secondary);border-radius:14px;pointer-events:none;opacity:.45}
.header{text-align:center;margin-bottom:35px;padding-top:15px}
.title-sticker{display:inline-block;background:#fff;padding:18px 50px;border:3px dashed var(--ink-primary);
  border-radius:15px;box-shadow:5px 5px 0 var(--color-blue);transform:rotate(-2deg);position:relative}
.title-sticker h1{font-family:var(--font-title);font-size:2.2rem;color:var(--accent-orange);margin:0}
.date-badge{position:absolute;bottom:-14px;right:-18px;background:var(--color-yellow);
  padding:4px 14px;font-family:var(--font-hand);font-size:.9rem;
  box-shadow:2px 2px 3px rgba(0,0,0,.1);transform:rotate(5deg);border:1px solid var(--ink-primary)}
.tape{position:absolute;top:-13px;left:50%;transform:translateX(-50%);width:110px;height:22px;
  background:rgba(255,171,145,.7);opacity:.8}
.section{margin-top:22px}
.section-title{font-family:var(--font-title);font-size:1.3rem;margin-bottom:12px;padding-bottom:6px;
  border-bottom:3px dotted var(--accent-orange);color:var(--ink-primary)}
.stats-wrapper{display:flex;gap:18px;margin-bottom:25px;align-items:stretch}
.stats-grid{flex:2;display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.stamp{background:#fff;padding:14px;box-shadow:4px 4px 0 rgba(0,0,0,.1);
  border:2px solid var(--ink-primary);border-radius:12px;text-align:center}
.stamp-num{font-family:var(--font-title);font-size:1.5rem;color:var(--accent-orange)}
.stamp-label{font-family:var(--font-hand);font-size:.85rem;color:var(--ink-secondary)}
.highlight-section{flex:1;background:var(--color-yellow);padding:18px;border:2px solid var(--ink-primary);
  border-radius:18px;box-shadow:6px 6px 0 var(--color-pink);text-align:center}
.time-big{font-family:var(--font-title);font-size:1.8rem;color:var(--ink-primary);margin:6px 0}
.topic-list{list-style:none}
.topic-item{display:flex;align-items:flex-start;margin-bottom:14px;padding:10px 14px;
  background:#fffdfa;border:1px solid rgba(216,179,107,.25);border-radius:10px}
.topic-num{width:26px;height:26px;background:var(--accent-orange);color:#fff;
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:var(--font-title);font-size:.85rem;flex-shrink:0;margin-right:10px;margin-top:1px}
.topic-title{font-weight:bold;color:var(--ink-primary);font-size:1rem}
.topic-detail{color:#888;font-size:.85rem;margin-top:3px}
.topic-contributors{color:#bbb;font-size:.75rem}
.masonry-grid{column-count:2;column-gap:18px}
.user-card{break-inside:avoid;background:#fffbf5;border:2px solid var(--ink-primary);
  border-radius:12px;padding:16px;margin-bottom:18px;box-shadow:3px 3px 0 rgba(0,0,0,.06)}
.user-card:nth-child(even){transform:rotate(.4deg);border-color:var(--color-blue)}
.user-card:nth-child(3n){transform:rotate(-.4deg);border-color:var(--color-pink)}
.user-header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.u-name{font-family:var(--font-title);font-size:1.1rem;flex:1}
.badges{display:flex;gap:5px;flex-wrap:wrap}
.badge{font-size:.75rem;padding:2px 7px;border-radius:7px;font-family:var(--font-hand)}
.badge.title{background:#fffde7;color:#bf360c;border:1px solid #ffb74d}
.badge.mbti{background:#ede7f6;color:#512da8;border:1px solid #9575cd;font-weight:bold}
.u-reason{font-family:var(--font-hand);font-size:.9rem;color:#555;line-height:1.5;
  background:#f8fbff;border-left:3px solid var(--color-blue);padding:8px 12px;border-radius:4px;margin-top:8px}
.quotes-section{display:flex;flex-direction:column;gap:14px}
.quote-block{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;
  padding:14px 18px;box-shadow:4px 4px 0 var(--color-blue)}
.quote-block:nth-child(even){background:var(--color-yellow);box-shadow:-4px 4px 0 var(--color-pink)}
.q-content{font-family:var(--font-title);font-size:1.05rem;line-height:1.4;margin-bottom:6px}
.q-author{font-family:var(--font-hand);color:#999;font-size:.8rem;text-align:right}
.quality-card{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;
  padding:18px 22px;box-shadow:5px 5px 0 rgba(0,0,0,.05)}
.quality-title{font-family:var(--font-title);font-size:1.2rem;color:var(--accent-orange)}
.quality-dims{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}
.quality-dim{flex:1;min-width:110px;text-align:center}
.dim-bar{height:6px;background:#eee;border-radius:3px;margin:4px 0;overflow:hidden}
.dim-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent-orange),var(--color-pink))}
.dim-name{font-size:.82rem;color:var(--ink-secondary)}
.dim-pct{font-family:var(--font-title);font-size:1.1rem;color:var(--ink-primary)}
.dim-comment{font-size:.78rem;color:#999}
.chart-section{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;padding:16px;margin-top:18px}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin-top:6px}
th{text-align:left;color:var(--ink-secondary);font-family:var(--font-hand);font-size:.82rem;padding:5px 7px}
td{padding:5px 7px;border-bottom:1px solid #f0f0f0}
.footer{margin-top:25px;text-align:center;color:#ccc;font-size:.75rem}
"""


def _format_scrapbook_html(
    group_name: str, group_stats: dict,
    topics: dict, titles: dict, quotes: dict, quality: dict,
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = [
        ("消息总数", group_stats.get("message_count", 0)),
        ("参与人数", group_stats.get("participant_count", 0)),
        ("总字数", group_stats.get("total_characters", 0)),
        ("表情统计", group_stats.get("emoji_count", 0)),
    ]
    stamps_html = "".join(
        f'<div class="stamp"><div class="stamp-num">{_escape_html(v)}</div><div class="stamp-label">{_escape_html(k)}</div></div>'
        for k, v in cards)

    # 话题
    tlist = topics.get("topics", [])
    topic_html = '<ul class="topic-list">' + "".join(
        f'<li class="topic-item"><span class="topic-num">{i}</span><div>'
        f'<div class="topic-title">{_escape_html(t.get("topic","?"))}</div>'
        f'<div class="topic-contributors">{"、".join(_escape_html(x) for x in t.get("contributors",[])[:5])}</div>'
        f'<div class="topic-detail">{_escape_html(t.get("detail",""))}</div></div></li>'
        for i, t in enumerate(tlist, 1)) + '</ul>' if tlist else '<p style="color:#999">暂无显著话题。</p>'

    # 用户
    ulist = titles.get("users", [])
    if ulist:
        cards_list = []
        for u in ulist:
            mbti = u.get("mbti", "").strip()
            badges = f'<span class="badge title">{_escape_html(u.get("title",""))}</span>'
            if mbti: badges += f'<span class="badge mbti">{_escape_html(mbti)}</span>'
            cards_list.append(
                f'<div class="user-card"><div class="user-header">'
                f'<div class="u-name">{_escape_html(u.get("user_id","?"))}</div>'
                f'<div class="badges">{badges}</div></div>'
                f'<div class="u-reason">{_escape_html(u.get("reason",""))}</div></div>')
        title_html = '<div class="masonry-grid">' + "".join(cards_list) + '</div>'
    else:
        title_html = '<p style="color:#999">暂无活跃用户画像。</p>'

    # 金句
    qlist = quotes.get("quotes", [])
    quote_html = '<div class="quotes-section">' + "".join(
        f'<div class="quote-block"><div class="q-content">{_escape_html(q.get("content",""))}</div>'
        f'<div class="q-author">—— {_escape_html(q.get("user_id","?"))}</div></div>'
        for q in qlist[:3]) + '</div>' if qlist else '<p style="color:#999">暂无可提取金句。</p>'

    # 质量
    dims = quality.get("dimensions", [])
    if dims:
        dim_cards = []
        for d in dims[:4]:
            pct = _safe_percentage(d.get("percentage", 50))
            dim_cards.append(
                f'<div class="quality-dim"><div class="dim-name">{_escape_html(d.get("name","?"))}</div>'
                f'<div class="dim-pct">{pct}%</div>'
                f'<div class="dim-bar"><div class="dim-fill" style="width:{pct}%"></div></div>'
                f'<div class="dim-comment">{_escape_html(d.get("comment",""))}</div></div>'
            )
        dim_html = '<div class="quality-dims">' + "".join(dim_cards) + '</div>'
        quality_html = (
            f'<div class="section"><div class="section-title">📊 聊天质量锐评</div>'
            f'<div class="quality-card"><div class="quality-title">{_escape_html(quality.get("title","群聊质量锐评"))}</div>'
            f'<p style="color:#888;margin:4px 0">{_escape_html(quality.get("subtitle",""))}</p>'
            f'{dim_html}<p style="color:#777;font-size:.85rem;margin-top:8px">{_escape_html(quality.get("summary",""))}</p></div></div>')
    else:
        quality_html = ""

    # 活跃图表
    hourly = group_stats.get("hourly_counts", {})
    chart_html = ""
    if hourly:
        peak = max(hourly.values()) or 1
        rows = "".join(
            f'<tr><td>{h:02d}:00</td><td>{c}</td><td>'
            f'<div style="display:inline-block;width:{max(10,int(c/peak*180))}px;height:10px;'
            f'background:var(--accent-orange);border-radius:5px;opacity:.7"></div></td></tr>'
            for h, c in sorted(hourly.items()))
        chart_html = (
            f'<div class="chart-section"><div class="section-title">📈 24H 活跃轨迹</div>'
            f'<table><tr><th>时段</th><th>消息数</th><th>活跃度</th></tr>{rows}</table></div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><style>{SCRAPBOOK_CSS}</style></head>
<body class="group-analysis-report">
<div class="container group-analysis-report">
  <div class="header">
    <div class="title-sticker">
      <div class="tape"></div>
      <h1>{_escape_html(group_name)} 群聊日报</h1>
      <div class="date-badge">{now_str}</div>
    </div>
  </div>
  <div class="stats-wrapper">
    <div class="stats-grid">{stamps_html}</div>
    <div class="highlight-section">
      <div style="font-family:var(--font-hand);color:var(--ink-secondary)">⭐ Highlight Time</div>
      <div class="time-big">{group_stats.get("most_active_period","00:00")}</div>
      <div style="font-family:var(--font-hand);color:var(--ink-secondary);font-size:.85rem">最活跃时段</div>
    </div>
  </div>
  {chart_html}
  <div class="section"><div class="section-title">📝 话题总结</div>{topic_html}</div>
  <div class="section"><div class="section-title">👥 群友画像</div>{title_html}</div>
  <div class="section"><div class="section-title">💬 群聊金句</div>{quote_html}</div>
  {quality_html}
  <div class="footer">Nanobot · {now_str}</div>
</div>
</body>
</html>"""
