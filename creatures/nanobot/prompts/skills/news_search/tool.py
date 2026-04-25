"""
News Search tool — KohakuTerrarium BaseTool adapter.

Uses DuckDuckGo for web search and trafilatura for high-quality article extraction.
"""

import logging
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Dict
from urllib.parse import urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from duckduckgo_search import DDGS
import trafilatura
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

logger = logging.getLogger("nanobot.news_search")

JUYA_RSS_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"
RSS_KEYWORDS = {
    "juya", "ai daily", "morning briefing", "日报", "早报", "每日", "快讯", "newsletter"
}

RSS_SOURCES = [
    {
        "name": "juya_ai_daily",
        "url": "https://imjuya.github.io/juya-ai-daily/rss.xml",
        "weight": 3,
    },
    {
        "name": "reddit_localllama",
        "url": "https://www.reddit.com/r/LocalLLaMA/.rss",
        "weight": 1,
    },
]

TRUSTED_NEWS_DOMAINS = {
    "openai.com", "anthropic.com", "googleblog.com", "deepmind.google", "microsoft.com",
    "aws.amazon.com", "ai.meta.com", "huggingface.co", "arxiv.org", "nature.com",
    "techcrunch.com", "theverge.com", "venturebeat.com", "wired.com", "reuters.com",
    "bloomberg.com", "ft.com", "wsj.com", "bbc.com",
}

VALUE_ALERT_KEYWORDS = {
    "free", "free tier", "open source", "open-source", "cheap", "low cost", "cost-effective",
    "trial", "credit", "coupon", "discount", "benchmark", "price", "pricing", "token", "api",
    "白嫖", "免费", "开源", "低价", "便宜", "优惠", "试用", "赠金", "充值", "调用价格", "token价格",
}

MODEL_NAME_HINTS = {
    "qwen", "deepseek", "kimi", "gpt", "claude", "gemini", "llama", "mistral", "hunyuan", "glm",
    "通义", "豆包", "混元", "智谱", "阶跃", "minimax",
}


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


def _source_score(item: Dict[str, Any]) -> int:
    url = item.get("href", "") or ""
    title = item.get("title", "") or ""
    body = item.get("body", "") or ""
    domain = _domain(url)

    score = 0
    if domain in TRUSTED_NEWS_DOMAINS:
        score += 4
    if domain.endswith(".edu") or domain.endswith(".gov"):
        score += 3
    if len(title) > 20:
        score += 1
    if len(body) > 60:
        score += 1
    if url.startswith("https://"):
        score += 1
    score += int(item.get("source_weight") or 0)
    return score


def _freshness_score(item: Dict[str, Any]) -> int:
    raw_date = (item.get("date") or "").strip()
    if not raw_date:
        return 0

    dt = None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw_date, fmt)
            break
        except Exception:
            continue
    if dt is None:
        return 0

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - dt
    if delta <= timedelta(days=1):
        return 4
    if delta <= timedelta(days=3):
        return 3
    if delta <= timedelta(days=7):
        return 2
    if delta <= timedelta(days=30):
        return 1
    return 0


def _combined_score(item: Dict[str, Any]) -> int:
    return _source_score(item) + _freshness_score(item)


def _value_signal_score(text: str) -> int:
    t = (text or "").lower()
    score = 0
    for kw in VALUE_ALERT_KEYWORDS:
        if kw in t:
            score += 1
    return score


def _extract_model_hints(text: str) -> List[str]:
    t = (text or "").lower()
    found = []
    for name in MODEL_NAME_HINTS:
        if name in t:
            found.append(name)
    return sorted(set(found))


def _build_value_alert(item: Dict[str, Any], content: str) -> Dict[str, Any]:
    title = item.get("title", "")
    body = item.get("body", "")
    url = item.get("href", "")
    merged = f"{title}\n{body}\n{content[:3000]}"
    signal = _value_signal_score(merged)
    models = _extract_model_hints(merged)
    if signal < 2:
        return {"triggered": False}
    return {
        "triggered": True,
        "signal": signal,
        "url": url,
        "title": title,
        "models": models,
    }


