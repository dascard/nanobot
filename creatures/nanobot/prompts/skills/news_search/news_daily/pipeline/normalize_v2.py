"""Article 标准化——title/source/time/entity/topic 归一化。"""

import re, hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .config import OFFICIAL_SOURCES, SOURCE_QUALITY, STOP_WORDS, TOPIC_KEYWORDS, KNOWN_ENTITIES
from .models import Article

_OFFICIAL_DOMAINS = {
    "openai.com", "anthropic.com", "deepmind.google",
    "mistral.ai", "deepseek.com", "qwen.ai", "kimi.com",
    "moonshot.cn", "x.ai", "nvidia.com", "cohere.com",
    "meta.com", "ai.meta.com",
}

_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S",
    "%b %d, %Y", "%B %d, %Y", "%b %d, %y",
]


def parse_date(raw: str) -> datetime | None:
    """多格式日期解析——ISO 8601 / RFC 2822 / 常见变体。"""
    value = (raw or "").strip()
    if not value:
        return None
    # ISO
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v).replace(tzinfo=None)
    except Exception:
        pass
    # RFC 2822
    try:
        dt = parsedate_to_datetime(value)
        if dt and dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
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
