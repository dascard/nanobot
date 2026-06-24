"""
Persona 预处理层——LLM 候选提取 + Python 状态机去重/聚类/计数/衰减/冲突检测。

职责分离：
- LLM: 只输出 candidates[{text, evidence, domain}]（语义理解，高 recall）
- Python: 状态机全权决定 NEW/UPDATE/ARCHIVE、去重、计数、置信度、冲突
"""

import hashlib
import json
import logging
import math
import os
import threading as _threading
import warnings
from datetime import datetime
from typing import Any

import numpy as np

# 无 GPU 环境：抑制 CUDA 初始化 warning（PyTorch 会自动降级到 CPU）
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
warnings.filterwarnings("ignore", message=".*CUDA initialization.*")
from sqlalchemy.orm import Session  # noqa: E402

from core.database import ChatLog, PersonaFact, PersonaBehavior  # noqa: E402
from core.persona_candidate_prompt import (  # noqa: E402
    CANDIDATE_EXTRACTION_SYSTEM_PROMPT as CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
    build_candidate_extraction_prompt as build_candidate_extraction_prompt,
    filter_user_messages as filter_user_messages,
    format_candidate_logs as format_candidate_logs,
)

logger = logging.getLogger("nanobot.persona")

# ── 常量 ──
MATCH_THRESHOLD = 0.65          # cosine 相似度匹配阈值（BGE 相近语义 0.60-0.70，不同语义 0.34-0.59）
CONTRADICTION_THRESHOLD = 0.25  # 低于此值视为潜在矛盾（语义反向）
MAX_FACTS_PER_USER = 100        # 每人最多保留画像条数
CANONICAL_MIN_LENGTH_RATIO = 0.7  # canonical 保留信息密度比

STORABLE_MEMORY_TYPES = {
    "stable_preference",
    "interaction_style",
    "stable_background",
    "long_term_project",
}
REJECT_MEMORY_TYPES = {
    "temporary_task",
    "tool_contract",
    "complaint",
    "test_noise",
}
AUTO_INJECT_MEMORY_TYPES = {
    "stable_preference",
    "interaction_style",
    "stable_background",
    "long_term_project",
}

# 衰减参数
PREFERENCE_ARCHIVE_DAYS = 90    # 偏好 90 天无更新→归档
BEHAVIOR_ARCHIVE_DAYS = 45      # 行为 45 天无更新→归档
PREFERENCE_ARCHIVE_SCORE = 0.1  # 偏好置信度低于此值→归档
BEHAVIOR_ARCHIVE_SCORE = 0.15   # 行为置信度低于此值→归档

# ── 懒加载 ──
_embedder = None
_embedder_lock = _threading.Lock()
_nli_pipeline = None

_EMBEDDER_MODEL = "BAAI/bge-base-zh-v1.5"
_NLI_MODEL = "roberta-large-mnli"


def _get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                # 在 torch 首次导入前再次确保 CUDA warning 被抑制
                warnings.filterwarnings("ignore", message=".*CUDA initialization.*")
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading embedding model: {_EMBEDDER_MODEL}")
                _embedder = SentenceTransformer(_EMBEDDER_MODEL)
    return _embedder


def _get_nli():
    """懒加载 NLI 模型（用于矛盾检测）。加载失败返回 None，降级为 cosine 检测。"""
    global _nli_pipeline
    if _nli_pipeline is None:
        try:
            from transformers import pipeline
            logger.info(f"Loading NLI model: {_NLI_MODEL}")
            _nli_pipeline = pipeline("text-classification", model=_NLI_MODEL)
        except Exception as e:
            logger.warning(f"NLI model unavailable, falling back to cosine contradiction: {e}")
            _nli_pipeline = False
    return _nli_pipeline if _nli_pipeline is not False else None


# ── 工具函数 ──

