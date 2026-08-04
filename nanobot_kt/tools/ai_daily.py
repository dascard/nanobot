"""AI 日报工具的 KT Adapter 与兼容搜索 façade。"""

import asyncio as _asyncio
import html
import json
import logging
import re
from typing import Any
from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult
from app.tool_services.ai_daily import (
    build_ai_daily_tool_result,
    execute_ai_daily,
)
from core.async_bridge import run_awaitable_sync
from core.time_utils import db_now_naive
from core.tool_contracts.ai_daily import (
    AiDailyRequest,
    NewsRequest,
    ai_daily_parameters_schema,
    parse_news_search_request,
)
from nanobot_kt.tools.result_adapter import to_kt_tool_result
from creatures.nanobot.prompts.skills.news_search import (
    runtime_cache as _runtime_cache,
)
from creatures.nanobot.prompts.skills.news_search import (
    search_backend as _search_backend,
)
from creatures.nanobot.prompts.skills.news_search.legacy_report import (
    MODEL_NAME_HINTS as MODEL_NAME_HINTS,
    TRUSTED_NEWS_DOMAINS as TRUSTED_NEWS_DOMAINS,
    VALUE_ALERT_KEYWORDS as VALUE_ALERT_KEYWORDS,
    _build_news_brief_items as _build_news_brief_items,
    _build_news_conclusion as _build_news_conclusion,
    _build_news_layout_fallback,
    _build_value_alert,
    _coerce_layout_list as _coerce_layout_list,
    _coerce_layout_text as _coerce_layout_text,
    _combined_score,
    _domain,
    _escape_html as _escape_html,
    _escape_md_table_cell as _escape_md_table_cell,
    _extract_model_hints,
    _format_news_html_report,
    _format_news_unavailable_report,
    _freshness_score as _freshness_score,
    _merge_layout_with_fallback,
    _merge_specific_items as _merge_specific_items,
    _normalize_summary_text,
    _parse_news_layout_payload,
    _source_score as _source_score,
    _specificity_score as _specificity_score,
    _truncate_text as _truncate_text,
    _value_signal_score,
)

logger = logging.getLogger("nanobot.ai_daily")
# 旧测试会在工具模块上 patch asyncio.to_thread；保留同一标准库模块对象。
asyncio = _asyncio

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

RSS_SOURCES = _search_backend.RSS_SOURCES

def _run_async_blocking(coro: Any) -> Any:
    return run_awaitable_sync(coro)


def _summarize_news_layout(
    query: str,
    search_results: list[dict[str, Any]],
    extracted_contents: list[str],
    *,
    deepen: bool,
    decision_reason: str,
    value_alerts: list[dict[str, Any]],
) -> dict[str, Any]:
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

        async def _ask() -> dict[str, Any]:
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


def _heuristic_should_deepen(query: str, coarse_results: list[dict[str, Any]], max_results: int) -> bool:
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


def _model_should_deepen(query: str, coarse_results: list[dict[str, Any]], max_results: int) -> tuple[bool, str]:
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

        async def _ask() -> dict[str, Any]:
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

        parsed: dict[str, Any] = {}
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
    alerts: list[dict[str, Any]],
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


def _dedup_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _search_backend._dedup_results(results)


def _coerce_date(year: int | str, month: int | str, day: int | str) -> str | None:
    return _runtime_cache._coerce_date(year, month, day)


def _extract_date(query: str) -> str | None:
    return _runtime_cache._extract_date(query, now=db_now_naive())


def _is_daily_digest_query(query: str) -> bool:
    return _runtime_cache._is_daily_digest_query(query)


def _extract_item_date(item: Any) -> str:
    return _search_backend._extract_item_date(item)


