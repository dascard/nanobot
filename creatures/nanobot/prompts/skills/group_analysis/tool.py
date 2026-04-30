"""
Group Analysis tool — 群聊消息分析，生成话题总结/活跃用户/金句提取。

基于 astrbot_plugin_qq_group_daily_analysis 的核心思路复刻，
适配 nanobot 的 chat_logs 数据模型。
"""

import json
import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.group_analysis")

GROUP_ANALYSIS_PROMPT = """分析以下群聊记录，生成一份简洁的群聊日报。

## 消息格式
[时间] [用户ID]: 消息内容

## 分析要求
1. **话题总结**（2-5个）：每个话题包含话题名、参与者和一句话详情
2. **活跃用户**（3-8个）：根据发言频率和质量，给每位用户一个有趣的称号
3. **金句提取**（0-3条）：提取最有趣/有深度的发言
4. **整体氛围**：用1-2句话概括群聊氛围

## 输出 JSON
{{
  "topics": [
    {{"topic": "话题名", "contributors": ["用户ID"], "detail": "一句话详情"}}
  ],
  "active_users": [
    {{"user_id": "用户ID", "title": "称号", "reason": "理由"}}
  ],
  "golden_quotes": [
    {{"user_id": "用户ID", "content": "发言内容"}}
  ],
  "atmosphere": "氛围描述"
}}

## 群聊消息
{messages_text}"""


class GroupAnalysisTool(BaseTool):
    """分析群聊消息，生成日报总结。"""

    @property
    def tool_name(self) -> str:
        return "group_analysis"

    @property
    def description(self) -> str:
        return (
            "分析群聊消息生成日报。提取话题、活跃用户、金句和氛围总结。"
            "当用户要求总结群聊、分析群消息、群日报时使用。"
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
                    "description": "可选的分析指引，如'重点分析技术讨论''只看最近2小时'",
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
            from core.database import SessionLocal, ChatLog
            from clients.new_api_client import NewAPIClient
            from config import NEW_API_KEY, NEW_API_BASE_URL

            db = SessionLocal()
            try:
                # 1. 读取群消息（最近200条）
                logs = (
                    db.query(ChatLog)
                    .filter(ChatLog.session_id == group_id)
                    .order_by(ChatLog.id.desc())
                    .limit(200)
                    .all()
                )
                logs.reverse()

                if not logs:
                    return ToolResult(
                        output=f"未找到群 {group_id} 的消息记录", exit_code=0
                    )

                # 2. 格式化消息
                messages_lines = []
                for log in logs:
                    sender = log.sender_name or log.user_id or "unknown"
                    time_str = (
                        log.created_at.strftime("%H:%M")
                        if log.created_at else "??:??"
                    )
                    content = (log.content or "").strip()
                    if content:
                        messages_lines.append(
                            f"[{time_str}] [{sender}]: {content}"
                        )

                if not messages_lines:
                    return ToolResult(
                        output=f"群 {group_id} 没有可分析的文本消息", exit_code=0
                    )

                messages_text = "\n".join(messages_lines)

                # 3. 构建 prompt
                prompt = GROUP_ANALYSIS_PROMPT.format(
                    messages_text=messages_text
                )
                if instructions:
                    prompt += f"\n\n## 额外指引\n{instructions}"

                # 4. 调用 LLM
                client = NewAPIClient(
                    api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL, timeout=180
                )

                system_prompt = (
                    "你是一个群聊分析助手。根据群聊消息生成日报JSON。"
                    "只输出JSON，不要额外说明。"
                )
                resp = await client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    model_tier="smart",
                    manual_model="deepseek-v4-flash",
                    temperature=0.3,
                )

                if "error" in resp:
                    return ToolResult(error=f"LLM 调用失败: {resp['error']}")

                raw = resp["choices"][0]["message"]["content"]

                # 5. 解析 + 格式化输出
                from core.legacy_adapter import EvolutionUtils
                data = EvolutionUtils.json_repair(raw)

                if isinstance(data, dict) and not data.get("parse_error"):
                    return ToolResult(
                        output=_format_report(data), exit_code=0
                    )
                else:
                    return ToolResult(output=raw[:3000], exit_code=0)

            finally:
                db.close()
        except Exception as e:
            logger.error(f"[group_analysis] Failed: {e}", exc_info=True)
            return ToolResult(error=f"群聊分析失败: {str(e)}")


def _format_report(data: dict) -> str:
    """将 LLM 返回的 JSON 格式化为可读文本。"""
    lines = []

    topics = data.get("topics", [])
    if topics:
        lines.append("📊 **话题总结**")
        for i, t in enumerate(topics, 1):
            contributors = "、".join(t.get("contributors", [])[:5])
            lines.append(f"  {i}. {t.get('topic', '?')} ({contributors})")
            lines.append(f"     {t.get('detail', '')}")
        lines.append("")

    users = data.get("active_users", [])
    if users:
        lines.append("👥 **活跃用户**")
        for u in users:
            lines.append(
                f"  • {u.get('user_id', '?')}【{u.get('title', '')}】"
                f"{u.get('reason', '')}"
            )
        lines.append("")

    quotes = data.get("golden_quotes", [])
    if quotes:
        lines.append("💬 **金句**")
        for q in quotes:
            lines.append(f"  > {q.get('content', '')}")
            lines.append(f"    —— {q.get('user_id', '?')}")
        lines.append("")

    atmosphere = data.get("atmosphere", "")
    if atmosphere:
        lines.append(f"🎭 **氛围**: {atmosphere}")

    return "\n".join(lines) if lines else json.dumps(data, ensure_ascii=False, indent=2)
