from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.prompt_v2.template_loader import default_template_dir, runtime_template_dir


class PromptFlowError(ValueError):
    """V2 图形编排配置校验失败。"""


RUNTIME_NODE_KEYS = {
    "runtime_context",
    "persona_reference",
    "conversation_context_header",
    "history_messages",
    "group_context",
    "effort_constraint",
    "runtime_tool_prompt",
    "current_user_event",
}

_RESERVED_RUNTIME_NODE_IDS = {
    "persona_reference",
    "runtime_tool_prompt",
    "current_user_event",
}
_RESERVED_POLICY_TEMPLATE_KEYS = {
    "chat/branch_private": "private_policy",
    "chat/branch_group": "group_policy",
}
_RESERVED_POLICY_NODE_IDS = {
    node_id: template_key
    for template_key, node_id in _RESERVED_POLICY_TEMPLATE_KEYS.items()
}

CHAT_TYPES = {"group", "private"}
RUNTIME_PLATFORMS = {"qq", "web"}
_ANY_PLATFORM = "*"
_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


DEFAULT_FLOW: dict[str, Any] = {
    "version": 1,
    "nodes": [
        {"id": "base_contract", "type": "template", "label": "system: V2 base contract", "template_key": "chat/main"},
        {"id": "group_policy", "type": "template", "label": "system: group policy", "template_key": "chat/branch_group", "chat_types": ["group"]},
        {"id": "private_policy", "type": "template", "label": "system: private policy", "template_key": "chat/branch_private", "chat_types": ["private"]},
        {"id": "runtime_context", "type": "runtime", "label": "system: runtime_context", "runtime_key": "runtime_context"},
        {"id": "identity_context", "type": "template", "label": "system: identity_context", "template_key": "chat/identity_context"},
        {"id": "persona_reference", "type": "runtime", "label": "system: persona_reference", "runtime_key": "persona_reference"},
        {"id": "conversation_context_header", "type": "runtime", "label": "system: conversation_context_header", "runtime_key": "conversation_context_header"},
        {"id": "history_messages", "type": "runtime", "label": "history: messages", "runtime_key": "history_messages"},
        {"id": "group_context", "type": "runtime", "label": "system: group profile / expression / jargon", "runtime_key": "group_context", "chat_types": ["group"]},
        {"id": "effort_constraint", "type": "runtime", "label": "system: effort_constraint", "runtime_key": "effort_constraint", "optional": True},
        {"id": "runtime_tool_prompt", "type": "runtime", "label": "system: runtime_tool_prompt", "runtime_key": "runtime_tool_prompt"},
        {"id": "current_user_event", "type": "runtime", "label": "user: current_user_input", "runtime_key": "current_user_event"},
    ],
    "edges": [
        {"from": "base_contract", "to": "group_policy", "chat_types": ["group"]},
        {"from": "base_contract", "to": "private_policy", "chat_types": ["private"]},
        {"from": "group_policy", "to": "runtime_context", "chat_types": ["group"]},
        {"from": "private_policy", "to": "runtime_context", "chat_types": ["private"]},
        {"from": "runtime_context", "to": "identity_context"},
        {"from": "identity_context", "to": "persona_reference"},
        {"from": "persona_reference", "to": "conversation_context_header"},
        {"from": "conversation_context_header", "to": "history_messages"},
        {"from": "history_messages", "to": "group_context", "chat_types": ["group"]},
        {"from": "history_messages", "to": "effort_constraint", "chat_types": ["private"]},
        {"from": "group_context", "to": "effort_constraint", "chat_types": ["group"]},
        {"from": "effort_constraint", "to": "runtime_tool_prompt"},
        {"from": "runtime_tool_prompt", "to": "current_user_event"},
    ],
}


@dataclass(frozen=True)
class PromptFlow:
    flow: dict[str, Any]
    path: Path
    source: str


def default_flow_path() -> Path:
    return default_template_dir() / "chat" / "flow.json"


def runtime_flow_path() -> Path:
    return runtime_template_dir() / "chat" / "flow.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_platform(platform: str) -> str:
    return str(platform or "").strip().lower() or "qq"


def _applies(item: dict[str, Any], chat_type: str, platform: str) -> bool:
    chat_types = item.get("chat_types")
    if chat_types:
        if isinstance(chat_types, str):
            chat_types = [chat_types]
        if chat_type not in {str(value).strip().lower() for value in chat_types}:
            return False
    platforms = item.get("platforms")
    if not platforms:
        return True
    if isinstance(platforms, str):
        platforms = [platforms]
    return platform in {str(value).strip().lower() for value in platforms}


