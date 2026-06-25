"""评分排序。"""

import logging
import re
from datetime import datetime
from core.time_utils import db_now_naive
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.rank")

AI_KEYWORDS = re.compile(
    r"(model|api|llm|gpt|claude|gemini|qwen|deepseek|mistral|llama|grok|kimi|"
    r"openai|anthropic|open.source|weights|benchmark|context|reasoning|"
    r"transformer|embedding|fine.tun|checkpoint|nvidia|nemotron|granite|"
    r"模型|大模型|开源|权重|上下文|推理|多模态|智能体|"
    r"发布|API|价格|token|免费|付费|评测|训练|微调|参数|GPU|"
    r"编程|代码|agent|扩散|视频生成|语音|视觉|机器人|"
    r"计算基础设施|网络安全|数据中心|芯片|算力)",
    re.IGNORECASE,
)

NON_AI_PATTERNS = re.compile(
    r"(brain|sleep|diagnos\w+|medical|patient|clinical|disease|drug|"
    r"hospital|surgery|cancer|cognitive|neuroscience|"
    r"lawsuit|copyright|stole|sues|patent.infr|oscar|movie|film|"
    r"actor|script|music.stream|curiosity.driven|campus|student)",
    re.IGNORECASE,
)


def is_ai_industry_relevant(item) -> bool:
    text = f"{item.title} {item.summary}"
    if item.source_name in ("mit_ai",):
        return bool(AI_KEYWORDS.search(text))
    if item.source_name in ("techcrunch_ai", "theverge_ai"):
        if NON_AI_PATTERNS.search(text):
            return False
    return bool(AI_KEYWORDS.search(text))


def rank_items(items: list[NewsItem]) -> list[NewsItem]:
    """评分排序 + AI行业过滤。"""
    now = db_now_naive()

    for item in items:
        if item.published_at:
            try:
                dt = datetime.fromisoformat(item.published_at)
                days = (now - dt).days
                item.freshness = 1.0 if days <= 1 else (0.8 if days <= 3 else (0.5 if days <= 7 else 0.2))
            except Exception:
                item.freshness = 0.5
        else:
            item.freshness = 0.3

        text = f"{item.title} {item.summary}"
        hits = len(AI_KEYWORDS.findall(text))
        item.relevance = min(1.0, hits * 0.15)

        item.score = round(
            item.trust * 0.40 + item.freshness * 0.25 +
            item.relevance * 0.25,
            2,
        )

    items = [i for i in items if is_ai_industry_relevant(i)]
    items.sort(key=lambda x: x.score, reverse=True)
    return items
