"""群分析 Scrapbook 风格 HTML 渲染——不依赖数据库。"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")


def escape_html(text: Any) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def safe_percentage(value: Any, default: int = 50) -> int:
    try:
        pct = int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        pct = default
    return max(0, min(100, pct))


SCRAPBOOK_CSS = """
:root{--ink-primary:#5d4037;--ink-secondary:#8d6e63;--accent-orange:#ff7043;--bg-paper:#fdfbf7;
  --color-yellow:#fff9c4;--color-pink:#ffccbc;--color-blue:#b3e5fc;--color-green:#c8e6c9;--color-purple:#e1bee7;
  --font-title:'WenQuanYi Zen Hei','Microsoft YaHei',cursive;--font-hand:'KaiTi','STKaiti',serif;
  --font-body:'WenQuanYi Micro Hei','Noto Sans SC','Microsoft YaHei',sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font-body);color:var(--ink-primary);background-color:var(--bg-paper);
  background-image:radial-gradient(#ddd 2px,transparent 2px);background-size:20px 20px;
  min-height:100vh;padding:30px 15px;line-height:1.6}
.container{max-width:960px;margin:0 auto;background:#fff;border:2px solid var(--ink-primary);
  border-radius:20px;padding:35px;position:relative;
  box-shadow:8px 8px 0 var(--color-blue),16px 16px 0 var(--color-pink),0 18px 36px rgba(0,0,0,.1)}
.container::before{content:"";position:absolute;top:10px;bottom:10px;left:10px;right:10px;
  border:2px dashed var(--ink-secondary);border-radius:14px;pointer-events:none;opacity:.45}
.header{text-align:center;margin-bottom:35px;padding-top:15px}
.title-sticker{display:inline-block;background:#fff;padding:18px 50px;border:3px dashed var(--ink-primary);
  border-radius:15px;box-shadow:5px 5px 0 var(--color-blue);transform:rotate(-2deg);position:relative}
.title-sticker h1{font-family:var(--font-title);font-size:2.2rem;color:var(--accent-orange);margin:0}
.date-badge{position:absolute;bottom:-14px;right:-18px;background:var(--color-yellow);
  padding:4px 14px;font-family:var(--font-hand);font-size:.9rem;
  box-shadow:2px 2px 3px rgba(0,0,0,.1);transform:rotate(5deg);border:1px solid var(--ink-primary)}
.tape{position:absolute;top:-13px;left:50%;transform:translateX(-50%);width:110px;height:22px;
  background:rgba(255,171,145,.7);opacity:.8}
.section{margin-top:22px}
.section-title{font-family:var(--font-title);font-size:1.3rem;margin-bottom:12px;padding-bottom:6px;
  border-bottom:3px dotted var(--accent-orange);color:var(--ink-primary)}