def _normalize_chat_types(item: dict[str, Any], *, label: str) -> list[str]:
    raw = item.get("chat_types")
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    values = [str(value).strip().lower() for value in raw if str(value).strip()]
    invalid = sorted(set(values) - CHAT_TYPES)
    if invalid:
        raise PromptFlowError(f"{label}.chat_types 不支持: {', '.join(invalid)}")
    return sorted(set(values), key=values.index)


def _normalize_platforms(item: dict[str, Any], *, label: str) -> list[str]:
    raw = item.get("platforms")
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    values = [str(value).strip().lower() for value in raw if str(value).strip()]
    invalid = sorted(value for value in set(values) if not _PLATFORM_RE.fullmatch(value))
    if invalid:
        raise PromptFlowError(f"{label}.platforms 不支持: {', '.join(invalid)}")
    return sorted(set(values), key=values.index)


def _condition_chat_types(item: dict[str, Any]) -> set[str]:
    chat_types = _normalize_chat_types(item, label="edge")
    return set(chat_types or CHAT_TYPES)


def _condition_platforms(item: dict[str, Any], *, label: str) -> set[str]:
    platforms = _normalize_platforms(item, label=label)
    return set(platforms or [_ANY_PLATFORM])


def _intersect_platform_conditions(*conditions: set[str]) -> set[str]:
    result: set[str] = {_ANY_PLATFORM}
    for condition in conditions:
        if _ANY_PLATFORM in result:
            result = set(condition)
        elif _ANY_PLATFORM in condition:
            continue
        else:
            result &= condition
    return result


def _platform_sets_overlap(left: set[str], right: set[str]) -> bool:
    return _ANY_PLATFORM in left or _ANY_PLATFORM in right or bool(left & right)


def _node_index(nodes: list[dict[str, Any]]) -> dict[str, int]:
    return {str(node.get("id")): idx for idx, node in enumerate(nodes)}


def _validate_reserved_node_identity(node: dict[str, Any]) -> None:
    node_id = str(node.get("id") or "").strip()
    node_type = str(node.get("type") or "").strip()
    template_key = str(node.get("template_key") or "").strip()
    runtime_key = str(node.get("runtime_key") or "").strip()

    expected_runtime_id = ""
    if node_id in _RESERVED_RUNTIME_NODE_IDS:
        expected_runtime_id = node_id
    elif runtime_key in _RESERVED_RUNTIME_NODE_IDS:
        expected_runtime_id = runtime_key
    if expected_runtime_id:
        label = f"singleton {expected_runtime_id}"
        if node_id != expected_runtime_id:
            raise PromptFlowError(
                f"{label} node_id must be {expected_runtime_id}, got {node_id or '<empty>'}"
            )
        if node_type != "runtime":
            raise PromptFlowError(
                f"{label} node_type must be runtime, got {node_type or '<empty>'}"
            )
        if runtime_key != expected_runtime_id:
            raise PromptFlowError(
                f"{label} runtime_key must be {expected_runtime_id}, got {runtime_key or '<empty>'}"
            )
        if template_key:
            raise PromptFlowError(
                f"{label} template_key must be empty, got {template_key}"
            )

    expected_policy_id = ""
    expected_policy_template = ""
    if node_id in _RESERVED_POLICY_NODE_IDS:
        expected_policy_id = node_id
        expected_policy_template = _RESERVED_POLICY_NODE_IDS[node_id]
    elif template_key in _RESERVED_POLICY_TEMPLATE_KEYS:
        expected_policy_id = _RESERVED_POLICY_TEMPLATE_KEYS[template_key]
        expected_policy_template = template_key
    if expected_policy_id:
        label = f"{expected_policy_id.removesuffix('_policy')} policy"
        if node_id != expected_policy_id:
            raise PromptFlowError(
                f"{label} node_id must be {expected_policy_id}, got {node_id or '<empty>'}"
            )
        if node_type != "template":
            raise PromptFlowError(
                f"{label} node_type must be template, got {node_type or '<empty>'}"
            )
        if template_key != expected_policy_template:
            raise PromptFlowError(
                f"{label} template_key must be {expected_policy_template}, "
                f"got {template_key or '<empty>'}"
            )
        if runtime_key:
            raise PromptFlowError(
                f"{label} runtime_key must be empty, got {runtime_key}"
            )


