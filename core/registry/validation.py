"""Registry Kernel 的通用验证和规范化逻辑。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any


_IDENTIFIER_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
)
_RESOURCE_IDENTIFIER_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$"
)
_MAX_IDENTIFIER_LENGTH = 64


class RegistryError(RuntimeError):
    """Registry Kernel 的稳定错误基类。"""


class RegistryValidationError(RegistryError, ValueError):
    """描述符或构建参数不满足 Kernel 合同。"""


class RegistryConflictError(RegistryError):
    """同一 namespace 中出现重复 ID 或未授权替换。"""


class RegistryDependencyError(RegistryError):
    """依赖缺失或拓扑存在环。"""


class RegistryFrozenError(RegistryError):
    """冻结后的 Builder 收到写操作。"""


class RegistryPublishConflictError(RegistryError):
    """候选构建期间已有其他 generation 完成发布。"""


def validate_identifier(
    value: object,
    *,
    field_name: str,
    allow_path: bool = False,
) -> str:
    """验证稳定标识符；不做静默 trim 或大小写归一化。"""

    if not isinstance(value, str) or not value:
        raise RegistryValidationError(f"{field_name} 必须是非空字符串")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise RegistryValidationError(
            f"{field_name} 长度不能超过 {_MAX_IDENTIFIER_LENGTH}"
        )
    pattern = (
        _RESOURCE_IDENTIFIER_PATTERN
        if allow_path
        else _IDENTIFIER_PATTERN
    )
    if pattern.fullmatch(value) is None:
        raise RegistryValidationError(
            f"{field_name} 不是合法稳定标识符: {value!r}"
        )
    return value


def _validate_immutable_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(
        value,
        (str, bytes, int, float, bool, Enum),
    ):
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_immutable_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, frozenset):
        for item in value:
            _validate_immutable_value(item, path=f"{path}[]")
        return
    if isinstance(value, MappingProxyType):
        for key, item in value.items():
            _validate_immutable_value(key, path=f"{path}.key")
            _validate_immutable_value(item, path=f"{path}[{key!r}]")
        return
    if is_dataclass(value):
        params = getattr(type(value), "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise RegistryValidationError(
                f"{path} 必须是不可变 dataclass"
            )
        for descriptor_field in fields(value):
            _validate_immutable_value(
                getattr(value, descriptor_field.name),
                path=f"{path}.{descriptor_field.name}",
            )
        return
    raise RegistryValidationError(
        f"{path} 包含无法证明不可变的 {type(value).__name__}"
    )


def validate_descriptor(
    descriptor: object,
    *,
    expected_namespace: str,
) -> tuple[str, tuple[str, ...], Mapping[str, object]]:
    """验证描述符身份、依赖、不可变性和 Hash payload。"""

    params = getattr(type(descriptor), "__dataclass_params__", None)
    if not is_dataclass(descriptor) or params is None or not params.frozen:
        raise RegistryValidationError(
            "Registry descriptor 必须是 frozen dataclass，确保对外不可变"
        )
    _validate_immutable_value(descriptor, path="descriptor")

    namespace = validate_identifier(
        getattr(descriptor, "registry_namespace", None),
        field_name="descriptor.registry_namespace",
    )
    if namespace != expected_namespace:
        raise RegistryValidationError(
            "descriptor namespace 与 Builder namespace 不一致: "
            f"{namespace!r} != {expected_namespace!r}"
        )
    descriptor_id = validate_identifier(
        getattr(descriptor, "registry_id", None),
        field_name="descriptor.registry_id",
        allow_path=True,
    )

    dependencies_value = getattr(
        descriptor,
        "registry_dependencies",
        None,
    )
    if not isinstance(dependencies_value, tuple):
        raise RegistryValidationError(
            "descriptor.registry_dependencies 必须是 tuple"
        )
    dependencies = tuple(
        validate_identifier(
            dependency,
            field_name=f"{descriptor_id}.dependency",
            allow_path=True,
        )
        for dependency in dependencies_value
    )
    if len(dependencies) != len(set(dependencies)):
        raise RegistryValidationError(
            f"Registry descriptor {descriptor_id} 的依赖不能重复"
        )
    if descriptor_id in dependencies:
        raise RegistryDependencyError(
            f"Registry descriptor {descriptor_id} 不能依赖自身"
        )

    payload_factory = getattr(descriptor, "registry_payload", None)
    if not callable(payload_factory):
        raise RegistryValidationError(
            f"Registry descriptor {descriptor_id} 缺少 registry_payload()"
        )
    payload = payload_factory()
    if not isinstance(payload, Mapping):
        raise RegistryValidationError(
            f"Registry descriptor {descriptor_id} payload 必须是 Mapping"
        )
    normalize_json_value(payload, path=f"{descriptor_id}.payload")
    return descriptor_id, dependencies, payload


def normalize_json_value(value: Any, *, path: str = "value") -> Any:
    """转换为可确定性编码的 JSON 值，并拒绝含糊或不稳定类型。"""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RegistryValidationError(
                f"{path} 不能包含 NaN 或 Infinity"
            )
        return value
    if isinstance(value, Enum):
        return normalize_json_value(value.value, path=path)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RegistryValidationError(
                    f"{path} 的 JSON object key 必须是字符串"
                )
            normalized[key] = normalize_json_value(
                item,
                path=f"{path}.{key}",
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            normalize_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise RegistryValidationError(
        f"{path} 包含不可 JSON 序列化的 {type(value).__name__}"
    )


def canonical_json(value: object) -> str:
    """生成跨注册顺序稳定的 canonical JSON。"""

    normalized = normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def resolve_dependency_order(
    dependencies_by_id: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """按 ID 确定性排序同层节点，并保证依赖先于使用方。"""

    known = frozenset(dependencies_by_id)
    for descriptor_id, dependencies in dependencies_by_id.items():
        missing = sorted(set(dependencies) - known)
        if missing:
            raise RegistryDependencyError(
                f"Registry descriptor {descriptor_id} 依赖未注册 ID: {missing}"
            )

    remaining = set(known)
    resolved: list[str] = []
    resolved_set: set[str] = set()
    while remaining:
        ready = sorted(
            descriptor_id
            for descriptor_id in remaining
            if set(dependencies_by_id[descriptor_id]).issubset(resolved_set)
        )
        if not ready:
            raise RegistryDependencyError(
                f"Registry 存在循环依赖: {sorted(remaining)}"
            )
        for descriptor_id in ready:
            remaining.remove(descriptor_id)
            resolved.append(descriptor_id)
            resolved_set.add(descriptor_id)
    return tuple(resolved)
