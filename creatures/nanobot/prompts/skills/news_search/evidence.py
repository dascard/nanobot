"""Evidence Cards —— 搜索结果→相关句子→事实提取→结构化卡片。

Pipeline:
  raw_search → dedup & score → content extract → related sentences → evidence cards
"""

import hashlib
import json
import logging
import re
import time as _time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("nanobot.news_search.evidence")

# ── 数据结构 ──

@dataclass
class SourceItem:
    source_id: int
    title: str
    domain: str
    url: str
    published_at: str = ""
    snippet: str = ""
    content_excerpt: str = ""
    full_content: str = ""
    credibility_score: float = 0.5
    extraction_failed: bool = False


@dataclass
class EvidenceCard:
    source_id: int
    title: str
    domain: str
    url: str
    published_at: str
    entities: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    related_sentences: list[str] = field(default_factory=list)
    status: dict = field(default_factory=dict)
    why_it_matters: str = ""
    confidence: str = "medium"


@dataclass
class NewsDigest:
    """最终结构化输出——LLM 填充，模板渲染。"""
    title: str = ""
    subtitle: str = ""
    verdict: str = ""
    generated_at: str = ""
    mode: str = "fast"
    top_story: dict | None = None
    highlights: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    closing: str = ""


# ── 来源评分 ──

TRUSTED_DOMAINS = {
    "openai.com": 0.95, "anthropic.com": 0.90, "huggingface.co": 0.85,
    "reuters.com": 0.95, "techcrunch.com": 0.85, "theverge.com": 0.80,
    "arstechnica.com": 0.85, "venturebeat.com": 0.80, "github.com": 0.75,
    "arxiv.org": 0.85, "blog.google": 0.80, "ai.meta.com": 0.80,
    "deepmind.google": 0.90, "mistral.ai": 0.85, "cohere.com": 0.80,
}


