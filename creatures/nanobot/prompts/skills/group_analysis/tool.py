"""
Group Analysis tool — 群聊消息分析，生成话题总结/活跃用户称号/金句/质量锐评。

基于 astrbot_plugin_qq_group_daily_analysis 架构复刻：
- 消息过滤（去 game bot 命令/纯符号/超短消息）
- 用户统计（发言数/平均字数/夜间比例）→ LLM 称号分析
- 四路并发 LLM（话题/称号/金句/质量），各自 JSON→正则→降温重试
- 画像注入到分析 prompt 中保持风格一致
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from sqlalchemy import or_

logger = logging.getLogger("nanobot.tool.group_analysis")

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

# 游戏 bot 命令关键词
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

def _clean_message(content: str) -> str | None:
    """过滤并清理消息。返回 None 表示该消息应被丢弃。"""
    text = content.strip()
    if not text or len(text) <= 2:
        return None
    # slash 指令 / bot mention 指令
    if re.match(r"^\s*(?:<@!?\d+>|@\S+)?\s*/\S+", text):
        return None
    # 纯 mention
    if re.match(r"^(?:<@!?\d+>|@\S+)\s*$", text):
        return None
    # 纯数字/符号
    if re.match(r"^[\d\s\-+*/=.~`!@#$%^&*()\[\]{{}}|\\:;,.<>?/]+$", text):
        return None
    # 纯 URL
    if re.match(r"^https?://\S+$", text):
        return None
    # game bot commands
    if _is_game_command(text):
        return None
    # 清理特殊字符
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    return text


def _parse_instruction_window_hours(instructions: str) -> int | None:
    text = str(instructions or "").strip()
    if not text:
        return None

    hour_match = re.search(r"最近\s*(\d+)\s*小时", text)
    if hour_match:
        return max(1, int(hour_match.group(1)))

    day_match = re.search(r"最近\s*(\d+)\s*天", text)
    if day_match:
        return max(1, int(day_match.group(1))) * 24

    return None


def _filter_messages_by_hours(logs: list, hours: int | None, *, now: datetime | None = None) -> list:
    if not hours or hours <= 0:
        return list(logs)

    now = now or datetime.now()
    cutoff = now - timedelta(hours=hours)
    filtered = []
    for log in logs:
        created_at = log.get("created_at") if isinstance(log, dict) else getattr(log, "created_at", None)
        if created_at and created_at >= cutoff:
            filtered.append(log)
    return filtered


# ── 用户统计 ──

def _compute_user_stats(messages: list[dict]) -> dict:
    """计算每个用户的统计: 发言数/平均字数/夜间比例/回复比例"""
    stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "total_chars": 0, "night_count": 0, "reply_count": 0,
    })
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
        if s["count"] < 2:  # 忽略只发了1条的
            continue
        avg_chars = round(s["total_chars"] / s["count"], 1)
        night_ratio = round(s["night_count"] / s["count"], 2)
        reply_ratio = round(s["reply_count"] / s["count"], 2)
        result[uid] = {
            "count": s["count"], "avg_chars": avg_chars,
            "night_ratio": night_ratio, "reply_ratio": reply_ratio,
        }
    return result


def _compute_group_statistics(messages: list[dict]) -> dict[str, Any]:
    hour_counts: dict[int, int] = defaultdict(int)
    participants: set[str] = set()
    total_characters = 0
    emoji_count = 0

    for message in messages:
        participants.add(str(message.get("user_id") or "?"))
        content = str(message.get("content") or "")
        total_characters += len(content)
        emoji_count += _count_emojis(content)
        hour_counts[int(message.get("hour", 0))] += 1

    message_count = len(messages)
    participant_count = len(participants)
    average_message_length = round(total_characters / message_count, 1) if message_count else 0.0
    most_active_hour = max(hour_counts.items(), key=lambda item: item[1])[0] if hour_counts else 0

    return {
        "message_count": message_count,
        "participant_count": participant_count,
        "total_characters": total_characters,
        "average_message_length": average_message_length,
        "most_active_period": f"{most_active_hour:02d}:00-{(most_active_hour + 1) % 24:02d}:00",
        "hourly_counts": dict(hour_counts),
        "emoji_count": emoji_count,
    }


def _count_emojis(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text))


def _format_hourly_activity(hourly_counts: dict[int, int]) -> list[str]:
    if not hourly_counts:
        return ["_暂无活跃度分布数据_"]

    peak = max(hourly_counts.values()) or 1
    lines = [
        "| 时段 | 消息数 | 活跃条 |",
        "| --- | ---: | --- |",
    ]
    for hour in sorted(hourly_counts):
        count = hourly_counts[hour]
        bar_len = max(1, round(count / peak * 8))
        lines.append(f"| {hour:02d}:00 | {count} | {'█' * bar_len} |")
    return lines


# ── LLM 调用 + 重试 ──

async def _call_llm_with_retry(
    client, system_prompt: str, prompt: str,
    max_retries: int = 2,
) -> str:
    """调用 LLM，JSON 解析失败时正则降级，仍失败则降温重试。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    last_raw = ""
    for attempt in range(max_retries + 1):
        temp = max(0.05, 0.3 * (0.5 ** attempt))  # 0.3 → 0.15 → 0.05
        resp = await client.chat_completion(
            messages=messages, model_tier="smart",
            manual_model="deepseek-v4-flash", temperature=temp,
        )
        if "error" in resp:
            if attempt < max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM failed after {max_retries + 1} attempts: {resp['error']}")

        raw = resp["choices"][0]["message"]["content"]
        last_raw = raw

        # 尝试 JSON 解析
        from core.legacy_adapter import EvolutionUtils
        data = EvolutionUtils.json_repair(raw)
        if isinstance(data, dict) and not data.get("parse_error"):
            return raw
        # 最后尝试不做重试
        if attempt == max_retries:
            return raw

    return last_raw


