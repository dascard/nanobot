"""Skill Registry 描述索引与请求级最小选择。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models.semantic import SemanticIndexItem
from core.registry import RegistryBuilder, RegistrySnapshot
from core.semantic.adapters import SemanticChunk
from core.semantic.indexer import source_hash_for_chunk, upsert_semantic_chunks
from core.semantic.retriever import fts_recall_hits, lexical_overlap_score
from core.semantic.schema import ensure_semantic_schema
from core.skills.contracts import RuntimeSkillLock, RuntimeSkillLockEntry


SKILL_DESCRIPTION_SOURCE_TYPE = "skill_description"
SKILL_DESCRIPTION_INDEX_VERSION = "skill-description:registry-v1"
SKILL_DIRECT_SELECTION_LIMIT = 4
SKILL_TOTAL_SELECTION_LIMIT = 16
SKILL_LEXICAL_MATCH_THRESHOLD = 0.20


@dataclass(frozen=True, slots=True)
class SkillRegistryDescriptor:
    """只投影检索和治理字段，绝不包含 SKILL.md 正文或资源。"""

    entry: RuntimeSkillLockEntry
    registry_namespace: str = field(default="skill", init=False)

    @property
    def registry_id(self) -> str:
        return self.entry.name

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return tuple(
            dependency.rsplit("@", 1)[0]
            for dependency in self.entry.dependencies
        )

    @property
    def index_text(self) -> str:
        return "\n".join(
            (
                self.entry.name,
                self.entry.description,
                " ".join(self.entry.capability_tags),
                " ".join(self.entry.allowed_tools),
                self.entry.compatibility,
            )
        )

    def registry_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "package_id": self.entry.package_id,
                "name": self.entry.name,
                "version": self.entry.version,
                "scope": self.entry.scope.value,
                "description": self.entry.description,
                "capability_tags": self.entry.capability_tags,
                "applies_to": self.entry.applies_to,
                "allowed_tools": self.entry.allowed_tools,
                "dependencies": self.entry.dependencies,
                "required_permissions": self.entry.required_permissions,
                "body_prompt_tokens": self.entry.body_prompt_tokens,
                "catalog_prompt_tokens": self.entry.catalog_prompt_tokens,
                "content_sha256": self.entry.content_sha256,
                "bundle_sha256": self.entry.bundle_sha256,
                "source_kind": self.entry.source_kind,
            }
        )


@dataclass(frozen=True, slots=True)
class SkillSelectionScore:
    name: str
    score: float
    matched_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillSelectionResult:
    registry: RegistrySnapshot[SkillRegistryDescriptor]
    selected_lock: RuntimeSkillLock
    scores: tuple[SkillSelectionScore, ...]
    retrieval_mode: str
    indexed_count: int


def build_skill_registry(
    lock: RuntimeSkillLock,
) -> RegistrySnapshot[SkillRegistryDescriptor]:
    """把请求可见精确锁冻结为不可隐式覆盖的 Skill Registry。"""

    builder = RegistryBuilder[SkillRegistryDescriptor]("skill")
    for entry in lock.entries:
        builder.register(SkillRegistryDescriptor(entry))
    return builder.freeze()


def _description_chunk(
    descriptor: SkillRegistryDescriptor,
) -> SemanticChunk:
    entry = descriptor.entry
    return SemanticChunk(
        source_type=SKILL_DESCRIPTION_SOURCE_TYPE,
        source_id=entry.package_id,
        source_sub_id="descriptor-v1",
        title=entry.name,
        text=entry.description,
        lexical_text=descriptor.index_text,
        embedding_text=descriptor.index_text,
        metadata={
            "document_id": entry.name,
            "chunk_id": "descriptor-v1",
            "skill_name": entry.name,
            "skill_version": entry.version,
            "scope": entry.scope.value,
            "capability_tags": list(entry.capability_tags),
            "applies_to": list(entry.applies_to),
            "allowed_tools": list(entry.allowed_tools),
            "dependencies": list(entry.dependencies),
            "required_permissions": list(entry.required_permissions),
            "body_prompt_tokens": entry.body_prompt_tokens,
            "catalog_prompt_tokens": entry.catalog_prompt_tokens,
            "source_revision": entry.bundle_sha256,
        },
        visibility="recall",
        quality_score=1.0,
        trust_level="high",
        source_prior=0.8,
    )


def synchronize_skill_description_index(
    db: Session,
    registry: RegistrySnapshot[SkillRegistryDescriptor],
) -> int:
    """幂等投影当前 Registry 描述；正文和 owner scope_key 不进入共享索引。"""

    if not registry.ordered_ids:
        return 0
    ensure_semantic_schema(db.bind)
    chunks = [_description_chunk(descriptor) for descriptor in registry]
    package_ids = [chunk.source_id for chunk in chunks]
    existing = {
        str(row.source_id): row
        for row in (
            db.query(SemanticIndexItem)
            .filter(
                SemanticIndexItem.source_type == SKILL_DESCRIPTION_SOURCE_TYPE,
                SemanticIndexItem.source_id.in_(package_ids),
                SemanticIndexItem.source_sub_id == "descriptor-v1",
                SemanticIndexItem.index_version
                == SKILL_DESCRIPTION_INDEX_VERSION,
            )
            .all()
        )
    }
    changed = [
        chunk
        for chunk in chunks
        if (
            (row := existing.get(chunk.source_id)) is None
            or str(row.status) != "active"
            or str(row.source_hash) != source_hash_for_chunk(chunk)
        )
    ]
    if not changed:
        return 0
    upsert_semantic_chunks(
        db,
        changed,
        index_version=SKILL_DESCRIPTION_INDEX_VERSION,
        embedding_enabled=False,
        commit=False,
        ensure_schema=False,
    )
    return len(changed)


def _applicability(runtime_chat_type: str) -> frozenset[str]:
    normalized = str(runtime_chat_type or "").strip().lower()
    values = {normalized}
    if normalized.startswith("private"):
        values.update({"private", "chat"})
    elif normalized == "group":
        values.update({"group", "chat"})
    elif normalized in {"scheduled", "task"}:
        values.update({"scheduled", "task"})
    return frozenset(values)


def _is_applicable(
    descriptor: SkillRegistryDescriptor,
    runtime_chat_type: str,
) -> bool:
    declared = frozenset(descriptor.entry.applies_to)
    return "all" in declared or bool(declared & _applicability(runtime_chat_type))


def _dependency_closure(
    registry: RegistrySnapshot[SkillRegistryDescriptor],
    root_name: str,
) -> frozenset[str]:
    pending = [root_name]
    selected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(registry.require(name).registry_dependencies)
    return frozenset(selected)


def select_skills_for_query(
    db: Session,
    *,
    lock: RuntimeSkillLock,
    query: str,
    runtime_chat_type: str,
    direct_limit: int = SKILL_DIRECT_SELECTION_LIMIT,
) -> SkillSelectionResult:
    """用共享 RAG 描述索引选择少量 Skill，并自动补齐精确依赖。"""

    registry = build_skill_registry(lock)
    normalized_query = str(query or "").strip()
    if not normalized_query or not registry.ordered_ids:
        return SkillSelectionResult(
            registry=registry,
            selected_lock=lock.select(()),
            scores=(),
            retrieval_mode="empty_query",
            indexed_count=0,
        )

    indexed_count = 0
    retrieval_mode = "fts_lexical"
    try:
        with db.begin_nested():
            indexed_count = synchronize_skill_description_index(db, registry)
    except SQLAlchemyError:
        retrieval_mode = "lexical_fallback"

    applicable = {
        descriptor.entry.package_id: descriptor
        for descriptor in registry
        if _is_applicable(descriptor, runtime_chat_type)
    }
    if not applicable:
        return SkillSelectionResult(
            registry=registry,
            selected_lock=lock.select(()),
            scores=(),
            retrieval_mode=retrieval_mode,
            indexed_count=indexed_count,
        )

    fts_by_package: dict[str, float] = {}
    if retrieval_mode == "fts_lexical":
        hits = fts_recall_hits(
            db,
            normalized_query,
            source_types={SKILL_DESCRIPTION_SOURCE_TYPE},
            source_ids=set(applicable),
            limit=max(32, len(applicable)),
            ensure_schema=False,
        )
        if hits:
            rows = (
                db.query(SemanticIndexItem)
                .filter(
                    SemanticIndexItem.id.in_([hit.item_id for hit in hits]),
                    SemanticIndexItem.source_id.in_(sorted(applicable)),
                )
                .all()
            )
            package_by_item = {int(row.id): str(row.source_id) for row in rows}
            for hit in hits:
                package_id = package_by_item.get(hit.item_id)
                if package_id:
                    fts_by_package[package_id] = max(
                        fts_by_package.get(package_id, 0.0),
                        float(hit.lexical_score),
                    )

    lowered_query = normalized_query.lower()
    ranked: list[tuple[float, SkillRegistryDescriptor, tuple[str, ...]]] = []
    for package_id, descriptor in applicable.items():
        matched_by: list[str] = []
        lexical = lexical_overlap_score(normalized_query, descriptor.index_text)
        score = lexical
        if lexical >= SKILL_LEXICAL_MATCH_THRESHOLD:
            matched_by.append("lexical")
        fts_score = fts_by_package.get(package_id, 0.0)
        # FTS5 使用 OR 召回，单个常见二元词只能作为候选信号；仍要求
        # 描述/标签具有最低词面重叠，避免“执行一次研究”误选一次性提醒。
        if fts_score > 0 and lexical >= SKILL_LEXICAL_MATCH_THRESHOLD:
            score = max(score, fts_score)
            matched_by.append("fts")
        if descriptor.entry.name in lowered_query:
            score = 1.0
            matched_by.append("name")
        if any(
            len(tag) >= 2 and tag.lower() in lowered_query
            for tag in descriptor.entry.capability_tags
        ):
            score = max(score, 0.9)
            matched_by.append("capability")
        if matched_by:
            ranked.append(
                (
                    score,
                    descriptor,
                    tuple(sorted(set(matched_by))),
                )
            )
    ranked.sort(key=lambda item: (-item[0], item[1].registry_id))

    selected_names: set[str] = set()
    selected_scores: list[SkillSelectionScore] = []
    applicable_names = {
        descriptor.registry_id for descriptor in applicable.values()
    }
    normalized_limit = max(1, min(int(direct_limit), SKILL_DIRECT_SELECTION_LIMIT))
    for score, descriptor, matched_by in ranked[:normalized_limit]:
        closure = _dependency_closure(registry, descriptor.registry_id)
        if not closure.issubset(applicable_names):
            continue
        if len(selected_names | set(closure)) > SKILL_TOTAL_SELECTION_LIMIT:
            continue
        selected_names.update(closure)
        selected_scores.append(
            SkillSelectionScore(descriptor.registry_id, score, matched_by)
        )

    ordered_names = tuple(
        name for name in registry.ordered_ids if name in selected_names
    )
    return SkillSelectionResult(
        registry=registry,
        selected_lock=lock.select(ordered_names),
        scores=tuple(selected_scores),
        retrieval_mode=retrieval_mode,
        indexed_count=indexed_count,
    )


__all__ = [
    "SKILL_DESCRIPTION_INDEX_VERSION",
    "SKILL_DESCRIPTION_SOURCE_TYPE",
    "SkillRegistryDescriptor",
    "SkillSelectionResult",
    "SkillSelectionScore",
    "build_skill_registry",
    "select_skills_for_query",
    "synchronize_skill_description_index",
]
