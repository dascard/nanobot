"""Canonical Prompt 的稳定前缀与工具顺序合同。

本模块只生成哈希、计量和顺序证明，不保存 Prompt 正文。稳定前缀仅允许
system／安全／稳定策略层。遇到请求级动态上下文后立即结束前缀，后续内容
即使自身稳定也只属于动态后缀，避免时间戳等请求态污染缓存边界。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.context_engine import (
    ContextLayer,
    ContextManifestError,
    validate_context_manifest,
)
from core.prompt_v2.section_renderer import estimate_tokens, sha256_text, stable_json


PREFIX_CACHE_MANIFEST_SCHEMA_VERSION = "1.0"
_STABLE_PREFIX_LAYERS = frozenset({
    ContextLayer.STABLE_SYSTEM.value,
    ContextLayer.SECURITY_POLICY.value,
    ContextLayer.STABLE_POLICY.value,
})
_STABLE_PREFIX_ENTRY_IDS = frozenset({
    "base_contract",
    "qq_common_policy",
    "group_policy",
    "qq_group_policy",
    "private_policy",
    "identity_context",
})


class PromptPrefixCacheError(ValueError):
    """Prompt 前缀缓存合同无效。"""


def _tool_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "").strip()
    return str(schema.get("name") or "").strip()


def canonicalize_tool_schemas(
    schemas: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按工具名和完整 schema 摘要生成唯一、确定性的 wire 顺序。"""

    normalized: list[dict[str, Any]] = []
    for index, schema in enumerate(schemas or ()):
        if not isinstance(schema, Mapping):
            raise PromptPrefixCacheError(
                f"tool schema[{index}] 必须是对象"
            )
        normalized.append(copy.deepcopy(dict(schema)))
    normalized.sort(
        key=lambda item: (
            _tool_name(item),
            sha256_text(stable_json(item)),
        )
    )
    names = [_tool_name(item) for item in normalized]
    named = [name for name in names if name]
    if len(named) != len(set(named)):
        raise PromptPrefixCacheError("tool schema function.name 不能重复")
    return normalized