def _normalize_search_result(item: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    return _search_backend._normalize_search_result(item, strategy=strategy)


def _filter_stale_news_results(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    request = parse_news_search_request(query, max_results=max(1, len(results)))
    return _search_backend._filter_stale_news_results(results, request)


def _fetch_rss_source(source: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    return _search_backend._fetch_rss_source(source, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_multi_rss(query: str | None = None, max_results: int = 5) -> list[dict[str, Any]]:
    return _search_backend._fetch_multi_rss(query=query, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_juya_rss(max_results: int, target_date: str | None = None) -> list[dict[str, Any]]:
    return _search_backend._fetch_juya_rss(
        max_results=max_results,
        target_date=target_date,
        urlopen_fn=_urlopen,
    )


def _build_query_variants(query: str) -> list[str]:
    request = parse_news_search_request(query)
    return _search_backend._build_query_variants(request, deep=False)


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
        now=db_now_naive(),
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


def _rerank_with_domain_diversity(results: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    return _search_backend._rerank_with_domain_diversity(results, max_results=max_results)


class WebTools:
    last_error: str = ""

    @staticmethod
    def search(
        query: str,
        max_results: int = 5,
        deep: bool = False,
        *,
        request: NewsRequest | None = None,
    ) -> list[dict]:
        typed_request = request or parse_news_search_request(
            query,
            max_results=max_results,
        )

        def _rss_adapter(
            current_request: NewsRequest,
            limit: int,
        ) -> list[dict[str, Any]]:
            return _fetch_multi_rss(
                query=current_request.query,
                max_results=limit,
            )

        results, last_error = _search_backend.search_news(
            typed_request,
            deep=deep,
            ddg_enabled=NEWS_SEARCH_DDG_ENABLED,
            ddgs_factory=DDGS,
            ddgs_kwargs_fn=_ddgs_kwargs,
            multi_rss_fetcher=_rss_adapter,
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


def _run_news_daily_pipeline(request: AiDailyRequest) -> str:
    """统一入口——quality → daily fallback。"""
    from creatures.nanobot.prompts.skills.news_search.news_daily.tool import (
        run_news_search_auto,
    )
    return run_news_search_auto(request)

def search_and_extract_news_v2(
    query: str,
    max_results: int = 5,
    mode: str = "fast",
) -> str:
    """新 Pipeline: 搜索→去重评分→Evidence Cards→结构化JSON→validate→模板HTML。"""
    import time as _t
    t0 = _t.time()
    from creatures.nanobot.prompts.skills.news_search.evidence import (
        build_evidence_pipeline, validate_digest, safe_digest, FALLBACK_DIGEST,
    )
    from creatures.nanobot.prompts.skills.news_search.prompts import (
        build_evidence_prompt,
        get_system_prompt,
    )
    from creatures.nanobot.prompts.skills.news_search.render import render_html
    from core.legacy_adapter import EvolutionUtils

    # 1. 搜索
    search_results = []
    request = parse_news_search_request(
        query,
        max_results=max_results,
    )
    logger.info(
        "[news_v2] route kind=%s freshness=%s mode=%s",
        request.request_kind,
        request.freshness,
        mode,
    )
    search_results = WebTools.search(
        query,
        max_results=max_results,
        deep=(mode == "deep"),
        request=request,
    )
    if not search_results:
        logger.info("[news_v2] no search results")
        return render_html(FALLBACK_DIGEST)

    # 2. Evidence Pipeline
    evidence_sources = _normalize_for_evidence(search_results, query)
    cards, _ = build_evidence_pipeline(evidence_sources, query, max_sources=max_results)
    if not cards:
        logger.info(
            "[news_v2] no cards raw=%d norm=%d",
            len(search_results),
            len(evidence_sources),
        )
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
    digest["generated_at"] = db_now_naive().strftime("%Y-%m-%d %H:%M")
    digest["mode"] = mode

    # 6. 渲染 HTML
    html = render_html(digest)

    ok, v_issues = validate_digest(digest, cards)
    logger.info(
        "[news_v2] done %.1fs mode=%s raw=%d norm=%d "
        "cards=%d html=%d validator=%s",
        _t.time()-t0, mode,
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

    value_alerts: list[dict[str, Any]] = []
    extracted_contents: list[str] = []
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
    return to_kt_tool_result(
        build_ai_daily_tool_result(
            html_result,
            query=query,
        )
    )


def _render_ai_daily_fallback(title: str, verdict: str, missing_info: list[str]) -> str:
    try:
        from creatures.nanobot.prompts.skills.news_search.evidence import (
            FALLBACK_DIGEST,
        )
        from creatures.nanobot.prompts.skills.news_search.render import (
            render_html,
        )

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
        return ai_daily_parameters_schema()

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        result = await execute_ai_daily(
            args,
            pipeline=_run_news_daily_pipeline,
            make_cache_key=_runtime_cache.make_ai_daily_cache_key,
            read_cache=_get_cached_news_result,
            write_cache=_store_cached_news_result,
            render_fallback=_render_ai_daily_fallback,
        )
        return to_kt_tool_result(result)


__all__ = [
    "AiDailyTool",
    "WebTools",
    "search_and_extract_news",
    "search_and_extract_news_v2",
]
