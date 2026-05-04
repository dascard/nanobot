"""从 group_analysis 结果提取 GroupMemory 候选。

LLM 负责提候选文本 + confidence_hint，Python 负责写入/去重。
"""

import logging

logger = logging.getLogger("nanobot.tool.group_analysis.memory")


def extract_and_persist(group_id: str, analysis: dict) -> dict:
    """映射分析结果 → GroupMemory 候选并写入。返回 {"new": N, "updated": N}。"""
    from core.group_memory import upsert

    stats = {"new": 0, "updated": 0}

    # topics → memory_type=topic
    for t in analysis.get("topics", {}).get("topics", [])[:5]:
        topic = (t.get("topic") or "").strip()
        detail = (t.get("detail") or "").strip()
        if not topic:
            continue
        content = f"{topic}: {detail}" if detail else topic
        r = upsert(group_id, "topic", content, confidence_hint=0.65)
        stats[r] = stats.get(r, 0) + 1

    # quality dimensions → style
    for d in analysis.get("quality", {}).get("dimensions", [])[:3]:
        name = (d.get("name") or "").strip()
        comment = (d.get("comment") or "").strip()
        if not name:
            continue
        content = f"「{name}」: {comment}" if comment else name
        r = upsert(group_id, "style", content, confidence_hint=0.60)
        stats[r] = stats.get(r, 0) + 1

    summary = (analysis.get("quality", {}).get("summary") or "").strip()
    if summary and len(summary) > 10:
        r = upsert(group_id, "style", f"整体风格: {summary}", confidence_hint=0.55)
        stats[r] = stats.get(r, 0) + 1

    # quotes → event
    for q in analysis.get("quotes", {}).get("quotes", [])[:3]:
        content = (q.get("content") or "").strip()
        user = (q.get("user_id") or "").strip()
        if not content:
            continue
        label = f"金句({user}): {content}" if user else f"金句: {content}"
        r = upsert(group_id, "event", label, confidence_hint=0.50)
        stats[r] = stats.get(r, 0) + 1

    # user titles → relationship
    for u in analysis.get("titles", {}).get("users", [])[:5]:
        uid = (u.get("user_id") or "").strip()
        title = (u.get("title") or "").strip()
        reason = (u.get("reason") or "").strip()
        if not uid:
            continue
        content = f"{uid}: {title}" if title else uid
        if reason:
            content += f"（{reason}）"
        r = upsert(group_id, "relationship", content, confidence_hint=0.55)
        stats[r] = stats.get(r, 0) + 1

    logger.info("[memory] group=%s new=%d updated=%d",
                group_id, stats.get("new", 0), stats.get("updated", 0))
    return stats
