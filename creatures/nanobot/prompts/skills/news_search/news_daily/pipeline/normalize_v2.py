"""Article 标准化——title/source/time/entity/topic 归一化。"""

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from core.tool_contracts.ai_daily import AI_DAILY_TIMEZONE

from .config import OFFICIAL_SOURCES, SOURCE_QUALITY, STOP_WORDS, TOPIC_KEYWORDS, KNOWN_ENTITIES
from .models import Article

_OFFICIAL_DOMAINS = {
    "openai.com", "anthropic.com", "deepmind.google",
    "mistral.ai", "deepseek.com", "qwen.ai", "kimi.com",
    "moonshot.cn", "x.ai", "nvidia.com", "cohere.com",
    "meta.com", "ai.meta.com",
}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _build_datetime(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}")
    except ValueError:
        return None


def parse_date(raw: str) -> datetime | None:
    """多格式日期解析——ISO 8601 / RFC 2822 / 常见变体。"""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return (
            dt.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)
            if dt.tzinfo is not None
            else dt
        )
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt and dt.tzinfo:
            dt = dt.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        pass
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$", value)
    if m:
        return _build_datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4) or 0),
            int(m.group(5) or 0),
            int(m.group(6) or 0),
        )
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", value)
    if m:
        return _build_datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", value)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return _build_datetime(int(m.group(3)), month, int(m.group(2)))
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$", value)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return _build_datetime(int(m.group(3)), month, int(m.group(1)))
    return None


def _is_official(source_name: str, domain: str) -> bool:
    sn = source_name.strip().lower().replace(" ", "_")
    return sn in OFFICIAL_SOURCES or domain.lower() in _OFFICIAL_DOMAINS


def _norm_key(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]


def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^\w\s一-鿿]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _simple_tokenize(text: str) -> list[str]:
    tokens = []
    for part in text.split():
        if re.search(r"[一-鿿]", part):
            tokens.extend(part)
        else:
            tokens.append(part)
    return tokens


def token_set(text: str) -> set[str]:
    text = normalize_title(text)
    tokens = _simple_tokenize(text)
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 1}


def extract_entities(text: str) -> list[str]:
    found, text_l = [], text.lower()
    for key, aliases in KNOWN_ENTITIES.items():
        if any(alias.lower() in text_l for alias in aliases):
            found.append(key)
    return found


def extract_topic_keys(text: str) -> list[str]:
    keys, text_l = [], text.lower()
    for key, words in TOPIC_KEYWORDS.items():
        if any(w.lower() in text_l for w in words):
            keys.append(key)
    return keys


def compute_source_quality(source_group: str) -> float:
    return SOURCE_QUALITY.get(source_group, 0.3)


def normalize_articles(raw_items: list) -> list[Article]:
    articles = []
    for item in raw_items:
        pub = parse_date(getattr(item, "published_at", "") or "")
        source_name = getattr(item, "source_name", "") or ""
        source_group = getattr(item, "source_group", "") or "unknown"
        title = getattr(item, "title", "") or ""
        domain = getattr(item, "domain", "") or ""
        text = f"{title} {getattr(item, 'summary', '') or ''} {getattr(item, 'detail_text', '') or ''}"
        articles.append(Article(
            id=getattr(item, "id", "") or _norm_key(title),
            title=title, url=getattr(item, "url", "") or "",
            source=source_name, source_group=source_group, domain=domain,
            published_at=pub,
            summary=(getattr(item, "summary", "") or "")[:400],
            content=(getattr(item, "detail_text", "") or getattr(item, "content_excerpt", "") or "")[:1200],
            title_norm=normalize_title(title),
            entity_keys=extract_entities(text),
            topic_keys=extract_topic_keys(text),
            source_quality_score=compute_source_quality(source_group),
            is_official=_is_official(source_name, domain),
            is_time_unknown=pub is None,
        ))
    return articles
