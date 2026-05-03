"""评分排序。"""

import logging
import re
from datetime import datetime, timedelta
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.rank")

AI_KEYWORDS = re.compile(
    r"(模型|发布|开源|API|价格|token|免费|付费|benchmark|评测|"
    r"GPT|Claude|Gemini|DeepSeek|Qwen|Llama|Mistral|"
    r"上下文|context|多模态|推理|embedding|agent|"
    r"训练|微调|参数|权重|GPU)",
    re.IGNORECASE,
)


def rank_items(items: list[NewsItem]) -> list[NewsItem]:
    """计算综合评分并排序。"""
    now = datetime.now()

    for item in items:
        # freshness
        if item.published_at:
            try:
                dt = datetime.strptime(item.published_at, "%Y-%m-%d")
                days = (now - dt).days
                item.freshness = 1.0 if days <= 1 else (0.8 if days <= 3 else (0.5 if days <= 7 else 0.2))
            except Exception:
                item.freshness = 0.5
        else:
            item.freshness = 0.3

        # relevance
        text = f"{item.title} {item.summary}"
        hits = len(AI_KEYWORDS.findall(text))
        item.relevance = min(1.0, hits * 0.15)

        # score
        item.score = round(
            item.trust * 0.40 + item.freshness * 0.25 +
            item.relevance * 0.20 + 0.10 + 0.05,
            2,
        )

    items.sort(key=lambda x: x.score, reverse=True)
    return items
