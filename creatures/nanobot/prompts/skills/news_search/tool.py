"""
News Search tool — KohakuTerrarium BaseTool adapter.

Uses DuckDuckGo for web search and trafilatura for high-quality article extraction.
"""

import asyncio
import logging
import os
import re
import json
import html
import threading
import time
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

NEWS_SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("NEWS_SEARCH_CACHE_TTL_SECONDS", "300"))
_NEWS_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, str]] = {}
_NEWS_SEARCH_CACHE_LOCK = threading.Lock()

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


def _truncate_text(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _normalize_summary_text(text: str, max_chars: int) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    return _truncate_text(value, max_chars)


def _escape_md_table_cell(text: str) -> str:
    value = (text or "").replace("\n", " ").replace("|", "\\|").strip()
    return value or "-"


def _escape_html(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _build_news_conclusion(
    query: str,
    search_results: List[Dict[str, Any]],
    value_alerts: List[Dict[str, Any]],
) -> str:
    if value_alerts:
        top_alert = max(value_alerts, key=lambda x: int(x.get("signal", 0)))
        models = "、".join(top_alert.get("models", [])[:3]) or "相关模型"
        return (
            f"这轮检索里最值得优先关注的是 **{top_alert.get('title') or '高价值条目'}**，"
            f"它同时带出了 `{models}` 的免费、低价或性价比信号。"
        )

    if search_results:
        lead = search_results[0]
        domain = _domain(lead.get("href", "")) or "未知来源"
        return (
            f"本轮更像是一次常规资讯更新，优先值得看的主线是 "
            f"**{lead.get('title') or '首条资讯'}**，来源于 `{domain}`。"
        )

    return f"这次没有拿到足够的相关新闻结果，建议改写查询词后重试：`{query}`。"


def _build_news_brief_items(
    search_results: List[Dict[str, Any]],
    extracted_contents: List[str],
    *,
    limit: int = 5,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for idx, (item, content) in enumerate(zip(search_results, extracted_contents), start=1):
        if idx > limit:
            break
        summary = _normalize_summary_text(content or item.get("body") or "暂无摘要", 88)
        domain = _domain(item.get("href", "")) or "unknown"
        items.append(
            {
                "index": str(idx),
                "title": item.get("title") or "未命名条目",
                "summary": summary,
                "domain": domain,
            }
        )
    return items


def _format_news_unavailable_report(query: str, reason: str = "") -> str:
    detail = _normalize_summary_text(reason or "外部新闻搜索源暂时不可用或被限流。", 120)
    return f"""
<article class="news-brief news-brief-unavailable">
  <style>
    .news-brief {{
      font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: #18212f;
      background: linear-gradient(180deg, #f6f1e8 0%, #eef3f8 100%);
      padding: 28px;
      width: 960px;
      box-sizing: border-box;
    }}
    .news-brief * {{ box-sizing: border-box; }}
    .hero {{
      background: linear-gradient(135deg, #5a2b2b 0%, #8b4b2d 52%, #d7b36a 100%);
      color: #fffaf0;
      border-radius: 24px;
      padding: 28px 30px;
    }}
    .section {{
      background: rgba(255, 255, 255, 0.9);
      border: 1px solid rgba(19, 35, 61, 0.08);
      border-radius: 20px;
      padding: 20px 22px;
      margin-top: 18px;
    }}
    .section h2 {{ margin: 0 0 14px; font-size: 22px; color: #13233d; }}
    .tip {{
      padding: 14px 16px;
      border-radius: 16px;
      background: #fff7ef;
      border: 1px solid rgba(216, 179, 107, 0.35);
      line-height: 1.8;
    }}
  </style>
  <section class="hero">
    <p>AI 资讯简报</p>
    <h1>{_escape_html(query)}</h1>
    <p>当前搜索源不可用，未生成资讯正文卡片。</p>
  </section>
  <section class="section">
    <h2>当前状态</h2>
    <div class="tip">外部新闻搜索源暂时不可用或被限流，当前无法可靠获取今天的 AI 新闻。</div>
  </section>
  <section class="section">
    <h2>诊断信息</h2>
    <div class="tip">{_escape_html(detail)}</div>
  </section>
  <section class="section">
    <h2>处理建议</h2>
    <div class="tip">这一轮不要继续重试同类搜索请求。建议稍后再试，或改用更具体的单一来源查询。</div>
  </section>
</article>
""".strip()


def _coerce_layout_text(value: Any, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return _normalize_summary_text(value, max_chars)


def _coerce_layout_list(value: Any, *, max_items: int, max_chars: int) -> List[str]:
    if not isinstance(value, list):
        return []
    cleaned: List[str] = []
    for item in value:
        text = _coerce_layout_text(item, max_chars)
        if text:
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _parse_news_layout_payload(raw: str) -> Dict[str, Any]:
    from core.legacy_adapter import EvolutionUtils

    parsed = EvolutionUtils.json_repair(raw or "")
    if not isinstance(parsed, dict) or parsed.get("parse_error"):
        return {}

    return {
        "title": _coerce_layout_text(parsed.get("title"), 28),
        "subtitle": _coerce_layout_text(parsed.get("subtitle"), 40),
        "summary": _coerce_layout_text(parsed.get("summary"), 160),
        "highlights": _coerce_layout_list(parsed.get("highlights"), max_items=4, max_chars=72),
        "alerts": _coerce_layout_list(parsed.get("alerts"), max_items=3, max_chars=72),
        "closing": _coerce_layout_text(parsed.get("closing"), 88),
    }


def _specificity_score(text: str) -> int:
    value = (text or "").strip().lower()
    if not value:
        return 0

    score = 0
    if re.search(r"\d", value):
        score += 1
    if any(name in value for name in MODEL_NAME_HINTS):
        score += 2
    if any(token in value for token in ["api", "token", "免费", "低价", "价格", "额度", "开源", "benchmark", "成本", "%", "$"]):
        score += 1
    if len(value) >= 18:
        score += 1
    return score


def _merge_specific_items(
    preferred: List[str],
    fallback: List[str],
    *,
    min_items: int,
    max_items: int,
    specificity_floor: int,
) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()

    for item in preferred:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        if _specificity_score(normalized) >= specificity_floor:
            merged.append(normalized)
            seen.add(normalized)
        if len(merged) >= max_items:
            return merged

    for item in fallback:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
        if len(merged) >= max_items:
            return merged

    for item in preferred:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
        if len(merged) >= max_items:
            return merged

    return merged[:max(min_items, len(merged))]


def _merge_layout_with_fallback(parsed: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(fallback)

    title = _coerce_layout_text(parsed.get("title"), 28)
    subtitle = _coerce_layout_text(parsed.get("subtitle"), 40)
    summary = _coerce_layout_text(parsed.get("summary"), 160)
    closing = _coerce_layout_text(parsed.get("closing"), 88)

    if title:
        merged["title"] = title
    if subtitle:
        merged["subtitle"] = subtitle
    if closing:
        merged["closing"] = closing

    if summary and _specificity_score(summary) >= 2:
        merged["summary"] = summary

    merged["highlights"] = _merge_specific_items(
        list(parsed.get("highlights") or []),
        list(fallback.get("highlights") or []),
        min_items=3,
        max_items=4,
        specificity_floor=2,
    ) or list(fallback.get("highlights") or [])

    fallback_alerts = list(fallback.get("alerts") or [])
    merged["alerts"] = _merge_specific_items(
        list(parsed.get("alerts") or []),
        fallback_alerts,
        min_items=1 if not fallback_alerts else min(2, len(fallback_alerts)),
        max_items=3,
        specificity_floor=1,
    ) or fallback_alerts

    return merged


def _build_news_layout_fallback(
    query: str,
    search_results: List[Dict[str, Any]],
    extracted_contents: List[str],
    *,
    deepen: bool,
    decision_reason: str,
    value_alerts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    conclusion = _build_news_conclusion(query, search_results, value_alerts)
    brief_items = _build_news_brief_items(search_results, extracted_contents, limit=4)
    highlights = [
        _normalize_summary_text(f"{item['title']}：{item['summary']}", 72)
        for item in brief_items
    ]
    alerts = []
    if value_alerts:
        for alert in sorted(value_alerts, key=lambda x: int(x.get("signal", 0)), reverse=True)[:3]:
            models = "、".join(alert.get("models", [])[:3]) or "相关模型"
            alerts.append(
                _normalize_summary_text(
                    f"{alert.get('title') or '高价值条目'}：出现与 {models} 相关的免费、低价或性价比信号。",
                    72,
                )
            )
    if not alerts:
        alerts.append("暂无明显的免费、低价或高性价比信号，可先关注来源索引里的首条资讯。")

    subtitle_parts = []
    if query:
        subtitle_parts.append(_truncate_text(query, 24))
    subtitle_parts.append("深搜已启用" if deepen else "常规速报")

    closing = "更细节的链接、来源与正文摘录见下方来源索引和延伸阅读。"
    if decision_reason:
        closing = _normalize_summary_text(
            f"{closing} 本次检索决策：{decision_reason}.",
            88,
        )

    return {
        "title": "AI 今日速报",
        "subtitle": "｜".join(subtitle_parts) if subtitle_parts else "AI 最新资讯整理",
        "summary": conclusion,
        "highlights": highlights or ["暂无足够结果生成重点速览。"],
        "alerts": alerts,
        "closing": closing,
    }


def _run_async_blocking(coro: Any) -> Any:
    result: Dict[str, Any] = {}
    error: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - 仅用于线程桥接
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


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


def _format_news_html_report(
    query: str,
    search_results: List[Dict[str, Any]],
    extracted_contents: List[str],
    *,
    layout: Dict[str, Any],
    deepen: bool,
    decision_reason: str,
    value_alerts: List[Dict[str, Any]],
) -> str:
    title = layout.get("title") or "AI 今日速报"
    subtitle = layout.get("subtitle") or _truncate_text(query, 36)
    summary = layout.get("summary") or _build_news_conclusion(query, search_results, value_alerts)
    highlights = list(layout.get("highlights") or [])
    alerts = list(layout.get("alerts") or [])
    closing = layout.get("closing") or "更细节的信息见下方来源索引和延伸阅读。"

    brief_html = "".join(
        f"""
        <div class="brief-card">
          <div class="brief-index">{idx}</div>
          <div class="brief-body">
            <div class="brief-title">{_escape_html(item)}</div>
          </div>
        </div>
        """.strip()
        for idx, item in enumerate(highlights[:4], start=1)
    ) or '<div class="brief-empty">暂无足够结果可生成简报。</div>'

    alert_cards = "".join(
        f"""
        <div class="alert-card">
          <div class="alert-title">关注点 {idx}</div>
          <div class="alert-meta">{_escape_html(item)}</div>
        </div>
        """.strip()
        for idx, item in enumerate(alerts[:3], start=1)
    ) or '<div class="alert-empty">暂无明显的免费、低价或高性价比模型信号。</div>'

    overview_rows = []
    detail_sections = []
    for idx, (item, content) in enumerate(zip(search_results, extracted_contents), start=1):
        url = item.get("href", "") or ""
        safe_url = _escape_html(url)
        domain = _domain(url) or "unknown"
        score = _source_score(item)
        item_title = item.get("title") or "未命名条目"
        strategy = item.get("search_strategy", "web_ddg")
        snippet = _normalize_summary_text(item.get("body") or content or "无摘要", 42)
        overview_rows.append(
            f"""
            <tr>
              <td>{idx}</td>
              <td><a href="{safe_url}">{_escape_html(item_title)}</a></td>
              <td>{_escape_html(domain)}</td>
              <td>{score}</td>
              <td>{_escape_html(strategy)}</td>
              <td>{_escape_html(snippet)}</td>
            </tr>
            """.strip()
        )
        detail_body = content if content.startswith("Error extracting") or content.startswith("Failed") else _truncate_text(content, 900)
        detail_sections.append(
            f"""
            <section class="detail-item">
              <h3>{idx}. {_escape_html(item_title)}</h3>
              <div class="detail-meta">
                <span>来源：{_escape_html(domain)}</span>
                <span>质量分：{score}</span>
                <span>检索策略：{_escape_html(strategy)}</span>
              </div>
              <div class="detail-link"><a href="{safe_url}">{safe_url}</a></div>
              <p class="detail-snippet">{_escape_html(_normalize_summary_text(item.get('body', ''), 180))}</p>
              <div class="detail-content">{_escape_html(detail_body)}</div>
            </section>
            """.strip()
        )

    return f"""
<article class="news-brief">
  <style>
    .news-brief {{
      font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: #18212f;
      background: linear-gradient(180deg, #f6f1e8 0%, #eef3f8 100%);
      padding: 28px;
      width: 960px;
      box-sizing: border-box;
    }}
    .news-brief * {{ box-sizing: border-box; }}
    .hero {{
      background: linear-gradient(135deg, #13233d 0%, #2b5a88 52%, #d8b36b 100%);
      color: #fffaf0;
      border-radius: 24px;
      padding: 28px 30px;
      box-shadow: 0 18px 40px rgba(19, 35, 61, 0.18);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.15;
    }}
    .hero p {{
      margin: 0;
      font-size: 15px;
      opacity: 0.92;
    }}
    .hero-subtitle {{
      margin-top: 10px !important;
      font-size: 18px !important;
      line-height: 1.6 !important;
      opacity: 0.96 !important;
    }}
    .hero-query {{
      margin-top: 8px !important;
      font-size: 13px !important;
      opacity: 0.82 !important;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .meta-card, .section {{
      background: rgba(255, 255, 255, 0.88);
      border: 1px solid rgba(19, 35, 61, 0.08);
      border-radius: 20px;
      padding: 20px 22px;
      margin-top: 18px;
      box-shadow: 0 10px 24px rgba(35, 49, 66, 0.08);
    }}
    .meta-label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #6d7a8a;
      margin-bottom: 6px;
    }}
    .meta-value {{
      font-size: 18px;
      font-weight: 700;
      color: #152235;
    }}
    .section h2 {{
      margin: 0 0 14px;
      font-size: 22px;
      color: #13233d;
    }}
    .conclusion {{
      font-size: 18px;
      line-height: 1.8;
    }}
    .brief-grid {{
      display: grid;
      gap: 12px;
    }}
    .brief-card, .alert-card {{
      display: flex;
      gap: 14px;
      background: #fffdfa;
      border: 1px solid rgba(216, 179, 107, 0.28);
      border-radius: 18px;
      padding: 16px 18px;
    }}
    .brief-index {{
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: #13233d;
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      flex: 0 0 auto;
    }}
    .brief-title, .alert-title {{
      font-size: 18px;
      font-weight: 700;
      color: #1c2b3f;
      margin-bottom: 4px;
    }}
    .brief-summary, .alert-meta, .detail-content, .detail-snippet {{
      white-space: pre-wrap;
      line-height: 1.7;
      color: #3a4657;
    }}
    .brief-domain {{
      margin-top: 8px;
      font-size: 13px;
      color: #76603c;
    }}
    .alert-link, .detail-link {{
      margin-top: 8px;
      font-size: 13px;
      word-break: break-all;
    }}
    a {{
      color: #1d5b93;
      text-decoration: none;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      overflow: hidden;
      border-radius: 16px;
      background: #fff;
    }}
    th, td {{
      padding: 12px 10px;
      border-bottom: 1px solid #e7edf3;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #15283f;
      color: white;
      font-weight: 700;
    }}
    .detail-item + .detail-item {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid #dde6ef;
    }}
    .detail-item h3 {{
      margin: 0 0 10px;
      color: #13233d;
      font-size: 19px;
    }}
    .detail-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      font-size: 13px;
      color: #6a7787;
      margin-bottom: 8px;
    }}
  </style>
  <section class="hero">
    <p>AI 资讯简报</p>
    <h1>{_escape_html(title)}</h1>
    <p class="hero-subtitle">{_escape_html(subtitle)}</p>
    <p class="hero-query">查询词：{_escape_html(query)}</p>
    <div class="meta-grid">
      <div class="meta-card"><div class="meta-label">深搜</div><div class="meta-value">{'已启用' if deepen else '未启用'}</div></div>
      <div class="meta-card"><div class="meta-label">命中结果</div><div class="meta-value">{len(search_results)}</div></div>
      <div class="meta-card"><div class="meta-label">决策原因</div><div class="meta-value">{_escape_html(decision_reason or '-')}</div></div>
      <div class="meta-card"><div class="meta-label">时间</div><div class="meta-value">{_escape_html(datetime.now().strftime('%m-%d %H:%M'))}</div></div>
    </div>
  </section>
  <section class="section">
    <h2>今日结论</h2>
    <div class="conclusion">{_escape_html(summary)}</div>
  </section>
  <section class="section">
    <h2>重点速览</h2>
    <div class="brief-grid">{brief_html}</div>
  </section>
  <section class="section">
    <h2>机会关注</h2>
    <div class="brief-grid">{alert_cards}</div>
  </section>
  <section class="section">
    <h2>来源索引</h2>
    <table>
      <thead>
        <tr><th>序号</th><th>标题</th><th>来源</th><th>质量分</th><th>检索策略</th><th>摘要线索</th></tr>
      </thead>
      <tbody>
        {''.join(overview_rows)}
      </tbody>
    </table>
  </section>
  <section class="section">
    <h2>延伸阅读</h2>
    {''.join(detail_sections)}
  </section>
  <section class="section">
    <h2>一句收尾</h2>
    <div class="conclusion">{_escape_html(closing)}</div>
  </section>
</article>
""".strip()


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


def _coerce_date(year: int | str, month: int | str, day: int | str) -> str | None:
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _extract_date(query: str) -> str | None:
    text = query or ""

    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(datetime.now().year, match.group(1), match.group(2))

    if re.search(r"\b(today)\b|今天|今日", text, flags=re.IGNORECASE):
        return datetime.now().strftime("%Y-%m-%d")

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


def _is_urgent_news_query(query: str) -> bool:
    q = (query or "").lower()
    markers = ["today", "今日", "今天", "latest", "最新", "刚刚", "breaking", "24h", "24小时"]
    return any(k in q for k in markers)


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


def _normalize_search_result(item: Dict[str, Any], *, strategy: str) -> Dict[str, Any]:
    normalized = dict(item)
    href = normalized.get("href") or normalized.get("url") or ""
    normalized["href"] = href
    normalized.setdefault("search_strategy", strategy)
    return normalized


def _filter_stale_news_results(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    if not _is_news_query(query):
        return results

    hours = 36 if _is_urgent_news_query(query) else 72
    filtered: List[Dict[str, Any]] = []
    for item in results:
        raw_date = (item.get("date") or "").strip()
        if raw_date and not _is_recent_enough(raw_date, hours=hours):
            continue
        filtered.append(item)
    return filtered


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
        raw_date = (item.get("date") or "").strip()
        if target_date and target_date not in title and not raw_date.startswith(target_date):
            continue
        if not _is_recent_enough(raw_date, hours=72):
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

            item_date = _extract_item_date(item)
            if target_date and target_date not in title and not item_date.startswith(target_date):
                continue

            parsed.append(
                {
                    "title": title or "Juya AI Daily",
                    "href": link,
                    "body": (description or content_encoded)[:800],
                    "date": item_date,
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
    if _is_news_query(q):
        today = datetime.now().strftime("%Y-%m-%d")
        variants.append(f"{today} {q}")
        variants.append(f"{q} today breaking")
        variants.append(f"{q} site:openai.com OR site:anthropic.com OR site:huggingface.co")
    return variants


def _news_search_cache_key(query: str, max_results: int) -> tuple[Any, ...]:
    q = re.sub(r"\s+", " ", (query or "").lower()).strip()
    target_date = _extract_date(query)
    ai_like = any(k in q for k in ["ai", "人工智能", "大模型", "llm", "模型"])
    news_like = _is_news_query(q) or bool(target_date)
    if ai_like and news_like:
        return ("daily_ai", target_date or datetime.now().strftime("%Y-%m-%d"), int(max_results))
    return ("query", q, int(max_results))


def _get_cached_news_result(key: tuple[Any, ...]) -> str | None:
    now = time.monotonic()
    with _NEWS_SEARCH_CACHE_LOCK:
        cached = _NEWS_SEARCH_CACHE.get(key)
        if not cached:
            return None
        created_at, output = cached
        if now - created_at > NEWS_SEARCH_CACHE_TTL_SECONDS:
            _NEWS_SEARCH_CACHE.pop(key, None)
            return None
        return output


def _store_cached_news_result(key: tuple[Any, ...], output: str) -> None:
    with _NEWS_SEARCH_CACHE_LOCK:
        if len(_NEWS_SEARCH_CACHE) > 64:
            oldest_key = min(_NEWS_SEARCH_CACHE, key=lambda k: _NEWS_SEARCH_CACHE[k][0])
            _NEWS_SEARCH_CACHE.pop(oldest_key, None)
        _NEWS_SEARCH_CACHE[key] = (time.monotonic(), output)


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
    last_error: str = ""

    @staticmethod
    def search(query: str, max_results: int = 5, deep: bool = False) -> List[Dict]:
        WebTools.last_error = ""
        errors: list[str] = []

        # Strategy 1: structured RSS sources.
        rss_limit = max_results * (2 if deep else 1)
        try:
            rss_results = list(_fetch_multi_rss(query=query, max_results=rss_limit))
        except Exception as e:
            errors.append(f"rss:{e}")
            logger.warning(f"RSS aggregate search failed: {e}")
            rss_results = []

        # Strategy 1b: Juya direct date match for briefing style queries.
        target_date = _extract_date(query)
        if _is_rss_first_query(query):
            try:
                rss_results.extend(_fetch_juya_rss(max_results=max_results, target_date=target_date))
            except Exception as e:
                errors.append(f"juya:{e}")
                logger.warning(f"Juya RSS search failed: {e}")

        # Strategy 2: multi-query web retrieval with per-source partial retention.
        results: list[dict[str, Any]] = []
        timelimit = _infer_timelimit(query)
        if timelimit is None and _is_news_query(query):
            timelimit = "d"
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

        try:
            with DDGS() as ddgs:
                if _is_news_query(query):
                    try:
                        for r in ddgs.news(
                            keywords=query,
                            region='wt-wt',
                            safesearch='moderate',
                            timelimit=timelimit,
                            max_results=per_variant,
                        ):
                            results.append(_normalize_search_result(dict(r), strategy="web_ddg_news"))
                    except Exception as e:
                        errors.append(f"ddg_news:{e}")
                        logger.warning(f"DDG news search failed: {e}")

                for variant in variants:
                    try:
                        for r in ddgs.text(
                            variant,
                            region='wt-wt',
                            safesearch='moderate',
                            timelimit=timelimit,
                            max_results=per_variant,
                        ):
                            results.append(
                                _normalize_search_result(
                                    dict(r),
                                    strategy="web_ddg_deep" if deep else "web_ddg_multi_variant",
                                )
                            )
                    except Exception as e:
                        errors.append(f"ddg_text:{variant}: {e}")
                        logger.warning(f"DDG text search failed for variant={variant!r}: {e}")
        except Exception as e:
            errors.append(f"ddg:{e}")
            logger.warning(f"DDG search session failed: {e}")

        merged = _filter_stale_news_results(rss_results + results, query)
        merged = _dedup_results(merged)
        merged = _rerank_with_domain_diversity(merged, max_results=max_results)
        if not merged and errors:
            WebTools.last_error = " | ".join(errors[-4:])
            logger.error(f"Search failed: {WebTools.last_error}")
        return merged

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

        cache_key = _news_search_cache_key(query, max_results)
        cached = _get_cached_news_result(cache_key)
        if cached is not None:
            logger.info("[news_search] reuse cached result for key=%s", cache_key)
            return ToolResult(output=cached, exit_code=0)
        
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
        _store_cached_news_result(cache_key, result)
        return ToolResult(output=result, exit_code=0)