@dataclass(frozen=True, slots=True)
class PromptPrefixCacheManifest:
    policy_id: str
    stable_entry_ids: tuple[str, ...]
    stable_message_count: int
    dynamic_suffix_start_index: int
    stable_prefix_sha256: str
    stable_prefix_token_estimate: int
    tool_schema_sha256: str
    tool_names: tuple[str, ...]
    canonical_order_sha256: str
    cache_key: str

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PREFIX_CACHE_MANIFEST_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "stable_entry_ids": list(self.stable_entry_ids),
            "stable_message_count": self.stable_message_count,
            "dynamic_suffix_start_index": self.dynamic_suffix_start_index,
            "stable_prefix_sha256": self.stable_prefix_sha256,
            "stable_prefix_token_estimate": self.stable_prefix_token_estimate,
            "tool_schema_sha256": self.tool_schema_sha256,
            "tool_names": list(self.tool_names),
            "canonical_order_sha256": self.canonical_order_sha256,
            "cache_key": self.cache_key,
        }

    @property
    def sha256(self) -> str:
        return sha256_text(stable_json(self._unsigned_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "sha256": self.sha256}


def build_prompt_prefix_cache_manifest(
    *,
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
    flow_sections: Sequence[Mapping[str, Any]],
    context_manifest: Mapping[str, Any],
) -> PromptPrefixCacheManifest:
    """从已审计 Context Manifest 构建不含正文的稳定前缀证明。"""

    try:
        validate_context_manifest(context_manifest)
    except ContextManifestError as exc:
        raise PromptPrefixCacheError(
            f"context manifest 无效：{exc}"
        ) from exc
    entries = context_manifest.get("entries")
    assert isinstance(entries, list)
    entries_by_id = {
        str(entry.get("entry_id") or ""): entry
        for entry in entries
        if isinstance(entry, Mapping)
    }

    expected_message_index = 0
    dynamic_seen = False
    stable_entry_ids: list[str] = []
    ordered_node_ids: list[str] = []
    for raw_section in flow_sections:
        if not isinstance(raw_section, Mapping):
            raise PromptPrefixCacheError("flow section 必须是对象")
        node_id = str(raw_section.get("node_id") or "").strip()
        if node_id:
            ordered_node_ids.append(node_id)
        if str(raw_section.get("status") or "") != "emitted":
            continue
        indexes = list(raw_section.get("message_indexes") or [])
        if not indexes:
            continue
        entry = entries_by_id.get(node_id)
        if entry is None:
            dynamic_seen = True
            continue
        is_stable = (
            node_id in _STABLE_PREFIX_ENTRY_IDS
            and str(entry.get("layer") or "") in _STABLE_PREFIX_LAYERS
        )
        if is_stable and dynamic_seen:
            continue
        if not is_stable:
            dynamic_seen = True
            continue
        expected_indexes = list(range(
            expected_message_index,
            expected_message_index + len(indexes),
        ))
        if indexes != expected_indexes:
            raise PromptPrefixCacheError(
                f"稳定段 {node_id} 未形成连续 Prompt 前缀"
            )
        expected_message_index += len(indexes)
        stable_entry_ids.append(node_id)

    if expected_message_index <= 0:
        raise PromptPrefixCacheError("Prompt 缺少稳定前缀")
    if expected_message_index > len(messages):
        raise PromptPrefixCacheError("稳定前缀 message 数量越界")

    canonical_tools = canonicalize_tool_schemas(tool_schemas)
    if canonical_tools != [copy.deepcopy(dict(item)) for item in tool_schemas]:
        raise PromptPrefixCacheError("tool schema 未使用 canonical 顺序")
    tool_names = tuple(_tool_name(item) for item in canonical_tools)
    stable_messages: list[dict[str, Any]] = []
    for index, item in enumerate(messages[:expected_message_index]):
        if not isinstance(item, Mapping):
            raise PromptPrefixCacheError(
                f"稳定前缀 message[{index}] 必须是对象"
            )
        stable_messages.append(copy.deepcopy(dict(item)))
    stable_payload = stable_json(stable_messages)
    tool_payload = stable_json(canonical_tools)
    stable_prefix_sha256 = sha256_text(stable_payload)
    tool_schema_sha256 = sha256_text(tool_payload)
    canonical_order_sha256 = sha256_text(stable_json({
        "flow_node_ids": ordered_node_ids,
        "tool_names": list(tool_names),
    }))
    policy_id = "prompt-prefix-cache-v1"
    cache_key = sha256_text(stable_json({
        "policy_id": policy_id,
        "stable_prefix_sha256": stable_prefix_sha256,
        "tool_schema_sha256": tool_schema_sha256,
    }))
    return PromptPrefixCacheManifest(
        policy_id=policy_id,
        stable_entry_ids=tuple(stable_entry_ids),
        stable_message_count=expected_message_index,
        dynamic_suffix_start_index=expected_message_index,
        stable_prefix_sha256=stable_prefix_sha256,
        stable_prefix_token_estimate=estimate_tokens(stable_payload),
        tool_schema_sha256=tool_schema_sha256,
        tool_names=tool_names,
        canonical_order_sha256=canonical_order_sha256,
        cache_key=cache_key,
    )


def validate_prompt_prefix_cache_manifest(value: Mapping[str, Any]) -> None:
    """验证序列化前缀合同的字段、签名和无正文边界。"""

    if not isinstance(value, Mapping):
        raise PromptPrefixCacheError("prefix cache manifest 必须是对象")
    payload = copy.deepcopy(dict(value))
    digest = str(payload.pop("sha256", "") or "").lower()
    if digest != sha256_text(stable_json(payload)):
        raise PromptPrefixCacheError("prefix cache manifest sha256 不匹配")
    expected_fields = {
        "schema_version",
        "policy_id",
        "stable_entry_ids",
        "stable_message_count",
        "dynamic_suffix_start_index",
        "stable_prefix_sha256",
        "stable_prefix_token_estimate",
        "tool_schema_sha256",
        "tool_names",
        "canonical_order_sha256",
        "cache_key",
    }
    if set(payload) != expected_fields:
        raise PromptPrefixCacheError("prefix cache manifest 字段无效")
    if payload.get("schema_version") != PREFIX_CACHE_MANIFEST_SCHEMA_VERSION:
        raise PromptPrefixCacheError("prefix cache manifest 版本不支持")
    if payload.get("policy_id") != "prompt-prefix-cache-v1":
        raise PromptPrefixCacheError("prefix cache policy_id 无效")
    for key in (
        "stable_prefix_sha256",
        "tool_schema_sha256",
        "canonical_order_sha256",
        "cache_key",
    ):
        value_digest = str(payload.get(key) or "").lower()
        if len(value_digest) != 64 or any(
            char not in "0123456789abcdef" for char in value_digest
        ):
            raise PromptPrefixCacheError(f"prefix cache {key} 无效")
    stable_count = payload.get("stable_message_count")
    suffix_index = payload.get("dynamic_suffix_start_index")
    tokens = payload.get("stable_prefix_token_estimate")
    if type(stable_count) is not int or stable_count <= 0:
        raise PromptPrefixCacheError("prefix cache stable_message_count 无效")
    if suffix_index != stable_count:
        raise PromptPrefixCacheError("prefix cache 动态后缀边界无效")
    if type(tokens) is not int or tokens < 0:
        raise PromptPrefixCacheError("prefix cache token 计量无效")
    for key in ("stable_entry_ids", "tool_names"):
        items = payload.get(key)
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or len(item) > 128 for item in items)
        ):
            raise PromptPrefixCacheError(f"prefix cache {key} 无效")
    names = list(payload.get("tool_names") or [])
    if names != sorted(names) or len(names) != len(set(name for name in names if name)):
        raise PromptPrefixCacheError("prefix cache tool_names 顺序无效")


__all__ = [
    "PREFIX_CACHE_MANIFEST_SCHEMA_VERSION",
    "PromptPrefixCacheError",
    "PromptPrefixCacheManifest",
    "build_prompt_prefix_cache_manifest",
    "canonicalize_tool_schemas",
    "validate_prompt_prefix_cache_manifest",
]
