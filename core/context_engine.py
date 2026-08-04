"""模型上下文的分层、预算与来源清单。

本模块不读取记忆数据库，也不生成第二份上下文。调用方必须把 canonical
Prompt Runtime 最终将要发送的 messages/tools 交给这里，由这里生成不含正文的
Context Manifest，并在模型调用前执行逐层预算门禁。
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from core.prompt_v2.section_renderer import estimate_tokens, sha256_text, stable_json


CONTEXT_MANIFEST_SCHEMA_VERSION = "1.0"


def _is_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(
        char in "0123456789abcdef" for char in digest
    )


class ContextLayer(str, Enum):
    """模型上下文的功能层；每层独立计量和限额。"""

    STABLE_SYSTEM = "stable_system"
    SECURITY_POLICY = "security_policy"
    STABLE_POLICY = "stable_policy"
    TOOL_CONTRACT = "tool_contract"
    DYNAMIC_CONTEXT = "dynamic_context"
    MEMORY_RECALL = "memory_recall"
    SUMMARY = "summary"
    RECENT_CONVERSATION = "recent_conversation"
    TOOL_RESULT = "tool_result"
    CURRENT_REQUEST = "current_request"


class ContextScope(str, Enum):
    """上下文数据的授权作用域。"""

    GLOBAL = "global"
    SESSION = "session"
    USER = "user"
    GROUP = "group"
    PROJECT = "project"
    TURN = "turn"


class ContextStability(str, Enum):
    """段落在缓存和恢复语义上的稳定程度。"""

    STABLE = "stable"
    FROZEN = "frozen"
    DYNAMIC = "dynamic"


class ContextManifestError(ValueError):
    """Context Manifest 不满足结构或一致性约束。"""


class ContextBudgetExceededError(ContextManifestError):
    """某个上下文层超过服务端固定预算。"""

    def __init__(self, layer: ContextLayer, used_tokens: int, max_tokens: int):
        self.layer = ContextLayer(layer)
        self.used_tokens = int(used_tokens)
        self.max_tokens = int(max_tokens)
        super().__init__(
            "上下文层预算超限："
            f"layer={self.layer.value}, used={self.used_tokens}, max={self.max_tokens}"
        )


@dataclass(frozen=True, slots=True)
class ContextProvenance:
    """不含正文的来源证明。"""

    source_kind: str
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        kind = str(self.source_kind or "").strip()
        if not kind or len(kind) > 64:
            raise ContextManifestError("context provenance source_kind 无效")
        refs = tuple(_normalize_source_ref(item) for item in self.source_refs)
        refs = tuple(item for item in refs if item)
        if len(refs) > 64:
            raise ContextManifestError("context provenance source_refs 超过 64 项")
        object.__setattr__(self, "source_kind", kind)
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True, slots=True)
class ContextLayerBudget:
    layer: ContextLayer
    max_tokens: int
    used_tokens: int
    overflow_policy: str = "reject"

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer", ContextLayer(self.layer))
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ContextManifestError("context budget max_tokens 必须是正整数")
        if type(self.used_tokens) is not int or self.used_tokens < 0:
            raise ContextManifestError("context budget used_tokens 必须是非负整数")
        if self.overflow_policy != "reject":
            raise ContextManifestError("阶段 5.1 只允许 reject 预算策略")

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "max_tokens": self.max_tokens,
            "used_tokens": self.used_tokens,
            "overflow_policy": self.overflow_policy,
        }


@dataclass(frozen=True, slots=True)
class ContextManifestEntry:
    entry_id: str
    layer: ContextLayer
    scope: ContextScope
    stability: ContextStability
    source_kind: str
    source_refs: tuple[str, ...]
    token_estimate: int
    content_sha256: str
    message_indexes: tuple[int, ...] = ()
    authority: str = "data"
    trust: str = "untrusted_data"

    def __post_init__(self) -> None:
        entry_id = str(self.entry_id or "").strip()
        if not entry_id or len(entry_id) > 96:
            raise ContextManifestError("context entry_id 无效")
        object.__setattr__(self, "entry_id", entry_id)
        object.__setattr__(self, "layer", ContextLayer(self.layer))
        object.__setattr__(self, "scope", ContextScope(self.scope))
        object.__setattr__(self, "stability", ContextStability(self.stability))
        provenance = ContextProvenance(self.source_kind, self.source_refs)
        object.__setattr__(self, "source_kind", provenance.source_kind)
        object.__setattr__(self, "source_refs", provenance.source_refs)
        if type(self.token_estimate) is not int or self.token_estimate < 0:
            raise ContextManifestError("context entry token_estimate 必须是非负整数")
        digest = str(self.content_sha256 or "").strip().lower()
        if not _is_sha256(digest):
            raise ContextManifestError("context entry content_sha256 无效")
        object.__setattr__(self, "content_sha256", digest)
        indexes = tuple(self.message_indexes)
        if any(type(index) is not int or index < 0 for index in indexes):
            raise ContextManifestError("context entry message_indexes 无效")
        if len(indexes) != len(set(indexes)):
            raise ContextManifestError("context entry message_indexes 不能重复")
        object.__setattr__(self, "message_indexes", indexes)
        authority = str(self.authority or "data").strip()
        trust = str(self.trust or "untrusted_data").strip()
        if not authority or len(authority) > 64:
            raise ContextManifestError("context entry authority 无效")
        if not trust or len(trust) > 64:
            raise ContextManifestError("context entry trust 无效")
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "trust", trust)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "layer": self.layer.value,
            "scope": self.scope.value,
            "stability": self.stability.value,
            "source_kind": self.source_kind,
            "source_refs": list(self.source_refs),
            "token_estimate": self.token_estimate,
            "content_sha256": self.content_sha256,
            "message_indexes": list(self.message_indexes),
            "authority": self.authority,
            "trust": self.trust,
        }


@dataclass(frozen=True, slots=True)
class ContextManifest:
    policy_id: str
    request_prompt_sha256: str
    entries: tuple[ContextManifestEntry, ...]
    layer_budgets: tuple[ContextLayerBudget, ...]

    def __post_init__(self) -> None:
        policy_id = str(self.policy_id or "").strip()
        if not policy_id or len(policy_id) > 96:
            raise ContextManifestError("context policy_id 无效")
        object.__setattr__(self, "policy_id", policy_id)
        prompt_digest = str(self.request_prompt_sha256 or "").strip().lower()
        if not _is_sha256(prompt_digest):
            raise ContextManifestError("context request_prompt_sha256 无效")
        object.__setattr__(self, "request_prompt_sha256", prompt_digest)
        entries = tuple(self.entries)
        if len({entry.entry_id for entry in entries}) != len(entries):
            raise ContextManifestError("context entry_id 不能重复")
        object.__setattr__(self, "entries", entries)
        budgets = tuple(self.layer_budgets)
        budget_layers = [budget.layer for budget in budgets]
        if len(set(budget_layers)) != len(budget_layers):
            raise ContextManifestError("context layer budget 不能重复")
        if set(budget_layers) != set(ContextLayer):
            raise ContextManifestError("context layer budget 必须覆盖全部层")
        object.__setattr__(self, "layer_budgets", budgets)

    @property
    def total_tokens(self) -> int:
        return sum(entry.token_estimate for entry in self.entries)

    @property
    def scope_usage(self) -> dict[ContextScope, int]:
        usage = {scope: 0 for scope in ContextScope}
        for entry in self.entries:
            usage[entry.scope] += entry.token_estimate
        return usage

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "request_prompt_sha256": self.request_prompt_sha256,
            "entries": [entry.to_dict() for entry in self.entries],
            "layer_budgets": [budget.to_dict() for budget in self.layer_budgets],
            "scope_usage": [
                {"scope": scope.value, "used_tokens": used_tokens}
                for scope, used_tokens in self.scope_usage.items()
            ],
            "total_tokens": self.total_tokens,
        }

    @property
    def sha256(self) -> str:
        return sha256_text(stable_json(self._unsigned_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class _SectionClassification:
    layer: ContextLayer
    scope: ContextScope
    stability: ContextStability
    source_kind: str


_SECTION_CLASSIFICATIONS = MappingProxyType({
    "base_contract": _SectionClassification(
        ContextLayer.SECURITY_POLICY,
        ContextScope.GLOBAL,
        ContextStability.STABLE,
        "prompt_template",
    ),
    "identity_context": _SectionClassification(
        ContextLayer.STABLE_SYSTEM,
        ContextScope.USER,
        ContextStability.FROZEN,
        "prompt_template",
    ),
    "conversation_context_header": _SectionClassification(
        ContextLayer.STABLE_SYSTEM,
        ContextScope.SESSION,
        ContextStability.STABLE,
        "context_contract",
    ),
    "qq_common_policy": _SectionClassification(
        ContextLayer.STABLE_POLICY,
        ContextScope.GLOBAL,
        ContextStability.STABLE,
        "prompt_template",
    ),
    "group_policy": _SectionClassification(
        ContextLayer.STABLE_POLICY,
        ContextScope.GLOBAL,
        ContextStability.STABLE,
        "prompt_template",
    ),
    "qq_group_policy": _SectionClassification(
        ContextLayer.STABLE_POLICY,
        ContextScope.GLOBAL,
        ContextStability.STABLE,
        "prompt_template",
    ),
    "private_policy": _SectionClassification(
        ContextLayer.STABLE_POLICY,
        ContextScope.GLOBAL,
        ContextStability.STABLE,
        "prompt_template",
    ),
    "session_guidance": _SectionClassification(
        ContextLayer.DYNAMIC_CONTEXT,
        ContextScope.SESSION,
        ContextStability.DYNAMIC,
        "session_guidance",
    ),
    "runtime_context": _SectionClassification(
        ContextLayer.DYNAMIC_CONTEXT,
        ContextScope.SESSION,
        ContextStability.DYNAMIC,
        "request_runtime",
    ),
    "persona_reference": _SectionClassification(
        ContextLayer.MEMORY_RECALL,
        ContextScope.USER,
        ContextStability.DYNAMIC,
        "persona_fact",
    ),
    "group_context": _SectionClassification(
        ContextLayer.MEMORY_RECALL,
        ContextScope.GROUP,
        ContextStability.DYNAMIC,
        "group_memory",
    ),
    "project_context": _SectionClassification(
        ContextLayer.MEMORY_RECALL,
        ContextScope.PROJECT,
        ContextStability.DYNAMIC,
        "project_context",
    ),
    "summary_context": _SectionClassification(
        ContextLayer.SUMMARY,
        ContextScope.SESSION,
        ContextStability.FROZEN,
        "rolling_summary",
    ),
    "history_messages": _SectionClassification(
        ContextLayer.RECENT_CONVERSATION,
        ContextScope.SESSION,
        ContextStability.DYNAMIC,
        "conversation_turn",
    ),
    "current_user_event": _SectionClassification(
        ContextLayer.CURRENT_REQUEST,
        ContextScope.TURN,
        ContextStability.DYNAMIC,
        "inbound_message",
    ),
})


_BASE_LAYER_LIMITS = MappingProxyType({
    ContextLayer.STABLE_SYSTEM: 12_000,
    ContextLayer.SECURITY_POLICY: 16_000,
    ContextLayer.STABLE_POLICY: 16_000,
    ContextLayer.TOOL_CONTRACT: 64_000,
    ContextLayer.DYNAMIC_CONTEXT: 6_000,
    ContextLayer.MEMORY_RECALL: 12_000,
    ContextLayer.SUMMARY: 8_000,
    ContextLayer.RECENT_CONVERSATION: 8_000,
    ContextLayer.TOOL_RESULT: 16_000,
    ContextLayer.CURRENT_REQUEST: 8_000,
})


def _normalize_source_ref(value: object) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    if not text:
        return ""
    if len(text) <= 160:
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


def hashed_context_ref(prefix: str, value: object) -> str:
    """把会话、消息等外部标识转换成不泄露原值的稳定引用。"""

    normalized_prefix = str(prefix or "context").strip()[:32] or "context"
    text = str(value or "").strip()
    if not text:
        return ""
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"{normalized_prefix}:sha256:{digest}"


def context_layer_limits(chat_type: str) -> Mapping[ContextLayer, int]:
    """返回当前会话类型的服务端预算；调用方不能通过请求提高限额。"""

    limits = dict(_BASE_LAYER_LIMITS)
    if str(chat_type or "").strip().lower() == "group":
        limits[ContextLayer.RECENT_CONVERSATION] = 24_000
    return MappingProxyType(limits)


def _coerce_provenance(
    value: ContextProvenance | Mapping[str, Any] | None,
    *,
    default_kind: str,
    default_refs: Sequence[object] = (),
) -> ContextProvenance:
    if isinstance(value, ContextProvenance):
        return value
    if isinstance(value, Mapping):
        raw_refs = value.get("source_refs")
        refs = tuple(raw_refs) if isinstance(raw_refs, (list, tuple)) else ()
        return ContextProvenance(
            str(value.get("source_kind") or default_kind),
            refs,
        )
    return ContextProvenance(
        default_kind,
        tuple(str(item) for item in default_refs if str(item or "").strip()),
    )


def _section_payload(
    messages: Sequence[Mapping[str, Any]],
    indexes: Sequence[int],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for index in indexes:
        if type(index) is not int or index < 0 or index >= len(messages):
            raise ContextManifestError("context section message index 越界")
        message = messages[index]
        if not isinstance(message, Mapping):
            raise ContextManifestError("context section message 必须是对象")
        payload.append(copy.deepcopy(dict(message)))
    return payload


def _classification_for_section(
    section: Mapping[str, Any],
    *,
    chat_type: str,
) -> _SectionClassification:
    node_id = str(section.get("node_id") or "").strip()
    runtime_key = str(section.get("runtime_key") or "").strip()
    classification = _SECTION_CLASSIFICATIONS.get(node_id)
    classification = classification or _SECTION_CLASSIFICATIONS.get(runtime_key)
    if classification is None:
        return _SectionClassification(
            ContextLayer.DYNAMIC_CONTEXT,
            ContextScope.SESSION,
            ContextStability.DYNAMIC,
            "extension_context",
        )
    if node_id == "summary_context" and str(chat_type).lower() == "group":
        return _SectionClassification(
            classification.layer,
            ContextScope.GROUP,
            classification.stability,
            classification.source_kind,
        )
    if node_id == "history_messages" and str(chat_type).lower() == "group":
        return _SectionClassification(
            classification.layer,
            ContextScope.GROUP,
            classification.stability,
            "chat_log",
        )
    return classification


def build_prompt_context_manifest(
    *,
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
    flow_sections: Sequence[Mapping[str, Any]],
    section_hashes: Mapping[str, str],
    request_prompt_sha256: str,
    chat_type: str,
    provenance: Mapping[str, ContextProvenance | Mapping[str, Any]] | None = None,
) -> ContextManifest:
    """从最终 Prompt envelope 生成安全 Context Manifest 并执行预算门禁。"""

    normalized_chat_type = (
        "group" if str(chat_type or "").strip().lower() == "group" else "private"
    )
    provenance = dict(provenance or {})
    entries: list[ContextManifestEntry] = []
    for raw_section in flow_sections:
        if not isinstance(raw_section, Mapping):
            raise ContextManifestError("context flow section 必须是对象")
        section = dict(raw_section)
        status = str(section.get("status") or "").strip()
        indexes = tuple(section.get("message_indexes") or ())
        if status != "emitted" or not indexes:
            continue
        node_id = str(section.get("node_id") or "").strip()
        runtime_key = str(section.get("runtime_key") or "").strip()
        entry_id = node_id or runtime_key
        classification = _classification_for_section(
            section,
            chat_type=normalized_chat_type,
        )
        payload = _section_payload(messages, indexes)
        payload_json = stable_json(payload)
        digest = str(section_hashes.get(node_id) or "").strip().lower()
        if len(digest) != 64:
            digest = sha256_text(payload_json)
        source = _coerce_provenance(
            provenance.get(node_id) or provenance.get(runtime_key),
            default_kind=classification.source_kind,
            default_refs=(
                str(section.get("template_key") or "").strip(),
                str(section.get("active_source") or "").strip(),
            ),
        )
        entries.append(ContextManifestEntry(
            entry_id=entry_id,
            layer=classification.layer,
            scope=classification.scope,
            stability=classification.stability,
            source_kind=source.source_kind,
            source_refs=source.source_refs,
            token_estimate=estimate_tokens(payload_json),
            content_sha256=digest,
            message_indexes=indexes,
            authority=str(section.get("authority") or "data"),
            trust=str(section.get("trust") or "untrusted_data"),
        ))

    normalized_tools = [copy.deepcopy(dict(item)) for item in tool_schemas]
    if normalized_tools:
        tool_payload = stable_json(normalized_tools)
        tool_names: list[str] = []
        for schema in normalized_tools:
            function = schema.get("function")
            if isinstance(function, Mapping):
                name = str(function.get("name") or "").strip()
                if name:
                    tool_names.append(name)
        source = _coerce_provenance(
            provenance.get("tool_schemas"),
            default_kind="tool_plan",
            default_refs=tuple(f"tool:{name}" for name in tool_names),
        )
        entries.append(ContextManifestEntry(
            entry_id="tool_schemas",
            layer=ContextLayer.TOOL_CONTRACT,
            scope=ContextScope.TURN,
            stability=ContextStability.FROZEN,
            source_kind=source.source_kind,
            source_refs=source.source_refs,
            token_estimate=estimate_tokens(tool_payload),
            content_sha256=sha256_text(tool_payload),
            authority="tool",
            trust="trusted_instruction",
        ))

    limits = context_layer_limits(normalized_chat_type)
    usage = {layer: 0 for layer in ContextLayer}
    for entry in entries:
        usage[entry.layer] += entry.token_estimate
    budgets: list[ContextLayerBudget] = []
    for layer in ContextLayer:
        used_tokens = usage[layer]
        max_tokens = int(limits[layer])
        if used_tokens > max_tokens:
            raise ContextBudgetExceededError(layer, used_tokens, max_tokens)
        budgets.append(ContextLayerBudget(
            layer=layer,
            max_tokens=max_tokens,
            used_tokens=used_tokens,
        ))

    return ContextManifest(
        policy_id=f"prompt-context-v1-{normalized_chat_type}",
        request_prompt_sha256=request_prompt_sha256,
        entries=tuple(entries),
        layer_budgets=tuple(budgets),
    )


def validate_context_manifest(value: Mapping[str, Any]) -> None:
    """验证序列化 Manifest 的签名、计量和无正文合同。"""

    if not isinstance(value, Mapping):
        raise ContextManifestError("context manifest 必须是对象")
    manifest = copy.deepcopy(dict(value))
    digest = str(manifest.pop("sha256", "") or "").strip().lower()
    expected = sha256_text(stable_json(manifest))
    if digest != expected:
        raise ContextManifestError("context manifest sha256 不匹配")
    if manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION:
        raise ContextManifestError("context manifest schema_version 不支持")
    if set(manifest) != {
        "schema_version",
        "policy_id",
        "request_prompt_sha256",
        "entries",
        "layer_budgets",
        "scope_usage",
        "total_tokens",
    }:
        raise ContextManifestError("context manifest 顶层字段无效")
    policy_id = str(manifest.get("policy_id") or "").strip()
    if not policy_id or len(policy_id) > 96:
        raise ContextManifestError("context manifest policy_id 无效")
    prompt_digest = str(manifest.get("request_prompt_sha256") or "")
    if not _is_sha256(prompt_digest):
        raise ContextManifestError("context manifest prompt 摘要无效")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ContextManifestError("context manifest entries 必须是列表")
    expected_entry_fields = {
        "entry_id",
        "layer",
        "scope",
        "stability",
        "source_kind",
        "source_refs",
        "token_estimate",
        "content_sha256",
        "message_indexes",
        "authority",
        "trust",
    }
    entry_ids: set[str] = set()
    layer_usage = {layer: 0 for layer in ContextLayer}
    scope_usage = {scope: 0 for scope in ContextScope}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected_entry_fields:
            raise ContextManifestError("context manifest entry 字段无效")
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id or entry_id in entry_ids:
            raise ContextManifestError("context manifest entry_id 无效或重复")
        entry_ids.add(entry_id)
        try:
            layer = ContextLayer(entry.get("layer"))
            scope = ContextScope(entry.get("scope"))
            ContextStability(entry.get("stability"))
        except (TypeError, ValueError) as exc:
            raise ContextManifestError("context manifest entry 枚举值无效") from exc
        tokens = entry.get("token_estimate")
        if type(tokens) is not int or tokens < 0:
            raise ContextManifestError("context manifest entry token 无效")
        content_digest = str(entry.get("content_sha256") or "")
        if not _is_sha256(content_digest):
            raise ContextManifestError("context manifest entry 摘要无效")
        source_kind = str(entry.get("source_kind") or "").strip()
        if not source_kind or len(source_kind) > 64:
            raise ContextManifestError("context manifest source_kind 无效")
        refs = entry.get("source_refs")
        if (
            not isinstance(refs, list)
            or len(refs) > 64
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 160
                for item in refs
            )
        ):
            raise ContextManifestError("context manifest source_refs 无效")
        indexes = entry.get("message_indexes")
        if (
            not isinstance(indexes, list)
            or len(indexes) != len(set(indexes))
            or any(type(item) is not int or item < 0 for item in indexes)
        ):
            raise ContextManifestError("context manifest message_indexes 无效")
        for field_name in ("authority", "trust"):
            field_value = str(entry.get(field_name) or "").strip()
            if not field_value or len(field_value) > 64:
                raise ContextManifestError(
                    f"context manifest {field_name} 无效"
                )
        layer_usage[layer] += tokens
        scope_usage[scope] += tokens

    budgets = manifest.get("layer_budgets")
    if not isinstance(budgets, list) or len(budgets) != len(ContextLayer):
        raise ContextManifestError("context manifest layer_budgets 无效")
    seen_layers: set[ContextLayer] = set()
    for budget in budgets:
        if not isinstance(budget, dict) or set(budget) != {
            "layer", "max_tokens", "used_tokens", "overflow_policy"
        }:
            raise ContextManifestError("context manifest budget 字段无效")
        try:
            layer = ContextLayer(budget.get("layer"))
        except (TypeError, ValueError) as exc:
            raise ContextManifestError("context manifest budget layer 无效") from exc
        if layer in seen_layers:
            raise ContextManifestError("context manifest budget layer 重复")
        seen_layers.add(layer)
        if budget.get("overflow_policy") != "reject":
            raise ContextManifestError("context manifest overflow_policy 无效")
        used_tokens = budget.get("used_tokens")
        if type(used_tokens) is not int or used_tokens < 0:
            raise ContextManifestError("context manifest used_tokens 无效")
        if used_tokens != layer_usage[layer]:
            raise ContextManifestError("context manifest layer usage 不匹配")
        max_tokens = budget.get("max_tokens")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ContextManifestError("context manifest max_tokens 无效")
        if layer_usage[layer] > max_tokens:
            raise ContextManifestError("context manifest 层预算超限")

    scopes = manifest.get("scope_usage")
    if not isinstance(scopes, list) or len(scopes) != len(ContextScope):
        raise ContextManifestError("context manifest scope_usage 无效")
    seen_scopes: set[ContextScope] = set()
    for item in scopes:
        if not isinstance(item, dict) or set(item) != {"scope", "used_tokens"}:
            raise ContextManifestError("context manifest scope usage 字段无效")
        try:
            scope = ContextScope(item.get("scope"))
        except (TypeError, ValueError) as exc:
            raise ContextManifestError("context manifest scope 无效") from exc
        used_tokens = item.get("used_tokens")
        if type(used_tokens) is not int or used_tokens < 0:
            raise ContextManifestError("context manifest scope token 无效")
        if scope in seen_scopes or used_tokens != scope_usage[scope]:
            raise ContextManifestError("context manifest scope usage 不匹配")
        seen_scopes.add(scope)
    if manifest.get("total_tokens") != sum(layer_usage.values()):
        raise ContextManifestError("context manifest total_tokens 不匹配")


def context_manifest_fingerprint(value: Mapping[str, Any]) -> tuple[str, int, int]:
    """返回已验证 Manifest 的摘要、entry 数和 token 数。"""

    validate_context_manifest(value)
    payload = dict(value)
    return (
        str(payload["sha256"]),
        len(list(payload.get("entries") or [])),
        int(payload.get("total_tokens") or 0),
    )


__all__ = [
    "CONTEXT_MANIFEST_SCHEMA_VERSION",
    "ContextBudgetExceededError",
    "ContextLayer",
    "ContextLayerBudget",
    "ContextManifest",
    "ContextManifestEntry",
    "ContextManifestError",
    "ContextProvenance",
    "ContextScope",
    "ContextStability",
    "build_prompt_context_manifest",
    "context_layer_limits",
    "context_manifest_fingerprint",
    "hashed_context_ref",
    "validate_context_manifest",
]
