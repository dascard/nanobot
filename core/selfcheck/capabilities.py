"""跨 API、WebUI、Agent 与共享运行面的自检能力清单。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from fastapi import FastAPI

from core.model_provider.route_registry import list_model_route_descriptors
from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier
from core.tool_registration import TOOL_REGISTRATION_REGISTRY


CapabilityKind = Literal[
    "api",
    "webui",
    "agent",
    "tool",
    "model_route",
    "rag_source",
    "worker",
    "integration",
    "storage",
]
CapabilityCriticality = Literal["critical", "high", "medium", "low"]
CapabilityCoveragePolicy = Literal["required", "optional", "exempt"]
CapabilityCoverageStatus = Literal["covered", "unverified", "exempted"]

_KINDS = frozenset({
    "api",
    "webui",
    "agent",
    "tool",
    "model_route",
    "rag_source",
    "worker",
    "integration",
    "storage",
})
_CRITICALITIES = frozenset({"critical", "high", "medium", "low"})
_COVERAGE_POLICIES = frozenset({"required", "optional", "exempt"})
_OPERATION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WEBUI_CAPABILITIES = (
    _PROJECT_ROOT / "config" / "webui-capabilities.v1.json"
)

RAG_CAPABILITY_SOURCES = (
    "memory",
    "memory_digest",
    "session_summary",
    "group_memory",
    "sticker",
    "knowledge",
    "group_analysis",
    "all",
)

WORKER_CAPABILITY_SOURCES = (
    "session-summary-worker",
    "outbound-delivery-worker",
    "semantic-index-worker",
    "daily-digest-scheduler",
    "scheduled-task-runner",
    "chat-delivery-worker",
    "eval-sampling-scheduler",
    "proactive-outreach-scheduler",
    "group-learning-scheduler",
    "selfcheck-watchdog",
)


def _nonempty_text(value: object, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    if value != value.strip():
        raise ValueError(f"{field_name} 不能包含首尾空白")
    if "\x00" in value or len(value) > maximum:
        raise ValueError(f"{field_name} 非法")
    return value


def _unique_identifiers(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} 必须是 tuple")
    normalized = tuple(
        validate_identifier(value, field_name=field_name, allow_path=True)
        for value in values
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} 不能重复")
    return tuple(sorted(normalized))


def _unique_operation_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError("capability.related_operation_ids 必须是 tuple")
    if any(_OPERATION_ID_RE.fullmatch(value) is None for value in values):
        raise ValueError("capability.related_operation_ids 包含非法 operation_id")
    if len(values) != len(set(values)):
        raise ValueError("capability.related_operation_ids 不能重复")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """一个可寻址能力及其真实自检覆盖状态。"""

    capability_id: str
    kind: CapabilityKind
    source_id: str
    label: str
    owner: str
    criticality: CapabilityCriticality = "medium"
    lifecycle: str = "active"
    coverage_policy: CapabilityCoveragePolicy = "required"
    probe_ids: tuple[str, ...] = ()
    verification_suite_ids: tuple[str, ...] = ()
    related_operation_ids: tuple[str, ...] = ()
    exemption_reason: str = ""
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        validate_identifier(
            self.capability_id,
            field_name="capability.capability_id",
            allow_path=True,
        )
        if self.kind not in _KINDS:
            raise ValueError("capability.kind 非法")
        if self.criticality not in _CRITICALITIES:
            raise ValueError("capability.criticality 非法")
        if self.coverage_policy not in _COVERAGE_POLICIES:
            raise ValueError("capability.coverage_policy 非法")
        _nonempty_text(self.source_id, field_name="capability.source_id", maximum=512)
        _nonempty_text(self.label, field_name="capability.label", maximum=256)
        _nonempty_text(self.owner, field_name="capability.owner", maximum=128)
        _nonempty_text(self.lifecycle, field_name="capability.lifecycle", maximum=64)
        object.__setattr__(
            self,
            "probe_ids",
            _unique_identifiers(self.probe_ids, field_name="capability.probe_ids"),
        )
        object.__setattr__(
            self,
            "verification_suite_ids",
            _unique_identifiers(
                self.verification_suite_ids,
                field_name="capability.verification_suite_ids",
            ),
        )
        object.__setattr__(
            self,
            "related_operation_ids",
            _unique_operation_ids(self.related_operation_ids),
        )
        if not isinstance(self.attributes, tuple):
            raise ValueError("capability.attributes 必须是 tuple")
        attribute_keys: set[str] = set()
        normalized_attributes: list[tuple[str, str]] = []
        for item in self.attributes:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("capability.attributes 项必须是二元 tuple")
            key, value = item
            key = _nonempty_text(
                key,
                field_name="capability.attributes.key",
                maximum=128,
            )
            if key in attribute_keys:
                raise ValueError("capability.attributes key 不能重复")
            if not isinstance(value, str) or "\x00" in value or len(value) > 2048:
                raise ValueError("capability.attributes value 非法")
            attribute_keys.add(key)
            normalized_attributes.append((key, value))
        object.__setattr__(
            self,
            "attributes",
            tuple(sorted(normalized_attributes)),
        )
        reason = str(self.exemption_reason or "")
        if self.coverage_policy == "exempt":
            if not reason.strip():
                raise ValueError("覆盖豁免必须声明豁免原因")
            if self.probe_ids:
                raise ValueError("已豁免能力不能同时声明 probe")
        elif reason:
            raise ValueError("非豁免能力不能声明豁免原因")
        if len(reason) > 512 or "\x00" in reason:
            raise ValueError("capability.exemption_reason 非法")

    @property
    def registry_namespace(self) -> str:
        return "selfcheck_capability"

    @property
    def registry_id(self) -> str:
        return self.capability_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def coverage_status(self) -> CapabilityCoverageStatus:
        if self.coverage_policy == "exempt":
            return "exempted"
        if self.probe_ids:
            return "covered"
        return "unverified"

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "label": self.label,
            "owner": self.owner,
            "criticality": self.criticality,
            "lifecycle": self.lifecycle,
            "coverage_policy": self.coverage_policy,
            "coverage_status": self.coverage_status,
            "probe_ids": list(self.probe_ids),
            "verification_suite_ids": list(self.verification_suite_ids),
            "related_operation_ids": list(self.related_operation_ids),
            "exemption_reason": self.exemption_reason,
            "attributes": dict(self.attributes),
        }

    def to_public_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            **self.registry_payload(),
        }


def _hashed_capability_id(prefix: str, source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}.{digest}"


def _attributes(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(key), str(value if value is not None else ""))
            for key, value in values.items()
        )
    )


def _api_capabilities(
    app: FastAPI,
    endpoint_contracts: Iterable[object],
) -> tuple[CapabilityDescriptor, ...]:
    contracts_by_route = {
        (
            str(getattr(contract, "method", "")).upper(),
            str(getattr(contract, "path", "")),
        ): contract
        for contract in endpoint_contracts
    }
    descriptors: list[CapabilityDescriptor] = []
    schema = app.openapi()
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI paths 缺失")
    for path, path_item in sorted(paths.items()):
        if not str(path).startswith("/api/") or not isinstance(path_item, dict):
            continue
        for method_lower in ("get", "put", "post", "delete", "patch"):
            operation = path_item.get(method_lower)
            if not isinstance(operation, dict):
                continue
            method = method_lower.upper()
            source_id = f"{method} {path}"
            contract = contracts_by_route.get((method, path))
            operation_id = str(operation.get("operationId") or "")
            contract_id = str(getattr(contract, "contract_id", "") or "")
            tags = operation.get("tags")
            owner = str(
                getattr(contract, "owner_module", "")
                or (
                    tags[0]
                    if isinstance(tags, list) and tags
                    else "api.compatibility"
                )
            )
            criticality: CapabilityCriticality = (
                "critical"
                if path in {"/api/v1/chat", "/api/v1/chat/stream"}
                else "high"
                if any(token in path for token in ("/health", "/scheduled", "/outbound"))
                else "medium"
            )
            descriptors.append(CapabilityDescriptor(
                capability_id=_hashed_capability_id("api", source_id),
                kind="api",
                source_id=source_id,
                label=str(operation.get("summary") or operation_id or source_id),
                owner=owner,
                criticality=criticality,
                lifecycle="active" if contract is not None else "compatibility",
                verification_suite_ids=("backend-full",),
                related_operation_ids=((operation_id,) if operation_id else ()),
                attributes=_attributes(
                    method=method,
                    path=path,
                    operation_id=operation_id,
                    contract_id=contract_id,
                    contract_lifecycle=str(
                        operation.get("x-nanobot-contract-lifecycle")
                        or ("typed" if contract is not None else "compatibility")
                    ),
                ),
            ))
    return tuple(descriptors)


def _load_webui_capabilities(path: Path) -> tuple[CapabilityDescriptor, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("WebUI 能力清单不可读取") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "capabilities"}:
        raise ValueError("WebUI 能力清单顶层字段非法")
    if payload["schema_version"] != 1:
        raise ValueError("WebUI 能力清单 schema_version 不受支持")
    items = payload["capabilities"]
    if not isinstance(items, list):
        raise ValueError("WebUI capabilities 必须是数组")
    allowed_fields = {
        "feature_id",
        "route",
        "label",
        "owner",
        "criticality",
        "route_kind",
        "manifest_managed",
        "backend_operation_ids",
        "coverage_policy",
        "exemption_reason",
    }
    descriptors: list[CapabilityDescriptor] = []
    seen_routes: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - allowed_fields:
            raise ValueError("WebUI capability 包含未知字段")
        missing = {
            "feature_id",
            "route",
            "label",
            "owner",
            "criticality",
            "route_kind",
            "manifest_managed",
            "backend_operation_ids",
        } - set(item)
        if missing:
            raise ValueError(f"WebUI capability 缺少字段：{sorted(missing)}")
        route = _nonempty_text(item["route"], field_name="webui.route", maximum=256)
        if not route.startswith("/") or route in seen_routes:
            raise ValueError("WebUI route 非法或重复")
        seen_routes.add(route)
        operations = item["backend_operation_ids"]
        if not isinstance(operations, list) or not all(
            isinstance(value, str) for value in operations
        ):
            raise ValueError("WebUI backend_operation_ids 必须是字符串数组")
        policy = str(item.get("coverage_policy") or "required")
        reason = str(item.get("exemption_reason") or "")
        descriptors.append(CapabilityDescriptor(
            capability_id=_hashed_capability_id("webui", route),
            kind="webui",
            source_id=route,
            label=_nonempty_text(item["label"], field_name="webui.label", maximum=256),
            owner=_nonempty_text(item["owner"], field_name="webui.owner", maximum=128),
            criticality=str(item["criticality"]),
            lifecycle=(
                "compatibility"
                if item["route_kind"] == "redirect"
                else "active"
            ),
            coverage_policy=policy,
            related_operation_ids=tuple(operations),
            exemption_reason=reason,
            attributes=_attributes(
                feature_id=item["feature_id"],
                route=route,
                route_kind=item["route_kind"],
                manifest_managed=str(bool(item["manifest_managed"])).lower(),
            ),
        ))
    return tuple(descriptors)


def _agent_capabilities(
    agent_descriptors: Iterable[object],
) -> tuple[CapabilityDescriptor, ...]:
    return tuple(
        CapabilityDescriptor(
            capability_id=f"agent.{agent.agent_id}",
            kind="agent",
            source_id=agent.agent_id,
            label=agent.display_name,
            owner="agent.runtime",
            criticality="high",
            verification_suite_ids=("backend-full", "kt-compatibility"),
            attributes=_attributes(
                adapter=agent.adapter,
                default=str(bool(agent.default)).lower(),
                allowed_entrypoints=",".join(agent.allowed_entrypoints),
            ),
        )
        for agent in sorted(agent_descriptors, key=lambda item: item.agent_id)
    )


def _tool_capabilities() -> tuple[CapabilityDescriptor, ...]:
    descriptors: list[CapabilityDescriptor] = []
    for registration in TOOL_REGISTRATION_REGISTRY.registry_snapshot:
        tool = registration.descriptor
        criticality: CapabilityCriticality = (
            "high" if tool.effect_policy == "external" else "medium"
        )
        descriptors.append(CapabilityDescriptor(
            capability_id=f"tool.{registration.name}",
            kind="tool",
            source_id=registration.name,
            label=tool.definition.label or registration.name,
            owner=tool.owner_module,
            criticality=criticality,
            lifecycle=registration.lifecycle,
            verification_suite_ids=("backend-full",),
            attributes=_attributes(
                domain=tool.domain,
                effect_policy=tool.effect_policy,
                execution_policy=tool.execution_policy,
                framework_owned=str(bool(tool.framework_owned)).lower(),
            ),
        ))
    return tuple(descriptors)


def _model_route_capabilities() -> tuple[CapabilityDescriptor, ...]:
    return tuple(
        CapabilityDescriptor(
            capability_id=f"model.{route.route_key}",
            kind="model_route",
            source_id=route.route_key,
            label=route.label,
            owner=route.owner,
            criticality="high",
            lifecycle=route.lifecycle.value,
            verification_suite_ids=("backend-full",),
            attributes=_attributes(
                domain=route.domain,
                route_type=route.route_type,
                execution_mode=route.execution_mode.value,
                fallback_route=route.fallback_route or "",
                output_contract_id=route.output_contract_id,
            ),
        )
        for route in list_model_route_descriptors()
    )


def _rag_capabilities() -> tuple[CapabilityDescriptor, ...]:
    return tuple(
        CapabilityDescriptor(
            capability_id=f"rag.{source.replace('_', '-')}",
            kind="rag_source",
            source_id=source,
            label=f"RAG {source}",
            owner="semantic.runtime",
            criticality="high",
            verification_suite_ids=("backend-full",),
            attributes=_attributes(
                aggregate=str(source in {"memory", "all"}).lower(),
                debug_supported="true",
            ),
        )
        for source in RAG_CAPABILITY_SOURCES
    )


def _worker_capabilities() -> tuple[CapabilityDescriptor, ...]:
    external = {
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    }
    critical = {
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
        "daily-digest-scheduler",
        "scheduled-task-runner",
        "proactive-outreach-scheduler",
        "selfcheck-watchdog",
    }
    return tuple(
        CapabilityDescriptor(
            capability_id=f"worker.{source}",
            kind="worker",
            source_id=source,
            label=source,
            owner="runtime.workers",
            criticality="critical" if source in critical else "high",
            verification_suite_ids=("compose-config", "backend-full"),
            attributes=_attributes(
                deployment_mode=("external" if source in external else "embedded"),
            ),
        )
        for source in WORKER_CAPABILITY_SOURCES
    )


def _platform_capabilities() -> tuple[CapabilityDescriptor, ...]:
    definitions = (
        ("storage.database", "storage", "database", "数据库", "database.runtime", "critical"),
        ("storage.workspace-assets", "storage", "workspace_assets", "Workspace 与资产", "sandbox.storage", "high"),
        ("integration.prompt-runtime", "integration", "prompt_runtime", "Prompt Runtime", "prompt.runtime", "critical"),
        ("integration.model-observability", "integration", "model_observability", "模型调用观测", "model.observability", "critical"),
        ("integration.agent-observability", "integration", "agent_observability", "Agent 运行观测", "runtime.observability", "high"),
        ("integration.memory-quality", "integration", "memory_quality", "记忆质量", "memory.runtime", "critical"),
        ("integration.agent-collaboration", "integration", "agent_collaboration", "多 Agent 协作", "agent.collaboration", "high"),
        ("integration.run-ledger", "integration", "run_ledger", "Run Ledger", "runtime.observability", "high"),
        ("integration.session-policy", "integration", "session_policy", "会话策略", "session.config", "critical"),
    )
    return tuple(
        CapabilityDescriptor(
            capability_id=capability_id,
            kind=kind,
            source_id=source_id,
            label=label,
            owner=owner,
            criticality=criticality,
            verification_suite_ids=("backend-full",),
        )
        for capability_id, kind, source_id, label, owner, criticality in definitions
    )


def build_capability_registry(
    app: FastAPI,
    *,
    agent_descriptors: Iterable[object] = (),
    endpoint_contracts: Iterable[object] = (),
    webui_manifest_path: Path | None = None,
) -> RegistrySnapshot[CapabilityDescriptor]:
    """从代码所有事实源组合并冻结一代完整能力清单。"""

    if not isinstance(app, FastAPI):
        raise TypeError("app 必须是 FastAPI")
    builder = RegistryBuilder[CapabilityDescriptor]("selfcheck_capability")
    descriptor_groups: Sequence[tuple[CapabilityDescriptor, ...]] = (
        _api_capabilities(app, endpoint_contracts),
        _load_webui_capabilities(
            webui_manifest_path or _DEFAULT_WEBUI_CAPABILITIES
        ),
        _agent_capabilities(agent_descriptors),
        _tool_capabilities(),
        _model_route_capabilities(),
        _rag_capabilities(),
        _worker_capabilities(),
        _platform_capabilities(),
    )
    from core.selfcheck.probes import probe_ids_for_capability

    for descriptors in descriptor_groups:
        for descriptor in descriptors:
            implemented = probe_ids_for_capability(
                descriptor.kind,
                descriptor.source_id,
            )
            if implemented and descriptor.coverage_policy != "exempt":
                descriptor = replace(
                    descriptor,
                    probe_ids=tuple(sorted(set(descriptor.probe_ids) | set(implemented))),
                )
            builder.register(descriptor)
    return builder.freeze()


def capability_coverage_summary(
    snapshot: RegistrySnapshot[CapabilityDescriptor],
) -> dict[str, object]:
    """按能力种类汇总覆盖缺口；不把验证套件等同于运行探针。"""

    totals = {"covered": 0, "unverified": 0, "exempted": 0}
    required_unverified = 0
    by_kind: dict[str, dict[str, int]] = {}
    for descriptor in snapshot:
        status = descriptor.coverage_status
        totals[status] += 1
        if status == "unverified" and descriptor.coverage_policy == "required":
            required_unverified += 1
        kind_summary = by_kind.setdefault(
            descriptor.kind,
            {"total": 0, "covered": 0, "unverified": 0, "exempted": 0},
        )
        kind_summary["total"] += 1
        kind_summary[status] += 1
    return {
        "total": len(snapshot),
        **totals,
        "required_unverified": required_unverified,
        "by_kind": {key: by_kind[key] for key in sorted(by_kind)},
    }


__all__ = [
    "RAG_CAPABILITY_SOURCES",
    "WORKER_CAPABILITY_SOURCES",
    "CapabilityDescriptor",
    "build_capability_registry",
    "capability_coverage_summary",
]
