"""
News Search tool — KohakuTerrarium BaseTool adapter.

Uses DuckDuckGo for web search and trafilatura for high-quality article extraction.
"""

import asyncio
import logging
import re
import json
import html
from datetime import datetime
from typing import Any, List, Dict
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from core.async_bridge import run_awaitable_sync
from creatures.nanobot.prompts.skills.reply.tool import build_reply_tool_result
from . import runtime_cache as _runtime_cache
from . import search_backend as _search_backend
from .legacy_report import (
    MODEL_NAME_HINTS,
    TRUSTED_NEWS_DOMAINS,
    VALUE_ALERT_KEYWORDS,
    _build_news_brief_items,
    _build_news_conclusion,
    _build_news_layout_fallback,
    _build_value_alert,
    _coerce_layout_list,
    _coerce_layout_text,
    _combined_score,
    _domain,
    _escape_html,
    _escape_md_table_cell,
    _extract_model_hints,
    _format_news_html_report,
    _format_news_unavailable_report,
    _freshness_score,
    _merge_layout_with_fallback,
    _merge_specific_items,
    _normalize_summary_text,
    _parse_news_layout_payload,
    _source_score,
    _specificity_score,
    _truncate_text,
    _value_signal_score,
)

logger = logging.getLogger("nanobot.ai_daily")

# ── 代理感知 ──
DDGS = _search_backend.DDGS
trafilatura = _search_backend.trafilatura

def _urlopen(url, timeout=10):
    return _search_backend._urlopen(url, timeout=timeout)

def _ddgs_kwargs():
    return _search_backend._ddgs_kwargs()

NEWS_SEARCH_CACHE_TTL_SECONDS = _runtime_cache.NEWS_SEARCH_CACHE_TTL_SECONDS
NEWS_SEARCH_DDG_ENABLED = _search_backend.NEWS_SEARCH_DDG_ENABLED
_NEWS_SEARCH_CACHE = _runtime_cache._NEWS_SEARCH_CACHE
_NEWS_SEARCH_CACHE_LOCK = _runtime_cache._NEWS_SEARCH_CACHE_LOCK

JUYA_RSS_URL = _search_backend.JUYA_RSS_URL
RSS_KEYWORDS = _search_backend.RSS_KEYWORDS
# 只有日报/早报/简报才用 Juya RSS 快路径（"最新/新闻/资讯"太宽泛）
DAILY_DIGEST_KEYWORDS = _runtime_cache.DAILY_DIGEST_KEYWORDS

RSS_SOURCES = _search_backend.RSS_SOURCES

def _run_async_blocking(coro: Any) -> Any:
    return run_awaitable_sync(coro)