# ── 主工具类 ──

class GroupAnalysisTool(BaseTool):
    """分析群聊消息，生成日报总结。"""

    @property
    def tool_name(self) -> str:
        return "group_analysis"

    @property
    def description(self) -> str:
        return (
            "分析群聊消息生成日报。提取话题总结、活跃用户称号、金句和聊天质量锐评。"
            "当用户要求总结群聊、分析群消息、看群日报时使用。"
            "group_id 可以是群号或群名（如'凡赛尔图书馆'）。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "要分析的群号或 session_id",
                },
                "instructions": {
                    "type": "string",
                    "description": "可选的分析指引，如'只看最近2小时'",
                },
            },
            "required": ["group_id"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        group_id = str(args.get("group_id", "")).strip()
        instructions = str(args.get("instructions", "")).strip()

        if not group_id:
            return ToolResult(error="Missing 'group_id' argument")

        try:
            from core.database import SessionLocal, ChatLog, User
            from clients.new_api_client import NewAPIClient
            from config import NEW_API_KEY, NEW_API_BASE_URL

            db = SessionLocal()
            try:
                # 0. 群名——从 users 表查（群也是 user，id=group_xxx）
                normalized_group_id = f"group_{group_id}" if not group_id.startswith("group_") else group_id
                legacy_group_id = normalized_group_id.removeprefix("group_")
                u = db.query(User).filter(User.id == normalized_group_id).first()
                group_name = (u.name or normalized_group_id) if u else normalized_group_id

                # 1. 读取群消息
                logs = (
                    db.query(ChatLog)
                    .filter(
                        or_(
                            ChatLog.session_id == normalized_group_id,
                            ChatLog.session_id == legacy_group_id,
                        )
                    )
                    .order_by(ChatLog.id.desc())
                    .limit(300)
                    .all()
                )
                logs.reverse()
                logs = _filter_messages_by_hours(logs, _parse_instruction_window_hours(instructions))

                if not logs:
                    return ToolResult(output=f"未找到群 {group_id} 的消息记录", exit_code=0)

                # 2. 过滤 + 清洗消息
                messages = []
                for log in logs:
                    content = _clean_message(log.content or "")
                    if not content:
                        continue
                    sender = log.sender_name or log.user_id or "?"
                    hour = log.created_at.hour if log.created_at else 12
                    messages.append({
                        "user_id": str(sender),
                        "content": content,
                        "time": log.created_at.strftime("%H:%M") if log.created_at else "??:??",
                        "hour": hour,
                        "is_reply": "回复" in (log.content or ""),
                    })

                if len(messages) < 3:
                    return ToolResult(output=f"群 {group_id} 可分析的消息不足（需≥3条）", exit_code=0)

                logger.info(f"[group_analysis] {len(logs)} raw → {len(messages)} cleaned for {group_name}")

                group_stats = _compute_group_statistics(messages)

                # 4. 格式化消息文本
                messages_text = "\n".join(
                    f"[{m['time']}] [{m['user_id']}]: {m['content']}"
                    for m in messages
                )

                # 4. 用户统计
                user_stats = _compute_user_stats(messages)
                top_users = sorted(user_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:15]
                users_text = "\n".join(
                    f"- {uid} | {s['count']}条 | 均{s['avg_chars']}字 | "
                    f"夜间{s['night_ratio']:.0%} | 回复{s['reply_ratio']:.0%}"
                    for uid, s in top_users
                )

                # 5. 注入画像（如有）
                from core.database import Persona as PersonaModel
                persona = db.query(PersonaModel).filter(
                    PersonaModel.user_id == normalized_group_id
                ).first()
                persona_text = ""
                if persona and persona.persona_json and persona.persona_json != "{}":
                    try:
                        pd = json.loads(persona.persona_json)
                        if pd.get("facts"):
                            persona_text = "分析风格参考: " + "；".join(
                                f["content"] for f in pd["facts"][:5]
                            )
                    except Exception:
                        pass

                persona_hint = f"\n\n## 分析风格指引\n{persona_text}" if persona_text else ""

                # 6. 四路并发 LLM
                client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL, timeout=180)
                system_base = "你是群聊分析助手。只输出JSON，不要markdown标记或额外说明。" + persona_hint

                async def analyze_topics():
                    prompt = TOPIC_PROMPT.format(messages_text=messages_text)
                    return await _call_llm_with_retry(client, system_base, prompt)

                async def analyze_titles():
                    prompt = USER_TITLE_PROMPT.format(
                        users_text=users_text, messages_text=messages_text[-3000:],
                    )
                    return await _call_llm_with_retry(client, system_base, prompt)

                async def analyze_quotes():
                    prompt = GOLDEN_QUOTE_PROMPT.format(messages_text=messages_text)
                    return await _call_llm_with_retry(client, system_base, prompt)

                async def analyze_quality():
                    prompt = CHAT_QUALITY_PROMPT.format(messages_text=messages_text[-4000:])
                    return await _call_llm_with_retry(client, system_base, prompt)

                topic_raw, title_raw, quote_raw, quality_raw = await asyncio.gather(
                    analyze_topics(), analyze_titles(), analyze_quotes(), analyze_quality(),
                )

                # 7. 解析四路结果
                from core.legacy_adapter import EvolutionUtils
                topics = EvolutionUtils.json_repair(topic_raw)
                titles = EvolutionUtils.json_repair(title_raw)
                quotes = EvolutionUtils.json_repair(quote_raw)
                quality = EvolutionUtils.json_repair(quality_raw)

                # 8. Markdown 输出（QQbot 端 md_to_pic 自动渲染为图片）
                report = _format_markdown(
                    group_name,
                    group_stats,
                    user_stats,
                    topics if isinstance(topics, dict) else {},
                    titles if isinstance(titles, dict) else {},
                    quality if isinstance(quality, dict) else {},
                    quotes if isinstance(quotes, dict) else {},
                )
                return ToolResult(output=report, exit_code=0)

            finally:
                db.close()
        except Exception as e:
            logger.error(f"[group_analysis] Failed: {e}", exc_info=True)
            return ToolResult(error=f"群聊分析失败: {str(e)}")