def validate_flow(flow: dict[str, Any]) -> dict[str, Any]:
    nodes = list(flow.get("nodes") or [])
    edges = list(flow.get("edges") or [])
    if not nodes:
        raise PromptFlowError("flow.nodes 不能为空")

    ids: set[str] = set()
    normalized_nodes: list[dict[str, Any]] = []
    for raw in nodes:
        node = dict(raw or {})
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if not node_id:
            raise PromptFlowError("node.id 不能为空")
        if not all(ch.isalnum() or ch in {"_", "-"} for ch in node_id):
            raise PromptFlowError(f"node.id 非法: {node_id}")
        if node_id in ids:
            raise PromptFlowError(f"node.id 重复: {node_id}")
        if node_type not in {"template", "runtime"}:
            raise PromptFlowError(f"node.type 不支持: {node_type}")
        if node_type == "template" and not str(node.get("template_key") or "").strip():
            raise PromptFlowError(f"template node 缺少 template_key: {node_id}")
        if node_type == "runtime" and str(node.get("runtime_key") or "") not in RUNTIME_NODE_KEYS:
            raise PromptFlowError(f"runtime_key 不支持: {node.get('runtime_key')}")
        _validate_reserved_node_identity(node)
        chat_types = _normalize_chat_types(node, label=f"node {node_id}")
        if chat_types:
            node["chat_types"] = chat_types
        platforms = _normalize_platforms(node, label=f"node {node_id}")
        if platforms:
            node["platforms"] = platforms
        ids.add(node_id)
        normalized_nodes.append(node)

    normalized_edges: list[dict[str, Any]] = []
    node_chat_conditions = {str(node.get("id")): _condition_chat_types(node) for node in normalized_nodes}
    node_platform_conditions = {
        str(node.get("id")): _condition_platforms(node, label=f"node {node.get('id')}")
        for node in normalized_nodes
    }
    outgoing_conditions: dict[str, list[tuple[set[str], set[str], str]]] = {}
    for raw in edges:
        edge = dict(raw or {})
        start = str(edge.get("from") or "").strip()
        end = str(edge.get("to") or "").strip()
        if start not in ids or end not in ids:
            raise PromptFlowError(f"edge 引用了不存在的节点: {start} -> {end}")
        chat_types = _normalize_chat_types(edge, label=f"edge {start}->{end}")
        if chat_types:
            edge["chat_types"] = chat_types
        platforms = _normalize_platforms(edge, label=f"edge {start}->{end}")
        if platforms:
            edge["platforms"] = platforms
        active_chat_types = (
            _condition_chat_types(edge)
            & node_chat_conditions.get(start, set())
            & node_chat_conditions.get(end, set())
        )
        active_platforms = _intersect_platform_conditions(
            _condition_platforms(edge, label=f"edge {start}->{end}"),
            node_platform_conditions.get(start, {_ANY_PLATFORM}),
            node_platform_conditions.get(end, {_ANY_PLATFORM}),
        )
        if active_chat_types and active_platforms:
            for previous_chat_types, previous_platforms, previous_end in outgoing_conditions.get(start, []):
                if active_chat_types & previous_chat_types and _platform_sets_overlap(active_platforms, previous_platforms):
                    overlap_chat = ", ".join(sorted(active_chat_types & previous_chat_types))
                    raise PromptFlowError(
                        f"node {start} 在 {overlap_chat} 同一条件只能有一条出边: {previous_end}, {end}"
                    )
            outgoing_conditions.setdefault(start, []).append((active_chat_types, active_platforms, end))
        normalized_edges.append(edge)

    return {"version": int(flow.get("version") or 1), "nodes": normalized_nodes, "edges": normalized_edges}


def load_flow() -> PromptFlow:
    runtime = runtime_flow_path()
    default = default_flow_path()
    if runtime.exists():
        return PromptFlow(validate_flow(_read_json(runtime)), runtime, "runtime")
    if default.exists():
        return PromptFlow(validate_flow(_read_json(default)), default, "default")
    return PromptFlow(validate_flow(DEFAULT_FLOW), default, "built-in")


def _runtime_contract_platforms(flow: dict[str, Any]) -> list[str]:
    platforms = set(RUNTIME_PLATFORMS)
    for item in [*(flow.get("nodes") or []), *(flow.get("edges") or [])]:
        values = item.get("platforms")
        if isinstance(values, str):
            values = [values]
        platforms.update(str(value).strip().lower() for value in (values or []) if str(value).strip())
    return sorted(platforms)


