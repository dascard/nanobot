"""新闻日报 HTML 模板渲染——LLM 只填 JSON，模板负责视觉。"""

from datetime import datetime
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")


NEWS_TEMPLATE_CSS = """
:root{--bg:#faf9f7;--card:#fff;--ink:#1a1a2e;--sub:#666;--accent:#e65100;
  --tag-bg:#fff3e0;--tag-ink:#bf360c;--green:#2e7d32;--border:#e8e5df;
  --shadow:0 2px 8px rgba(0,0,0,.06)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.7;padding:24px 16px 40px}
.container{max-width:720px;margin:0 auto}
.header{margin-bottom:28px;padding-bottom:20px;border-bottom:2px solid var(--border)}
.header h1{font-size:1.6rem;font-weight:700;color:var(--ink)}
.header .sub{font-size:.9rem;color:var(--sub);margin-top:4px}
.header .meta{font-size:.75rem;color:#999;margin-top:6px}
.verdict{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:18px 20px;margin-bottom:24px;font-size:.95rem;box-shadow:var(--shadow)}
.verdict .label{font-size:.75rem;color:var(--accent);font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.top-story{background:linear-gradient(135deg,#fff8e1,#fff3e0);
  border:2px solid var(--accent);border-radius:14px;padding:22px;margin-bottom:24px;
  box-shadow:var(--shadow)}
.top-story .flag{font-size:.7rem;color:var(--accent);font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.top-story h2{font-size:1.15rem;margin-bottom:8px}
.top-story .what{font-size:.9rem;color:var(--ink);margin-bottom:8px}
.top-story .why{font-size:.82rem;color:var(--sub);font-style:italic}
.top-story .confidence{display:inline-block;margin-top:10px;padding:2px 10px;
  border-radius:10px;font-size:.72rem;background:var(--tag-bg);color:var(--tag-ink)}
.highlights{margin-bottom:24px}
.highlights .section-title{font-size:.85rem;font-weight:700;color:var(--sub);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.highlight-card{background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:14px 18px;margin-bottom:10px;box-shadow:var(--shadow)}
.highlight-card .label{font-size:.72rem;font-weight:600;color:var(--accent);
  margin-bottom:4px}
.highlight-card .txt{font-size:.9rem}
.highlight-card .src{font-size:.72rem;color:#999;margin-top:6px}
.importance{display:inline-block;margin-left:6px}
.importance .dot{display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--accent);margin-right:2px;opacity:.3}
.importance .dot.active{opacity:1}
.watchlist{background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:18px 20px;margin-bottom:24px;box-shadow:var(--shadow)}
.watchlist .section-title{font-size:.85rem;font-weight:700;color:var(--sub);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.watch-item{font-size:.88rem;padding:6px 0;border-bottom:1px solid #f5f3ef}
.watch-item:last-child{border-bottom:none}
.watch-item .reason{font-size:.78rem;color:var(--sub);margin-left:4px}
.details{margin-bottom:24px}
.details .section-title{font-size:.85rem;font-weight:700;color:var(--sub);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.detail-card{background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:14px 18px;margin-bottom:10px;box-shadow:var(--shadow)}
.detail-title{font-weight:700;font-size:.95rem;margin-bottom:8px;color:var(--ink)}
.detail-block{font-size:.88rem;color:var(--ink);margin-top:6px}
.detail-block ul{margin:4px 0 0 16px;padding:0}
.detail-block li{margin-bottom:2px}
.detail-impact{font-size:.86rem;color:var(--sub);margin-top:8px;font-style:italic}
.detail-src{font-size:.72rem;color:#999;margin-top:6px}
.missing{font-size:.8rem;color:#999;padding:12px 16px;margin-bottom:24px;
  border-left:3px solid #ddd;background:#f8f8f6;border-radius:0 8px 8px 0}
.source-index{background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:16px;margin-bottom:24px;box-shadow:var(--shadow)}
.source-index .section-title{font-size:.85rem;font-weight:700;color:var(--sub);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.source-table{width:100%;border-collapse:collapse;font-size:.78rem}
.source-table th,.source-table td{padding:7px 6px;border-bottom:1px solid #f0eee8;
  text-align:left;vertical-align:top}
.source-table th{color:#8a5a2b;font-weight:700;background:#fff8e1}
.source-table a{color:#8a4b00;text-decoration:none;word-break:break-all}
.closing{font-size:.88rem;color:var(--sub);text-align:center;
  padding:16px 0;border-top:1px solid var(--border);margin-top:8px}
.footer{text-align:center;font-size:.7rem;color:#ccc;margin-top:20px}
"""


def importance_dots(score: int) -> str:
    max_dots = 5
    active = min(max(score, 0), max_dots)
    dots = ""
    for i in range(max_dots):
        dots += '<span class="dot' + (' active' if i < active else '') + '"></span>'
    return f'<span class="importance">{dots}</span>'


def confidence_badge(conf: str) -> str:
    if not conf:
        return ""
    return f'<span class="confidence">{conf}</span>'