def _heuristic_should_deepen(query: str, coarse_results: List[Dict[str, Any]], max_results: int) -> bool:
    q = (query or "").lower()
    deepen_markers = ["深入", "全面", "对比", "价格", "白嫖", "便宜", "free", "cheap", "pricing"]
    if any(m in q for m in deepen_markers):
        return True

    if len(coarse_results) < max_results:
        return True

    if not coarse_results:
        return True

    avg_score = sum(_combined_score(x) for x in coarse_results) / max(len(coarse_results), 1)
    return avg_score < 5


def _model_should_deepen(query: str, coarse_results: List[Dict[str, Any]], max_results: int) -> tuple[bool, str]:
    from config import NEW_API_KEY

    if not NEW_API_KEY:
        decision = _heuristic_should_deepen(query, coarse_results, max_results)
        return decision, "heuristic_no_model_key"

    try:
        from clients.new_api_client import NewAPIClient

        brief_lines = []
        for i, item in enumerate(coarse_results[:6], 1):
            brief_lines.append(
                f"{i}. title={item.get('title', '')} | url={item.get('href', '')} | score={_combined_score(item)}"
            )
        brief = "\n".join(brief_lines) or "(no results)"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a retrieval planner. Decide whether to run deeper web search after a coarse pass. "
                    "Return strict JSON with keys deepen (bool) and reason (string)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"query: {query}\n"
                    f"target_result_count: {max_results}\n"
                    f"coarse_results:\n{brief}\n"
                    "If coarse results are sparse/low confidence/not diverse or query implies pricing/free model opportunities, choose deepen=true."
                ),
            },
        ]

        async def _ask() -> Dict[str, Any]:
            client = NewAPIClient(api_key=NEW_API_KEY, max_retries=1)
            return await client.chat_completion(messages=messages, temperature=0.0, model_tier="fast")

        resp = __import__("asyncio").run(_ask())
        content = (
            resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        parsed: Dict[str, Any] = {}
        try:
            parsed = json.loads(content)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                parsed = json.loads(m.group(0))

        if isinstance(parsed, dict) and "deepen" in parsed:
            return bool(parsed.get("deepen")), f"model:{parsed.get('reason', '')}"
    except Exception as e:
        logger.warning(f"Model deepen decision failed, fallback to heuristic: {e}")

    decision = _heuristic_should_deepen(query, coarse_results, max_results)
    return decision, "heuristic_fallback"


def _persist_news_artifacts(
    query: str,
    report_text: str,
    alerts: List[Dict[str, Any]],
    *,
    user_id: str = "",
    session_id: str = "",
) -> None:
    tags = {
        "model_hints": _extract_model_hints(report_text),
        "value_signal_score": _value_signal_score(report_text),
        "alerts_count": len(alerts),
        "query": query,
    }

    source_domains = sorted(set(re.findall(r"https?://([^/\s]+)", report_text, flags=re.IGNORECASE)))
    if source_domains:
        tags["source_domains"] = source_domains[:20]

    # Persist to relational DB for audit/history.
    # Keep storage consistent with KT runtime and existing SQL log pipeline.
    try:
        from core.database import SessionLocal, ChatLog

        db = SessionLocal()
        try:
            db.add(
                ChatLog(
                    user_id=user_id or "news_tool",
                    session_id=session_id or "news_search",
                    sender_name="news_search_tool",
                    session_name="news_search",
                    role="tool",
                    content=(
                        f"[news_search]\nnews_meta_json={json.dumps(tags, ensure_ascii=False)}\n"
                        f"query={query}\n"
                        f"alerts={len(alerts)}\n\n{report_text[:6000]}"
                    ),
                    processed=1,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Persist to DB skipped/failed: {e}")


def _dedup_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    cleaned = []
    for r in results:
        url = (r.get("href") or "").strip()
        title = (r.get("title") or "").strip().lower()
        key = (url, title)
        if not url or key in seen:
            continue
        seen.add(key)
        cleaned.append(r)
    return cleaned


def _extract_date(query: str) -> str | None:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", query)
    if not match:
        return None
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
        return match.group(1)
    except ValueError:
        return None


def _is_rss_first_query(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in RSS_KEYWORDS)


def _is_news_query(query: str) -> bool:
    q = (query or "").lower()
    markers = ["news", "daily", "brief", "briefing", "最新", "快讯", "早报", "资讯", "日报", "发布"]
    return any(m in q for m in markers)


def _infer_timelimit(query: str) -> str | None:
    q = (query or "").lower()
    if any(k in q for k in ["today", "今日", "今天", "latest", "最新", "刚刚", "24h", "24小时"]):
        return "d"
    if any(k in q for k in ["this week", "本周", "一周", "7天", "7 days"]):
        return "w"
    if any(k in q for k in ["this month", "本月", "30天", "30 days"]):
        return "m"
    return None


def _tokenize_query(query: str) -> List[str]:
    q = (query or "").lower().strip()
    if not q:
        return []
    tokens = re.findall(r"[a-z0-9\-\+\.]{2,}|[\u4e00-\u9fff]{2,}", q)
    blacklist = {"news", "daily", "brief", "briefing", "最新", "快讯", "早报", "资讯", "日报", "发布"}
    return [t for t in tokens if t not in blacklist]


def _extract_item_date(item: ET.Element) -> str:
    pub = (item.findtext("pubDate") or item.findtext("published") or "").strip()
    if not pub:
        return ""
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return ""


def _is_recent_enough(raw_date: str, hours: int = 72) -> bool:
    if not raw_date:
        return True
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw_date, fmt)
            break
        except Exception:
            continue
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=hours)


def _match_query(item: Dict[str, Any], query: str) -> bool:
    tokens = _tokenize_query(query)
    if not tokens:
        return True
    text = f"{item.get('title', '')} {item.get('body', '')}".lower()
    return any(t in text for t in tokens)


def _fetch_rss_source(source: Dict[str, Any], max_results: int) -> List[Dict[str, Any]]:
    try:
        with urlopen(source["url"], timeout=6) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")
        parsed: List[Dict[str, Any]] = []

        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            content_encoded = ""

            for child in list(item):
                if child.tag.endswith("content") or child.tag.endswith("encoded"):
                    content_encoded = (child.text or "").strip()
                    if content_encoded:
                        break

            if not link:
                continue

            parsed.append(
                {
                    "title": title or source["name"],
                    "href": link,
                    "body": (description or content_encoded)[:800],
                    "date": _extract_item_date(item),
                    "source_weight": source.get("weight", 0),
                    "search_strategy": f"rss:{source['name']}",
                }
            )

        return parsed[:max_results]
    except Exception as e:
        logger.warning(f"RSS source fetch failed: {source.get('name')} {e}")
        return []


def _fetch_multi_rss(query: str, max_results: int) -> List[Dict[str, Any]]:
    target_date = _extract_date(query)
    all_items: List[Dict[str, Any]] = []
    for source in RSS_SOURCES:
        all_items.extend(_fetch_rss_source(source, max_results=max_results * 2))

    filtered: List[Dict[str, Any]] = []
    for item in all_items:
        title = (item.get("title") or "").strip()
        if target_date and target_date not in title:
            continue
        if not _is_recent_enough(item.get("date", ""), hours=72):
            continue
        if not _match_query(item, query):
            continue
        filtered.append(item)

    filtered = _dedup_results(filtered)
    filtered = _rerank_with_domain_diversity(filtered, max_results=max_results)
    return filtered


def _fetch_juya_rss(max_results: int, target_date: str | None = None) -> List[Dict[str, Any]]:
    try:
        with urlopen(JUYA_RSS_URL, timeout=6) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")
        parsed: List[Dict[str, Any]] = []

        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            content_encoded = ""

            for child in list(item):
                if child.tag.endswith("content") or child.tag.endswith("encoded"):
                    content_encoded = (child.text or "").strip()
                    if content_encoded:
                        break

            if not link:
                continue

            if target_date and target_date not in title:
                continue

            parsed.append(
                {
                    "title": title or "Juya AI Daily",
                    "href": link,
                    "body": (description or content_encoded)[:800],
                    "source_weight": 3,
                    "search_strategy": "juya_rss",
                }
            )

        return parsed[:max_results]
    except Exception as e:
        logger.warning(f"Juya RSS fetch failed: {e}")
        return []


def _build_query_variants(query: str) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    if "news" not in q.lower() and "资讯" not in q and "日报" not in q:
        variants.append(f"{q} AI news")
    variants.append(f"{q} model pricing OR free tier OR api")
    variants.append(f"{q} 开源 OR 免费 OR 低价 模型")
    variants.append(f"{q} site:reuters.com OR site:techcrunch.com OR site:theverge.com")
    return variants


def _rerank_with_domain_diversity(results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    if not results:
        return []

    ranked = sorted(results, key=_combined_score, reverse=True)
    picked: List[Dict[str, Any]] = []
    used_domains: set[str] = set()

    # First pass: prioritize source diversity.
    for item in ranked:
        d = _domain(item.get("href", ""))
        if d and d in used_domains:
            continue
        picked.append(item)
        if d:
            used_domains.add(d)
        if len(picked) >= max_results:
            return picked

    # Second pass: fill remaining slots by score.
    for item in ranked:
        if item in picked:
            continue
        picked.append(item)
        if len(picked) >= max_results:
            break

    return picked

class WebTools:
    @staticmethod
    def search(query: str, max_results: int = 5, deep: bool = False) -> List[Dict]:
        try:
            # Strategy 1: structured RSS sources first.
            rss_limit = max_results * (2 if deep else 1)
            rss_agg = _fetch_multi_rss(query=query, max_results=rss_limit)
            if rss_agg:
                return rss_agg[:max_results]

            # Strategy 1b: Juya direct date match for briefing style queries.
            target_date = _extract_date(query)
            if _is_rss_first_query(query):
                rss_results = _fetch_juya_rss(max_results=max_results, target_date=target_date)
                if rss_results:
                    return rss_results

            # Strategy 2: multi-query web retrieval with dedup/ranking fallback.
            results = []
            timelimit = _infer_timelimit(query)
            if timelimit is None and _is_news_query(query):
                timelimit = "w"
            per_variant = max_results * (4 if deep else 2)
            variants = _build_query_variants(query)
            if deep:
                variants.extend(
                    [
                        f"{query} official blog release notes",
                        f"{query} site:openai.com OR site:anthropic.com OR site:ai.googleblog.com",
                        f"{query} benchmark price token cost",
                    ]
                )
            with DDGS() as ddgs:
                for variant in variants:
                    for r in ddgs.text(
                        variant,
                        region='wt-wt',
                        safesearch='moderate',
                        timelimit=timelimit,
                        max_results=per_variant,
                    ):
                        r = dict(r)
                        r.setdefault("search_strategy", "web_ddg_deep" if deep else "web_ddg_multi_variant")
                        results.append(r)
            results = _dedup_results(results)
            results = _rerank_with_domain_diversity(results, max_results=max_results)
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    @staticmethod
    def extract_web_content(url: str) -> str:
        try:
            downloaded = trafilatura.fetch_url(url, timeout=5)
            if downloaded:
                result = trafilatura.extract(
                    downloaded,
                    include_comments=False,
                    include_tables=False,
                    favor_precision=True,
                    with_metadata=False,
                )
                return result or "Failed to extract content"
            return "Failed to download url"
        except Exception as e:
            return f"Error extracting {url}: {e}"

def search_and_extract_news(
    query: str,
    max_results: int = 3,
    *,
    persist: bool = False,
    user_id: str = "",
    session_id: str = "",
) -> str:
    """Combine search and content extraction"""
    coarse_results = WebTools.search(query, max_results, deep=False)
    deepen, decision_reason = _model_should_deepen(query, coarse_results, max_results)

    if deepen:
        deep_results = WebTools.search(query, max_results=max(max_results * 2, 6), deep=True)
        merged = _dedup_results(coarse_results + deep_results)
        search_results = _rerank_with_domain_diversity(merged, max_results=max_results)
    else:
        search_results = coarse_results

    if not search_results:
        return "No results found."
    
    final_text = []
    final_text.append("News intelligence report (ranked by source quality):")
    final_text.append(f"DeepSearchDecision: {'enabled' if deepen else 'skipped'} | Reason: {decision_reason}")
    value_alerts: List[Dict[str, Any]] = []
    for idx, r in enumerate(search_results):
        url = r.get('href', '')
        domain = _domain(url)
        score = _source_score(r)
        final_text.append(f"\n--- Result {idx+1} ---")
        final_text.append(f"Title: {r.get('title')}")
        final_text.append(f"URL: {url}")
        final_text.append(f"Source: {domain or 'unknown'} | QualityScore: {score}")
        final_text.append(f"SearchStrategy: {r.get('search_strategy', 'web_ddg')}")
        final_text.append(f"Snippet: {r.get('body')}")
        
        # 抽取前 3 个结果正文，保障可追溯信息密度
        content = WebTools.extract_web_content(url)
        if content.startswith("Error extracting") or content.startswith("Failed"):
            final_text.append(f"Content Summary: {content}\n")
        else:
            final_text.append(f"Content Summary:\n{content[:1800]}...\n")

        alert = _build_value_alert(r, content)
        if alert.get("triggered"):
            value_alerts.append(alert)

    if value_alerts:
        final_text.append("\n=== High Value Model Alerts ===")
        value_alerts.sort(key=lambda x: int(x.get("signal", 0)), reverse=True)
        for i, alert in enumerate(value_alerts, 1):
            models = ", ".join(alert.get("models", [])) or "unspecified"
            final_text.append(f"[{i}] {alert.get('title')}")
            final_text.append(f"URL: {alert.get('url')}")
            final_text.append(f"SignalScore: {alert.get('signal')} | ModelHints: {models}")
    else:
        final_text.append("\n=== High Value Model Alerts ===")
        final_text.append("No strong cost-performance/free-model signals found in current results.")

    report = "\n".join(final_text)
    if persist:
        _persist_news_artifacts(
            query=query,
            report_text=report,
            alerts=value_alerts,
            user_id=user_id,
            session_id=session_id,
        )
            
    return report

class NewsSearchTool(BaseTool):
    """Search for AI/tech news and extract article summaries."""

    @property
    def tool_name(self) -> str:
        return "news_search"

    @property
    def description(self) -> str:
        return "搜索 AI/科技领域最新资讯并提取正文摘要"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或自然语言查询",
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回的最大结果数量（默认 3）",
                    "default": 3,
                },
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not query.strip():
            return ToolResult(error="Missing 'query' argument")
        max_results = int(args.get("max_results", 3) or 3)

        # Best-effort context extraction for persistence tags.
        user_id = str(args.get("user_id") or kwargs.get("user_id") or "")
        session_id = str(args.get("session_id") or kwargs.get("session_id") or "")
        metadata = kwargs.get("metadata") or {}
        if not user_id and isinstance(metadata, dict):
            user_id = str(metadata.get("user_id") or "")
        if not session_id and isinstance(metadata, dict):
            session_id = str(metadata.get("session_id") or "")
        
        # KT runs tools concurrently; run blocking code in thread
        import asyncio
        result = await asyncio.to_thread(
            search_and_extract_news,
            query,
            max_results,
            persist=True,
            user_id=user_id,
            session_id=session_id,
        )
        return ToolResult(output=result, exit_code=0)