.stats-wrapper{display:flex;gap:18px;margin-bottom:25px;align-items:stretch}
.stats-grid{flex:2;display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.stamp{background:#fff;padding:14px;box-shadow:4px 4px 0 rgba(0,0,0,.1);
  border:2px solid var(--ink-primary);border-radius:12px;text-align:center}
.stamp-num{font-family:var(--font-title);font-size:1.5rem;color:var(--accent-orange)}
.stamp-label{font-family:var(--font-hand);font-size:.85rem;color:var(--ink-secondary)}
.highlight-section{flex:1;background:var(--color-yellow);padding:18px;border:2px solid var(--ink-primary);
  border-radius:18px;box-shadow:6px 6px 0 var(--color-pink);text-align:center}
.time-big{font-family:var(--font-title);font-size:1.8rem;color:var(--ink-primary);margin:6px 0}
.topic-list{list-style:none}
.topic-item{display:flex;align-items:flex-start;margin-bottom:14px;padding:10px 14px;
  background:#fffdfa;border:1px solid rgba(216,179,107,.25);border-radius:10px}
.topic-num{width:26px;height:26px;background:var(--accent-orange);color:#fff;
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:var(--font-title);font-size:.85rem;flex-shrink:0;margin-right:10px;margin-top:1px}
.topic-title{font-weight:bold;color:var(--ink-primary);font-size:1rem}
.topic-detail{color:#888;font-size:.85rem;margin-top:3px}
.topic-contributors{color:#bbb;font-size:.75rem}
.masonry-grid{column-count:2;column-gap:18px}
.user-card{break-inside:avoid;background:#fffbf5;border:2px solid var(--ink-primary);
  border-radius:12px;padding:16px;margin-bottom:18px;box-shadow:3px 3px 0 rgba(0,0,0,.06)}
.user-card:nth-child(even){transform:rotate(.4deg);border-color:var(--color-blue)}
.user-card:nth-child(3n){transform:rotate(-.4deg);border-color:var(--color-pink)}
.user-header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.u-name{font-family:var(--font-title);font-size:1.1rem;flex:1}
.badges{display:flex;gap:5px;flex-wrap:wrap}
.badge{font-size:.75rem;padding:2px 7px;border-radius:7px;font-family:var(--font-hand)}
.badge.title{background:#fffde7;color:#bf360c;border:1px solid #ffb74d}
.badge.mbti{background:#ede7f6;color:#512da8;border:1px solid #9575cd;font-weight:bold}
.u-reason{font-family:var(--font-hand);font-size:.9rem;color:#555;line-height:1.5;
  background:#f8fbff;border-left:3px solid var(--color-blue);padding:8px 12px;border-radius:4px;margin-top:8px}
.quotes-section{display:flex;flex-direction:column;gap:14px}
.quote-block{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;
  padding:14px 18px;box-shadow:4px 4px 0 var(--color-blue)}
.quote-block:nth-child(even){background:var(--color-yellow);box-shadow:-4px 4px 0 var(--color-pink)}
.q-content{font-family:var(--font-title);font-size:1.05rem;line-height:1.4;margin-bottom:6px}
.q-author{font-family:var(--font-hand);color:#999;font-size:.8rem;text-align:right}
.quality-card{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;
  padding:18px 22px;box-shadow:5px 5px 0 rgba(0,0,0,.05)}
.quality-title{font-family:var(--font-title);font-size:1.2rem;color:var(--accent-orange)}
.quality-dims{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}
.quality-dim{flex:1;min-width:110px;text-align:center}
.dim-bar{height:6px;background:#eee;border-radius:3px;margin:4px 0;overflow:hidden}
.dim-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent-orange),var(--color-pink))}
.dim-name{font-size:.82rem;color:var(--ink-secondary)}
.dim-pct{font-family:var(--font-title);font-size:1.1rem;color:var(--ink-primary)}
.dim-comment{font-size:.78rem;color:#999}
.chart-section{background:#fff;border:2px solid var(--ink-primary);border-radius:14px;padding:16px;margin-top:18px}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin-top:6px}
th{text-align:left;color:var(--ink-secondary);font-family:var(--font-hand);font-size:.82rem;padding:5px 7px}
td{padding:5px 7px;border-bottom:1px solid #f0f0f0}
.footer{margin-top:25px;text-align:center;color:#ccc;font-size:.75rem}
"""


def format_scrapbook_html(
    group_name: str, group_stats: dict,
    topics: dict, titles: dict, quotes: dict, quality: dict,
    *,
    aspects: tuple[str, ...] | None = None,
) -> str:
    from core.group_learning import (
        default_tool_aspects,
        validate_aspect_selection,
    )

    selected_aspects = frozenset(
        default_tool_aspects()
        if aspects is None
        else validate_aspect_selection(aspects)
    )
    now_str = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")

    cards = [
        ("消息总数", group_stats.get("message_count", 0)),
        ("参与人数", group_stats.get("participant_count", 0)),
        ("总字数", group_stats.get("total_characters", 0)),
        ("表情统计", group_stats.get("emoji_count", 0)),
    ]
    if group_stats.get("analysis_window"):
        cards.append(("分析范围", group_stats.get("analysis_window")))
    stamps_html = "".join(
        f'<div class="stamp"><div class="stamp-num">{escape_html(v)}</div><div class="stamp-label">{escape_html(k)}</div></div>'
        for k, v in cards)

    tlist = topics.get("topics", [])
    topic_html = '<ul class="topic-list">' + "".join(
        f'<li class="topic-item"><span class="topic-num">{i}</span><div>'
        f'<div class="topic-title">{escape_html(t.get("topic","?"))}</div>'
        f'<div class="topic-contributors">{"、".join(escape_html(x) for x in t.get("contributors",[])[:5])}</div>'
        f'<div class="topic-detail">{escape_html(t.get("detail",""))}</div></div></li>'
        for i, t in enumerate(tlist, 1)) + '</ul>' if tlist else '<p style="color:#999">暂无显著话题。</p>'

    ulist = titles.get("users", [])
    if ulist:
        cards_list = []
        for u in ulist:
            mbti = u.get("mbti", "").strip()
            badges = f'<span class="badge title">{escape_html(u.get("title",""))}</span>'
            if mbti:
                badges += f'<span class="badge mbti">{escape_html(mbti)}</span>'
            cards_list.append(
                f'<div class="user-card"><div class="user-header">'
                f'<div class="u-name">{escape_html(u.get("user_id","?"))}</div>'
                f'<div class="badges">{badges}</div></div>'
                f'<div class="u-reason">{escape_html(u.get("reason",""))}</div></div>')
        title_html = '<div class="masonry-grid">' + "".join(cards_list) + '</div>'
    else:
        title_html = '<p style="color:#999">暂无活跃用户画像。</p>'

    qlist = quotes.get("quotes", [])
    quote_html = '<div class="quotes-section">' + "".join(
        f'<div class="quote-block"><div class="q-content">{escape_html(q.get("content",""))}</div>'
        f'<div class="q-author">—— {escape_html(q.get("user_id","?"))}</div></div>'
        for q in qlist[:3]) + '</div>' if qlist else '<p style="color:#999">暂无可提取金句。</p>'

    dims = quality.get("dimensions", [])
    if "quality" in selected_aspects and dims:
        dim_cards = []
        for d in dims[:4]:
            pct = safe_percentage(d.get("percentage", 50))
            dim_cards.append(
                f'<div class="quality-dim"><div class="dim-name">{escape_html(d.get("name","?"))}</div>'
                f'<div class="dim-pct">{pct}%</div>'
                f'<div class="dim-bar"><div class="dim-fill" style="width:{pct}%"></div></div>'
                f'<div class="dim-comment">{escape_html(d.get("comment",""))}</div></div>')
        dim_html = '<div class="quality-dims">' + "".join(dim_cards) + '</div>'
        quality_html = (
            f'<div class="section"><div class="section-title">📊 聊天质量锐评</div>'
            f'<div class="quality-card"><div class="quality-title">{escape_html(quality.get("title","群聊质量锐评"))}</div>'
            f'<p style="color:#888;margin:4px 0">{escape_html(quality.get("subtitle",""))}</p>'
            f'{dim_html}<p style="color:#777;font-size:.85rem;margin-top:8px">{escape_html(quality.get("summary",""))}</p></div></div>')
    else:
        quality_html = ""

    topic_section_html = (
        '<div class="section"><div class="section-title">'
        f"📝 话题总结</div>{topic_html}</div>"
        if "topics" in selected_aspects
        else ""
    )
    title_section_html = (
        '<div class="section"><div class="section-title">'
        f"👥 群友画像</div>{title_html}</div>"
        if "titles" in selected_aspects
        else ""
    )
    quote_section_html = (
        '<div class="section"><div class="section-title">'
        f"💬 群聊金句</div>{quote_html}</div>"
        if "quotes" in selected_aspects
        else ""
    )

    hourly = group_stats.get("hourly_counts", {})
    chart_html = ""
    if hourly:
        peak = max(hourly.values()) or 1
        rows = "".join(
            f'<tr><td>{h:02d}:00</td><td>{c}</td><td>'
            f'<div style="display:inline-block;width:{max(10,int(c/peak*180))}px;height:10px;'
            f'background:var(--accent-orange);border-radius:5px;opacity:.7"></div></td></tr>'
            for h, c in sorted(hourly.items()))
        chart_html = (
            f'<div class="chart-section"><div class="section-title">📈 24H 活跃轨迹</div>'
            f'<table><tr><th>时段</th><th>消息数</th><th>活跃度</th></tr>{rows}</table></div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><style>{SCRAPBOOK_CSS}</style></head>
<body class="group-analysis-report">
<div class="container group-analysis-report">
  <div class="header">
    <div class="title-sticker">
      <div class="tape"></div>
      <h1>{escape_html(group_name)} 群聊日报</h1>
      <div class="date-badge">{now_str}</div>
    </div>
  </div>
  <div class="stats-wrapper">
    <div class="stats-grid">{stamps_html}</div>
    <div class="highlight-section">
      <div style="font-family:var(--font-hand);color:var(--ink-secondary)">⭐ Highlight Time</div>
      <div class="time-big">{group_stats.get("most_active_period","00:00")}</div>
      <div style="font-family:var(--font-hand);color:var(--ink-secondary);font-size:.85rem">最活跃时段</div>
    </div>
  </div>
  {chart_html}
  {topic_section_html}
  {title_section_html}
  {quote_section_html}
  {quality_html}
  <div class="footer">Nanobot · {now_str}</div>
</div>
</body>
</html>"""


def format_error_html(title: str, message: str, details: list[str] | None = None) -> str:
    detail_html = ""
    if details:
        detail_html = '<ul style="text-align:left;max-width:400px;margin:20px auto;color:#888">' + \
            "".join(f"<li>{escape_html(d)}</li>" for d in details) + "</ul>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><style>{SCRAPBOOK_CSS}</style></head>
<body class="group-analysis-report">
<div class="container group-analysis-report">
  <div class="header">
    <div class="title-sticker">
      <h1>{escape_html(title)}</h1>
    </div>
  </div>
  <p style="text-align:center;color:#999;padding:20px 40px">{escape_html(message)}</p>
  {detail_html}
</div>
</body>
</html>"""
