#!/usr/bin/env python3
"""检查新增模块的依赖方向，阻止架构边界继续退化。"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class BoundaryRule:
    path: Path
    forbidden_roots: frozenset[str]
    description: str


RULES = (
    BoundaryRule(
        ROOT / "core" / "agent_manifest",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Agent Manifest 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "core" / "agent_runtime",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Agent Runtime 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "core" / "runtime",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Runtime 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "core" / "model_provider",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Model Provider 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "core" / "memory_provider",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Memory Provider 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "core" / "retrieval",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Retrieval 合同层不得依赖框架或 Adapter",
    ),
    BoundaryRule(
        ROOT / "foundation",
        frozenset(
            {
                "api",
                "app",
                "clients",
                "core",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
                "sqlalchemy",
            }
        ),
        "Foundation 基础层不得反向依赖业务或 Adapter",
    ),
    BoundaryRule(
        ROOT / "sandboxd",
        frozenset({"api", "app", "clients", "creatures", "nanobot_kt"}),
        "sandboxd 不得依赖 Nanobot 交付层或 KT Runtime",
    ),
)


FORBIDDEN_KT_PRIVATE_MEMBERS = frozenset(
    {
        "_api_key",
        "_client",
        "_emergency_drop_callbacks",
        "_event_queue",
        "_extra_headers",
        "_get_native_tool_schemas",
        "_interrupt_requested",
        "_max_retries",
        "_messages",
        "_metadata",
        "_maybe_truncate",
        "_path_guard",
        "_pending_events",
        "_pending_injections",
        "_plugins",
        "_process_event",
        "_profile_max_context",
        "_rebuild_client",
        "_retry_policy",
        "_running",
        "_timeout",
        "_tokens",
        "_tools",
    }
)

CREATURE_TOOL_PATHS = tuple(
    sorted((ROOT / "creatures" / "nanobot" / "prompts" / "skills").rglob("tool.py"))
)

DATABASE_PORT_CONTRACT_PATHS = (
    ROOT / "core" / "db" / "contracts.py",
    ROOT / "core" / "db" / "group_learning_contracts.py",
    ROOT / "core" / "db" / "group_memory_contracts.py",
    ROOT / "core" / "db" / "settings_contracts.py",
)

DATABASE_PORT_MIGRATED_PATHS = (
    ROOT / "api" / "admin" / "group_memory_routes.py",
    ROOT / "api" / "admin" / "model_routes.py",
    ROOT / "api" / "chat_persistence.py",
    ROOT / "api" / "chat_recovery.py",
    ROOT / "app" / "group_analysis" / "service.py",
    ROOT / "app" / "group_learning" / "migration_audit.py",
    ROOT / "app" / "group_learning" / "query_service.py",
    ROOT / "app" / "group_memory" / "extraction_service.py",
    ROOT / "app" / "group_memory" / "injection_service.py",
    ROOT / "app" / "group_memory" / "retrieval_service.py",
    ROOT / "app" / "persona" / "injection_service.py",
    ROOT / "app" / "persona" / "retrieval_service.py",
    ROOT / "core" / "db" / "adapter.py",
    ROOT / "core" / "chat_delivery_outbox.py",
    ROOT / "core" / "inbound_idempotency.py",
    ROOT / "core" / "persona_preprocess.py",
    ROOT / "core" / "repositories" / "chat_logs.py",
    ROOT / "core" / "repositories" / "users.py",
    ROOT / "core" / "settings_service.py",
    *sorted((ROOT / "app" / "memory_digest").glob("*.py")),
    *sorted((ROOT / "app" / "session_memory").glob("*.py")),
    *sorted((ROOT / "core" / "outbound").glob("*.py")),
    ROOT / "core" / "outbound_delivery.py",
    ROOT / "core" / "outbound_delivery_service.py",
    ROOT / "core" / "scheduled_task_outbound.py",
    *(
        path
        for path in sorted((ROOT / "core" / "proactive").glob("*.py"))
        if path.name
        not in {"delivery_runtime.py", "grounding.py", "runtime_support.py"}
    ),
)

DATABASE_SQL_ADAPTER_PATHS = (
    ROOT / "app" / "group_analysis" / "repository.py",
    ROOT / "core" / "db" / "adapter.py",
    ROOT / "core" / "db" / "group_learning_adapter.py",
    ROOT / "core" / "db" / "group_memory_adapter.py",
    ROOT / "core" / "db" / "settings_adapter.py",
)

PURE_MODULE_RULES = {
    ROOT / "core" / "outbound" / "contracts.py": {
        "sqlalchemy",
        "core.database",
    },
    ROOT / "core" / "outbound" / "policy.py": {
        "sqlalchemy",
        "core.database",
    },
    ROOT / "core" / "proactive" / "model_policy.py": {
        "sqlalchemy",
        "core.database",
    },
    ROOT / "core" / "proactive" / "serialization.py": {
        "sqlalchemy",
        "core.database",
    },
    ROOT / "core" / "proactive" / "identity_policy.py": {
        "sqlalchemy",
        "core.database",
    },
}

LEGACY_CREATURE_PROMPT_DIR = ROOT / "creatures" / "nanobot" / "prompts" / "system"
RETIRED_EVOLUTION_SYMBOLS = frozenset(
    {
        "LOG_ANALYST_LLM_PROMPT",
        "PERSONA_MERGE_PROMPT",
        "PERSONA_CRITIQUE_PROMPT",
        "PROMPT_DRAFT_PROMPT",
        "PROMPT_AUDIT_PROMPT",
        "PROMPT_SYNTHESIZE_PROMPT",
        "LogAnalystAgent",
        "PersonaArchitectAgent",
        "PromptAuditorAgent",
    }
)
PRODUCTION_PYTHON_ROOTS = (
    ROOT / "api",
    ROOT / "app",
    ROOT / "bootstrap",
    ROOT / "clients",
    ROOT / "core",
    ROOT / "creatures",
    ROOT / "nanobot_kt",
    ROOT / "sandboxd",
)
MONOLITH_LINE_LIMITS = {
    ROOT / "core" / "database.py": 1123,
    ROOT / "nanobot_kt" / "bridge.py": 2759,
}
_IDENTITY_PREFIX_CALL_ARGUMENTS = {
    "startswith": frozenset({"group_", "qq:"}),
    "endswith": frozenset({":group"}),
    "removeprefix": frozenset({"group_", "qq:"}),
    "removesuffix": frozenset({":group"}),
    "like": frozenset({"group_%", "qq:%:group"}),
    "ilike": frozenset({"group_%", "qq:%:group"}),
    "notlike": frozenset({"group_%", "qq:%:group"}),
    "not_like": frozenset({"group_%", "qq:%:group"}),
}
_IDENTITY_SQL_LIKE_RE = re.compile(
    r"\blike\s+['\"](?:group_%|qq:%:group)['\"]",
    re.IGNORECASE,
)
_DYNAMIC_CHANNEL_REGISTRY_NAMES = frozenset(
    {
        "ChannelRegistry",
        "ChannelPluginRegistry",
        "DynamicChannelRegistry",
    }
)
_IDENTITY_COMPATIBILITY_ADAPTER = ROOT / "core" / "chat_stream_identity.py"


def imported_roots(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                (node.lineno, alias.name.split(".", 1)[0])
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module.split(".", 1)[0]))
    return imports


def imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


def check_rule(rule: BoundaryRule) -> list[str]:
    if not rule.path.exists():
        return []
    errors: list[str] = []
    for path in sorted(rule.path.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: 无法解析：{exc}")
            continue
        for line, root in imported_roots(tree):
            if root in rule.forbidden_roots:
                errors.append(
                    f"{path.relative_to(ROOT)}:{line}: 禁止依赖 {root}；"
                    f"{rule.description}"
                )
    return errors


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _private_member_calls(tree: ast.AST) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_KT_PRIVATE_MEMBERS:
                calls.append((node.lineno, node.attr))
            continue
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id not in {
            "getattr",
            "hasattr",
            "setattr",
            "delattr",
        }:
            continue
        member = node.args[1]
        if (
            isinstance(member, ast.Constant)
            and isinstance(member.value, str)
            and member.value in FORBIDDEN_KT_PRIVATE_MEMBERS
        ):
            calls.append((node.lineno, member.value))
    return sorted(set(calls))


def check_bridge_private_access(
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    """Nanobot KT Adapter 禁止访问已知框架私有成员。"""

    candidates = paths or tuple(
        sorted((ROOT / "nanobot_kt").rglob("*.py"))
    )
    errors: list[str] = []
    for path in candidates:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for line, member in _private_member_calls(tree):
            errors.append(
                f"{_display_path(path)}:{line}: 禁止访问 KT 私有成员 {member}；"
                "请改用公开 API 或 Nanobot 自有实现"
            )
    return errors


def check_creature_tool_boundaries(
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    """creature 工具核心不得依赖 KT 框架或 Nanobot KT Adapter。"""

    errors: list[str] = []
    for path in paths or CREATURE_TOOL_PATHS:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        display_path = _display_path(path)
        imports = tuple(imported_modules(tree))
        forbidden_imports = [
            (line, module)
            for line, module in imports
            if module == "nanobot_kt"
            or module.startswith("nanobot_kt.")
            or module == "kohakuterrarium"
            or module.startswith("kohakuterrarium.")
        ]
        for line, module in forbidden_imports:
            errors.append(
                f"{display_path}:{line}: creature 工具核心不得直接依赖 {module}"
            )
    return errors


def check_database_port_boundaries() -> list[str]:
    errors: list[str] = []
    for contracts_path in DATABASE_PORT_CONTRACT_PATHS:
        contracts_tree = ast.parse(
            contracts_path.read_text(encoding="utf-8"),
            filename=str(contracts_path),
        )
        for line, root in imported_roots(contracts_tree):
            if root not in {"sqlalchemy", "core"}:
                continue
            errors.append(
                f"{contracts_path.relative_to(ROOT)}:{line}: 数据库 Port 合同不得依赖 "
                f"{root} 实现层"
            )

    for path in DATABASE_PORT_MIGRATED_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "core.database"
            ):
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: 已迁移子域不得重新直接导入 "
                    "core.database"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "core.database":
                        errors.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: 已迁移子域不得重新直接导入 "
                            "core.database"
                        )

    for path in DATABASE_SQL_ADAPTER_PATHS:
        if not path.is_file():
            errors.append(
                f"{path.relative_to(ROOT)}: 已登记的数据库 Adapter 不存在"
            )
            continue
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for line, root in imported_roots(tree):
            if root not in {
                "api",
                "clients",
                "creatures",
                "fastapi",
                "nanobot_kt",
                "sandboxd",
            }:
                continue
            errors.append(
                f"{path.relative_to(ROOT)}:{line}: 数据库 Adapter 不得反向依赖 "
                f"{root} 交付层"
            )
    return errors


def check_core_client_dependencies() -> list[str]:
    """核心业务层不得直接依赖外部 transport Adapter。"""

    errors: list[str] = []
    for path in sorted((ROOT / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in imported_modules(tree):
            if module == "clients" or module.startswith("clients."):
                errors.append(
                    f"{path.relative_to(ROOT)}:{line}: core 不得依赖 {module}；"
                    "请通过 Port 和 composition root 注入 Adapter"
                )
    return errors


def check_kt_framework_boundaries(
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    """core/app/api 不得直接导入 KT，包含函数内动态 import。"""

    if paths is None:
        paths = tuple(
            path
            for root in (ROOT / "core", ROOT / "app", ROOT / "api")
            for path in sorted(root.rglob("*.py"))
        )
    errors: list[str] = []
    for path in paths:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for line, module in imported_modules(tree):
            if (
                module == "nanobot_kt"
                or module.startswith("nanobot_kt.")
                or module == "kohakuterrarium"
                or module.startswith("kohakuterrarium.")
            ):
                errors.append(
                    f"{display_path}:{line}: core/app/api 不得依赖 {module}；"
                    "请通过框架无关 Port 和 Composition Root 注入 Adapter"
                )
    return errors


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def check_message_contract_boundaries(
    paths: tuple[Path, ...] | None = None,
    *,
    bridge_path: Path | None = None,
) -> list[str]:
    """消息差异只能由薄 Adapter 处理，不引入动态 Channel 插件层。"""

    if paths is None:
        paths = tuple(
            path
            for root in (*PRODUCTION_PYTHON_ROOTS, ROOT / "foundation")
            for path in sorted(root.rglob("*.py"))
            if path != ROOT / "nanobot_kt" / "bridge.py"
        )
    errors: list[str] = []
    for path in paths:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and node.name in _DYNAMIC_CHANNEL_REGISTRY_NAMES
            ):
                errors.append(
                    f"{display_path}:{node.lineno}: 禁止新增动态 "
                    f"{node.name}；协议差异必须留在 MessageContract Adapter"
                )

    resolved_bridge_path = (
        bridge_path
        if bridge_path is not None
        else ROOT / "nanobot_kt" / "bridge.py"
    )
    bridge_tree = ast.parse(
        resolved_bridge_path.read_text(encoding="utf-8"),
        filename=str(resolved_bridge_path),
    )
    bridge_classes = {
        node.name: node
        for node in ast.walk(bridge_tree)
        if isinstance(node, ast.ClassDef)
    }
    for class_name in ("NanobotBridge", "NanobotBridgePool"):
        node = bridge_classes.get(class_name)
        if node is None:
            errors.append(
                f"{resolved_bridge_path}:{class_name}: 缺少生产 Bridge 类"
            )
            continue
        if not any(
            _base_name(base) == "MessageContractBridgeMixin"
            for base in node.bases
        ):
            errors.append(
                f"{resolved_bridge_path}:{node.lineno}: {class_name} 必须经 "
                "MessageContractBridgeMixin 接收类型化消息"
            )
    return errors


def check_monolith_growth_boundaries() -> list[str]:
    """既有兼容 façade 不得继续无界增长。"""

    errors: list[str] = []
    for path, max_lines in MONOLITH_LINE_LIMITS.items():
        actual_lines = len(
            path.read_text(encoding="utf-8").splitlines()
        )
        if actual_lines > max_lines:
            errors.append(
                f"{path.relative_to(ROOT)}: 当前 {actual_lines} 行，"
                f"超过兼容上限 {max_lines}；新增逻辑必须迁入所属模块"
            )
    return errors


def check_identity_prefix_inference_boundaries(
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    """业务模块不得通过旧字符串前缀推断会话类型。"""

    if paths is None:
        paths = tuple(
            path
            for root in (ROOT / "core", ROOT / "app")
            for path in sorted(root.rglob("*.py"))
        )
    errors: list[str] = []
    for path in paths:
        if path.resolve() == _IDENTITY_COMPATIBILITY_ADAPTER.resolve():
            continue
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.args
                and node.func.attr in _IDENTITY_PREFIX_CALL_ARGUMENTS
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value
                in _IDENTITY_PREFIX_CALL_ARGUMENTS[node.func.attr]
            ):
                errors.append(
                    f"{display_path}:{node.lineno}: 禁止按字符串前缀推断会话类型："
                    f"{ast.unparse(node)}"
                )
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _IDENTITY_SQL_LIKE_RE.search(node.value)
            ):
                errors.append(
                    f"{display_path}:{node.lineno}: 禁止使用 SQL LIKE 群聊前缀；"
                    "请先解析类型化 ChatStreamIdentity"
                )
    return errors


def check_tool_descriptor_consumers() -> list[str]:
    """生产消费者必须读取冻结 Descriptor Registry，不得绕回兼容元数据表。"""

    errors: list[str] = []
    forbidden_names = {"TOOL_METADATA", "FRAMEWORK_TOOL_METADATA"}
    registry_path = ROOT / "core" / "tool_registry.py"
    for root in PRODUCTION_PYTHON_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path == registry_path:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level != 0 or node.module != "core.tool_registry":
                    continue
                imported = forbidden_names & {alias.name for alias in node.names}
                if imported:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: 生产消费者不得直接读取 "
                        f"{', '.join(sorted(imported))}；请使用 ToolDescriptor Registry"
                    )
    return errors


def check_model_route_descriptor_consumers(
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    """模型路由消费者不得重新维护兼容元数据或 route key 映射。"""

    if paths is None:
        paths = tuple(
            path
            for root in PRODUCTION_PYTHON_ROOTS
            if root.exists()
            for path in sorted(root.rglob("*.py"))
        )
    errors: list[str] = []
    compatibility_facade = ROOT / "core" / "route_metadata.py"
    forbidden_assignments = {
        "_MODEL_SETTING_KEYS",
        "_MODEL_FALLBACK_SETTING_KEYS",
        "_REPLY_INHERITED_ROUTE_KEYS",
    }
    for path in paths:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for node in ast.walk(tree):
            if (
                path != compatibility_facade
                and isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "core.route_metadata"
            ):
                imported = {
                    alias.name for alias in node.names
                } & {"ROUTE_METADATA"}
                if imported:
                    errors.append(
                        f"{display_path}:{node.lineno}: 生产消费者不得读取 "
                        "ROUTE_METADATA；请使用 ModelRouteDescriptorRegistry"
                    )
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            assigned = {
                target.id
                for target in targets
                if isinstance(target, ast.Name)
            } & forbidden_assignments
            for name in sorted(assigned):
                errors.append(
                    f"{display_path}:{node.lineno}: 禁止重新定义 {name}；"
                    "请从 ModelRouteDescriptorRegistry 投影"
                )
    return errors


def check_model_setting_consumers() -> list[str]:
    """模型选择只能通过 SettingSpec/SettingsService，禁止绕回 config 常量。"""

    errors: list[str] = []
    forbidden_names = {
        "LLM_MODEL_REPLY",
        "LLM_MODEL_FAST",
        "LLM_MODEL_SMART",
        "LLM_MODEL_REASONING",
    }
    for root in PRODUCTION_PYTHON_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level != 0 or node.module != "config":
                    continue
                imported = forbidden_names & {alias.name for alias in node.names}
                if imported:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: 模型设置不得直接读取 "
                        f"{', '.join(sorted(imported))}；请通过 SettingSpec/SettingsService"
                    )
    return errors


def check_pure_module_boundaries() -> list[str]:
    errors: list[str] = []
    for path, forbidden_modules in PURE_MODULE_RULES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in imported_modules(tree):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in forbidden_modules
            ):
                errors.append(
                    f"{path.relative_to(ROOT)}:{line}: 纯策略/合同模块不得依赖 "
                    f"{module}"
                )
    return errors


def check_legacy_prompt_boundaries() -> list[str]:
    """旧 PromptManager 与 creature fragment 不得重新进入生产热路径。"""

    errors: list[str] = []
    if LEGACY_CREATURE_PROMPT_DIR.exists():
        legacy_files = sorted(LEGACY_CREATURE_PROMPT_DIR.glob("*.md"))
        errors.extend(
            f"{path.relative_to(ROOT)}: 旧 system fragment 只能保存在 "
            "docs/legacy-prompts，禁止恢复运行时双轨"
            for path in legacy_files
        )

    legacy_adapter_path = ROOT / "core" / "legacy_adapter.py"
    legacy_adapter_tree = ast.parse(
        legacy_adapter_path.read_text(encoding="utf-8"),
        filename=str(legacy_adapter_path),
    )
    for node in ast.walk(legacy_adapter_tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {node.name}
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {
                target.id for target in targets if isinstance(target, ast.Name)
            }
        else:
            continue
        retired = names & RETIRED_EVOLUTION_SYMBOLS
        if retired:
            errors.append(
                f"{legacy_adapter_path.relative_to(ROOT)}:{node.lineno}: "
                "已退出热路径的 Evolution Prompt/Agent 不得恢复："
                + ", ".join(sorted(retired))
            )

    allowed_prompt_manager_root = ROOT / "core" / "prompts"
    for root in PRODUCTION_PYTHON_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.is_relative_to(allowed_prompt_manager_root):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line, module in imported_modules(tree):
                if module == "core.prompts" or module.startswith("core.prompts."):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{line}: 旧 PromptManager 仅保留兼容，"
                        "生产路径必须使用 core.prompt_v2"
                    )
    return errors


def _repository_paths() -> list[str]:
    """只读取 Git 可见文件；忽略构建缓存和被 ignore 的本地状态。"""

    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [
        line
        for line in completed.stdout.splitlines()
        if line
    ]


def check_release_impact_ownership(
    *,
    paths: list[str] | None = None,
) -> list[str]:
    """生产文件必须命中代码所有的 Release Impact Descriptor。"""

    from core.release import build_release_impact_report

    candidates = _repository_paths() if paths is None else paths
    report = build_release_impact_report(candidates)
    return [
        f"{path}: 未归属任何 ReleaseImpactDescriptor"
        for path in report.unowned_production_paths
    ]


def main() -> int:
    errors = [error for rule in RULES for error in check_rule(rule)]
    errors.extend(check_bridge_private_access())
    errors.extend(check_database_port_boundaries())
    errors.extend(check_core_client_dependencies())
    errors.extend(check_kt_framework_boundaries())
    errors.extend(check_creature_tool_boundaries())
    errors.extend(check_message_contract_boundaries())
    errors.extend(check_monolith_growth_boundaries())
    errors.extend(check_identity_prefix_inference_boundaries())
    errors.extend(check_tool_descriptor_consumers())
    errors.extend(check_model_route_descriptor_consumers())
    errors.extend(check_model_setting_consumers())
    errors.extend(check_pure_module_boundaries())
    errors.extend(check_legacy_prompt_boundaries())
    errors.extend(check_release_impact_ownership())
    if errors:
        print("架构边界检查失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("架构边界检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
