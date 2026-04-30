"""
News Search tool — KohakuTerrarium BaseTool adapter.

Uses DuckDuckGo for web search and trafilatura for high-quality article extraction.
"""

import logging
import re
import json
import html
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


def _format_news_markdown_report(
    query: str,
    search_results: List[Dict[str, Any]],
    extracted_contents: List[str],
    *,
    deepen: bool,
    decision_reason: str,
    value_alerts: List[Dict[str, Any]],
) -> str:
    conclusion = _build_news_conclusion(query, search_results, value_alerts)
    brief_items = _build_news_brief_items(search_results, extracted_contents)
    alert_cards = []
    if value_alerts:
        value_alerts = sorted(value_alerts, key=lambda x: int(x.get("signal", 0)), reverse=True)
        for alert in value_alerts[:5]:
            models = "、".join(alert.get("models", [])) or "未识别"
            link_html = ""
            if alert.get("url"):
                safe_url = _escape_html(alert["url"])
                link_html = f'<div class="alert-link"><a href="{safe_url}">{safe_url}</a></div>'
            alert_cards.append(
                f"""
                <div class="alert-card">
                  <div class="alert-title">{_escape_html(alert.get('title') or '未命名条目')}</div>
                  <div class="alert-meta">信号分 {int(alert.get('signal', 0))} · 模型线索：{_escape_html(models)}</div>
                  {link_html}
                </div>
                """.strip()
            )
    else:
        alert_cards.append('<div class="alert-empty">暂无明显的免费、低价或高性价比模型信号。</div>')

    overview_rows = []
    detail_sections = []
    for idx, (item, content) in enumerate(zip(search_results, extracted_contents), start=1):
        url = item.get("href", "") or ""
        safe_url = _escape_html(url)
        domain = _domain(url) or "unknown"
        score = _source_score(item)
        title = item.get("title") or "未命名条目"
        strategy = item.get("search_strategy", "web_ddg")
        snippet = _normalize_summary_text(item.get("body") or content or "无摘要", 42)
        overview_rows.append(
            f"""
            <tr>
              <td>{idx}</td>
              <td><a href="{safe_url}">{_escape_html(title)}</a></td>
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
              <h3>{idx}. {_escape_html(title)}</h3>
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

    brief_html = "".join(
        f"""
        <div class="brief-card">
          <div class="brief-index">{_escape_html(item['index'])}</div>
          <div class="brief-body">
            <div class="brief-title">{_escape_html(item['title'])}</div>
            <div class="brief-summary">{_escape_html(item['summary'])}</div>
            <div class="brief-domain">{_escape_html(item['domain'])}</div>
          </div>
        </div>
        """.strip()
        for item in brief_items
    ) or '<div class="brief-empty">暂无足够结果可生成简报。</div>'

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
    <h1>{_escape_html(query)}</h1>
    <div class="meta-grid">
      <div class="meta-card"><div class="meta-label">深搜</div><div class="meta-value">{'已启用' if deepen else '未启用'}</div></div>
      <div class="meta-card"><div class="meta-label">命中结果</div><div class="meta-value">{len(search_results)}</div></div>
      <div class="meta-card"><div class="meta-label">决策原因</div><div class="meta-value">{_escape_html(decision_reason or '-')}</div></div>
      <div class="meta-card"><div class="meta-label">时间</div><div class="meta-value">{_escape_html(datetime.now().strftime('%m-%d %H:%M'))}</div></div>
    </div>
  </section>
  <section class="section">
    <h2>今日结论</h2>
    <div class="conclusion">{_escape_html(conclusion)}</div>
  </section>
  <section class="section">
    <h2>重点速览</h2>
    <div class="brief-grid">{brief_html}</div>
  </section>
  <section class="section">
    <h2>机会关注</h2>
    <div class="brief-grid">{''.join(alert_cards)}</div>
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

    report = _format_news_markdown_report(
        query=query,
        search_results=search_results,
        extracted_contents=extracted_contents,
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