def _summarize_news_layout(
    query: str,
    search_results: List[Dict[str, Any]],
    extracted_contents: List[str],
    *,
    deepen: bool,
    decision_reason: str,
    value_alerts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    fallback = _build_news_layout_fallback(
        query,
        search_results,
        extracted_contents,
        deepen=deepen,
        decision_reason=decision_reason,
        value_alerts=value_alerts,
    )

    from config import NEW_API_KEY, NEW_API_BASE_URL

    if not NEW_API_KEY or not search_results:
        return fallback

    try:
        from clients.new_api_client import NewAPIClient

        source_lines = []
        for idx, (item, content) in enumerate(zip(search_results[:4], extracted_contents[:4]), start=1):
            source_lines.append(
                f"{idx}. 标题={item.get('title', '')}\n"
                f"来源={_domain(item.get('href', '')) or 'unknown'}\n"
                f"线索={_normalize_summary_text(item.get('body') or content or '', 120)}\n"
                f"正文摘录={_normalize_summary_text(content, 180)}"
            )
        alert_lines = [
            _normalize_summary_text(
                f"{alert.get('title') or '高价值条目'} | 信号分={int(alert.get('signal', 0))} | 模型={','.join(alert.get('models', [])) or '未识别'}",
                100,
            )
            for alert in value_alerts[:3]
        ]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是资讯版式整理器。请先在内部完成判断，但不要展示推理过程。"
                    "你必须优先保留具体模型名、价格/API/token/免费额度/发布时间等可核对信息。"
                    "不要输出空泛判断，不要只写一句行业趋势，不要省略具体对象。"
                    "请基于检索结果输出严格 JSON，不要输出 Markdown、HTML、解释或代码块。"
                    "JSON 只允许这 6 个键：title, subtitle, summary, highlights, alerts, closing。"
                    "title/subtitle/summary/closing 必须是字符串；highlights/alerts 必须是字符串数组。"
                    "summary 必须点名至少 1 个具体模型或公司。"
                    "highlights 必须 3 到 4 条，每条都尽量包含具体模型、价格、API、免费额度、发布时间或能力变化中的至少一项。"
                    "alerts 为 1 到 3 条，优先写成本、免费额度、可用性、上线节奏等提醒。"
                    "全部使用中文，单条简洁但信息密度高。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"查询词：{query}\n"
                    f"是否深搜：{'是' if deepen else '否'}\n"
                    f"检索决策：{decision_reason or '-'}\n"
                    f"候选资讯：\n{chr(10).join(source_lines)}\n"
                    f"价值信号：\n{chr(10).join(alert_lines) or '无明显价值信号'}\n"
                    "请输出适合日报卡片顶部区域的结构化摘要。"
                ),
            },
        ]

        async def _ask() -> Dict[str, Any]:
            client = NewAPIClient(
                api_key=NEW_API_KEY,
                base_url=NEW_API_BASE_URL,
                max_retries=1,
            )
            from core.llm_trace_context import llm_trace_scope
            with llm_trace_scope(source="ai_daily"):
                return await client.chat_completion(
                    messages=messages,
                    temperature=0.1,
                    model_tier="fast",
                )

        resp = _run_async_blocking(_ask())
        raw = (
            resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _parse_news_layout_payload(raw)
        if not parsed.get("title") or not parsed.get("summary") or not parsed.get("highlights"):
            return fallback
        return _merge_layout_with_fallback(parsed, fallback)
    except Exception as e:
        logger.warning(f"News layout summarization failed, fallback to deterministic template: {e}")
        return fallback


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
            from core.llm_trace_context import llm_trace_scope
            with llm_trace_scope(source="ai_daily"):
                return await client.chat_completion(messages=messages, temperature=0.0, model_tier="fast")

        resp = run_awaitable_sync(_ask())
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
                    session_id=session_id or "ai_daily",
                    sender_name="ai_daily_tool",
                    session_name="ai_daily",
                    role="tool",
                    content=(
                        f"[ai_daily]\nnews_meta_json={json.dumps(tags, ensure_ascii=False)}\n"
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
    return _search_backend._dedup_results(results)


def _coerce_date(year: int | str, month: int | str, day: int | str) -> str | None:
    return _runtime_cache._coerce_date(year, month, day)


def _extract_date(query: str) -> str | None:
    return _runtime_cache._extract_date(query, now=datetime.now())


def _is_daily_digest_query(query: str) -> bool:
    return _runtime_cache._is_daily_digest_query(query)


def _is_rss_first_query(query: str) -> bool:
    return _search_backend._is_rss_first_query(query)


def _should_use_juya_direct(query: str) -> bool:
    return _search_backend._should_use_juya_direct(query)


def _is_news_query(query: str) -> bool:
    return _search_backend._is_news_query(query)


def _infer_timelimit(query: str) -> str | None:
    return _search_backend._infer_timelimit(query)


def _is_urgent_news_query(query: str) -> bool:
    return _search_backend._is_urgent_news_query(query)


def _tokenize_query(query: str) -> List[str]:
    return _search_backend._tokenize_query(query)


def _extract_item_date(item: Any) -> str:
    return _search_backend._extract_item_date(item)


def _is_recent_enough(raw_date: str, hours: int = 72) -> bool:
    return _search_backend._is_recent_enough(raw_date, hours=hours)


def _normalize_search_result(item: Dict[str, Any], *, strategy: str) -> Dict[str, Any]:
    return _search_backend._normalize_search_result(item, strategy=strategy)


def _filter_stale_news_results(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    return _search_backend._filter_stale_news_results(results, query)


def _match_query(item: Dict[str, Any], query: str) -> bool:
    return _search_backend._match_query(item, query)


def _fetch_rss_source(source: Dict[str, Any], max_results: int) -> List[Dict[str, Any]]:
    return _search_backend._fetch_rss_source(source, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_multi_rss(query: str | None = None, max_results: int = 5) -> List[Dict[str, Any]]:
    return _search_backend._fetch_multi_rss(query=query, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_juya_rss(max_results: int, target_date: str | None = None) -> List[Dict[str, Any]]:
    return _search_backend._fetch_juya_rss(
        max_results=max_results,
        target_date=target_date,
        urlopen_fn=_urlopen,
    )


def _build_query_variants(query: str) -> List[str]:
    return _search_backend._build_query_variants(query)


def _news_search_cache_key(
    query: str, max_results: int,
    mode: str = "fast", user_id: str = "", session_id: str = "",
) -> tuple[Any, ...]:
    return _runtime_cache._news_search_cache_key(
        query,
        max_results,
        mode=mode,
        user_id=user_id,
        session_id=session_id,
        now=datetime.now(),
        date_extractor=_extract_date,
        daily_digest_detector=_is_daily_digest_query,
    )


def _get_cached_news_result(key: tuple[Any, ...]) -> str | None:
    return _runtime_cache._get_cached_news_result(
        key,
        ttl_seconds=NEWS_SEARCH_CACHE_TTL_SECONDS,
    )


def _store_cached_news_result(key: tuple[Any, ...], output: str) -> None:
    _runtime_cache._store_cached_news_result(key, output)


def _rerank_with_domain_diversity(results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    return _search_backend._rerank_with_domain_diversity(results, max_results=max_results)


class WebTools:
    last_error: str = ""

    @staticmethod
    def search(query: str, max_results: int = 5, deep: bool = False) -> List[Dict]:
        results, last_error = _search_backend.search(
            query,
            max_results=max_results,
            deep=deep,
            ddg_enabled=NEWS_SEARCH_DDG_ENABLED,
            ddgs_factory=DDGS,
            ddgs_kwargs_fn=_ddgs_kwargs,
            multi_rss_fetcher=_fetch_multi_rss,
            juya_fetcher=_fetch_juya_rss,
        )
        WebTools.last_error = last_error
        return results

    @staticmethod
    def extract_web_content(url: str) -> str:
        return _search_backend.extract_web_content(url, trafilatura_module=trafilatura)

# ═══════════════════════════════════════
#  V2 Pipeline: Evidence Cards + 结构化 JSON + Validator + 模板渲染
# ═══════════════════════════════════════



def _normalize_for_evidence(results: list[dict], query: str = "") -> list[dict]:
    """适配 WebTools.search() 的 href/body/date → url/snippet/published_at。"""
    out = []
    for r in results:
        url = r.get("url") or r.get("href") or ""
        if not url:
            continue
        body = r.get("body") or r.get("snippet") or r.get("description") or ""
        out.append({
            "title": r.get("title", ""),
            "url": url,
            "domain": _domain(url),
            "published_at": r.get("published_at") or r.get("date") or "",
            "snippet": body,
            "content_excerpt": body,
            "source_weight": r.get("source_weight", 0),
            "search_strategy": r.get("search_strategy", ""),
            "_query": query,
        })
    return out


def _extract_json_object(raw: str) -> str:
    """从 LLM 输出中提取 JSON object——去掉 markdown 代码块和前后缀。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
        raw = re.sub(r"```\s*$", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    return m.group(0) if m else raw


def _run_news_daily_pipeline(query: str, mode: str = "quality", limit: int = 8) -> str:
    """统一入口——quality → daily fallback。"""
    from .news_daily.tool import run_news_search_auto
    return run_news_search_auto(query, limit)

def search_and_extract_news_v2(
    query: str,
    max_results: int = 5,
    mode: str = "fast",
) -> str:
    """新 Pipeline: 搜索→去重评分→Evidence Cards→结构化JSON→validate→模板HTML。"""
    import time as _t
    t0 = _t.time()
    from .evidence import (
        build_evidence_pipeline, validate_digest, safe_digest, FALLBACK_DIGEST,
    )
    from .prompts import build_evidence_prompt, get_system_prompt
    from .render import render_html
    from core.legacy_adapter import EvolutionUtils

    # 1. 搜索
    search_results = []
    juya_attempted = False
    juya_hit = False
    juya_used = False

    rss_first = _should_use_juya_direct(query)
    target_date = _extract_date(query)
    logger.info("[news_v2] route query=%r rss_first=%s target=%s mode=%s",
                query[:60], rss_first, target_date or "latest", mode)

    if rss_first:
        juya_attempted = True
        juya_raw = _fetch_juya_rss(max_results=max_results, target_date=target_date)
        juya_hit = bool(juya_raw)
        logger.info("[news_v2] juya attempted rss_count=%d titles=%s",
                    len(juya_raw or []),
                    [x.get("title","")[:40] for x in (juya_raw or [])[:3]])
        if juya_raw:
            juya_used = True
            search_results = juya_raw

    if not search_results:
        search_results = WebTools.search(query, max_results=max_results, deep=(mode == "deep"))
    if not search_results:
        logger.info("[news_v2] no search results, fallback juya=%s/%s/%s",
                    juya_attempted, juya_hit, juya_used)
        return render_html(FALLBACK_DIGEST)

    # 2. Evidence Pipeline
    evidence_sources = _normalize_for_evidence(search_results, query)
    cards, _ = build_evidence_pipeline(evidence_sources, query, max_sources=max_results)
    if not cards:
        logger.info("[news_v2] no cards juya=%s/%s/%s raw=%d norm=%d",
                    juya_attempted, juya_hit, juya_used,
                    len(search_results), len(evidence_sources))
        return render_html(FALLBACK_DIGEST)

    # 3. LLM 结构化生成
    cards_json = []
    for c in cards:
        d = {"source_id": c.source_id, "title": c.title, "domain": c.domain,
             "published_at": c.published_at, "entities": c.entities,
             "claims": c.claims, "numbers": c.numbers,
             "related_sentences": c.related_sentences,
             "why_it_matters": c.why_it_matters, "confidence": c.confidence}
        cards_json.append(d)

    prompt = build_evidence_prompt(cards_json, mode)
    raw = _call_llm_simple(get_system_prompt(), prompt, temperature=0.1)

    # 4. JSON 解析
    digest = EvolutionUtils.json_repair(raw)
    if not isinstance(digest, dict) or digest.get("parse_error"):
        logger.warning("[news_v2] LLM output parse failed, using fallback")
        return render_html({**FALLBACK_DIGEST, "missing_info": ["LLM生成结果无法解析"]})

    # 5. Validate
    digest = safe_digest(digest, cards)
    digest["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    digest["mode"] = mode

    # 6. 渲染 HTML
    html = render_html(digest)

    ok, v_issues = validate_digest(digest, cards)
    logger.info(
        "[news_v2] done %.1fs mode=%s juya_att=%s/hit=%s/used=%s "
        "raw=%d norm=%d cards=%d html=%d validator=%s",
        _t.time()-t0, mode, juya_attempted, juya_hit, juya_used,
        len(search_results), len(evidence_sources), len(cards), len(html),
        "PASS" if ok else f"WARN:{v_issues}",
    )
    return html


def _call_llm_simple(system: str, prompt: str, temperature: float = 0.1, max_tokens: int = 2000) -> str:
    """简化 LLM 调用——用于结构化 JSON 生成。"""
    try:
        from clients.new_api_client import NewAPIClient
        from config import NEW_API_KEY, NEW_API_BASE_URL

        async def _call():
            client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
            from core.llm_trace_context import llm_trace_scope
            with llm_trace_scope(source="ai_daily"):
                resp = await client.chat_completion(
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}],
                    model_tier="fast", temperature=temperature,
                )
            if isinstance(resp, dict) and "choices" in resp:
                return resp["choices"][0]["message"]["content"]
            return ""

        return run_awaitable_sync(_call())
    except Exception as e:
        logger.warning("[news_v2] LLM call failed: %s", e)
        return ""


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

    if not coarse_results and WebTools.last_error:
        return _format_news_unavailable_report(query, WebTools.last_error)

    deepen, decision_reason = _model_should_deepen(query, coarse_results, max_results)

    if deepen:
        deep_results = WebTools.search(query, max_results=max(max_results * 2, 6), deep=True)
        merged = _dedup_results(coarse_results + deep_results)
        search_results = _rerank_with_domain_diversity(merged, max_results=max_results)
    else:
        search_results = coarse_results

    if not search_results:
        return _format_news_unavailable_report(query, "当前可用搜索结果为空，建议稍后再试，不要在本轮继续重试同类查询。")
    
    value_alerts: List[Dict[str, Any]] = []
    extracted_contents: List[str] = []
    for r in search_results:
        url = r.get('href', '')

        # 抽取前 3 个结果正文，保障可追溯信息密度
        content = WebTools.extract_web_content(url)
        extracted_contents.append(content)

        alert = _build_value_alert(r, content)
        if alert.get("triggered"):
            value_alerts.append(alert)

    layout = _summarize_news_layout(
        query=query,
        search_results=search_results,
        extracted_contents=extracted_contents,
        deepen=deepen,
        decision_reason=decision_reason,
        value_alerts=value_alerts,
    )
    report = _format_news_html_report(
        query=query,
        search_results=search_results,
        extracted_contents=extracted_contents,
        layout=layout,
        deepen=deepen,
        decision_reason=decision_reason,
        value_alerts=value_alerts,
    )
    if persist:
        _persist_news_artifacts(
            query=query,
            report_text=report,
            alerts=value_alerts,
            user_id=user_id,
            session_id=session_id,
        )
            
    return report


def _build_ai_daily_tool_result(html_result: str, query: str) -> ToolResult:
    result = build_reply_tool_result(html_result)
    try:
        from core.ai_daily_ingest import best_effort_ingest_ai_daily_result

        result.metadata["ai_daily_ingest"] = best_effort_ingest_ai_daily_result(
            html_result,
            query=query,
        )
    except Exception as exc:
        logger.warning("[ai_daily] ingest metadata failed: %s", exc)
        result.metadata["ai_daily_ingest"] = {"created": 0, "updated": 0, "warnings": [str(exc)]}
    return result


def _render_ai_daily_fallback(title: str, verdict: str, missing_info: list[str]) -> str:
    try:
        from .evidence import FALLBACK_DIGEST
        from .render import render_html

        return render_html({
            **FALLBACK_DIGEST,
            "title": title,
            "verdict": verdict,
            "missing_info": missing_info,
        })
    except Exception as exc:
        logger.warning("[ai_daily] fallback render failed: %s", exc)
        details = "".join(f"<li>{html.escape(str(item))}</li>" for item in missing_info[:3])
        return (
            "<article class=\"news-brief news-brief-unavailable\">"
            f"<h1>{html.escape(title)}</h1>"
            f"<p>{html.escape(verdict)}</p>"
            f"<ul>{details}</ul>"
            "</article>"
        )


class AiDailyTool(BaseTool):
    """Generate an AI/tech daily digest from curated sources."""

    @property
    def tool_name(self) -> str:
        return "ai_daily"

    @property
    def description(self) -> str:
        return "聚合 AI/科技领域可信来源，生成可直接发送的 AI 日报或资讯简报 HTML。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "日报主题或自然语言请求；今天/最新类请求必须基于 runtime_context.current_time，不要自行编造年份。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "候选新闻数量（默认 8）；日报/最新资讯类请求会至少使用 8 条候选。",
                    "default": 8,
                },
                "freshness": {
                    "type": "string",
                    "description": "时效范围：today/latest/week/custom。今天、最新、日报、早报优先使用 today 或 latest。",
                    "enum": ["today", "latest", "week", "custom"],
                    "default": "latest",
                },
                "target_date": {
                    "type": "string",
                    "description": "目标日期，YYYY-MM-DD；仅用户明确指定日期时填写。",
                },
                "no_cache": {"type": "boolean", "description": "跳过缓存强制重新检索", "default": False},
                "refresh": {"type": "boolean", "description": "强制刷新", "default": False},
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not query.strip():
            return ToolResult(error="Missing 'query' argument")
        max_results = int(args.get("max_results", 8) or 8)
        freshness = str(args.get("freshness") or "").strip().lower()
        # 日报/新闻类请求强制至少8条候选
        if freshness in {"today", "latest"} or any(
            k in query for k in ("日报", "早报", "简报", "每日", "今日", "今天", "新闻", "资讯", "AI 新闻", "AI新闻", "最新新闻", "最新资讯")
        ):
            max_results = max(max_results, 8)
        no_cache = bool(args.get("no_cache") or args.get("refresh"))

        user_id = str(args.get("user_id") or kwargs.get("user_id") or "")
        session_id = str(args.get("session_id") or kwargs.get("session_id") or "")
        metadata = kwargs.get("metadata") or {}
        if not user_id and isinstance(metadata, dict):
            user_id = str(metadata.get("user_id") or "")
        if not session_id and isinstance(metadata, dict):
            session_id = str(metadata.get("session_id") or "")

        logger.info("[ai_daily] query=%r max=%d no_cache=%s",
                    query[:80], max_results, no_cache)

        cache_key = _news_search_cache_key(query, max_results, mode="quality",
                                           user_id=user_id, session_id=session_id)
        if not no_cache:
            cached = _get_cached_news_result(cache_key)
            if cached is not None:
                logger.info("[ai_daily] cache HIT")
                return _build_ai_daily_tool_result(cached, query)

        # KT runs tools concurrently; run blocking code in thread
        result = await asyncio.to_thread(
            _run_news_daily_pipeline, query, "quality", max_results,
        )
        # 强制HTML输出——永不为空/不裸文本
        if not result or not str(result).strip():
            logger.error("[ai_daily] empty output query=%r", query)
            result = _render_ai_daily_fallback(
                "暂无可用资讯",
                "本轮没有生成有效输出，已触发兜底。",
                ["工具返回为空"],
            )
        elif "<html" not in str(result).lower() and "<article" not in str(result).lower():
            logger.warning("[ai_daily] non-html output query=%r len=%d", query, len(str(result)))
            result = _render_ai_daily_fallback(
                "资讯结果不完整",
                "本轮生成结果非标准HTML，已转换兜底。",
                [str(result)[:200]],
            )
        _store_cached_news_result(cache_key, result)
        return _build_ai_daily_tool_result(result, query)
