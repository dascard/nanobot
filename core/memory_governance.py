"""记忆分层、作用域、注入预算与后端准入的稳定治理合同。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Any

from core.token_utils import estimate_tokens
from foundation.identity import (
    identity_storage_aliases,
    resolve_chat_stream_identity,
)


DEFAULT_AGENT_ID = "nanobot"
DEFAULT_KNOWLEDGE_PROJECT_ID = "nanobot"
MEMORY_GOVERNANCE_VERSION = "memory-governance-v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MemoryGovernanceError(ValueError):
    """记忆治理合同无效或访问超出授权。"""


class MemoryLayer(StrEnum):
    """模型可用记忆的三个稳定层次。"""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryScopeType(StrEnum):
    """记忆所有权与共享边界。"""

    AGENT = "agent"
    USER = "user"
    GROUP = "group"
    PROJECT = "project"


class MemoryStorageRole(StrEnum):
    """区分原始事实、规范记忆和可重建派生索引。"""

    RAW_EVIDENCE = "raw_evidence"
    CANONICAL_MEMORY = "canonical_memory"
    DERIVED_INDEX = "derived_index"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    scope_type: MemoryScopeType
    owner_id: str

    def __post_init__(self) -> None:
        scope_type = self.scope_type
        if not isinstance(scope_type, MemoryScopeType):
            try:
                scope_type = MemoryScopeType(str(scope_type))
            except ValueError as exc:
                raise MemoryGovernanceError("memory scope_type 无效") from exc
        owner_id = str(self.owner_id or "").strip()
        if not owner_id:
            raise MemoryGovernanceError("memory scope owner_id 不能为空")
        object.__setattr__(self, "scope_type", scope_type)
        object.__setattr__(self, "owner_id", owner_id)

    @property
    def canonical_id(self) -> str:
        return f"{self.scope_type.value}:{self.owner_id}"

    @property
    def shared(self) -> bool:
        return self.scope_type in {MemoryScopeType.GROUP, MemoryScopeType.PROJECT}

    def metadata(self) -> dict[str, str]:
        return {
            "scope_type": self.scope_type.value,
            "owner_id": self.owner_id,
        }


@dataclass(frozen=True, slots=True)
class MemorySourcePolicy:
    """既有数据源在记忆系统中的权威角色。"""

    source_type: str
    layer: MemoryLayer | None
    storage_role: MemoryStorageRole
    scope_types: tuple[MemoryScopeType, ...]
    evidence_fields: tuple[str, ...]
    confidence_fields: tuple[str, ...]
    conflict_fields: tuple[str, ...]
    decay_fields: tuple[str, ...]
    deletion_policy: str
    injection_policy: str

    def metadata(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "layer": self.layer.value if self.layer is not None else None,
            "storage_role": self.storage_role.value,
            "scope_types": [item.value for item in self.scope_types],
            "evidence_fields": list(self.evidence_fields),
            "confidence_fields": list(self.confidence_fields),
            "conflict_fields": list(self.conflict_fields),
            "decay_fields": list(self.decay_fields),
            "deletion_policy": self.deletion_policy,
            "injection_policy": self.injection_policy,
        }


MEMORY_SOURCE_POLICIES: Mapping[str, MemorySourcePolicy] = MappingProxyType({
    "chat_log": MemorySourcePolicy(
        source_type="chat_log",
        layer=None,
        storage_role=MemoryStorageRole.RAW_EVIDENCE,
        scope_types=(MemoryScopeType.USER, MemoryScopeType.GROUP),
        evidence_fields=("id", "message_id", "source_message_ids_json"),
        confidence_fields=(),
        conflict_fields=(),
        decay_fields=(),
        deletion_policy="原始档案保留；用户清除只删除工作记忆，不删除 ChatLog",
        injection_policy="不直接注入；仅作为受控摘要、画像和审计证据",
    ),
    "conversation_turn": MemorySourcePolicy(
        source_type="conversation_turn",
        layer=MemoryLayer.WORKING,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.USER,),
        evidence_fields=("id", "source_message_ids_json"),
        confidence_fields=(),
        conflict_fields=(),
        decay_fields=("created_at",),
        deletion_policy="history_clear_at 后删除，可由原始档案重新派生",
        injection_policy="按当前会话 raw window 和 Context Engine 预算注入",
    ),
    "rolling_session_summary": MemorySourcePolicy(
        source_type="rolling_session_summary",
        layer=MemoryLayer.WORKING,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.USER, MemoryScopeType.GROUP),
        evidence_fields=("source_ids_json", "source_turn_ids_json", "prompt_sha256"),
        confidence_fields=("quality_score", "issues_json", "llm_status"),
        conflict_fields=("supersedes_summary_id", "status"),
        decay_fields=("raw_window_start_source_id", "updated_at"),
        deletion_policy="归档或历史清除后从召回索引移除",
        injection_policy="仅注入当前会话且受 Context Engine 预算约束",
    ),
    "conversation_block_episode": MemorySourcePolicy(
        source_type="conversation_block_episode",
        layer=MemoryLayer.EPISODIC,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.USER,),
        evidence_fields=("source_turn_ids_json", "source_revision", "seed_summary_id"),
        confidence_fields=("quality_score", "issues_json", "summary_kind"),
        conflict_fields=("status",),
        decay_fields=("sealed_at", "refined_at"),
        deletion_policy="归档后同步软删除派生索引",
        injection_policy="只经会话摘要检索链按需展开，不无条件注入",
    ),
    "memory_digest": MemorySourcePolicy(
        source_type="memory_digest",
        layer=MemoryLayer.EPISODIC,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.USER, MemoryScopeType.GROUP),
        evidence_fields=("source_start_log_id", "source_end_log_id", "meta.source_id"),
        confidence_fields=("meta.quality", "meta.generator", "meta.llm_status"),
        conflict_fields=("meta.status", "generation_job_id"),
        decay_fields=("digest_date", "created_at"),
        deletion_policy="归档时软删除派生索引；原始 ChatLog 保持独立",
        injection_policy="不自动注入；仅由 memory_query 在授权作用域内按需召回",
    ),
    "persona_fact": MemorySourcePolicy(
        source_type="persona_fact",
        layer=MemoryLayer.SEMANTIC,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.USER,),
        evidence_fields=("evidence_log_ids_json", "source_log_ids", "derived_from"),
        confidence_fields=("confidence", "evidence_count", "status"),
        conflict_fields=("contradicted_by", "disabled_reason", "rejected_reason"),
        decay_fields=("last_seen", "last_injected_at"),
        deletion_policy="状态机归档或禁用；证据档案独立保留",
        injection_policy="active+auto、证据阈值、相关度和显式预算共同决定",
    ),
    "group_memory": MemorySourcePolicy(
        source_type="group_memory",
        layer=MemoryLayer.SEMANTIC,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.GROUP,),
        evidence_fields=("evidence_log_ids_json", "evidence_count", "source"),
        confidence_fields=("confidence", "governance_mode", "approval_source"),
        conflict_fields=("conflict_group_id", "merged_into_id", "status"),
        decay_fields=("decay_score", "last_seen", "last_injected_at"),
        deletion_policy="禁用、合并或归档后停止注入并移除派生索引",
        injection_policy="仅当前群组，经过治理状态、冲突、衰减、相关度和预算过滤",
    ),
    "knowledge_document": MemorySourcePolicy(
        source_type="knowledge_document",
        layer=MemoryLayer.SEMANTIC,
        storage_role=MemoryStorageRole.CANONICAL_MEMORY,
        scope_types=(MemoryScopeType.AGENT, MemoryScopeType.PROJECT),
        evidence_fields=("source_id", "url", "meta_json", "citation_json"),
        confidence_fields=("trust_level", "status"),
        conflict_fields=("disabled_reason", "disabled_at"),
        decay_fields=("published_at", "latest_seen", "updated_at"),
        deletion_policy="禁用文档并软删除全部派生 chunk 索引",
        injection_policy="仅授权 Agent/项目按需检索，且 citation 必须完整",
    ),
    "semantic_index_item": MemorySourcePolicy(
        source_type="semantic_index_item",
        layer=MemoryLayer.SEMANTIC,
        storage_role=MemoryStorageRole.DERIVED_INDEX,
        scope_types=(
            MemoryScopeType.AGENT,
            MemoryScopeType.USER,
            MemoryScopeType.GROUP,
            MemoryScopeType.PROJECT,
        ),
        evidence_fields=("source_type", "source_id", "source_sub_id", "source_revision"),
        confidence_fields=("quality_score", "trust_level", "source_prior"),
        conflict_fields=("status", "deleted_at"),
        decay_fields=("source_updated_at", "updated_at"),
        deletion_policy="可重建派生物；源归档或删除时立即软删除并清理 FTS",
        injection_policy="必须先按源作用域过滤，再召回、重排和预算裁剪",
    ),
})


@dataclass(frozen=True, slots=True)
class MemoryInjectionBudget:
    """模型可见记忆片段的统一条数、字符和估算 token 上限。"""

    max_items: int
    max_chars: int
    max_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("max_items", "max_chars", "max_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or int(value) <= 0:
                raise MemoryGovernanceError(f"{field_name} 必须是正整数")
            object.__setattr__(self, field_name, int(value))

    def usage(self, text: str, *, item_count: int) -> dict[str, int | str]:
        content = str(text or "")
        used_items = max(0, int(item_count or 0))
        used_chars = len(content)
        used_tokens = estimate_tokens(content)
        return {
            "policy_version": MEMORY_GOVERNANCE_VERSION,
            "max_items": self.max_items,
            "max_chars": self.max_chars,
            "max_tokens": self.max_tokens,
            "used_items": used_items,
            "used_chars": used_chars,
            "used_tokens": used_tokens,
            "remaining_items": max(0, self.max_items - used_items),
            "remaining_chars": max(0, self.max_chars - used_chars),
            "remaining_tokens": max(0, self.max_tokens - used_tokens),
        }

    def allows(self, text: str, *, item_count: int) -> bool:
        usage = self.usage(text, item_count=item_count)
        return (
            int(usage["used_items"]) <= self.max_items
            and int(usage["used_chars"]) <= self.max_chars
            and int(usage["used_tokens"]) <= self.max_tokens
        )


@dataclass(frozen=True, slots=True)
class MemoryAccessContext:
    """从受信 Runtime 身份派生的单次记忆访问授权。"""

    principal_id: str
    platform: str
    owner_type: MemoryScopeType
    owner_id: str
    session_id: str
    session_aliases: tuple[str, ...]
    agent_id: str = DEFAULT_AGENT_ID
    project_ids: tuple[str, ...] = (DEFAULT_KNOWLEDGE_PROJECT_ID,)
    actor_id: str = ""
    authorization: str = "runtime_governance"

    def __post_init__(self) -> None:
        owner_type = self.owner_type
        if not isinstance(owner_type, MemoryScopeType):
            try:
                owner_type = MemoryScopeType(str(owner_type))
            except ValueError as exc:
                raise MemoryGovernanceError("memory access owner_type 无效") from exc
        if owner_type not in {MemoryScopeType.USER, MemoryScopeType.GROUP}:
            raise MemoryGovernanceError("请求 principal 只能是 user 或 group")
        required = {
            "principal_id": self.principal_id,
            "platform": self.platform,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "authorization": self.authorization,
        }
        for field_name, value in required.items():
            if not str(value or "").strip():
                raise MemoryGovernanceError(f"memory access {field_name} 不能为空")
        aliases = tuple(sorted({
            str(item or "").strip()
            for item in (*self.session_aliases, self.session_id)
            if str(item or "").strip()
        }))
        if not aliases:
            raise MemoryGovernanceError("memory access session_aliases 不能为空")
        projects = tuple(sorted({
            DEFAULT_KNOWLEDGE_PROJECT_ID,
            *(str(item or "").strip() for item in self.project_ids),
        } - {""}))
        object.__setattr__(self, "owner_type", owner_type)
        object.__setattr__(self, "principal_id", str(self.principal_id).strip())
        object.__setattr__(self, "platform", str(self.platform).strip().lower())
        object.__setattr__(self, "owner_id", str(self.owner_id).strip())
        object.__setattr__(self, "session_id", str(self.session_id).strip())
        object.__setattr__(self, "session_aliases", aliases)
        object.__setattr__(self, "agent_id", str(self.agent_id).strip())
        object.__setattr__(self, "project_ids", projects)
        object.__setattr__(self, "actor_id", str(self.actor_id or "").strip())
        object.__setattr__(self, "authorization", str(self.authorization).strip())

    @property
    def principal_scope(self) -> MemoryScope:
        return MemoryScope(self.owner_type, self.owner_id)

    def allows(self, scope: MemoryScope) -> bool:
        if scope.scope_type is MemoryScopeType.AGENT:
            return scope.owner_id == self.agent_id
        if scope.scope_type is MemoryScopeType.PROJECT:
            return scope.owner_id in self.project_ids
        return (
            scope.scope_type is self.owner_type
            and scope.owner_id == self.owner_id
        )

    def audit_metadata(
        self,
        *,
        provider_id: str,
        tool_name: str,
        resource_scope: str,
    ) -> dict[str, object]:
        return {
            "policy_version": MEMORY_GOVERNANCE_VERSION,
            "subject": self.actor_id or self.principal_id,
            "principal": self.principal_id,
            "agent_id": self.agent_id,
            "authorization": self.authorization,
            "provider_id": str(provider_id or ""),
            "tool_name": str(tool_name or ""),
            "resource_scope": str(resource_scope or ""),
            "session_id": self.session_id,
            "project_ids": list(self.project_ids),
        }


@dataclass(frozen=True, slots=True)
class MemoryDataScopeFilter:
    """下推到数据库召回和展开读取的精确等值过滤器。"""

    user_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()

    def matches(self, *, user_id: str, session_id: str) -> bool:
        normalized_user = str(user_id or "").strip()
        normalized_session = str(session_id or "").strip()
        return (
            (not self.user_ids or normalized_user in self.user_ids)
            and (not self.session_ids or normalized_session in self.session_ids)
        )


def memory_data_scope_filter(
    access: MemoryAccessContext,
    *,
    source: str,
) -> MemoryDataScopeFilter:
    """为摘要源生成授权过滤；作用域过滤必须早于召回和重排。"""

    normalized_source = str(source or "digest").strip()
    if access.owner_type is MemoryScopeType.GROUP:
        return MemoryDataScopeFilter(session_ids=access.session_aliases)
    if normalized_source == "digest":
        return MemoryDataScopeFilter(user_ids=(access.owner_id,))
    return MemoryDataScopeFilter(
        user_ids=(access.owner_id,),
        session_ids=access.session_aliases,
    )


def _parse_principal_id(principal_id: str) -> tuple[str, MemoryScopeType, str]:
    parts = str(principal_id or "").strip().split(":", 2)
    if len(parts) != 3:
        raise MemoryGovernanceError("memory principal_id 格式无效")
    platform, raw_owner_type, owner_id = parts
    try:
        owner_type = MemoryScopeType(raw_owner_type)
    except ValueError as exc:
        raise MemoryGovernanceError("memory principal owner_type 无效") from exc
    if owner_type not in {MemoryScopeType.USER, MemoryScopeType.GROUP}:
        raise MemoryGovernanceError("memory principal 不是 user/group")
    if not platform or not owner_id:
        raise MemoryGovernanceError("memory principal_id 不完整")
    return platform, owner_type, owner_id


def build_memory_access_context(
    *,
    principal_id: str,
    session_id: str,
    agent_id: str = DEFAULT_AGENT_ID,
    project_ids: Sequence[str] = (),
    actor_id: str = "",
    authorization: str = "runtime_governance",
) -> MemoryAccessContext:
    platform, owner_type, owner_id = _parse_principal_id(principal_id)
    try:
        identity = resolve_chat_stream_identity(
            platform=platform,
            chat_type=(
                "private"
                if owner_type is MemoryScopeType.USER
                else "group"
            ),
            session_id=session_id,
        )
    except Exception as exc:
        raise MemoryGovernanceError("memory session 身份无效") from exc
    if identity.external_session_id != owner_id:
        raise MemoryGovernanceError("memory principal 与 session 不一致")
    aliases = identity_storage_aliases(
        identity,
        include_raw_group_id=owner_type is MemoryScopeType.GROUP,
    )
    return MemoryAccessContext(
        principal_id=principal_id,
        platform=platform,
        owner_type=owner_type,
        owner_id=owner_id,
        session_id=str(session_id or "").strip(),
        session_aliases=aliases,
        agent_id=agent_id or DEFAULT_AGENT_ID,
        project_ids=tuple(project_ids),
        actor_id=actor_id,
        authorization=authorization,
    )


def memory_access_from_runtime_context(
    context: Mapping[str, Any] | None,
) -> MemoryAccessContext | None:
    """只从 Runtime 绑定读取身份；普通工具参数不会参与授权派生。"""

    if not context:
        return None
    owner_type = str(context.get("owner_type") or "").strip()
    owner_id = str(context.get("owner_id") or "").strip()
    platform = str(context.get("platform") or "").strip().lower()
    session_id = str(context.get("session_id") or "").strip()
    if not all((owner_type, owner_id, platform, session_id)):
        raise MemoryGovernanceError("受信 Runtime 记忆身份不完整")
    project_ids = [DEFAULT_KNOWLEDGE_PROJECT_ID]
    skill_project_id = str(context.get("skill_project_id") or "").strip()
    if skill_project_id:
        project_ids.append(skill_project_id)
    return build_memory_access_context(
        principal_id=f"{platform}:{owner_type}:{owner_id}",
        session_id=session_id,
        agent_id=str(context.get("agent_id") or DEFAULT_AGENT_ID),
        project_ids=project_ids,
        actor_id=str(context.get("actor_id") or ""),
    )


_CURRENT_MEMORY_ACCESS: ContextVar[MemoryAccessContext | None] = ContextVar(
    "nanobot_memory_access_context",
    default=None,
)


def get_current_memory_access() -> MemoryAccessContext | None:
    return _CURRENT_MEMORY_ACCESS.get()


def set_current_memory_access(
    access: MemoryAccessContext,
) -> Token[MemoryAccessContext | None]:
    return _CURRENT_MEMORY_ACCESS.set(access)


def reset_current_memory_access(token: Token[MemoryAccessContext | None]) -> None:
    _CURRENT_MEMORY_ACCESS.reset(token)


@contextmanager
def memory_access_scope(
    access: MemoryAccessContext,
) -> Iterator[MemoryAccessContext]:
    token = set_current_memory_access(access)
    try:
        yield access
    finally:
        reset_current_memory_access(token)


def current_or_runtime_memory_access() -> MemoryAccessContext | None:
    current = get_current_memory_access()
    if current is not None:
        return current
    from core.agent_runtime.request_scope import get_current_runtime_context

    return memory_access_from_runtime_context(get_current_runtime_context())


def scope_memory_tool_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    access: MemoryAccessContext,
) -> dict[str, Any]:
    """移除模型提交的身份字段，并为群表情检索写入可信群号。"""

    scoped = {
        str(key): value
        for key, value in dict(arguments).items()
        if not str(key).startswith("__memory_")
    }
    name = str(tool_name or "").strip()
    if name == "memory_query":
        scoped.pop("user_id", None)
        scoped.pop("session_id", None)
    elif name == "sticker_search":
        scoped.pop("group_id", None)
        scoped["group_id"] = (
            access.owner_id
            if access.owner_type is MemoryScopeType.GROUP
            else ""
        )
    return scoped


def memory_tool_resource_scope(
    tool_name: str,
    access: MemoryAccessContext,
) -> str:
    name = str(tool_name or "").strip()
    if name == "knowledge_query":
        scopes = [f"agent:{access.agent_id}"]
        scopes.extend(f"project:{item}" for item in access.project_ids)
        return ",".join(scopes)
    if name == "sticker_search" and access.owner_type is MemoryScopeType.USER:
        return f"user:{access.owner_id},project:global-sticker"
    return access.principal_scope.canonical_id


def knowledge_scope_from_meta(
    meta: Mapping[str, Any] | str | None,
) -> MemoryScope:
    """解析知识文档作用域；历史无标记文档明确归入默认项目共享域。"""

    parsed: Mapping[str, Any]
    if isinstance(meta, str):
        try:
            value = json.loads(meta or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryGovernanceError("knowledge meta_json 损坏") from exc
        parsed = value if isinstance(value, Mapping) else {}
    elif isinstance(meta, Mapping):
        parsed = meta
    else:
        parsed = {}
    raw_scope = parsed.get("memory_scope")
    if raw_scope in (None, ""):
        return MemoryScope(
            MemoryScopeType.PROJECT,
            DEFAULT_KNOWLEDGE_PROJECT_ID,
        )
    if not isinstance(raw_scope, Mapping):
        raise MemoryGovernanceError("knowledge memory_scope 必须是对象")
    scope = MemoryScope(
        MemoryScopeType(str(raw_scope.get("scope_type") or "")),
        str(raw_scope.get("owner_id") or ""),
    )
    if scope.scope_type not in {MemoryScopeType.AGENT, MemoryScopeType.PROJECT}:
        raise MemoryGovernanceError("知识文档只支持 agent/project 作用域")
    return scope


def normalize_knowledge_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(meta or {})
    scope = knowledge_scope_from_meta(normalized)
    normalized["memory_scope"] = scope.metadata()
    return normalized


@dataclass(frozen=True, slots=True)
class ChineseMemoryEvaluationEvidence:
    """新记忆后端进入生产组合根前必须提供的真实中文会话证据。"""

    evaluation_id: str
    baseline_backend_id: str
    locale: str
    real_conversation_count: int
    passed_conversation_count: int
    scope_leak_count: int
    deletion_failure_count: int
    quality_delta: float
    manifest_sha256: str
    real_model: bool

    def validate(self) -> None:
        if not str(self.evaluation_id or "").strip():
            raise MemoryGovernanceError("memory evaluation_id 不能为空")
        if not str(self.baseline_backend_id or "").strip():
            raise MemoryGovernanceError("memory baseline_backend_id 不能为空")
        if self.locale not in {"zh-CN", "zh-Hans"}:
            raise MemoryGovernanceError("新记忆后端必须使用中文会话评测")
        if not self.real_model:
            raise MemoryGovernanceError("新记忆后端必须经过真实模型评测")
        if self.real_conversation_count < 50:
            raise MemoryGovernanceError("真实中文会话评测至少需要 50 条")
        if self.passed_conversation_count < self.real_conversation_count:
            raise MemoryGovernanceError("中文会话评测存在未通过案例")
        if self.scope_leak_count != 0 or self.deletion_failure_count != 0:
            raise MemoryGovernanceError("作用域泄漏或删除失败禁止后端准入")
        if float(self.quality_delta) <= 0:
            raise MemoryGovernanceError("新后端质量必须优于冻结基线")
        if not _SHA256_PATTERN.fullmatch(str(self.manifest_sha256 or "")):
            raise MemoryGovernanceError("memory evaluation manifest_sha256 无效")


@dataclass(frozen=True, slots=True)
class MemoryBackendCandidate:
    backend_id: str
    implementation_kind: str
    existing_production_backend: bool = False
    evaluation: ChineseMemoryEvaluationEvidence | None = None


ACTIVE_MEMORY_BACKEND = MemoryBackendCandidate(
    backend_id="existing-sqlite-rag-v1",
    implementation_kind="existing_relational_rag",
    existing_production_backend=True,
)


def validate_memory_backend_candidate(candidate: MemoryBackendCandidate) -> None:
    """拒绝仅凭外部项目宣称切换知识图谱或其他新后端。"""

    if not str(candidate.backend_id or "").strip():
        raise MemoryGovernanceError("memory backend_id 不能为空")
    if not str(candidate.implementation_kind or "").strip():
        raise MemoryGovernanceError("memory implementation_kind 不能为空")
    if candidate.existing_production_backend:
        if candidate != ACTIVE_MEMORY_BACKEND:
            raise MemoryGovernanceError("不得把新后端伪装为既有生产后端")
        return
    if candidate.evaluation is None:
        raise MemoryGovernanceError("新记忆后端缺少真实中文会话评测")
    candidate.evaluation.validate()
    if candidate.evaluation.baseline_backend_id != ACTIVE_MEMORY_BACKEND.backend_id:
        raise MemoryGovernanceError("新记忆后端没有对比当前生产 RAG 基线")


def memory_governance_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_version": MEMORY_GOVERNANCE_VERSION,
        "active_backend": ACTIVE_MEMORY_BACKEND.backend_id,
        "layers": [item.value for item in MemoryLayer],
        "scopes": [item.value for item in MemoryScopeType],
        "sources": {
            key: value.metadata()
            for key, value in sorted(MEMORY_SOURCE_POLICIES.items())
        },
    }


__all__ = [
    "ACTIVE_MEMORY_BACKEND",
    "ChineseMemoryEvaluationEvidence",
    "DEFAULT_AGENT_ID",
    "DEFAULT_KNOWLEDGE_PROJECT_ID",
    "MEMORY_GOVERNANCE_VERSION",
    "MEMORY_SOURCE_POLICIES",
    "MemoryAccessContext",
    "MemoryBackendCandidate",
    "MemoryDataScopeFilter",
    "MemoryGovernanceError",
    "MemoryInjectionBudget",
    "MemoryLayer",
    "MemoryScope",
    "MemoryScopeType",
    "MemorySourcePolicy",
    "MemoryStorageRole",
    "build_memory_access_context",
    "current_or_runtime_memory_access",
    "get_current_memory_access",
    "knowledge_scope_from_meta",
    "memory_access_from_runtime_context",
    "memory_access_scope",
    "memory_data_scope_filter",
    "memory_governance_manifest",
    "memory_tool_resource_scope",
    "normalize_knowledge_meta",
    "reset_current_memory_access",
    "scope_memory_tool_arguments",
    "set_current_memory_access",
    "validate_memory_backend_candidate",
]
