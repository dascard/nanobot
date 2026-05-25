"""MemoryDigest v2 渲染器。"""

from __future__ import annotations

from typing import Any


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def render_recall_card(card: dict[str, Any]) -> str:
    """将结构化 recall card 渲染成旧 recall 可 LIKE 命中的单行文本。"""
    keywords = [str(x).strip() for x in _as_list(card.get("keywords")) if str(x).strip()]
    prefix = " / ".join(keywords[:4]) if keywords else str(card.get("type") or "memory")
    text = str(card.get("text") or "").strip()
    return f"[card] {prefix}：{text}".strip()[:120]


def render_digest_levels(meta: dict[str, Any]) -> dict[int, str]:
    """保持 level=0/1/2 旧含义，同时在 v2 meta 中提供结构化字段。"""
    if str(meta.get("status") or "") != "active":
        return {0: "", 1: "", 2: ""}

    preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
    long_summary = meta.get("long_summary") if isinstance(meta.get("long_summary"), dict) else {}
    cards = [x for x in _as_list(meta.get("recall_cards")) if isinstance(x, dict)]

    card_lines = [render_recall_card(card) for card in cards]
    level2 = "\n".join(line for line in card_lines if line)

    preview_lines: list[str] = []
    brief = str(preview.get("brief") or "").strip()
    if brief:
        preview_lines.append(brief)
    keywords = [str(x).strip() for x in _as_list(preview.get("keywords")) if str(x).strip()]
    if keywords:
        preview_lines.append("关键词：" + "、".join(keywords[:12]))
    participants = [str(x).strip() for x in _as_list(preview.get("participants")) if str(x).strip()]
    if participants:
        preview_lines.append("参与者：" + "、".join(participants[:12]))
    if level2:
        preview_lines.append(level2)
    level1 = "\n".join(preview_lines).strip()

    level0_lines = [
        f"# {meta.get('digest_date', '')} {meta.get('session_id', '')} 详细摘要".strip(),
    ]
    topic_flow = str(long_summary.get("topic_flow") or "").strip()
    if topic_flow:
        level0_lines.extend(["", "## 话题脉络", topic_flow])
    for title, key in [
        ("重要细节", "important_details"),
        ("结论", "conclusions"),
        ("待跟进", "open_loops"),
    ]:
        values = [str(x).strip() for x in _as_list(long_summary.get(key)) if str(x).strip()]
        if values:
            level0_lines.extend(["", f"## {title}"])
            level0_lines.extend(f"- {x}" for x in values[:20])
    if level2:
        level0_lines.extend(["", "## 召回卡片", level2])
    level0 = "\n".join(line for line in level0_lines if line is not None).strip()

    return {0: level0, 1: level1, 2: level2}