def _validate_runtime_contract(flow: dict[str, Any]) -> None:
    from types import SimpleNamespace

    from core.prompt_v2.audit import audit_prompt_plan

    for chat_type in sorted(CHAT_TYPES):
        for platform in _runtime_contract_platforms(flow):
            ordered_nodes = ordered_nodes_for_chat(flow, chat_type, platform=platform)
            flow_sections = [
                {
                    "node_id": str(node.get("id") or ""),
                    "node_type": str(node.get("type") or ""),
                    "template_key": str(node.get("template_key") or ""),
                    "runtime_key": str(node.get("runtime_key") or ""),
                    "origin": "flow",
                    "status": "emitted",
                    "message_indexes": [],
                }
                for node in ordered_nodes
            ]
            audit = audit_prompt_plan(
                SimpleNamespace(chat_type=chat_type, flow_sections=flow_sections)
            )
            if not audit.ok:
                issues = "; ".join(audit.issues)
                raise PromptFlowError(
                    f"flow 在 chat_type={chat_type}, platform={platform} 下不满足运行契约: {issues}"
                )


def save_flow(flow: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_flow(flow)
    _validate_runtime_contract(normalized)
    path = runtime_flow_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"saved": True, "runtime_path": str(path), "flow": normalized}


def ordered_nodes_for_chat(flow: dict[str, Any], chat_type: str, platform: str = "qq") -> list[dict[str, Any]]:
    flow = validate_flow(flow)
    chat_type = str(chat_type or "").strip().lower()
    platform = _normalize_platform(platform)
    if chat_type not in CHAT_TYPES:
        chat_type = "private"
    all_nodes = [
        dict(node)
        for node in flow.get("nodes") or []
        if _applies(dict(node), chat_type, platform)
    ]
    node_ids = {str(node.get("id")) for node in all_nodes}
    order_index = _node_index(all_nodes)
    all_edges = [
        dict(edge)
        for edge in flow.get("edges") or []
        if str(edge.get("from")) in node_ids
        and str(edge.get("to")) in node_ids
    ]
    edges = [
        dict(edge)
        for edge in all_edges
        if _applies(dict(edge), chat_type, platform)
    ]
    all_incident: dict[str, bool] = {node_id: False for node_id in node_ids}
    active_incident: dict[str, bool] = {node_id: False for node_id in node_ids}
    for edge in all_edges:
        all_incident[str(edge.get("from"))] = True
        all_incident[str(edge.get("to"))] = True
    for edge in edges:
        active_incident[str(edge.get("from"))] = True
        active_incident[str(edge.get("to"))] = True

    relevant_node_ids = {
        node_id
        for node_id in node_ids
        if active_incident.get(node_id) or not all_incident.get(node_id)
    }
    if not relevant_node_ids:
        raise PromptFlowError(f"flow 在 {chat_type} 下没有可编排节点")

    edges = [
        edge
        for edge in edges
        if str(edge.get("from")) in relevant_node_ids
        and str(edge.get("to")) in relevant_node_ids
    ]
    incoming: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        start = str(edge.get("from"))
        end = str(edge.get("to"))
        incoming[end].add(start)
        outgoing[start].append(end)

    ready = sorted(
        [node_id for node_id in relevant_node_ids if not incoming.get(node_id)],
        key=lambda x: order_index.get(x, 0),
    )
    if not ready:
        raise PromptFlowError(f"flow 在 {chat_type} 下没有拓扑入口节点")

    result_ids: list[str] = []
    while ready:
        node_id = ready.pop(0)
        if node_id in result_ids:
            continue
        result_ids.append(node_id)
        targets = sorted(outgoing.get(node_id) or [], key=lambda x: order_index.get(x, 0))
        for target in targets:
            incoming[target].discard(node_id)
            if not incoming[target] and target not in result_ids and target not in ready:
                ready.append(target)
        ready.sort(key=lambda x: order_index.get(x, 0))
    if len(result_ids) != len(relevant_node_ids):
        blocked = sorted(relevant_node_ids - set(result_ids), key=lambda x: order_index.get(x, 0))
        raise PromptFlowError(f"flow 在 {chat_type} 下存在环或无法拓扑排序的节点: {', '.join(blocked)}")
    by_id = {str(node.get("id")): node for node in all_nodes}
    return [by_id[node_id] for node_id in result_ids]