def source_link(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        short = domain[:40]
    except Exception:
        short = url[:40]
    return f'<a href="{url}" target="_blank" style="color:#999;text-decoration:none">{short}</a>'


def render_html(digest: dict) -> str:
    """将 NewsDigest JSON 渲染为 HTML 新闻日报。"""
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")

    # 顶部
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><style>{NEWS_TEMPLATE_CSS}</style></head>
<body class="news-brief">
<div class="container">
  <div class="header">
    <h1>{_e(digest.get('title', 'AI 日报'))}</h1>
    <div class="sub">{_e(digest.get('subtitle', ''))}</div>
    <div class="meta">{_e(digest.get('generated_at', now))} · {_e(digest.get('mode', 'fast'))} mode</div>
  </div>"""

    # 总评
    verdict = digest.get("verdict", "")
    if verdict:
        html += f'\n  <div class="verdict"><div class="label">今日总评</div>{_e(verdict)}</div>'

    # 头条
    ts = digest.get("top_story")
    if ts:
        html += f"""
  <div class="top-story">
    <div class="flag">⚡ Top Story</div>
    <h2>{_e(ts.get('title', ''))}</h2>
    <div class="what">{_e(ts.get('what_happened', ''))}</div>
    <div class="why">{_e(ts.get('why_it_matters', ''))}</div>
    {confidence_badge(ts.get('confidence', ''))}
    <div style="margin-top:8px;font-size:.72rem;color:#999">
{_src_name(ts.get("source_ids",[]), digest.get("sources",[]))} · {_src_date(ts.get("source_ids",[]), digest.get("sources",[]))}
    </div>
  </div>"""

    # 亮点
    highlights = digest.get("highlights", [])
    if highlights:
        html += '\n  <div class="highlights"><div class="section-title">📌 要点速览</div>'
        for h in highlights:
            label = h.get("label", "")
            text = h.get("text", "")
            imp = h.get("importance", 3)
            html += f"""
    <div class="highlight-card">
      <div class="label">{_e(label)}{importance_dots(imp)}</div>
      <div class="txt">{_e(text)}</div>
      <div class="src">{_src_name(h.get("source_ids",[]), digest.get("sources",[]))}  {_src_date(h.get("source_ids",[]), digest.get("sources",[]))}</div>
    </div>"""
        html += '\n  </div>'

    # 重点详情
    details = digest.get("details", [])
    if details:
        html += '\n  <div class="details"><div class="section-title">🧩 重点详情</div>'
        for d in details[:3]:
            title = d.get("title", "")
            known = d.get("known", [])
            unknown = d.get("unknown", [])
            impact = d.get("impact", "")
            src_labels = d.get("source_labels", [])
            html += '\n    <div class="detail-card">'
            html += f'\n      <div class="detail-title">{_e(title)}</div>'
            if known:
                html += '\n      <div class="detail-block"><b>已知：</b><ul>'
                for k in known:
                    html += f'<li>{_e(k)}</li>'
                html += '</ul></div>'
            if unknown:
                html += '\n      <div class="detail-block"><b>缺失：</b><ul>'
                for u in unknown:
                    html += f'<li>{_e(u)}</li>'
                html += '</ul></div>'
            if impact:
                html += f'\n      <div class="detail-impact"><b>影响：</b>{_e(impact)}</div>'
            if src_labels:
                html += f'\n      <div class="detail-src">来源：{_e(", ".join(src_labels))}</div>'
            html += '\n    </div>'
        html += '\n  </div>'

    # 关注
    watchlist = digest.get("watchlist", [])
    if watchlist:
        html += '\n  <div class="watchlist"><div class="section-title">🔍 持续关注</div>'
        for w in watchlist:
            html += f"""
    <div class="watch-item">
      {_e(w.get('text', ''))}
      <span class="reason">{_e(w.get('reason', ''))}</span>
      <span style="font-size:.7rem;color:#ccc;margin-left:8px">{_format_source_ids(w.get('source_ids', []))}</span>
    </div>"""
        html += '\n  </div>'

    # 缺失
    missing = digest.get("missing_info", [])
    if missing:
        html += '\n  <div class="missing">'
        for m in missing:
            html += f'<div>⚠ {_e(m)}</div>'
        html += '\n  </div>'

    # 来源索引
    sources = digest.get("sources", [])
    if sources:
        html += '\n  <div class="source-index"><div class="section-title">🔗 来源索引</div>'
        html += '\n    <table class="source-table"><thead><tr><th>#</th><th>来源</th><th>标题</th><th>时间</th></tr></thead><tbody>'
        for src in sources[:12]:
            sid = src.get("source_id", "")
            url = src.get("url", "")
            title = src.get("title", "")
            source_name = src.get("source_name", "") or src.get("domain", "")
            published_at = src.get("published_at", "")
            title_html = f'<a href="{_attr(url)}" target="_blank" rel="noopener noreferrer">{_e(title)}</a>' if url else _e(title)
            html += (
                f'\n      <tr><td>{_e(sid)}</td><td>{_e(source_name)}</td>'
                f'<td>{title_html}</td><td>{_e(published_at)}</td></tr>'
            )
        html += '\n    </tbody></table>\n  </div>'

    # 结尾
    closing = digest.get("closing", "")
    if closing:
        html += f'\n  <div class="closing">{_e(closing)}</div>'

    html += f'\n  <div class="footer">Nanobot AI 日报 · {now}</div>\n</div>\n</body>\n</html>'
    return html


def _src_name(ids: list[int], sources: list[dict]) -> str:
    if not ids or not sources:
        return ""
    names = []
    for sid in ids:
        for s in sources:
            if s.get("source_id") == sid:
                names.append(s.get("source_name", f"#{sid}"))
                break
        else:
            names.append(f"#{sid}")
    return ", ".join(names[:3])

def _src_date(ids, sources):
    if not ids or not sources:
        return ""
    dates = []
    for sid in ids[:2]:
        for s in sources:
            if s.get("source_id") == sid:
                d = s.get("published_at", "")
                if d:
                    dates.append(d)
                break
    return ", ".join(dates) if dates else ""

def _e(text: str) -> str:
    """HTML 转义。"""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _attr(text: str) -> str:
    return _e(text).replace("'", "&#x27;")


def _format_source_ids(ids: list[int]) -> str:
    """fallback when just IDs available"""
    if not ids:
        return ""
    return "#" + ", #".join(str(i) for i in ids)
