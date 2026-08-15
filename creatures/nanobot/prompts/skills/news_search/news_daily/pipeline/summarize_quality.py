"""Quality 模式 LLM 摘要——基于 Light Evidence Cards 生成日报 JSON。"""

from collections.abc import Callable
import logging

from core.model_provider.contracts import ModelProviderResponse
from core.task_runtime import (
    TaskInvocation,
    TaskModelCompletion,
    TaskModelRequest,
    TaskRuntime,
    execute_task,
    thaw_task_value,
)

logger = logging.getLogger("nanobot.news_daily.quality")

QUALITY_SYSTEM_PROMPT = """你是 AI/科技日报编辑。只能基于给定的候选新闻卡片写日报。

硬规则：
1. 不得引入卡片之外的事实。
2. top_story/highlight/watchlist/details 必须绑定 source_ids。
3. 不要补全未知信息；没价格/API/benchmark 就写入 missing_info。
4. 不要写"行业持续发展""值得关注"等空话，除非后面有具体原因。
5. 不要把社区/媒体来源写成官方确认。
6. 不要 Markdown，不要 HTML，只输出 JSON。
7. 如果多条新闻重复，合并成一条 highlight，保留多个 source_ids。
8. 没有足够信息宁愿少写，不要编。
9. details 必须包含 known（已知2-3点）、unknown（缺失0-2点）、impact（一句话影响）。
10. 如果卡片没有足够细节，就明确写"信息不足"，不要扩写。
11. source_ids 必须是卡片“来源 #数字”对应的整数数组；禁止输出来源名、域名或字符串编号。
12. importance 必须是 1-5 的整数；只有 confidence 使用 high/medium 字符串。
13. title 不超过20个字符、subtitle 不超过30个字符、verdict 不超过90个字符、closing 不超过40个字符。

输出严格 JSON：
{
  "title": "≤20字",
  "subtitle": "≤30字",
  "verdict": "≤90字",
  "top_story": {
    "title": "头条标题", "what_happened": "≤160字", "why_it_matters": "≤100字",
    "source_ids": [1,2], "confidence": "high/medium"
  },
  "highlights": [
    {"label": "分类", "text": "100-150字，写清楚什么事+为什么重要+对谁有影响，不能只写标题", "source_ids": [1], "importance": 1-5}
  ],
  "details": [
    {"title": "事件标题", "known": ["已知事实"], "unknown": ["缺失信息"], "impact": "影响一句话", "source_labels": ["来源名"]}
  ],
  "watchlist": [{"text": "...", "reason": "...", "source_ids": [1]}],
  "missing_info": ["缺失信息"],
  "closing": "≤40字"
}"""


def get_quality_system_prompt() -> str:
    from core.prompt_v2.tool_templates import render_tool_execution_template

    return render_tool_execution_template(
        "tools/ai_daily/quality_system",
        {},
        fallback=QUALITY_SYSTEM_PROMPT,
        expected_tool_name="ai_daily",
    )