def _format_markdown(
    group_name: str,
    group_stats: dict[str, Any],
    user_stats: dict[str, dict],
    topics: dict,
    titles: dict,
    quality: dict,
    quotes: dict,
) -> str:
    """合并分析结果为 Markdown（QQbot 端 md_to_pic 自动渲染为图片）。"""
    lines = [
        f"# 📊 {group_name} 群聊日报",
        "",
        f"> 分析 {group_stats.get('message_count', 0)} 条消息",
        "",
        "## 统计概览",
        "",
        f"- 消息总数：{group_stats.get('message_count', 0)}",
        f"- 参与人数：{group_stats.get('participant_count', 0)}",
        f"- 总字数：{group_stats.get('total_characters', 0)}",
        f"- 平均消息长度：{group_stats.get('average_message_length', 0)} 字",
        f"- 最活跃时段：{group_stats.get('most_active_period', '00:00-01:00')}",
        f"- 表情使用次数：{group_stats.get('emoji_count', 0)}",
        "",
        "## 活跃度分布",
        "",
    ]

    lines.extend(_format_hourly_activity(group_stats.get("hourly_counts", {})))
    lines.append("")

    if user_stats:
        lines.append("## 活跃用户速览")
        lines.append("")
        lines.append("| 用户 | 发言数 | 平均字数 | 夜间比例 | 回复比例 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for uid, stats in sorted(user_stats.items(), key=lambda item: item[1]["count"], reverse=True)[:10]:
            lines.append(
                f"| {uid} | {stats.get('count', 0)} | {stats.get('avg_chars', 0)} | "
                f"{stats.get('night_ratio', 0):.0%} | {stats.get('reply_ratio', 0):.0%} |"
            )
        lines.append("")

    topic_list = topics.get("topics", [])
    if topic_list:
        lines.append("## 话题总结")
        lines.append("")
        for i, t in enumerate(topic_list, 1):
            contributors = "、".join(t.get("contributors", [])[:5])
            lines.append(f"### {i}. {t.get('topic', '?')}")
            lines.append(f"> 参与者: {contributors}")
            if t.get("detail"):
                lines.append(f"")
                lines.append(t["detail"])
            lines.append("")

    user_list = titles.get("users", [])
    if user_list:
        lines.append("## 活跃用户")
        lines.append("")
        lines.append("| 用户 | 称号 | MBTI | 理由 |")
        lines.append("| --- | --- | --- | --- |")
        for u in user_list:
            lines.append(
                f"| {u.get('user_id', '?')} | {u.get('title', '')} | "
                f"{u.get('mbti', '') or '-'} | {u.get('reason', '')} |"
            )
        lines.append("")

    if quality:
        lines.append("## 聊天质量锐评")
        lines.append("")
        if quality.get("title"):
            lines.append(f"### {quality.get('title')}")
        if quality.get("subtitle"):
            lines.append(f"> {quality.get('subtitle')}")
            lines.append("")
        for dimension in quality.get("dimensions", [])[:4]:
            lines.append(
                f"- {dimension.get('name', '维度')}：{dimension.get('percentage', 0)}% - {dimension.get('comment', '')}"
            )
        if quality.get("summary"):
            lines.append("")
            lines.append(str(quality.get("summary")))
        lines.append("")

    quote_list = quotes.get("quotes", [])
    if quote_list:
        lines.append("## 💬 金句")
        lines.append("")
        for q in quote_list:
            lines.append(f"> {q.get('content', '')}")
            lines.append(f"")
            lines.append(f"—— {q.get('user_id', '?')}")
            lines.append("")

    lines.extend(
        [
            "---",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    )

    return "\n".join(lines) if len(lines) > 2 else "分析完成，但未提取到足够信息。"
