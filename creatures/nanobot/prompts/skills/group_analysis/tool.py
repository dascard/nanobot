"""
Group Analysis tool — 群聊消息分析，生成手账风格 HTML 日报。

分层架构：
  tool.py         BaseTool 入口（仅调度）
  repository.py   SQL 查询
  preprocess.py   消息清洗、去重、统计
  analyzer.py     LLM 四路并发调用
  render.py       Scrapbook HTML 渲染
  cache.py        业务缓存 + 最近报告
"""

import logging
import os
import time
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.tool.group_analysis")

GROUP_ANALYSIS_MAX_LOGS = int(os.environ.get("GROUP_ANALYSIS_MAX_LOGS", "5000"))
GROUP_ANALYSIS_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_PROMPT_CHAR_BUDGET", "60000"))
GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET", "24000"))


class GroupAnalysisTool(BaseTool):
    """分析群聊消息，生成手账风格 HTML 日报。"""

    @property
    def tool_name(self) -> str:
        return "group_analysis"

    @property
    def description(self) -> str:
        return ("分析群聊消息并生成群日报。提取话题总结、活跃用户称号、金句和氛围。"
                "当用户要求总结群聊、分析群消息、生成群日报、查看某群近期讨论时使用。"
                "group_id 可以直接传群号、group_前缀ID或群名；工具内部会自动解析群号/群名"
                "并查询消息记录。不要为了查找群号而先调用 sql_analysis。")

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "要分析的群号或 session_id"},
                "instructions": {"type": "string", "description": "可选的分析指引"},
            },
            "required": ["group_id"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        group_id = str(args.get("group_id", "")).strip()
        instructions = str(args.get("instructions", "")).strip()
        if not group_id:
            return ToolResult(error="Missing 'group_id' argument")

        try:
            from core.database import SessionLocal
            from .repository import GroupAnalysisRepository
            from .preprocess import parse_instruction_window_hours
            from .preprocess import dedupe_group_logs, build_analysis_payload
            from .analyzer import analyze_group
            from .render import format_scrapbook_html, format_error_html
            from . import cache

            t0 = time.monotonic()
            db = SessionLocal()
            try:
                repo = GroupAnalysisRepository(db)

                group = repo.resolve_group(group_id)
                if not group:
                    # 检查是否多匹配
                    candidates = repo.get_group_candidates(group_id)
                    if candidates:
                        lines = [f"group_{c['id']} — {c['name']}" for c in candidates[:10]]
                        return ToolResult(
                            output=format_error_html(
                                "匹配到多个群",
                                f"关键词 \"{group_id}\" 匹配到 {len(candidates)} 个群，请使用精确的群号或群名：",
                                lines,
                            ),
                            exit_code=0,
                        )
                    return ToolResult(
                        output=format_error_html(
                            "未找到群",
                            f"未找到群 \"{group_id}\"，群号或群名不匹配。",
                        ),
                        exit_code=0,
                    )

                window_hours = parse_instruction_window_hours(instructions)
                batch = repo.fetch_group_logs(
                    group,
                    window_hours=window_hours,
                    limit=GROUP_ANALYSIS_MAX_LOGS,
                )

                # 业务缓存
                cached = cache.get_cached_report(
                    group.group_id, window_hours or 0, instructions, batch.latest_log_id, batch.raw_count,
                )
                if cached:
                    cache.remember_group_analysis_report(cached)
                    logger.info("[group_analysis] cache_hit=true group=%s", group.group_id)
                    return ToolResult(output=cached, exit_code=0)

                logs = dedupe_group_logs(batch.logs)
                if not logs:
                    return ToolResult(
                        output=format_error_html("消息不足", f"群 {group.name} 暂无消息记录。"),
                        exit_code=0,
                    )

                preprocess_t0 = time.monotonic()
                payload = build_analysis_payload(
                    logs,
                    prompt_budget=GROUP_ANALYSIS_PROMPT_CHAR_BUDGET,
                    style_budget=GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET,
                )
                preprocess_ms = round((time.monotonic() - preprocess_t0) * 1000)

                if len(payload["messages"]) < 3:
                    return ToolResult(
                        output=format_error_html(
                            "消息不足", f"群 {group.name} 可分析的消息不足（需≥3条）。",
                        ),
                        exit_code=0,
                    )

                llm_t0 = time.monotonic()
                analysis = await analyze_group(payload, instructions)
                llm_ms = round((time.monotonic() - llm_t0) * 1000)

                render_t0 = time.monotonic()
                report = format_scrapbook_html(
                    group.name, payload["group_stats"],
                    analysis["topics"], analysis["titles"],
                    analysis["quotes"], analysis["quality"],
                )
                render_ms = round((time.monotonic() - render_t0) * 1000)

                cache.set_cached_report(
                    group.group_id, window_hours or 0, instructions, batch.latest_log_id, batch.raw_count, report,
                )
                cache.remember_group_analysis_report(report)

                total_ms = round((time.monotonic() - t0) * 1000)
                logger.info(
                    "[group_analysis] group=%s window=%sh raw=%d deduped=%d cleaned=%d "
                    "msg_chars=%d style_chars=%d "
                    "preprocess_ms=%d llm_ms=%d render_ms=%d total_ms=%d cache_hit=false",
                    group.group_id, window_hours or 0,
                    batch.raw_count, len(logs), len(payload["messages"]),
                    len(payload["msg_text"]), len(payload["style_msg_text"]),
                    preprocess_ms, llm_ms, render_ms, total_ms,
                )

                return ToolResult(output=report, exit_code=0)

            finally:
                db.close()

        except Exception as e:
            logger.error("[group_analysis] Failed: %s", e, exc_info=True)
            from .render import format_error_html
            return ToolResult(
                output=format_error_html("群聊分析失败", "工具执行时发生异常。", [str(e)[:300]]),
                exit_code=0,
            )
