"""群体记忆——写入、查询、合并、衰减。

LLM 提候选 + confidence_hint，Python 决定 NEW/UPDATE/MERGE/ARCHIVE/DECAY。
"""

import hashlib
import json
import logging

from sqlalchemy import and_, or_

from core import database
from core.chat_stream_identity import (
    ChatStreamIdentity,
    identity_storage_aliases,
    resolve_chat_stream_identity,
)
from core.database import GroupMemory
from core.group_learning.prompt_injection import (
    evaluate_group_memory_prompt_injection,
)
from core.time_utils import db_now_naive

logger = logging.getLogger("nanobot.group_memory")

MEMORY_TYPES = {"topic", "slang", "relationship", "style", "event", "preference"}
CONFIDENCE_FLOOR = 0.55
MANUAL_REVIEW_TYPES = {"relationship", "event", "slang", "style"}


class GroupMemoryIdentityConflictError(RuntimeError):
    """GroupMemory canonical 身份与兼容投影发生冲突。"""


def _normalize_evidence_ids(value: list[int] | None) -> list[int]:
    result: list[int] = []
    for item in value or []:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result[:10]


def _normalize_content(content: str) -> str:
    import re
    return re.sub(r"\s+", " ", (content or "").strip().lower()).rstrip("。.!！?？")


def resolve_group_memory_identity(
    group_id: str,
    *,
    platform: str,
) -> ChatStreamIdentity:
    return resolve_chat_stream_identity(
        platform=platform,
        chat_type="group",
        session_id=group_id,
    )


def group_memory_compatibility_projection(
    identity: ChatStreamIdentity,
) -> str:
    if identity.platform == "qq":
        return identity.legacy_runtime_session_id
    return identity.chat_stream_id


def group_memory_identity_filter(identity: ChatStreamIdentity):
    canonical = GroupMemory.chat_stream_id == identity.chat_stream_id
    if identity.platform != "qq":
        return canonical
    legacy_aliases = identity_storage_aliases(
        identity,
        include_raw_group_id=True,
    )
    return or_(
        canonical,
        and_(
            or_(
                GroupMemory.chat_stream_id.is_(None),
                GroupMemory.chat_stream_id == "",
            ),
            GroupMemory.group_id.in_(legacy_aliases),
        ),
    )