def build_quality_prompt(cards: list[dict]) -> str:
    card_texts = []
    for c in cards:
        detail = c.get('detail_text', '')
        known = '\n  - '.join(c.get('known_facts', [])) or '无'
        parts = [
            f"### 来源 #{c['source_id']}",
            f"标题: {c.get('title', '')}",
            f"来源: {c.get('source_name', '')} (组: {c.get('source_group', 'curated')})",
            f"域名: {c.get('domain', '')}",
            f"时间: {c.get('published_at', 'unknown')}",
            f"可信度: {c.get('trust', 0):.0%} ({c.get('confidence', 'medium')})",
            f"分类: {c.get('category', '未分类')}",
            f"实体: {', '.join(c.get('entities', []))}",
            f"数字: {', '.join(c.get('numbers', []))}",
            f"断言: {'; '.join(c.get('claims', []))}",
            f"摘要: {c.get('summary', '')}",
        ]
        if detail:
            parts.append(f"详情正文:\n  {detail}")
            parts.append(f"已知事实:\n  - {known}")
        parts.append(f"影响提示: {c.get('why_it_matters_hint', '')}")
        parts.append("---")
        card_texts.append("\n".join(parts))

    fallback = f"""## 候选新闻卡片 ({len(cards)} 条)

{chr(10).join(card_texts)}

## 要求
生成 1-6 条 highlights、1-3 条 details、0-2 条 watchlist；条目数不得超过候选卡片能独立支撑的事件数，候选不足时必须少写。
每条 highlight 100-150字，必须写清楚：什么事、为什么重要、对谁有影响。要像新闻导语一样有信息量，不能只写标题。
每条 detail 必须有 known（已知信息2-3点）、unknown（缺失信息0-2点）、impact（一句话影响）。
details 的 source_labels 使用卡片中的 "来源名（组）" 格式。
source_ids 只能填写“来源 #数字”中的整数，例如来源 #1 必须写 [1]；importance 只能填写 1-5 的整数。
只输出 JSON，第一个字符必须是 {{，最后一个必须是 }}。"""
    from core.prompt_v2.tool_templates import render_tool_execution_template

    return render_tool_execution_template(
        "tools/ai_daily/quality_user",
        {
            "candidate_cards": chr(10).join(card_texts),
            "card_count": str(len(cards)),
        },
        fallback=fallback,
        expected_tool_name="ai_daily",
    )


class _InjectedModelCallPort:
    """测试／显式调用方 Adapter；仍完整经过 TaskRuntime 校验。"""

    def __init__(self, model_call: Callable[..., object]) -> None:
        self._model_call = model_call

    @property
    def adapter_id(self) -> str:
        return "news_daily_injected_model_call"

    def complete_task(
        self,
        request: TaskModelRequest,
    ) -> TaskModelCompletion:
        response = self._model_call(
            route_key=request.route_key,
            messages=[
                dict(message)
                for message in request.messages
            ],
        )
        if not isinstance(response, ModelProviderResponse):
            raise TypeError("model_call 必须返回 ModelProviderResponse")
        return TaskModelCompletion(
            content=response.content,
            route_key=request.route_key,
            usage=response.usage,
            finish_reason=response.finish_reason,
        )


def _run_quality_task(
    cards: list[dict],
    prompt: str,
    *,
    model_call: Callable[..., object] | None,
):
    source_ids = tuple(
        int(card["source_id"])
        for card in cards
        if type(card.get("source_id")) is int
        and int(card["source_id"]) > 0
    )
    invocation = TaskInvocation(
        invocation_id="news_daily_quality",
        route_key="news_daily_quality",
        input_values={"message": prompt},
        request_context={"allowed_source_ids": source_ids},
        idempotency_key=(
            "news_daily_quality:"
            + ",".join(str(source_id) for source_id in source_ids)
        ),
        timeout_budget_seconds=20.0,
    )
    if model_call is not None:
        return TaskRuntime(
            _InjectedModelCallPort(model_call),
        ).execute(invocation)
    return execute_task(invocation)


def summarize_quality(
    cards: list[dict],
    fallback: dict,
    *,
    model_call: Callable[..., object] | None = None,
) -> dict:
    """调用 LLM 生成 quality 日报 JSON。失败返回 fallback。"""
    prompt = build_quality_prompt(cards)

    from core.llm_trace_context import llm_trace_scope

    with llm_trace_scope(source="news_daily.summarize_quality"):
        result = _run_quality_task(
            cards,
            prompt,
            model_call=model_call,
        )
    if not result.ok:
        failure_code = (
            result.failure.code.value
            if result.failure is not None
            else "provider_error"
        )
        logger.warning(
            "[quality] TaskRuntime failed code=%s fallback=true",
            failure_code,
        )
        return fallback
    parsed = thaw_task_value(result.parsed_value)
    logger.info(
        "[quality] TaskRuntime success output_bytes=%d",
        result.raw_output_bytes,
    )
    parsed["_quality_source"] = "llm"
    return parsed
