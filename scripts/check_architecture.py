#!/usr/bin/env python3
"""检查新增模块的依赖方向，阻止架构边界继续退化。"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class BoundaryRule:
    path: Path
    forbidden_roots: frozenset[str]
    description: str


RULES = (
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
        "_event_queue",
        "_interrupt_requested",
        "_messages",
        "_pending_events",
        "_pending_injections",
        "_process_event",
        "_timeout",
    }
)

DATABASE_PORT_MIGRATED_PATHS = (
    ROOT / "api" / "chat_persistence.py",
    ROOT / "api" / "chat_recovery.py",
    ROOT / "app" / "persona" / "injection_service.py",
    ROOT / "app" / "persona" / "retrieval_service.py",
    ROOT / "core" / "db" / "adapter.py",
    ROOT / "core" / "chat_delivery_outbox.py",
    ROOT / "core" / "inbound_idempotency.py",
    ROOT / "core" / "persona_preprocess.py",
    ROOT / "core" / "repositories" / "chat_logs.py",
    ROOT / "core" / "repositories" / "users.py",
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


def check_bridge_private_access() -> list[str]:
    """KT 私有兼容访问只能存在于 nanobot_kt/runtime_adapter.py。"""

    path = ROOT / "nanobot_kt" / "bridge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in FORBIDDEN_KT_PRIVATE_MEMBERS:
            errors.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: 禁止访问 KT 私有成员 "
                f"{node.attr}；必须通过 AgentRuntimePort"
            )
    return errors


def check_database_port_boundaries() -> list[str]:
    errors: list[str] = []
    contracts_path = ROOT / "core" / "db" / "contracts.py"
    contracts_tree = ast.parse(
        contracts_path.read_text(encoding="utf-8"),
        filename=str(contracts_path),
    )
    for line, root in imported_roots(contracts_tree):
        if root in {"sqlalchemy", "core"}:
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


def main() -> int:
    errors = [error for rule in RULES for error in check_rule(rule)]
    errors.extend(check_bridge_private_access())
    errors.extend(check_database_port_boundaries())
    errors.extend(check_core_client_dependencies())
    errors.extend(check_tool_descriptor_consumers())
    errors.extend(check_model_setting_consumers())
    errors.extend(check_pure_module_boundaries())
    errors.extend(check_legacy_prompt_boundaries())
    if errors:
        print("架构边界检查失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("架构边界检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