def embed_text(text: str) -> np.ndarray:
    """生成文本的 embedding 向量（已归一化）。"""
    return _get_embedder().encode(text, normalize_embeddings=True)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个归一化向量的余弦相似度。"""
    return float(np.dot(a, b))


def _to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _normalize_content(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split()).rstrip("。.!！?？")


def content_hash(value: str) -> str:
    norm = _normalize_content(value)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _safe_json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_json_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── 状态机核心 ──

class PersonaStateMachine:
    """画像状态机——去重/聚类/计数/衰减/冲突检测全部由 Python 确定性逻辑完成。"""

    def __init__(self, db: Session, user_id: str):
        self.db = db
        self.user_id = user_id

    # ── 公共入口 ──

    def process_candidates(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """处理 LLM 提取的候选列表。

        新画像候选 → persona_facts 表（治理字段 + cluster 去重）
        旧 behavior 候选 → persona_behaviors 表（兼容旧链路）

        返回: {"created": int, "merged": int, "conflicts": int, "rejected": int, "reject_reasons": dict}
        """
        stats: dict[str, Any] = {
            "created": 0,
            "merged": 0,
            "conflicts": 0,
            "rejected": 0,
            "reject_reasons": {},
        }
        if not candidates:
            return stats
        now = datetime.now()

        # 一次性预取所有现有 facts（避免 N+1）
        existing_facts = (
            self.db.query(PersonaFact)
            .filter(PersonaFact.user_id == self.user_id)
            .order_by(PersonaFact.last_seen.desc())
            .limit(MAX_FACTS_PER_USER)
            .all()
        )
        existing_behaviors = (
            self.db.query(PersonaBehavior)
            .filter(PersonaBehavior.user_id == self.user_id)
            .order_by(PersonaBehavior.last_observed.desc())
            .limit(MAX_FACTS_PER_USER)
            .all()
        )

        for c in candidates:
            text = (c.get("text") or "").strip()
            if not text:
                self._reject(stats, "empty_text")
                continue

            memory_type = self._memory_type(c)
            should_store = c.get("should_store", True)
            if isinstance(should_store, str):
                should_store = should_store.strip().lower() not in {"false", "0", "no", "否"}
            if should_store is False or memory_type in REJECT_MEMORY_TYPES:
                self._reject(stats, str(c.get("reject_reason") or memory_type or "not_storable"))
                continue

            if memory_type not in STORABLE_MEMORY_TYPES:
                self._reject(stats, "unsupported_memory_type")
                continue

            evidence_ids_raw = c.get("evidence_log_ids") or c.get("source_log_ids") or []
            evidence_ids, evidence_ids_valid = self._validate_evidence_log_ids(evidence_ids_raw)
            if evidence_ids_raw and not evidence_ids_valid:
                self._reject(stats, "invalid_evidence_log_ids")
                continue

            try:
                vec = embed_text(text)
            except Exception as e:
                logger.warning(f"Embedding failed for text hash={hashlib.sha256(text.encode()).hexdigest()[:12]}: {e}")
                continue

            domain = c.get("domain", "general")
            evidence = c.get("evidence_quote") or c.get("evidence", "")
            confidence_hint = str(c.get("confidence_hint") or "medium").strip().lower()
            should_inject = c.get("should_inject", False)
            if isinstance(should_inject, str):
                should_inject = should_inject.strip().lower() not in {"false", "0", "no", "否"}
            status, inject_policy = self._initial_governance(
                memory_type=memory_type,
                confidence_hint=confidence_hint,
                should_inject=bool(should_inject),
                evidence_ids=evidence_ids,
            )
            candidate_meta = {
                "confidence_hint": confidence_hint,
                "evidence_quote": evidence,
                "reason": c.get("reason", ""),
                "reject_reason": c.get("reject_reason", ""),
            }
            ctype = c.get("type", "preference")

            if not c.get("memory_type") and ctype == "behavior":
                self._upsert_behavior(text, evidence, domain, vec, existing_behaviors, now)
                stats["created"] += 1
            else:
                hash_match = self._find_content_hash_match(existing_facts, memory_type, text)
                if hash_match is not None:
                    self._merge_fact(
                        hash_match, text, evidence, vec, now,
                        memory_type=memory_type,
                        confidence_hint=confidence_hint,
                        evidence_log_ids=evidence_ids,
                        should_inject=bool(should_inject),
                        candidate_meta=candidate_meta,
                    )
                    stats["merged"] += 1
                    continue

                matches = self._find_matches(vec, existing_facts)
                if not matches:
                    new_fact = self._create_fact(
                        text, evidence, domain, vec, now,
                        memory_type=memory_type,
                        confidence_hint=confidence_hint,
                        evidence_log_ids=evidence_ids,
                        status=status,
                        inject_policy=inject_policy,
                        candidate_meta=candidate_meta,
                    )
                    existing_facts.insert(0, new_fact)
                    stats["created"] += 1
                elif len(matches) == 1:
                    self._merge_fact(
                        matches[0][0], text, evidence, vec, now,
                        memory_type=memory_type,
                        confidence_hint=confidence_hint,
                        evidence_log_ids=evidence_ids,
                        should_inject=bool(should_inject),
                        candidate_meta=candidate_meta,
                    )
                    stats["merged"] += 1
                else:
                    self._resolve_conflicts(
                        matches, text, evidence, domain, vec, now,
                        memory_type=memory_type,
                        confidence_hint=confidence_hint,
                        evidence_log_ids=evidence_ids,
                        should_inject=bool(should_inject),
                        candidate_meta=candidate_meta,
                    )
                    stats["conflicts"] += 1

        self._apply_decay(now)
        self._prune_excess()
        self.db.commit()

        logger.info(
            f"PersonaStateMachine: user={self.user_id}, "
            f"created={stats['created']}, merged={stats['merged']}, conflicts={stats['conflicts']}"
        )
        return stats

    @staticmethod
    def _reject(stats: dict[str, Any], reason: str) -> None:
        stats["rejected"] += 1
        reasons = stats.setdefault("reject_reasons", {})
        reasons[reason] = int(reasons.get(reason, 0)) + 1

    @staticmethod
    def _memory_type(candidate: dict[str, Any]) -> str:
        value = str(candidate.get("memory_type") or "").strip()
        if value:
            return value
        legacy_type = str(candidate.get("type") or "preference").strip()
        if legacy_type == "behavior":
            return "interaction_style"
        if legacy_type == "trait":
            return "stable_background"
        return "stable_preference"

    def _validate_evidence_log_ids(self, value: Any) -> tuple[list[int], bool]:
        if value in (None, "", []):
            return [], True
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return [], False
        if not isinstance(value, list):
            return [], False

        ids: list[int] = []
        for item in value:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                return [], False
        if not ids:
            return [], True
        rows = (
            self.db.query(ChatLog.id)
            .filter(ChatLog.id.in_(ids), ChatLog.user_id == self.user_id, ChatLog.role == "user")
            .all()
        )
        valid = {int(row[0]) for row in rows}
        ordered = [item for item in ids if item in valid]
        return ordered, len(ordered) == len(ids)

    def _find_content_hash_match(
        self,
        existing_facts: list[PersonaFact],
        memory_type: str,
        text: str,
    ) -> PersonaFact | None:
        target_hash = content_hash(text)
        if not target_hash:
            return None
        for fact in existing_facts:
            if (
                str(getattr(fact, "memory_type", "") or "") == memory_type
                and str(getattr(fact, "content_hash", "") or "") == target_hash
            ):
                return fact
        row = (
            self.db.query(PersonaFact)
            .filter(
                PersonaFact.user_id == self.user_id,
                PersonaFact.memory_type == memory_type,
                PersonaFact.content_hash == target_hash,
            )
            .first()
        )
        if row is not None and row not in existing_facts:
            existing_facts.insert(0, row)
        return row

    @staticmethod
    def _initial_governance(
        *,
        memory_type: str,
        confidence_hint: str,
        should_inject: bool,
        evidence_ids: list[int],
    ) -> tuple[str, str]:
        if memory_type not in AUTO_INJECT_MEMORY_TYPES or not should_inject:
            return "review", "manual_only"
        if confidence_hint == "high" and evidence_ids:
            return "active", "auto"
        if confidence_hint == "medium" and len(evidence_ids) >= 3:
            return "active", "auto"
        return "review", "manual_only"

    def build_summary(self) -> str:
        """将当前已确认的画像压缩为注入用 JSON 字符串。"""
        rows = (
            self.db.query(PersonaFact)
            .filter(
                PersonaFact.user_id == self.user_id,
            )
            .order_by(PersonaFact.evidence_count.desc())
            .limit(MAX_FACTS_PER_USER)
            .all()
        )
        facts = []
        for row in rows:
            status = str(getattr(row, "status", "") or "")
            inject_policy = str(getattr(row, "inject_policy", "") or "")
            if status in {"disabled", "rejected", "archived"} or row.confidence == "归档":
                continue
            is_new_active = status == "active" and inject_policy == "auto"
            is_legacy_confirmed = row.confidence == "确认"
            is_legacy_supported = row.confidence == "可能" and int(row.evidence_count or 0) >= 3
            if is_new_active or is_legacy_confirmed or is_legacy_supported:
                facts.append(row)
            if len(facts) >= 30:
                break

        if not facts:
            return "{}"

        items = []
        for f in facts:
            items.append({
                "content": f.content,
                "domain": f.domain_primary,
                "confidence": f.confidence,
                "evidence": f.evidence_count,
                "type": getattr(f, "memory_type", "") or f.fact_type,
            })

        return json.dumps({"facts": items, "count": len(items)}, ensure_ascii=False)

    # ── 匹配 ──

    def _find_matches(self, vec: np.ndarray, existing: list[PersonaFact],
                      threshold: float = MATCH_THRESHOLD) -> list[tuple[PersonaFact, float]]:
        """与每个 cluster 的 centroid 比较，返回 > threshold 的匹配（按相似度降序）。

        existing 由调用方预取（避免 N+1 查询），应为同一用户的现有 facts 列表。
        """
        # 按 cluster_id 分组，每组取 centroid 比较
        clusters: dict[int, np.ndarray] = {}
        for f in existing:
            if f.cluster_id is not None and f.cluster_id not in clusters:
                centroid_blob = f.cluster_centroid or f.embedding
                if centroid_blob:
                    clusters[f.cluster_id] = _from_blob(centroid_blob)

        matches = []
        seen_clusters = set()
        for f in existing:
            cid = f.cluster_id
            if cid is not None:
                if cid in seen_clusters:
                    continue
                seen_clusters.add(cid)
                centroid = clusters.get(cid)
            else:
                centroid = _from_blob(f.embedding) if f.embedding else None

            if centroid is None:
                continue
            sim = cosine_similarity(vec, centroid)
            if sim > threshold:
                matches.append((f, sim))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    # ── 创建 ──

    @staticmethod
    def _fact_type_from_memory_type(memory_type: str) -> str:
        if memory_type == "interaction_style":
            return "behavior"
        if memory_type == "stable_background":
            return "trait"
        if memory_type == "long_term_project":
            return "project"
        return "preference"

    def _create_fact(
        self,
        text: str,
        evidence: str,
        domain: str,
        vec: np.ndarray,
        now: datetime,
        *,
        memory_type: str = "stable_preference",
        confidence_hint: str = "medium",
        evidence_log_ids: list[int] | None = None,
        status: str = "review",
        inject_policy: str = "manual_only",
        candidate_meta: dict[str, Any] | None = None,
    ) -> PersonaFact:
        """无匹配 → 创建新 cluster。"""
        blob = _to_blob(vec)
        evidence_ids = list(evidence_log_ids or [])
        meta = dict(candidate_meta or {})
        meta.setdefault("confidence_hint", confidence_hint)
        fact = PersonaFact(
            user_id=self.user_id,
            domain_primary=domain,
            content=text,
            embedding=blob,
            cluster_centroid=blob,
            cluster_id=None,
            evidence_count=1,
            source_log_ids=json.dumps([evidence] if evidence else [], ensure_ascii=False),
            evidence_log_ids_json=json.dumps(evidence_ids, ensure_ascii=False),
            first_seen=now,
            last_seen=now,
            confidence="可能",
            fact_type=self._fact_type_from_memory_type(memory_type),
            memory_type=memory_type,
            status=status,
            inject_policy=inject_policy,
            content_hash=content_hash(text),
            candidate_meta_json=json.dumps(meta, ensure_ascii=False),
        )
        self.db.add(fact)
        self.db.flush()
        fact.cluster_id = fact.id
        return fact

    # ── Behavior 写入（简化去重，不做 centroid 聚类）──

    def _upsert_behavior(self, text: str, evidence: str, domain: str,
                         vec: np.ndarray, existing: list[PersonaBehavior], now: datetime):
        """Behavior 去重：embedding 相似 > MATCH_THRESHOLD 则合并，否则新建。"""
        blob = _to_blob(vec)
        for b in existing:
            if b.embedding is None:
                continue
            bv = _from_blob(b.embedding)
            if cosine_similarity(vec, bv) > MATCH_THRESHOLD:
                b.frequency += 1
                b.last_observed = now
                if evidence:
                    sources = json.loads(b.source_log_ids or "[]")
                    if evidence not in sources:
                        sources.append(evidence)
                        if len(sources) > 20:
                            sources = sources[-20:]
                        b.source_log_ids = json.dumps(sources, ensure_ascii=False)
                logger.debug(f"Behavior merged: '{text[:40]}' -> freq={b.frequency}")
                return
        # 新 behavior
        bh = PersonaBehavior(
            user_id=self.user_id,
            domain_primary=domain,
            pattern=text,
            embedding=blob,
            frequency=1,
            source_log_ids=json.dumps([evidence] if evidence else [], ensure_ascii=False),
            last_observed=now,
        )
        self.db.add(bh)

    # ── 合并 ──

    def _merge_fact(
        self,
        fact: PersonaFact,
        text: str,
        evidence: str,
        vec: np.ndarray,
        now: datetime,
        *,
        memory_type: str = "stable_preference",
        confidence_hint: str = "medium",
        evidence_log_ids: list[int] | None = None,
        should_inject: bool = False,
        candidate_meta: dict[str, Any] | None = None,
    ):
        """单匹配 → 合并到已有 cluster。"""
        fact.content = self._canonicalize(fact.content, text)
        fact.evidence_count += 1
        fact.last_seen = now
        fact.content_hash = content_hash(fact.content)
        if not getattr(fact, "memory_type", None):
            fact.memory_type = memory_type

        sources = json.loads(fact.source_log_ids or "[]")
        if evidence and evidence not in sources:
            sources.append(evidence)
            if len(sources) > 20:
                sources = sources[-20:]
            fact.source_log_ids = json.dumps(sources, ensure_ascii=False)

        evidence_ids = list(evidence_log_ids or [])
        if evidence_ids:
            existing_ids = _safe_json_list(getattr(fact, "evidence_log_ids_json", "[]"))
            merged_ids = list(dict.fromkeys([*existing_ids, *evidence_ids]))
            fact.evidence_log_ids_json = json.dumps(merged_ids[-50:], ensure_ascii=False)

        fact.embedding = _to_blob(vec)
        score = compute_confidence(fact.evidence_count, 0)
        fact.confidence = confidence_label(score)
        if should_inject and memory_type in AUTO_INJECT_MEMORY_TYPES:
            if confidence_hint == "high" or fact.evidence_count >= 3:
                fact.status = "active"
                fact.inject_policy = "auto"
        meta = _safe_json_dict(getattr(fact, "candidate_meta_json", "{}"))
        if candidate_meta:
            meta.update({k: v for k, v in candidate_meta.items() if v not in (None, "")})
            fact.candidate_meta_json = json.dumps(meta, ensure_ascii=False)
        self._update_centroid(fact.cluster_id)

    # ── 冲突解决 ──

    def _resolve_conflicts(self, matches: list[tuple[PersonaFact, float]],
                           text: str, evidence: str, domain: str,
                           vec: np.ndarray, now: datetime, *,
                           memory_type: str = "stable_preference",
                           confidence_hint: str = "medium",
                           evidence_log_ids: list[int] | None = None,
                           should_inject: bool = False,
                           candidate_meta: dict[str, Any] | None = None):
        """多匹配 → 取 evidence_count 最高的 cluster 合并，其余标记为 contradicted。"""
        matches.sort(key=lambda m: m[0].evidence_count, reverse=True)
        primary = matches[0][0]
        self._merge_fact(
            primary, text, evidence, vec, now,
            memory_type=memory_type,
            confidence_hint=confidence_hint,
            evidence_log_ids=evidence_log_ids or [],
            should_inject=should_inject,
            candidate_meta=candidate_meta or {},
        )

        for other, _ in matches[1:]:
            contradicted = json.loads(other.contradicted_by or "[]")
            if primary.cluster_id not in contradicted:
                contradicted.append(primary.cluster_id)
                other.contradicted_by = json.dumps(contradicted, ensure_ascii=False)
                if other.confidence == "确认":
                    other.confidence = "待确认"
            logger.debug(f"Contradiction: fact {other.id} contradicted by cluster {primary.cluster_id}")

    # ── Centroid 更新 ──

    def _update_centroid(self, cluster_id: int | None):
        """重新计算簇内均值向量。仅当 evidence >= 3 时才更新 centroid。"""
        if cluster_id is None:
            return
        members = (
            self.db.query(PersonaFact)
            .filter(PersonaFact.cluster_id == cluster_id)
            .all()
        )
        if len(members) < 3:
            return
        vecs = [_from_blob(m.embedding) for m in members if m.embedding]
        if not vecs:
            return
        centroid = np.mean(vecs, axis=0)
        centroid_blob = _to_blob(centroid)
        for m in members:
            m.cluster_centroid = centroid_blob

    # ── Canonicalization ──

    @staticmethod
    def _canonicalize(existing: str, new: str) -> str:
        """保留信息密度更高的表达，防止语义退化。"""
        if len(existing) >= len(new) * CANONICAL_MIN_LENGTH_RATIO:
            return existing
        return new

    # ── 衰减 ──

    def _apply_decay(self, now: datetime):
        """对所有现有 facts 应用时间衰减，低于阈值的归档。"""
        facts = (
            self.db.query(PersonaFact)
            .filter(
                PersonaFact.user_id == self.user_id,
                PersonaFact.confidence != "归档",
            )
            .all()
        )

        for f in facts:
            if f.last_seen is None:
                continue
            days = (now - f.last_seen).days
            score = compute_confidence(f.evidence_count, days)

            if f.fact_type == "preference":
                if days > PREFERENCE_ARCHIVE_DAYS or score < PREFERENCE_ARCHIVE_SCORE:
                    f.confidence = "归档"
                else:
                    f.confidence = confidence_label(score)
            else:
                if days > BEHAVIOR_ARCHIVE_DAYS or score < BEHAVIOR_ARCHIVE_SCORE:
                    f.confidence = "归档"
                else:
                    f.confidence = confidence_label(score)

    # ── 上限裁剪 ──

    def _prune_excess(self):
        """超过 MAX_FACTS_PER_USER 时，归档低置信度条目。"""
        active = (
            self.db.query(PersonaFact)
            .filter(
                PersonaFact.user_id == self.user_id,
                PersonaFact.confidence != "归档",
            )
            .order_by(PersonaFact.evidence_count.asc())
            .all()
        )

        excess = len(active) - MAX_FACTS_PER_USER
        if excess <= 0:
            return

        for f in active[:excess]:
            f.confidence = "归档"
        logger.info(f"Pruned {excess} low-evidence facts for user={self.user_id}")

    # ── 矛盾检测 ──

    def detect_contradiction(self, new_text: str, existing_text: str) -> bool:
        """检测两段表述是否存在语义矛盾。优先使用 NLI 模型，降级为 cosine 判定。"""
        nli = _get_nli()
        if nli is not None:
            try:
                result = nli(f"{existing_text} </s></s> {new_text}")
                label = result[0]["label"]
                score = result[0]["score"]
                if label == "CONTRADICTION" and score > 0.8:
                    return True
                return False
            except Exception as e:
                logger.warning(f"NLI contradiction check failed: {e}")

        try:
            v1 = embed_text(new_text)
            v2 = embed_text(existing_text)
            sim = cosine_similarity(v1, v2)
            return sim < CONTRADICTION_THRESHOLD
        except Exception:
            return False


# ── 置信度计算 ──

def compute_confidence(evidence_count: int, days_since_last: float) -> float:
    """软置信度 0~1，连续变化（不分段硬阈值）。

    evidence: S 曲线，3次→0.78, 5次→0.92
    recency:  线性衰减，30天归零
    """
    recency = max(0.0, 1.0 - days_since_last / 30.0)
    evidence = 1.0 - math.exp(-evidence_count * 0.5)
    return evidence * 0.7 + recency * 0.3


def confidence_label(score: float) -> str:
    """将连续置信度转为自然语言标签（仅用于注入显示）。"""
    if score > 0.75:
        return "确认"
    if score > 0.4:
        return "可能"
    return "待确认"
