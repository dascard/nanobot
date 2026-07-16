"""从 group_analysis 结果提取 GroupMemory 候选。

LLM 负责提候选文本 + confidence_hint，Python 负责写入/去重。
"""

import logging

logger = logging.getLogger("nanobot.tool.group_analysis.memory")


def extract_and_persist(group_id: str, analysis: dict, *,
                         source_meta: dict | None = None) -> dict:
    """映射分析结果 → GroupMemory 候选并写入。"""
    from core.group_memory import upsert

    stats = {"new": 0, "updated": 0, "skipped": 0}
    meta = dict(source_meta or {})
    source_log_ids = meta.pop("source_log_ids", []) if meta else []
    allowed_evidence_ids = {
        int(item)
        for item in source_log_ids
        if str(item).isdigit() and int(item) > 0
    }
    source = str(meta.get("source") or "group_analysis")

    def _candidate_evidence(candidate: dict) -> list[int]:
        raw = candidate.get("evidence_log_ids")
        if not isinstance(raw, list) or not raw or len(raw) > 8:
            return []
        try:
            ids = list(dict.fromkeys(int(item) for item in raw))
        except (TypeError, ValueError):
            return []
        if any(item <= 0 or item not in allowed_evidence_ids for item in ids):
            return []
        return ids

    def _u(mtype, content, hint, evidence_ids):
        return upsert(group_id, mtype, content, confidence_hint=hint,
                      meta=meta, evidence_log_ids=evidence_ids,
                      source=source)

    # topics → memory_type=topic
    for t in analysis.get("topics", {}).get("topics", [])[:5]:
        topic = (t.get("topic") or "").strip()
        detail = (t.get("detail") or "").strip()
        if not topic:
            continue
        evidence_ids = _candidate_evidence(t)
        if not evidence_ids:
            stats["skipped"] += 1
            continue
        content = f"{topic}: {detail}" if detail else topic
        stats[_u("topic", content, 0.65, evidence_ids)] += 1

    # 质量锐评、金句和用户称号只属于日报展示，不能自动升级为长期事实。
    quality = analysis.get("quality", {})
    stats["skipped"] += len((quality.get("dimensions") or [])[:3])
    if str(quality.get("summary") or "").strip():
        stats["skipped"] += 1
    stats["skipped"] += len((analysis.get("quotes", {}).get("quotes") or [])[:3])
    stats["skipped"] += len((analysis.get("titles", {}).get("users") or [])[:5])

    logger.info("[memory] group=%s new=%d updated=%d skipped=%d",
                group_id, stats["new"], stats["updated"], stats["skipped"])
    return stats
