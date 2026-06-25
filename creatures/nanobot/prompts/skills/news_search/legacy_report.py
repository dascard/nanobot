"""旧版新闻搜索报告渲染 helper。"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from core.json_utils import json_repair
from core.time_utils import db_now_naive


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


def _source_score(item: dict[str, Any]) -> int:
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


def _freshness_score(item: dict[str, Any]) -> int:
    raw_date = (item.get("date") or "").strip()
    if not raw_date:
        return 0

    try:
        dt = parsedate_to_datetime(raw_date)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except Exception:
            return 0
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


def _combined_score(item: dict[str, Any]) -> int:
    return _source_score(item) + _freshness_score(item)


def _value_signal_score(text: str) -> int:
    t = (text or "").lower()
    score = 0
    for kw in VALUE_ALERT_KEYWORDS:
        if kw in t:
            score += 1
    return score


def _extract_model_hints(text: str) -> list[str]:
    t = (text or "").lower()
    found = []
    for name in MODEL_NAME_HINTS:
        if name in t:
            found.append(name)
    return sorted(set(found))


def _build_value_alert(item: dict[str, Any], content: str) -> dict[str, Any]:
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
    search_results: list[dict[str, Any]],
    value_alerts: list[dict[str, Any]],
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
    search_results: list[dict[str, Any]],
    extracted_contents: list[str],
    *,
    limit: int = 5,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
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


def _coerce_layout_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _coerce_layout_text(item, max_chars)
        if text:
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _parse_news_layout_payload(raw: str) -> dict[str, Any]:
    parsed = json_repair(raw or "")
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
    preferred: list[str],
    fallback: list[str],
    *,
    min_items: int,
    max_items: int,
    specificity_floor: int,
) -> list[str]:
    merged: list[str] = []
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


def _merge_layout_with_fallback(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
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
    search_results: list[dict[str, Any]],
    extracted_contents: list[str],
    *,
    deepen: bool,
    decision_reason: str,
    value_alerts: list[dict[str, Any]],
) -> dict[str, Any]:
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


def _format_news_html_report(
    query: str,
    search_results: list[dict[str, Any]],
    extracted_contents: list[str],
    *,
    layout: dict[str, Any],
    deepen: bool,
    decision_reason: str,
    value_alerts: list[dict[str, Any]],
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
      <div class="meta-card"><div class="meta-label">时间</div><div class="meta-value">{_escape_html(db_now_naive().strftime('%m-%d %H:%M'))}</div></div>
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
