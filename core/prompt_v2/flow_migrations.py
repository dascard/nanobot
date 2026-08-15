"""Prompt Runtime flow 的受控迁移、备份与回滚。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
from typing import Any

from core.prompt_v2.flow import (
    PromptFlowError,
    validate_flow,
    validate_runtime_contract,
)
from core.prompt_v2.flow_contract import FLOW_SCHEMA_VERSION
from core.prompt_v2.flow_storage import (
    FlowStorageError,
    assert_no_symlink_components,
    atomic_replace_bytes,
    ensure_directory_without_symlinks,
    flow_write_lock,
    fsync_directory,
    template_governance_read_lock,
    template_governance_write_lock,
)


_BACKUP_NAME_RE = re.compile(
    r"^chat-flow\.(?P<timestamp>\d{8}T\d{12}Z)\."
    r"(?P<sha>[0-9a-f]{12})\.json\.bak$"
)
_FLOW_V2_PLAN_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_FLOW_V2_PLAN_SCHEMA_VERSION = 1
_FLOW_V2_MIGRATION_ID = "internal-private-flow-v2"
_SESSION_GUIDANCE_NODE = {
    "id": "session_guidance",
    "type": "runtime",
    "label": "system: session_guidance",
    "runtime_key": "session_guidance",
}
_PROJECT_CONTEXT_NODE = {
    "id": "project_context",
    "type": "runtime",
    "label": "context: governed project data",
    "runtime_key": "project_context",
}
_SUMMARY_CONTEXT_NODE = {
    "id": "summary_context",
    "type": "runtime",
    "label": "context: conversation summary",
    "runtime_key": "summary_context",
}


@contextmanager
def _runtime_flow_read_lock(runtime_flow_path: Path) -> Iterator[None]:
    with flow_write_lock(runtime_flow_path):
        with template_governance_read_lock(runtime_flow_path.parent.parent):
            yield


@contextmanager
def _runtime_flow_write_lock(runtime_flow_path: Path) -> Iterator[None]:
    with flow_write_lock(runtime_flow_path):
        with template_governance_write_lock(runtime_flow_path.parent.parent):
            yield


class PromptFlowMigrationError(PromptFlowError):
    """运行时 flow 无法安全迁移或回滚。"""


def _migration_error(message: str, exc: Exception | None = None) -> PromptFlowMigrationError:
    error = PromptFlowMigrationError(message)
    if exc is not None:
        error.__cause__ = exc
    return error


def _validate_base_flow(flow: Any) -> None:
    if not isinstance(flow, dict):
        raise PromptFlowMigrationError("flow 顶层必须是 JSON object")
    try:
        validate_flow(flow)
    except (PromptFlowError, TypeError, ValueError, AttributeError) as exc:
        raise _migration_error(f"flow 基础校验失败: {exc}", exc)


def _validate_migrated_flow(flow: dict[str, Any]) -> None:
    try:
        normalized = validate_flow(flow)
        validate_runtime_contract(normalized)
    except (PromptFlowError, TypeError, ValueError, AttributeError) as exc:
        raise _migration_error(f"flow 运行契约校验失败: {exc}", exc)


def _is_unconditional(edge: dict[str, Any]) -> bool:
    return not edge.get("chat_types") and not edge.get("platforms")


def _validate_existing_guidance_relationships(flow: dict[str, Any]) -> None:
    edges = list(flow.get("edges") or [])
    identity_outgoing = [
        edge for edge in edges if edge.get("from") == "identity_context"
    ]
    direct_edges = [
        edge
        for edge in identity_outgoing
        if edge.get("to") == "session_guidance"
    ]
    guidance_incoming = [
        edge for edge in edges if edge.get("to") == "session_guidance"
    ]
    guidance_outgoing = [
        edge for edge in edges if edge.get("from") == "session_guidance"
    ]

    if (
        len(identity_outgoing) != 1
        or len(direct_edges) != 1
        or not _is_unconditional(direct_edges[0])
    ):
        raise PromptFlowMigrationError(
            "identity_context 必须仅通过一条无条件边直连 session_guidance"
        )
    if len(guidance_incoming) != 1 or guidance_incoming[0] is not direct_edges[0]:
        raise PromptFlowMigrationError(
            "session_guidance 只能接收 identity_context 的唯一入边"
        )
    if not guidance_outgoing:
        raise PromptFlowMigrationError("session_guidance 必须至少有一条下游边")


def migrate_session_guidance_flow(
    flow: dict[str, Any],
    *,
    validate_result: bool = True,
) -> tuple[dict[str, Any], bool]:
    """在 identity 后插入 session guidance，并保留原下游边全部元数据。

    ``validate_result=False`` 仅供组合迁移使用：插入 guidance 后，旧 flow
    可能还缺少后续上下文节点，必须等上下文链迁移完成后再做最终运行契约校验。
    """
    migrated = copy.deepcopy(flow)
    _validate_base_flow(migrated)

    nodes = list(migrated.get("nodes") or [])
    identity_indexes = [
        index
        for index, node in enumerate(nodes)
        if node.get("id") == "identity_context"
    ]
    if len(identity_indexes) != 1:
        raise PromptFlowMigrationError("flow 必须且只能包含一个 identity_context 节点")

    guidance_nodes = [
        node
        for node in nodes
        if node.get("id") == "session_guidance"
        or node.get("runtime_key") == "session_guidance"
    ]
    if guidance_nodes:
        if len(guidance_nodes) != 1 or guidance_nodes[0] != {
            **guidance_nodes[0],
            "id": "session_guidance",
            "type": "runtime",
            "runtime_key": "session_guidance",
        }:
            raise PromptFlowMigrationError("session_guidance 节点不是唯一规范节点")
        _validate_existing_guidance_relationships(migrated)
        if validate_result:
            _validate_migrated_flow(migrated)
        return migrated, False

    edges = list(migrated.get("edges") or [])
    identity_outgoing = [
        edge for edge in edges if edge.get("from") == "identity_context"
    ]
    if not identity_outgoing:
        raise PromptFlowMigrationError("identity_context 缺少可迁移的下游边")

    identity_index = identity_indexes[0]
    nodes.insert(identity_index + 1, copy.deepcopy(_SESSION_GUIDANCE_NODE))
    migrated["nodes"] = nodes

    rewritten_edges: list[dict[str, Any]] = []
    direct_inserted = False
    for edge in edges:
        if edge.get("from") != "identity_context":
            rewritten_edges.append(edge)
            continue
        if not direct_inserted:
            rewritten_edges.append(
                {"from": "identity_context", "to": "session_guidance"}
            )
            direct_inserted = True
        rewritten = copy.deepcopy(edge)
        rewritten["from"] = "session_guidance"
        rewritten_edges.append(rewritten)
    migrated["edges"] = rewritten_edges

    if validate_result:
        _validate_migrated_flow(migrated)
    return migrated, True


def migrate_context_sections_flow(
    flow: dict[str, Any],
    *,
    validate_result: bool = True,
) -> tuple[dict[str, Any], bool]:
    """为旧 runtime flow 补齐上下文链并迁移到缓存稳定顺序。

    查询相关的 group/project context 必须位于历史之后；否则检索结果变化会
    使整个长历史失去前缀缓存。迁移仅改写可确认的旧 canonical 核心链，含
    自定义分支的 Flow 保持原样并由运行契约校验决定是否可用。
    """

    migrated = copy.deepcopy(flow)
    _validate_base_flow(migrated)
    nodes = list(migrated.get("nodes") or [])
    header_indexes = [
        index
        for index, node in enumerate(nodes)
        if node.get("id") == "conversation_context_header"
    ]
    if len(header_indexes) != 1:
        raise PromptFlowMigrationError(
            "flow 必须且只能包含一个 conversation_context_header 节点"
        )

    changed = False
    indexes: dict[str, int] = {}
    for node_id in ("project_context", "summary_context"):
        matches = [
            index
            for index, node in enumerate(nodes)
            if node.get("id") == node_id
            or node.get("runtime_key") == node_id
        ]
        if len(matches) > 1:
            raise PromptFlowMigrationError(
                f"flow 中 {node_id} 节点不唯一"
            )
        if matches:
            index = matches[0]
            node = nodes[index]
            if (
                node.get("id") != node_id
                or node.get("type") != "runtime"
                or node.get("runtime_key") != node_id
            ):
                raise PromptFlowMigrationError(
                    f"flow 中 {node_id} 节点不是规范 runtime 节点"
                )
            indexes[node_id] = index

    header_index = header_indexes[0]
    if "project_context" not in indexes:
        nodes.insert(header_index, copy.deepcopy(_PROJECT_CONTEXT_NODE))
        changed = True
        header_index += 1
    if "summary_context" not in indexes:
        nodes.insert(header_index, copy.deepcopy(_SUMMARY_CONTEXT_NODE))
        changed = True
    migrated["nodes"] = nodes

    edges = list(migrated.get("edges") or [])

    def edge_matches(
        edge: dict[str, Any],
        source: str,
        target: str,
        *,
        chat_type: str = "",
    ) -> bool:
        if edge.get("from") != source or edge.get("to") != target:
            return False
        chat_types = _normalized_condition_values(edge.get("chat_types"))
        platforms = _normalized_condition_values(edge.get("platforms"))
        if platforms:
            return False
        if chat_type:
            return chat_types == [chat_type]
        return not chat_types

    def has_core_edge(
        source: str,
        target: str,
        *,
        chat_type: str = "",
        source_edges: list[dict[str, Any]] | None = None,
    ) -> bool:
        return any(
            edge_matches(
                edge,
                source,
                target,
                chat_type=chat_type,
            )
            for edge in (source_edges if source_edges is not None else edges)
        )

    # 新缓存稳定链已经存在时不得再追加旧 project -> summary 边。
    cache_stable_tail = all((
        has_core_edge("session_guidance", "summary_context"),
        has_core_edge("summary_context", "conversation_context_header"),
        has_core_edge("conversation_context_header", "history_messages"),
        has_core_edge(
            "history_messages",
            "group_context",
            chat_type="group",
        ),
        has_core_edge(
            "history_messages",
            "project_context",
            chat_type="private",
        ),
        has_core_edge(
            "group_context",
            "project_context",
            chat_type="group",
        ),
        has_core_edge("project_context", "persona_reference"),
    ))
    if cache_stable_tail:
        if validate_result:
            _validate_migrated_flow(migrated)
        return migrated, changed

    # 现存 canonical Flow 的旧顺序可以精确识别；只替换核心边，不猜测
    # 管理员自定义分支。此分支也覆盖早期缺少 project/summary 节点的基线：
    # 上面的节点补齐后，旧 header 入边仍可作为唯一迁移锚点。
    old_core_edges = list(edges)
    rewritten_old_edges: list[dict[str, Any]] = []
    for edge in old_core_edges:
        if (
            edge.get("to") == "conversation_context_header"
            and edge.get("from") in {"session_guidance", "group_context"}
        ):
            replacement = copy.deepcopy(edge)
            replacement["to"] = "project_context"
            rewritten_old_edges.append(replacement)
        else:
            rewritten_old_edges.append(edge)

    def has_rewritten_core_edge(
        source: str,
        target: str,
        *,
        chat_type: str = "",
    ) -> bool:
        return has_core_edge(
            source,
            target,
            chat_type=chat_type,
            source_edges=rewritten_old_edges,
        )

    old_canonical_chain = all((
        has_rewritten_core_edge(
            "session_guidance",
            "group_context",
            chat_type="group",
        ),
        has_rewritten_core_edge(
            "session_guidance",
            "project_context",
            chat_type="private",
        ),
        has_rewritten_core_edge(
            "group_context",
            "project_context",
            chat_type="group",
        ),
        has_rewritten_core_edge("project_context", "summary_context"),
        has_rewritten_core_edge(
            "summary_context",
            "conversation_context_header",
        ),
        has_rewritten_core_edge(
            "conversation_context_header",
            "history_messages",
        ),
        has_rewritten_core_edge("history_messages", "persona_reference"),
    ))
    if old_canonical_chain:
        obsolete_edges = {
            ("session_guidance", "group_context"),
            ("session_guidance", "project_context"),
            ("project_context", "summary_context"),
            ("history_messages", "persona_reference"),
        }
        rewritten = [
            edge
            for edge in rewritten_old_edges
            if (edge.get("from"), edge.get("to")) not in obsolete_edges
        ]
        rewritten.extend([
            {"from": "session_guidance", "to": "summary_context"},
            {
                "from": "history_messages",
                "to": "group_context",
                "chat_types": ["group"],
            },
            {
                "from": "history_messages",
                "to": "project_context",
                "chat_types": ["private"],
            },
            {"from": "project_context", "to": "persona_reference"},
        ])
        migrated["edges"] = rewritten
        if validate_result:
            _validate_migrated_flow(migrated)
        return migrated, True

    rewritten: list[dict[str, Any]] = []
    for edge in edges:
        if (
            edge.get("to") == "conversation_context_header"
            and edge.get("from") in {"session_guidance", "group_context"}
        ):
            replacement = copy.deepcopy(edge)
            replacement["to"] = "project_context"
            rewritten.append(replacement)
            changed = True
        else:
            rewritten.append(edge)

    def has_edge(source: str, target: str) -> bool:
        return any(
            edge.get("from") == source and edge.get("to") == target
            for edge in rewritten
        )

    # 缓存稳定核心链已经由管理员显式保存，但 guidance 仍经自定义节点进入
    # persona 时，保留该自定义链，不能再补 project -> summary 造成分叉。
    has_stable_downstream = all((
        has_core_edge(
            "history_messages",
            "group_context",
            chat_type="group",
            source_edges=rewritten,
        ),
        has_core_edge(
            "history_messages",
            "project_context",
            chat_type="private",
            source_edges=rewritten,
        ),
        has_core_edge(
            "group_context",
            "project_context",
            chat_type="group",
            source_edges=rewritten,
        ),
        has_core_edge(
            "project_context",
            "persona_reference",
            source_edges=rewritten,
        ),
    ))
    if has_stable_downstream:
        guidance_outgoing = [
            edge
            for edge in rewritten
            if edge.get("from") == "session_guidance"
        ]
        if (
            len(guidance_outgoing) == 1
            and edge_matches(
                guidance_outgoing[0],
                "session_guidance",
                "persona_reference",
            )
        ):
            rewritten.remove(guidance_outgoing[0])
            rewritten.append(
                {"from": "session_guidance", "to": "summary_context"}
            )
            changed = True
        migrated["edges"] = rewritten
        if validate_result:
            _validate_migrated_flow(migrated)
        return migrated, changed

    # 如果旧 flow 的两条分支边已经被改写，下面的检查不会产生重复边；
    # 缺少某条分支时补上 canonical 条件，确保六个 live branch 都可达。
    def ensure_branch_edge(
        source: str,
        target: str,
        edge: dict[str, Any],
    ) -> None:
        """仅为没有任何下游的旧分支补 canonical 边。

        旧版本允许在 guidance 后挂载自定义节点；若该分支已有下游，贸然
        再追加 project_context 会制造同条件分叉。已有旧 header 边会在上面
        被改写，因此这里只处理真正没有下游的基线分支。
        """
        nonlocal changed
        if has_edge(source, target):
            return
        if any(item.get("from") == source for item in rewritten):
            return
        rewritten.append(edge)
        changed = True

    ensure_branch_edge(
        "session_guidance",
        "project_context",
        {
            "from": "session_guidance",
            "to": "project_context",
            "chat_types": ["private"],
        },
    )
    ensure_branch_edge(
        "group_context",
        "project_context",
        {
            "from": "group_context",
            "to": "project_context",
            "chat_types": ["group"],
        },
    )
    if not has_edge("project_context", "summary_context"):
        rewritten.append(
            {"from": "project_context", "to": "summary_context"}
        )
        changed = True
    if not has_edge("summary_context", "conversation_context_header"):
        rewritten.append(
            {
                "from": "summary_context",
                "to": "conversation_context_header",
            }
        )
        changed = True

    migrated["edges"] = rewritten

    if validate_result:
        _validate_migrated_flow(migrated)
    return migrated, changed


def migrate_runtime_flow(flow: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """合并当前 schema 内的 session guidance 与上下文链结构迁移。

    Flow v1 到 v2 受独立 ``plan/apply`` 治理；启动期结构迁移不能顺带修改
    schema 版本或 internal/private 核心边，因此 v1 在这里保持字节语义不变。
    """

    migrated = copy.deepcopy(flow)
    _validate_base_flow(migrated)
    if migrated.get("version", 1) != FLOW_SCHEMA_VERSION:
        return migrated, False

    has_guidance = any(
        node.get("id") == "session_guidance"
        or node.get("runtime_key") == "session_guidance"
        for node in list(migrated.get("nodes") or [])
    )
    if has_guidance:
        migrated, context_changed = migrate_context_sections_flow(
            migrated,
            validate_result=False,
        )
        migrated, guidance_changed = migrate_session_guidance_flow(migrated)
    else:
        # 旧 schema 基线通常已经包含 project/summary 节点；先插入 guidance，
        # 再执行上下文链迁移，避免在中间状态触发严格运行契约校验。
        migrated, guidance_changed = migrate_session_guidance_flow(
            migrated,
            validate_result=False,
        )
        migrated, context_changed = migrate_context_sections_flow(
            migrated,
            validate_result=False,
        )
        _validate_migrated_flow(migrated)
    return migrated, bool(guidance_changed or context_changed)


def _normalized_condition_values(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _internal_private_core_edge(flow: dict[str, Any]) -> dict[str, Any]:
    matches = [
        edge
        for edge in list(flow.get("edges") or [])
        if edge.get("from") == "base_contract"
        and edge.get("to") == "private_policy"
    ]
    if len(matches) != 1:
        raise PromptFlowMigrationError(
            "flow 必须且只能包含一条 base_contract -> private_policy 核心边"
        )
    return matches[0]


def _validate_core_edge_conditions(
    edge: dict[str, Any],
    *,
    expected_platforms: frozenset[str],
) -> None:
    chat_types = _normalized_condition_values(edge.get("chat_types"))
    platforms = _normalized_condition_values(edge.get("platforms"))
    if len(chat_types) != 1 or frozenset(chat_types) != frozenset({"private"}):
        raise PromptFlowMigrationError(
            "base_contract -> private_policy 核心边的 chat_types 已冲突"
        )
    if len(platforms) != len(expected_platforms) or frozenset(platforms) != expected_platforms:
        raise PromptFlowMigrationError(
            "base_contract -> private_policy 核心边的 platforms 已冲突"
        )


def _require_session_guidance_baseline(flow: dict[str, Any]) -> None:
    guidance_nodes = [
        node
        for node in list(flow.get("nodes") or [])
        if node.get("id") == "session_guidance"
        or node.get("runtime_key") == "session_guidance"
    ]
    if len(guidance_nodes) != 1:
        raise PromptFlowMigrationError(
            "Flow v2 迁移仅支持当前 v1 基线；请先完成 session_guidance Flow 迁移"
        )
    try:
        _validate_existing_guidance_relationships(flow)
    except PromptFlowMigrationError as exc:
        raise _migration_error(
            "Flow v2 迁移仅支持当前 v1 基线；请先完成 session_guidance Flow 迁移",
            exc,
        )


def migrate_internal_private_flow_v2(
    flow: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """把 v1 Flow 升级为支持内部与外部私聊策略的 v2。"""

    migrated = copy.deepcopy(flow)
    _validate_base_flow(migrated)
    version = migrated.get("version", 1)
    edge = _internal_private_core_edge(migrated)

    if version == FLOW_SCHEMA_VERSION:
        _validate_core_edge_conditions(
            edge,
            expected_platforms=frozenset(
                {"web", "internal", "external_private"}
            ),
        )
        _validate_migrated_flow(migrated)
        return migrated, False
    if version != 1:
        raise PromptFlowMigrationError(f"flow.version 无法迁移: {version}")

    _validate_core_edge_conditions(
        edge,
        expected_platforms=frozenset({"web"}),
    )
    _require_session_guidance_baseline(migrated)
    migrated["version"] = FLOW_SCHEMA_VERSION
    edge["platforms"] = ["web", "internal", "external_private"]
    _validate_migrated_flow(migrated)
    return migrated, True


def _read_regular_file(path: Path, *, label: str) -> bytes:
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _migration_error(f"无法读取{label}: {path}", exc)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PromptFlowMigrationError(f"{label}必须是普通文件且不能是符号链接: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise PromptFlowMigrationError(f"{label}不是普通文件: {path}")
            return handle.read()
    except PromptFlowMigrationError:
        raise
    except OSError as exc:
        raise _migration_error(f"无法读取{label}: {path}", exc)


def _decode_flow(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _migration_error(f"{label}不是合法 UTF-8 JSON: {exc}", exc)
    if not isinstance(value, dict):
        raise PromptFlowMigrationError(f"{label}顶层必须是 JSON object")
    return value


def _serialize_flow(flow: dict[str, Any]) -> bytes:
    return (
        json.dumps(flow, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_flow_v2_plan_dir(runtime_flow_path: Path) -> Path:
    """从 runtime 根目录推导 Flow v2 迁移计划目录。"""

    runtime_root = Path(runtime_flow_path).parent.parent
    return runtime_root.parent / "prompt_template_migration_plans" / "flow_v2"


def default_flow_v2_backup_dir(runtime_flow_path: Path) -> Path:
    """从 runtime 根目录推导 Flow v2 精确备份目录。"""

    runtime_root = Path(runtime_flow_path).parent.parent
    return runtime_root.parent / "prompt_template_backups" / "flow_v2"


def _flow_v2_plan_fields(
    *,
    runtime_flow_path: Path,
    source: bytes,
    target: bytes,
    from_version: int,
    changed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": _FLOW_V2_PLAN_SCHEMA_VERSION,
        "migration_id": _FLOW_V2_MIGRATION_ID,
        "runtime_flow_path": str(runtime_flow_path),
        "source_sha256": _sha256_bytes(source),
        "target_sha256": _sha256_bytes(target),
        "from_version": from_version,
        "to_version": FLOW_SCHEMA_VERSION,
        "changed": changed,
    }


def _flow_v2_plan_id(fields: dict[str, Any]) -> str:
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _flow_v2_plan_path(plan_dir: Path, plan_id: str) -> Path:
    if _FLOW_V2_PLAN_ID_RE.fullmatch(str(plan_id or "")) is None:
        raise PromptFlowMigrationError("plan_id 非法")
    return Path(plan_dir) / f"flow-v2.{plan_id}.json"


def _write_flow_v2_plan(plan_dir: Path, record: dict[str, Any]) -> Path:
    try:
        safe_plan_dir = ensure_directory_without_symlinks(Path(plan_dir))
    except FlowStorageError as exc:
        raise _migration_error(f"迁移计划目录包含符号链接或不安全组件: {exc}", exc)
    plan_path = _flow_v2_plan_path(safe_plan_dir, str(record.get("plan_id") or ""))
    payload = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    with flow_write_lock(plan_path):
        atomic_replace_bytes(plan_path, payload)
    return plan_path


def _read_flow_v2_plan(
    *,
    plan_dir: Path,
    plan_id: str,
    runtime_flow_path: Path,
) -> dict[str, Any]:
    try:
        safe_plan_dir = assert_no_symlink_components(Path(plan_dir))
    except FlowStorageError as exc:
        raise _migration_error(f"迁移计划目录包含符号链接或不安全组件: {exc}", exc)
    plan_path = _flow_v2_plan_path(safe_plan_dir, plan_id)
    record = _decode_flow(
        _read_regular_file(plan_path, label="Flow v2 迁移计划"),
        label="Flow v2 迁移计划",
    )
    stored_id = record.pop("plan_id", "")
    if type(stored_id) is not str:
        raise PromptFlowMigrationError("Flow v2 迁移计划摘要类型非法")
    if stored_id != plan_id or _flow_v2_plan_id(record) != plan_id:
        raise PromptFlowMigrationError("Flow v2 迁移计划摘要不匹配")
    expected_fields = {
        "schema_version",
        "migration_id",
        "runtime_flow_path",
        "source_sha256",
        "target_sha256",
        "from_version",
        "to_version",
        "changed",
    }
    if set(record) != expected_fields:
        raise PromptFlowMigrationError("Flow v2 迁移计划字段不匹配")
    schema_version = record.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != _FLOW_V2_PLAN_SCHEMA_VERSION
    ):
        raise PromptFlowMigrationError("Flow v2 迁移计划版本不支持")
    if record.get("migration_id") != _FLOW_V2_MIGRATION_ID:
        raise PromptFlowMigrationError("Flow v2 迁移计划类型不匹配")
    if record.get("runtime_flow_path") != str(runtime_flow_path):
        raise PromptFlowMigrationError("Flow v2 迁移计划目标路径不匹配")
    source_sha256 = record.get("source_sha256")
    target_sha256 = record.get("target_sha256")
    if (
        type(source_sha256) is not str
        or type(target_sha256) is not str
        or _FLOW_V2_PLAN_ID_RE.fullmatch(source_sha256) is None
        or _FLOW_V2_PLAN_ID_RE.fullmatch(target_sha256) is None
    ):
        raise PromptFlowMigrationError("Flow v2 迁移计划文件摘要非法")
    changed = record.get("changed")
    from_version = record.get("from_version")
    to_version = record.get("to_version")
    if (
        type(changed) is not bool
        or type(from_version) is not int
        or type(to_version) is not int
    ):
        raise PromptFlowMigrationError("Flow v2 迁移计划状态非法")
    if to_version != FLOW_SCHEMA_VERSION:
        raise PromptFlowMigrationError("Flow v2 迁移计划目标版本不匹配")
    if changed:
        if from_version != 1 or source_sha256 == target_sha256:
            raise PromptFlowMigrationError("Flow v2 迁移计划变更状态不一致")
    elif from_version != FLOW_SCHEMA_VERSION or source_sha256 != target_sha256:
        raise PromptFlowMigrationError("Flow v2 迁移计划无变更状态不一致")
    record["plan_id"] = stored_id
    record["plan_path"] = str(plan_path)
    return record


def plan_runtime_flow_v2(
    runtime_flow_path: Path,
    *,
    plan_dir: Path,
) -> dict[str, Any]:
    """生成并持久化不含 Flow 正文的 v2 迁移计划。"""

    try:
        runtime_flow_path = assert_no_symlink_components(Path(runtime_flow_path))
        with _runtime_flow_read_lock(runtime_flow_path):
            source = _read_regular_file(runtime_flow_path, label="runtime flow")
            source_flow = _decode_flow(source, label="runtime flow")
            migrated, changed = migrate_internal_private_flow_v2(source_flow)
            target = _serialize_flow(migrated) if changed else source
            fields = _flow_v2_plan_fields(
                runtime_flow_path=runtime_flow_path,
                source=source,
                target=target,
                from_version=int(source_flow.get("version", 1)),
                changed=changed,
            )
            plan_id = _flow_v2_plan_id(fields)
            record = {**fields, "plan_id": plan_id}
            plan_path = _write_flow_v2_plan(Path(plan_dir), record)
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)
    return {**record, "plan_path": str(plan_path)}


def apply_runtime_flow_v2(
    runtime_flow_path: Path,
    *,
    plan_dir: Path,
    backup_dir: Path,
    plan_id: str,
) -> dict[str, Any]:
    """在共享写锁内校验计划、精确备份并原子应用 Flow v2。"""

    try:
        runtime_flow_path = assert_no_symlink_components(Path(runtime_flow_path))
        plan = _read_flow_v2_plan(
            plan_dir=Path(plan_dir),
            plan_id=plan_id,
            runtime_flow_path=runtime_flow_path,
        )
        with _runtime_flow_write_lock(runtime_flow_path):
            source = _read_regular_file(runtime_flow_path, label="runtime flow")
            current_sha256 = _sha256_bytes(source)
            source_sha256 = str(plan.get("source_sha256") or "")
            target_sha256 = str(plan.get("target_sha256") or "")
            if current_sha256 == target_sha256 and current_sha256 != source_sha256:
                applied_flow = _decode_flow(source, label="runtime flow")
                remigrated, changed = migrate_internal_private_flow_v2(applied_flow)
                if changed or _serialize_flow(remigrated) != source:
                    raise PromptFlowMigrationError(
                        "Flow v2 迁移计划目标文件无法验证"
                    )
                return {
                    "applied": False,
                    "already_applied": True,
                    "plan_id": plan_id,
                    "runtime_flow_path": str(runtime_flow_path),
                    "backup_path": "",
                    "source_sha256": source_sha256,
                    "target_sha256": target_sha256,
                }
            if current_sha256 != source_sha256:
                raise PromptFlowMigrationError("Flow v2 迁移计划源文件已变化")

            source_flow = _decode_flow(source, label="runtime flow")
            migrated, changed = migrate_internal_private_flow_v2(source_flow)
            target = _serialize_flow(migrated) if changed else source
            fields = _flow_v2_plan_fields(
                runtime_flow_path=runtime_flow_path,
                source=source,
                target=target,
                from_version=int(source_flow.get("version", 1)),
                changed=changed,
            )
            if (
                _flow_v2_plan_id(fields) != plan_id
                or _sha256_bytes(target) != plan.get("target_sha256")
            ):
                raise PromptFlowMigrationError("Flow v2 迁移计划与当前目标不匹配")
            if not changed:
                return {
                    "applied": False,
                    "already_applied": False,
                    "plan_id": plan_id,
                    "runtime_flow_path": str(runtime_flow_path),
                    "backup_path": "",
                    "source_sha256": fields["source_sha256"],
                    "target_sha256": fields["target_sha256"],
                }

            backup_path = _create_exact_backup(source, backup_dir=Path(backup_dir))
            atomic_replace_bytes(runtime_flow_path, target)
            return {
                "applied": True,
                "already_applied": False,
                "plan_id": plan_id,
                "runtime_flow_path": str(runtime_flow_path),
                "backup_path": str(backup_path),
                "source_sha256": fields["source_sha256"],
                "target_sha256": fields["target_sha256"],
            }
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)


def _ensure_backup_directory(backup_dir: Path) -> Path:
    try:
        return ensure_directory_without_symlinks(Path(backup_dir))
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)


def _create_exact_backup(data: bytes, *, backup_dir: Path) -> Path:
    backup_dir = _ensure_backup_directory(backup_dir)
    digest = hashlib.sha256(data).hexdigest()
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".chat-flow-backup.",
        suffix=".tmp",
        dir=backup_dir,
    )
    temp_path = Path(temp_name)
    installed_path: Path | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(1000):
            timestamp = datetime.now(timezone.utc) + timedelta(microseconds=attempt)
            name = (
                f"chat-flow.{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}."
                f"{digest[:12]}.json.bak"
            )
            candidate = backup_dir / name
            try:
                os.link(temp_path, candidate, follow_symlinks=False)
            except FileExistsError:
                continue
            installed_path = candidate
            break
        if installed_path is None:
            raise PromptFlowMigrationError("无法生成唯一的 flow 备份文件名")
        fsync_directory(backup_dir)
        return installed_path
    finally:
        temp_path.unlink(missing_ok=True)


def default_session_guidance_flow_backup_dir(runtime_flow_path: Path) -> Path:
    """从 `<runtime>/chat/flow.json` 推导同级隔离备份目录。"""
    runtime_root = Path(runtime_flow_path).parent.parent
    return (
        runtime_root.parent
        / "prompt_template_backups"
        / "session_guidance_flow"
    )


def upgrade_runtime_flow_file(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
) -> dict[str, Any]:
    """验证并原子升级 runtime flow；已符合合同则保持字节不变。"""
    runtime_flow_path = Path(runtime_flow_path)
    try:
        with _runtime_flow_write_lock(runtime_flow_path):
            original = _read_regular_file(runtime_flow_path, label="runtime flow")
            flow = _decode_flow(original, label="runtime flow")
            migrated, changed = migrate_runtime_flow(flow)
            if not changed:
                return {
                    "flow_migrated": False,
                    "flow_backup_path": "",
                    "runtime_flow_path": str(runtime_flow_path),
                }

            backup_path = _create_exact_backup(
                original,
                backup_dir=Path(backup_dir),
            )
            updated = (
                json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            atomic_replace_bytes(runtime_flow_path, updated)
            return {
                "flow_migrated": True,
                "flow_backup_path": str(backup_path),
                "runtime_flow_path": str(runtime_flow_path),
            }
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)


def list_session_guidance_flow_backups(
    *,
    backup_dir: Path,
) -> list[dict[str, Any]]:
    """列出安全备份元数据，不返回 flow 正文或完整路径。"""
    backup_dir = Path(backup_dir)
    try:
        backup_dir = assert_no_symlink_components(backup_dir)
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)
    if not backup_dir.exists():
        return []
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise PromptFlowMigrationError("备份目录不是安全的普通目录")

    items: list[dict[str, Any]] = []
    for candidate in backup_dir.iterdir():
        match = _BACKUP_NAME_RE.fullmatch(candidate.name)
        if match is None or candidate.is_symlink():
            continue
        try:
            data = _read_regular_file(candidate, label="flow 备份")
        except PromptFlowMigrationError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest[:12] != match.group("sha"):
            continue
        created = datetime.strptime(
            match.group("timestamp"),
            "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc)
        items.append(
            {
                "name": candidate.name,
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "size_bytes": len(data),
                "sha256": digest,
            }
        )
    return sorted(items, key=lambda item: item["name"], reverse=True)


def _safe_backup_path(*, backup_dir: Path, backup_name: str) -> Path:
    raw_name = str(backup_name or "")
    if (
        not raw_name
        or "\x00" in raw_name
        or "/" in raw_name
        or "\\" in raw_name
        or raw_name in {".", ".."}
        or Path(raw_name).is_absolute()
        or PureWindowsPath(raw_name).is_absolute()
        or _BACKUP_NAME_RE.fullmatch(raw_name) is None
    ):
        raise PromptFlowMigrationError("backup_name 非法")

    backup_dir = Path(backup_dir)
    try:
        backup_dir = assert_no_symlink_components(backup_dir)
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise PromptFlowMigrationError("备份目录不存在或不是安全目录")
    candidate = backup_dir / raw_name
    if candidate.is_symlink():
        raise PromptFlowMigrationError("flow 备份不能是符号链接")
    return candidate


def rollback_session_guidance_flow(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
    backup_name: str,
) -> Path:
    """校验指定旧备份，保护当前 bytes 后原子恢复。"""
    runtime_flow_path = Path(runtime_flow_path)
    backup_dir = Path(backup_dir)
    try:
        with _runtime_flow_write_lock(runtime_flow_path):
            selected = _safe_backup_path(
                backup_dir=backup_dir,
                backup_name=backup_name,
            )
            selected_bytes = _read_regular_file(selected, label="flow 备份")
            match = _BACKUP_NAME_RE.fullmatch(selected.name)
            selected_digest = hashlib.sha256(selected_bytes).hexdigest()
            if match is None or selected_digest[:12] != match.group("sha"):
                raise PromptFlowMigrationError("flow 备份摘要与文件名不一致")
            selected_flow = _decode_flow(selected_bytes, label="flow 备份")
            _validate_base_flow(selected_flow)

            current_bytes = _read_regular_file(
                runtime_flow_path,
                label="runtime flow",
            )
            _create_exact_backup(current_bytes, backup_dir=backup_dir)
            atomic_replace_bytes(runtime_flow_path, selected_bytes)
            return runtime_flow_path
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)