def validate_group_memory_row_identity(
    row: GroupMemory,
    identity: ChatStreamIdentity,
) -> None:
    canonical = str(row.chat_stream_id or "")
    if canonical:
        if (
            canonical != identity.chat_stream_id
            or row.group_id
            != group_memory_compatibility_projection(identity)
        ):
            raise GroupMemoryIdentityConflictError(
                "群体记忆身份投影不一致"
            )
        return
    if identity.platform != "qq":
        raise GroupMemoryIdentityConflictError(
            "群体记忆身份投影不一致"
        )
    legacy = resolve_group_memory_identity(
        row.group_id,
        platform="qq",
    )
    if legacy.chat_stream_id != identity.chat_stream_id:
        raise GroupMemoryIdentityConflictError(
            "群体记忆身份投影不一致"
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(_normalize_content(content).encode("utf-8")).hexdigest()[:32]


def _cluster_key(content: str) -> str:
    stopwords = {"的", "了", "是", "在", "和", "也", "都", "就", "不", "会",
                 "很", "有", "这", "那", "群", "里", "经常", "喜欢", "大家",
                 "比较", "觉得", "有点", "非常", "特别"}
    words = content.strip().split()
    keywords = [w for w in words if w.lower() not in stopwords]
    return " ".join(keywords[:3]) if keywords else content.strip()[:20]


def _default_status_and_policy(
    memory_type: str,
    confidence_hint: float,
    evidence_log_ids: list[int] | None,
    *,
    evidence_speaker_count: int = 0,
    require_multi_speaker: bool = False,
) -> tuple[str, str]:
    if memory_type in MANUAL_REVIEW_TYPES:
        return "review", "manual_only"
    if memory_type == "preference" and confidence_hint >= 0.75 and len(evidence_log_ids or []) >= 2:
        return "active", "auto"
    if memory_type == "preference":
        return "review", "auto"
    if memory_type == "topic" and require_multi_speaker and evidence_speaker_count < 2:
        return "review", "auto"
    if confidence_hint >= 0.65 and len(_normalize_evidence_ids(evidence_log_ids)) >= 2:
        return "active", "auto"
    return "review", "auto"


def upsert(
    group_id: str, memory_type: str, content: str,
    *, platform: str = "qq",
    evidence_log_ids: list[int] | None = None,
    confidence_hint: float = 0.5, meta: dict | None = None,
    source: str = "group_analysis",
    cluster_key: str = "",
) -> str:
    """写入或更新一条群体记忆。返回 new/updated/skipped。

    去重逻辑：同 group_id + memory_type + content_hash 不重复插入。
    """
    if memory_type not in MEMORY_TYPES:
        return "skipped"

    identity = resolve_group_memory_identity(
        group_id,
        platform=platform,
    )
    evidence_log_ids = _normalize_evidence_ids(evidence_log_ids)
    incoming_meta = dict(meta or {})

    db = database.SessionLocal()
    try:
        ch = _content_hash(content)
        existing = (
            db.query(GroupMemory)
            .filter(and_(
                group_memory_identity_filter(identity),
                GroupMemory.memory_type == memory_type,
                GroupMemory.content_hash == ch,
            ))
            .first()
        )
        if existing:
            validate_group_memory_row_identity(existing, identity)
            existing.chat_stream_id = identity.chat_stream_id
            existing.group_id = group_memory_compatibility_projection(
                identity
            )
            now = db_now_naive()
            existing.last_seen = now
            existing.decay_score = min(1.0, existing.decay_score + 0.05)
            previous_ids = _safe_evidence_ids(existing.evidence_log_ids_json)
            merged_ids = _merge_evidence(existing, evidence_log_ids)
            existing.evidence_count = len(merged_ids)
            if len(merged_ids) > len(previous_ids):
                existing.confidence = min(1.0, existing.confidence + confidence_hint * 0.1)
            merged_meta = {**_safe_meta(existing.meta_json), **incoming_meta}
            previous_speakers = _safe_meta(existing.meta_json).get("evidence_speakers")
            incoming_speakers = incoming_meta.get("evidence_speakers")
            speakers = sorted({
                str(item).strip()
                for values in (previous_speakers, incoming_speakers)
                if isinstance(values, list)
                for item in values
                if str(item).strip()
            })
            if speakers or merged_meta.get("consensus_gate") == "multi_speaker":
                merged_meta["evidence_speakers"] = speakers
                merged_meta["evidence_speaker_count"] = len(speakers)
            next_status, next_policy = _default_status_and_policy(
                memory_type,
                existing.confidence,
                merged_ids,
                evidence_speaker_count=int(merged_meta.get("evidence_speaker_count") or 0),
                require_multi_speaker=merged_meta.get("consensus_gate") == "multi_speaker",
            )
            existing.inject_policy = next_policy
            if merged_meta.get("consensus_gate") == "multi_speaker" and next_status == "review":
                existing.status = "review"
            elif existing.status == "review" and next_status == "active":
                existing.status = "active"
            elif existing.status == "archived" and existing.confidence >= CONFIDENCE_FLOOR:
                existing.status = next_status
            if merged_meta:
                existing.meta_json = json.dumps(merged_meta, ensure_ascii=False)
            if source and source != existing.source:
                existing.source = source
            db.commit()
            return "updated"

        ck = cluster_key or _cluster_key(content)
        status, inject_policy = _default_status_and_policy(
            memory_type,
            confidence_hint,
            evidence_log_ids,
            evidence_speaker_count=int(incoming_meta.get("evidence_speaker_count") or 0),
            require_multi_speaker=incoming_meta.get("consensus_gate") == "multi_speaker",
        )
        now = db_now_naive()
        entry = GroupMemory(
            chat_stream_id=identity.chat_stream_id,
            group_id=group_memory_compatibility_projection(identity),
            memory_type=memory_type,
            content=content.strip(), content_hash=ch,
            cluster_key=ck,
            evidence_log_ids_json=json.dumps(evidence_log_ids),
            confidence=confidence_hint, evidence_count=len(evidence_log_ids),
            first_seen=now, last_seen=now,
            decay_score=1.0, source=source,
            status=status,
            inject_policy=inject_policy,
            meta_json=json.dumps(incoming_meta, ensure_ascii=False),
        )
        db.add(entry)
        db.commit()
        return "new"
    except Exception as e:
        db.rollback()
        logger.warning("[group_memory] upsert failed: %s", e)
        return "skipped"
    finally:
        db.close()


def query_active(
    group_id: str, *, platform: str = "qq",
    memory_type: str | None = None,
    min_confidence: float = 0.5, limit: int = 20,
) -> list[dict]:
    """查询活跃记忆——用于注入 GroupProfile。"""
    identity = resolve_group_memory_identity(
        group_id,
        platform=platform,
    )
    db = database.SessionLocal()
    try:
        q = db.query(GroupMemory).filter(and_(
            group_memory_identity_filter(identity),
            GroupMemory.status == "active",
            GroupMemory.confidence >= min_confidence,
        ))
        if memory_type:
            q = q.filter(GroupMemory.memory_type == memory_type)
        rows = (
            q.order_by(GroupMemory.confidence.desc(),
                       GroupMemory.last_seen.desc())
            .limit(limit).all()
        )
        for row in rows:
            validate_group_memory_row_identity(row, identity)
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def apply_decay(group_id: str, *, platform: str = "qq"):
    """对活跃记忆降 decay_score。<0.2 → archived。"""
    identity = resolve_group_memory_identity(
        group_id,
        platform=platform,
    )
    db = database.SessionLocal()
    try:
        rows = (
            db.query(GroupMemory)
            .filter(and_(group_memory_identity_filter(identity),
                         GroupMemory.status == "active"))
            .all()
        )
        for r in rows:
            validate_group_memory_row_identity(r, identity)
            if r.decay_score <= 0.2:
                r.status = "archived"
            else:
                r.decay_score = max(0.0, r.decay_score - 0.02)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("[group_memory] decay failed: %s", e)
    finally:
        db.close()


def query_injectable(
    group_id: str,
    *,
    platform: str = "qq",
    limit: int = 50,
) -> list[dict]:
    """查询可注入的 GroupMemory——与 build_profile 口径一致。"""
    return [
        m
        for m in query_active(
            group_id,
            platform=platform,
            min_confidence=CONFIDENCE_FLOOR,
            limit=limit,
        )
        if should_inject(m)
    ]


def build_profile_from_memories(memories: list[dict]) -> dict:
    """从已筛选的 memory 列表生成 profile——避免重复查库。"""
    by_type: dict[str, list[dict]] = {}
    for m in memories:
        by_type.setdefault(m["memory_type"], []).append(m)

    def _top(kind: str, n: int) -> list[str]:
        return [m["content"] for m in by_type.get(kind, [])[:n]]

    return {
        "common_topics": _top("topic", 5),
        "slang": {
            m["content"]: _safe_meta(m.get("meta_json", "{}")).get("meaning", "")
            for m in by_type.get("slang", [])[:8]
        },
        "style": _top("style", 5),
        "events": _top("event", 3),
        "relationships": _top("relationship", 5),
        "bot_preferences": _top("preference", 3),
    }


def build_profile(group_id: str, *, platform: str = "qq") -> dict:
    """从 GroupMemory 动态生成 GroupProfile JSON。"""
    all_mem = query_injectable(group_id, platform=platform)
    return build_profile_from_memories(all_mem)


def build_profile_with_evidence(
    group_id: str,
    db,
    *,
    platform: str = "qq",
) -> tuple[dict, dict[str, list[str]]]:
    """与 build_profile 相同，但额外返回 evidence_map: content → evidence 原文摘要列表。

    调用方负责管理 db session 生命周期。
    """
    from core.database import ChatLog

    all_mem = [
        m
        for m in query_active(
            group_id,
            platform=platform,
            min_confidence=CONFIDENCE_FLOOR,
        )
        if should_inject(m)
    ]
    by_type: dict[str, list[dict]] = {}
    for m in all_mem:
        by_type.setdefault(m["memory_type"], []).append(m)

    def _top(kind: str, n: int) -> list[str]:
        return [m["content"] for m in by_type.get(kind, [])[:n]]

    # 收集所有 memory 的 evidence_log_ids
    evidence_ids: set[int] = set()
    content_to_ids: dict[str, list[int]] = {}
    for m in all_mem:
        ids = _safe_evidence_ids(m.get("evidence_log_ids_json", ""))
        content_to_ids[m["content"]] = ids
        evidence_ids.update(ids)

    # 批量查 ChatLog 获取证据原文摘要
    evidence_map: dict[str, list[str]] = {}
    if evidence_ids and db is not None:
        rows = db.query(ChatLog).filter(ChatLog.id.in_(list(evidence_ids))).all()
        id_to_text: dict[int, str] = {}
        for row in rows:
            text = (row.content or "").strip()
            if text:
                id_to_text[row.id] = text[:120]
        for content, ids in content_to_ids.items():
            snippets = [id_to_text[i] for i in ids if i in id_to_text]
            if snippets:
                evidence_map[content] = snippets[:3]

    profile = {
        "common_topics": _top("topic", 5),
        "slang": {m["content"]: _safe_meta(m.get("meta_json", "{}")).get("meaning", "")
                  for m in by_type.get("slang", [])[:8]},
        "style": _top("style", 5),
        "events": _top("event", 3),
        "relationships": _top("relationship", 5),
        "bot_preferences": _top("preference", 3),
    }
    return profile, evidence_map


def _safe_evidence_ids(raw: str) -> list[int]:
    try:
        ids = json.loads(raw or "[]")
        return [int(x) for x in ids if str(x).isdigit()][:10]
    except (json.JSONDecodeError, TypeError):
        return []


def _merge_evidence(memory, new_ids: list[int]) -> list[int]:
    try:
        existing = json.loads(memory.evidence_log_ids_json or "[]")
        merged = _normalize_evidence_ids(existing + new_ids)
        memory.evidence_log_ids_json = json.dumps(merged)
        return merged
    except (json.JSONDecodeError, TypeError):
        merged = _normalize_evidence_ids(new_ids)
        memory.evidence_log_ids_json = json.dumps(merged)
        return merged


def _safe_meta(raw: str) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def should_inject(memory: dict) -> bool:
    """兼容 façade：复用正式群体记忆的唯一 Prompt 注入 Policy。"""

    return evaluate_group_memory_prompt_injection(memory).eligible


def _row_to_dict(r: GroupMemory) -> dict:
    return {
        "id": r.id,
        "chat_stream_id": r.chat_stream_id or "",
        "group_id": r.group_id,
        "memory_type": r.memory_type, "content": r.content,
        "content_hash": r.content_hash or "",
        "cluster_key": r.cluster_key or "",
        "confidence": r.confidence, "evidence_count": r.evidence_count,
        "decay_score": r.decay_score,
        "first_seen": r.first_seen.strftime("%Y-%m-%d") if r.first_seen else "",
        "last_seen": r.last_seen.strftime("%Y-%m-%d") if r.last_seen else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
        "status": r.status,
        "inject_policy": getattr(r, "inject_policy", "auto") or "auto",
        "disabled_reason": getattr(r, "disabled_reason", "") or "",
        "rejected_reason": getattr(r, "rejected_reason", "") or "",
        "merged_into_id": getattr(r, "merged_into_id", None),
        "last_injected_at": r.last_injected_at.strftime("%Y-%m-%d %H:%M") if getattr(r, "last_injected_at", None) else "",
        "injected_count": getattr(r, "injected_count", 0) or 0,
        "source": r.source or "group_analysis",
        "meta_json": r.meta_json,
        "evidence_log_ids_json": r.evidence_log_ids_json,
        "approval_source": r.approval_source or "",
        "governance_mode": r.governance_mode or "",
        "approved_content_hash": r.approved_content_hash or "",
        "model_review_run_id": r.model_review_run_id or "",
        "model_contract_version": r.model_contract_version or "",
        "human_reviewer_id": r.human_reviewer_id or "",
        "human_reviewed_at": r.human_reviewed_at,
        "human_action": r.human_action or "",
        "conflict_group_id": r.conflict_group_id or "",
    }
