"""Article 标准化——title/source/time/entity/topic 归一化。"""

import re, hashlib
from datetime import datetime
from .config import OFFICIAL_SOURCES, SOURCE_QUALITY, STOP_WORDS, TOPIC_KEYWORDS, KNOWN_ENTITIES
from .models import Article


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
        pub = None
        raw_date = getattr(item, "published_at", "") or ""
        if raw_date:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
                try:
                    pub = datetime.strptime(raw_date.strip(), fmt)
                    break
                except ValueError:
                    continue

        source_name = getattr(item, "source_name", "") or ""
        source_group = getattr(item, "source_group", "") or "unknown"
        title = getattr(item, "title", "") or ""
        text = f"{title} {getattr(item, 'summary', '') or ''} {getattr(item, 'detail_text', '') or ''}"

        articles.append(Article(
            id=getattr(item, "id", "") or _norm_key(title),
            title=title,
            url=getattr(item, "url", "") or "",
            source=source_name,
            source_group=source_group,
            domain=getattr(item, "domain", "") or "",
            published_at=pub,
            summary=(getattr(item, "summary", "") or "")[:400],
            content=(getattr(item, "detail_text", "") or getattr(item, "content_excerpt", "") or "")[:1200],
            title_norm=normalize_title(title),
            entity_keys=extract_entities(text),
            topic_keys=extract_topic_keys(text),
            source_quality_score=compute_source_quality(source_group),
            is_official=source_name in OFFICIAL_SOURCES,
            is_time_unknown=pub is None,
        ))
    return articles