def score_sources(sources: list[dict]) -> list[dict]:
    """去重 + 评分 + 排序。"""
    seen_urls = set()
    scored = []
    for s in sources:
        url = (s.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        domain = urlparse(url).netloc.lower().lstrip("www.")
        # 可信度评分
        cred = 0.5
        for trusted, score in TRUSTED_DOMAINS.items():
            if trusted in domain:
                cred = score
                break

        # 时效性评分
        freshness = 0.5
        pub = s.get("published_at") or s.get("date") or ""
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00")[:19])
                days = (datetime.now() - dt.replace(tzinfo=None)).days
                freshness = max(0.1, 1.0 - days / 7.0)
            except Exception:
                pass

        s["domain"] = domain
        s["credibility_score"] = round(cred * 0.6 + freshness * 0.4, 2)
        scored.append(s)

    scored.sort(key=lambda x: x["credibility_score"], reverse=True)
    # 重新编号
    for i, s in enumerate(scored):
        s["source_id"] = i + 1
    return scored


# ── 相关句子提取 ──

RELEVANCE_KEYWORDS = re.compile(
    r"(模型|发布|开源|API|价格|token|免费|付费|"
    r"上下文|context|benchmark|评测|参数|训练|微调|"
    r"GPT|Claude|Gemini|DeepSeek|Qwen|Llama|Mistral|"
    r"可用|地区|限制|访问|注册|权限|"
    r"\$\d+|million|billion|MB|GB|TB|"
    r"embedding|vision|multimodal|code|reasoning)",
    re.IGNORECASE
)

ENTITY_PATTERNS = {
    "model_name": re.compile(
        r"(GPT-?[\d.]+[a-z]?|Claude\s*[\d.]+|Gemini\s*[\d.]+|"
        r"DeepSeek[-\s]?[\w]+|Qwen[\d.\s-]?[\w]*|Llama\s*[\d.]+|"
        r"Mistral[-\s]?[\w]+|Gemma[-\s]?[\w]+|Phi[-\s]?[\d.]+|"
        r"Command\s*R\+?|DBRX|Falcon[-\s]?[\w]+|Yi[-\s]?[\w]+)",
        re.IGNORECASE
    ),
    "price": re.compile(
        r"(\$[\d.]+)\s*(/|per|/1[Kk]|/1[Mm]|每)",
        re.IGNORECASE
    ),
    "number": re.compile(
        r"(\d+[KkMmBb]?\s*(tokens?|context|参数|parameters|窗口|window|"
        r"上下文|tokens|billion|million|万|亿))",
        re.IGNORECASE
    ),
}

NON_CONTENT_SENTENCES = re.compile(
    r"^(登录|注册|订阅|广告|推广|相关文章|阅读更多|"
    r"分享到|Cookie|Privacy|Terms|©|All Rights)",
    re.IGNORECASE
)


def extract_related_sentences(text: str, query: str = "", max_chars: int = 900) -> str:
    """提取与AI/科技相关的句子，不粗暴截断。"""
    if not text or len(text) < 50:
        return text

    # 分句
    sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
    related = []
    query_lower = query.lower()
    query_keywords = set(query_lower.split()) if query_lower else set()

    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 10:
            continue
        if NON_CONTENT_SENTENCES.match(sent):
            continue

        score = 0
        # 关键词匹配
        if RELEVANCE_KEYWORDS.search(sent):
            score += 2
        # 查询词匹配
        if query_keywords:
            sent_lower = sent.lower()
            hits = sum(1 for kw in query_keywords if kw in sent_lower)
            score += hits * 3
        # 数字加分
        if re.search(r"\d+", sent):
            score += 1
        # 实体加分
        for _, pat in ENTITY_PATTERNS.items():
            if pat.search(sent):
                score += 1

        if score >= 2:
            related.append((score, sent))

    # 按相关度排序，取 top
    related.sort(key=lambda x: x[0], reverse=True)
    result = []
    total_chars = 0
    for _, sent in related:
        if total_chars + len(sent) > max_chars:
            break
        result.append(sent)
        total_chars += len(sent)

    return "\n".join(result) if result else text[:max_chars]


# ── Evidence Card 提取 ──

def build_evidence_card(source: dict) -> EvidenceCard:
    """从单个来源提取 Evidence Card。"""
    text = source.get("content_excerpt") or source.get("snippet") or ""
    domain = source.get("domain", "")
    title = source.get("title", "")

    entities = []
    for _, pat in ENTITY_PATTERNS.items():
        entities.extend(pat.findall(title + " " + text))

    claims = _extract_claims(text)
    numbers = _extract_numbers(text)
    related = extract_related_sentences(text, max_chars=600)

    status = {
        "official": "official" in title.lower() or domain in TRUSTED_DOMAINS,
        "confirmed": source.get("credibility_score", 0.5) > 0.6,
        "freshness": "recent" if source.get("credibility_score", 0) > 0.5 else "stale",
        "has_numbers": len(numbers) > 0,
    }

    why = _why_matters(text, entities, numbers)

    return EvidenceCard(
        source_id=source.get("source_id", 0),
        title=title,
        domain=domain,
        url=source.get("url", ""),
        published_at=source.get("published_at") or source.get("date") or "",
        entities=list(set(entities))[:10],
        claims=claims[:5],
        numbers=numbers[:8],
        related_sentences=related.split("\n") if related else [],
        status=status,
        why_it_matters=why,
        confidence="high" if status["official"] else "medium",
    )


def _extract_claims(text: str) -> list[str]:
    """提取断言句（包含发布/支持/宣布/开源等动词的句子）。"""
    claim_verbs = re.compile(
        r"(发布|推出|宣布|开源|支持|上线|开放|降价|免费|"
        r"超越|超过|达到|实现|launch|release|announce|support|open.source)",
        re.IGNORECASE
    )
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    claims = []
    for s in sentences:
        s = s.strip()
        if len(s) > 15 and claim_verbs.search(s) and len(claims) < 5:
            claims.append(s[:200])
    return claims


def _extract_numbers(text: str) -> list[str]:
    """提取有意义的数字信息。"""
    patterns = [
        r"\$\s*[\d.]+/?\s*(/|per|每)?\s*(1[KkMm]|token|million|万)?[^\w]?",
        r"\d+[Kk]\s*(tokens?|上下文|context)",
        r"\d+\s*(billion|million|万|亿)\s*(参数|parameters)?",
        r"\d{4}-\d{2}-\d{2}",
    ]
    results = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            s = m.group().strip()
            if len(s) < 30:
                results.add(s)
    return list(results)[:8]


def _why_matters(text: str, entities: list, numbers: list) -> str:
    """生成简短的重要性说明。"""
    reasons = []
    if "免费" in text or "free" in text.lower() or "开源" in text:
        reasons.append("涉及免费/开源")
    if any("$" in n for n in numbers):
        reasons.append("包含定价信息")
    if "API" in text or "api" in text.lower():
        reasons.append("涉及API可用性")
    return "、".join(reasons[:3]) if reasons else "待进一步分析"


# ── Pipeline ──

def build_evidence_pipeline(
    search_results: list[dict],
    query: str = "",
    max_sources: int = 6,
) -> tuple[list[EvidenceCard], list[dict]]:
    """完整 Pipeline: 搜索结果 → 去重评分 → 正文抽取 → Evidence Cards。"""
    t0 = _time.time()

    # 1. 去重 + 评分
    scored = score_sources(search_results)
    logger.info(f"[evidence] scored {len(scored)} sources from {len(search_results)} raw results")

    # 2. 取 top N
    top = scored[:max_sources]

    # 3. 相关句子提取
    for s in top:
        excerpt = s.get("content_excerpt") or s.get("snippet") or ""
        if excerpt:
            s["related_text"] = extract_related_sentences(excerpt, query)

    # 4. 构建 Evidence Cards
    cards = [build_evidence_card(s) for s in top]

    logger.info(f"[evidence] {len(cards)} cards built in {_time.time()-t0:.1f}s")
    return cards, top


# ── Validator ──

def validate_digest(digest: dict, cards: list[EvidenceCard]) -> tuple[bool, list[str]]:
    """校验 NewsDigest——返回 (pass, issues)。仅 fatal 导致 pass=False。"""
    fatal = []
    warnings = []
    valid_source_ids = {c.source_id for c in cards}

    # ── fatal: 字段缺失 / source_ids 无效 ──
    if not digest.get("title"):
        fatal.append("缺少 title")
    ts = digest.get("top_story")
    if not ts:
        fatal.append("缺少 top_story")
    else:
        ts_ids = set(ts.get("source_ids", []))
        if not ts_ids:
            fatal.append("top_story 缺少 source_ids")
        elif ts_ids - valid_source_ids:
            fatal.append(f"top_story source_ids 无效: {ts_ids - valid_source_ids}")

    for i, h in enumerate(digest.get("highlights", [])):
        h_ids = set(h.get("source_ids", []))
        if h_ids and h_ids - valid_source_ids:
            fatal.append(f"highlight[{i}] source_ids 无效: {h_ids - valid_source_ids}")
        if not h_ids:
            warnings.append(f"highlight[{i}] 缺少 source_ids")
        if len(h.get("text", "")) < 10:
            warnings.append(f"highlight[{i}] 内容过短")

    # ── warning: 空泛 / 字段缺失 / 实体越权 ──
    if not digest.get("verdict"):
        warnings.append("缺少 verdict")
    vague = re.compile(r"(值得关注|行业持续|不断进步|日益增长|越来越|趋势)")
    for field in ["verdict", "closing"]:
        text = digest.get(field, "")
        if vague.search(text):
            warnings.append(f"{field} 空泛: {vague.search(text).group()}")

    # 中文友好实体检测 (warning only)
    known_entity_re = re.compile(
        r"(GPT-?[\d.]+[a-z]?|Claude\s*[\d.]+|Gemini\s*[\d.]+|DeepSeek[-\s]?\w+|"
        r"Qwen[\w.\- ]+|Llama\s*[\d.]+|Mistral[-\s]?\w+|Gemma[-\s]?\w+|"
        r"Phi[-\s]?[\d.]+|Command\s*R\+?|DBRX|Falcon[-\s]?\w+|Yi[-\s]?\w+|"
        r"OpenAI|Anthropic|Google|Meta|Microsoft|NVIDIA|Cohere|Stability|"
        r"阿里|通义|腾讯|混元|字节|豆包|智谱|GLM|月之暗面|Kimi|MiniMax|阶跃|零一万物)",
        re.IGNORECASE)
    all_card_entities = set()
    for c in cards:
        all_card_entities.update(e.lower().strip() for e in c.entities)
        all_card_entities.update(c.domain.lower().split(".")[0:1])
        for claim in c.claims:
            all_card_entities.update(e.lower().strip() for e in known_entity_re.findall(claim))
    digest_text = json.dumps(digest, ensure_ascii=False)
    mentioned = {m.group(0).lower().strip() for m in known_entity_re.finditer(digest_text)}
    extra = mentioned - all_card_entities
    if extra:
        warnings.append(f"evidence卡外实体(warning): {sorted(extra)[:5]}")

    issues = fatal + warnings
    return len(fatal) == 0, issues


FALLBACK_DIGEST = {
    "title": "本期暂无高可信度AI资讯",
    "subtitle": "",
    "verdict": "本轮未找到足够可信的新消息，已找到的来源中缺乏可确认的发布/定价/开源等关键信息。",
    "top_story": None,
    "highlights": [],
    "watchlist": [],
    "missing_info": ["建议稍后重试或指定具体来源"],
    "closing": "下期日报将自动重新检索。",
}


def safe_digest(digest: dict, cards: list[EvidenceCard]) -> dict:
    """valid 通过返回 digest，不通过返回 fallback。"""
    ok, issues = validate_digest(digest, cards)
    if ok:
        logger.info(f"[validator] PASS")
        return digest
    logger.warning(f"[validator] FAIL: {issues}")
    return {**FALLBACK_DIGEST, "missing_info": issues}
