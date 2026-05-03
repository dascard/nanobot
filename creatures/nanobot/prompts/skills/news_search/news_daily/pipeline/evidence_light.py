"""Light Evidence Cards——从 NewsItem 提取结构化输入给 LLM。"""

import re
from ..schema import NewsItem

# 复用 evidence.py 的提取逻辑
AI_KEYWORDS = re.compile(
    r"(模型|发布|开源|API|价格|token|免费|付费|benchmark|评测|"
    r"GPT|Claude|Gemini|DeepSeek|Qwen|Llama|Mistral|"
    r"上下文|context|多模态|推理|embedding|agent|"
    r"训练|微调|参数|权重|GPU)",
    re.IGNORECASE,
)

ENTITY_PATTERNS = re.compile(
    r"(GPT-?[\d.]+[a-z]?|Claude\s*[\d.]+|Gemini\s*[\d.]+|DeepSeek[-\s]?\w+|"
    r"Qwen[\w.\- ]+|Llama\s*[\d.]+|Mistral[-\s]?\w+|Gemma[-\s]?\w+|"
    r"Phi[-\s]?[\d.]+|DBRX|Falcon[-\s]?\w+|Yi[-\s]?\w+|"
    r"OpenAI|Anthropic|Google|Meta|Microsoft|NVIDIA|Cohere|Stability)",
    re.IGNORECASE,
)

CLAIM_VERBS = re.compile(
    r"(发布|推出|宣布|开源|支持|上线|开放|降价|免费|"
    r"超越|超过|达到|实现|launch|release|announce|support|open.source)",
    re.IGNORECASE,
)

NUMBER_PATTERNS = re.compile(
    r"(\$[\d.]+|[\d.]+[KkMmBb]\s*(tokens?|参数|parameters)?|"
    r"\d+\s*(billion|million|万|亿)|"
    r"\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract_claims(text: str) -> list[str]:
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    claims = []
    for s in sentences:
        s = s.strip()
        if len(s) > 15 and CLAIM_VERBS.search(s) and len(claims) < 3:
            claims.append(s[:200])
    return claims


def extract_numbers(text: str) -> list[str]:
    results = set()
    for m in NUMBER_PATTERNS.finditer(text):
        s = m.group().strip()
        if len(s) < 30:
            results.add(s)
    return list(results)[:6]


def extract_entities(text: str) -> list[str]:
    return list(set(ENTITY_PATTERNS.findall(text)))[:8]


def trim_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    return text[:max_chars].rstrip() + ("..." if len(text) > max_chars else "")


def confidence_from_item(item: NewsItem) -> str:
    if item.trust >= 0.90:
        return "high"
    if item.trust >= 0.70:
        return "medium"
    return "low"


def build_light_evidence_cards(items: list[NewsItem]) -> list[dict]:
    cards = []
    for idx, item in enumerate(items, start=1):
        text = f"{item.title} {item.summary or item.content_excerpt}"
        cards.append({
            "source_id": idx,
            "title": item.title,
            "url": item.url,
            "domain": item.domain,
            "source_name": item.source_name,
            "published_at": item.published_at or "unknown",
            "category": item.category,
            "trust": round(item.trust, 2),
            "confidence": confidence_from_item(item),
            "summary": trim_text(item.summary, 220),
            "claims": extract_claims(text),
            "numbers": extract_numbers(text),
            "entities": extract_entities(text),
            "related_text": trim_text(item.summary or item.title, 260),
        })
    return cards
