"""运行时工具服务——解析最终工具集、生成说明并记录决策。"""

import json
import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from core.time_utils import db_now_naive
from core.tool_registration import (
    RESEARCH_TOOL_NAMES as _REGISTRY_RESEARCH_TOOL_NAMES,
    ToolProfileDescriptor,
    get_default_lightweight_tool_names,
    get_tool_profile,
    list_user_tool_registrations,
    normalize_tool_profile_id,
)
from core.tool_registry import (
    SANDBOX_TOOL_NAMES,
    get_tool_def,
    get_tool_descriptor,
)

logger = logging.getLogger("nanobot.runtime_tools")

# 兼容旧调用方；真实默认值由 ToolProfileDescriptor Registry 持有。
_DEFAULT_LIGHTWEIGHT_SET = set(get_default_lightweight_tool_names())
RESEARCH_TOOL_NAMES = _REGISTRY_RESEARCH_TOOL_NAMES
_REPO_ROOT = Path(__file__).resolve().parents[1]


def list_user_tool_descriptors():
    """兼容旧测试／调用方的只读投影；事实源仍是 ToolRegistration。"""

    return tuple(
        registration.descriptor
        for registration in list_user_tool_registrations()
    )


_SUPPORTED_CHAT_TYPES = {"group", "private", "private_superuser"}
_DEFAULT_FIELD_BY_CHAT_TYPE = {
    "group": "group_default",
    "private": "private_default",
    "private_superuser": "private_superuser_default",
}


def normalize_tool_chat_type(chat_type: str = "group") -> str:
    value = str(chat_type or "group").strip().lower()
    if value in {"superuser_private", "private_admin", "admin_private"}:
        return "private_superuser"
    if value in _SUPPORTED_CHAT_TYPES:
        return value
    return "private" if value.startswith("private") else "group"


def normalize_tool_platform(platform: str = "") -> str:
    return str(platform or "").strip().lower()


def _metadata_default(tool_name: str, chat_type: str) -> bool:
    td = get_tool_def(tool_name)
    if not td:
        return False
    if td.force_disabled:
        return False
    normalized = normalize_tool_chat_type(chat_type)
    if normalized == "group":
        return bool(td.group_default)
    if normalized == "private_superuser":
        # superuser 私聊默认比普通私聊更开放，具体禁用仍可由默认模板或用户覆盖控制。
        return True
    return bool(td.private_default)


def resolve_tool_default(tool_name: str, chat_type: str = "group", db=None) -> bool:
    """读取某个默认模板下的工具启用状态，不包含指定覆盖和运行时预设。"""
    normalized = normalize_tool_chat_type(chat_type)
    td = get_tool_def(tool_name)
    if td and td.force_disabled:
        return False
    default = _metadata_default(tool_name, normalized)
    if db is None:
        return default
    try:
        from core.database import SystemSetting
        field = _DEFAULT_FIELD_BY_CHAT_TYPE[normalized]
        row = db.query(SystemSetting).filter(
            SystemSetting.key == f"tool.defaults.{tool_name}.{field}"
        ).first()
        if row:
            return str(row.value).lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return default


def normalize_runtime_preset(value: str = "full") -> str:
    """把旧值和外部输入归一成运行时预设。"""
    return normalize_tool_profile_id(value)


def _parse_lightweight_set(raw: object) -> set[str] | None:
    if raw and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x) for x in parsed if str(x).strip()}
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _load_lightweight_set(db=None) -> set[str]:
    profile = get_tool_profile("lightweight")
    setting_key = profile.setting_key
    defaults = set(profile.default_tool_names)
    if db is not None:
        try:
            from core.database import SystemSetting
            row = db.query(SystemSetting).filter(
                SystemSetting.key == setting_key
            ).first()
            parsed = _parse_lightweight_set(row.value if row else None)
            if parsed is not None:
                return parsed
            return defaults
        except Exception:
            pass
    from core.settings_service import settings
    parsed = _parse_lightweight_set(settings.get(setting_key))
    if parsed is not None:
        return parsed
    return defaults


def resolve_lightweight_default(tool_name: str, db=None) -> bool:
    """读取显式 lightweight 兼容预设中的工具启用状态。"""
    td = get_tool_def(tool_name)
    if td and td.force_disabled:
        return False
    if td and td.force_enabled:
        return True
    return tool_name in _load_lightweight_set(db=db)


def _apply_sandbox_policy(
    enabled: dict[str, bool],
    disabled: dict[str, str],
    *,
    chat_type: str,
    platform: str,
    session_id: str,
    profile: ToolProfileDescriptor,
    db=None,
) -> None:
    """用唯一 session Policy 覆盖 Sandbox 工具，彻底退出 ToolOverride 链。"""

    from core.sandbox.access_policy import SandboxAccessPolicy

    policy = SandboxAccessPolicy(db)
    profile_tool_names = (
        _load_lightweight_set(db=db)
        if profile.mode == "allowlist"
        else set(profile.default_tool_names)
    )

    for tool_name in SANDBOX_TOOL_NAMES:
        descriptor = get_tool_descriptor(tool_name)
        if (
            descriptor is not None
            and descriptor.availability_policy == "force_disabled"
        ):
            enabled[tool_name] = False
            disabled[tool_name] = "系统安全硬禁用"
            continue
        if profile.mode == "force_only":
            enabled[tool_name] = False
            disabled[tool_name] = "当前运行时预设不允许 Sandbox 工具"
            continue
        if (
            profile.mode in {"allowlist", "ceiling"}
            and tool_name not in profile_tool_names
        ):
            enabled[tool_name] = False
            disabled[tool_name] = (
                "运行时轻量预设"
                if profile.mode == "allowlist"
                else "当前运行时预设不允许 Sandbox 工具"
            )
            continue
        decision = policy.evaluate(
            tool_name,
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )
        enabled[tool_name] = decision.allowed
        if decision.allowed:
            disabled.pop(tool_name, None)
        else:
            enabled[tool_name] = False
            disabled[tool_name] = decision.reason


def resolve_effective_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    session_id: str = "",
    runtime_preset: str = "full",
    db=None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """解析实际生效的工具启用/禁用。

    合并顺序（后面覆盖前面）：
    1. ToolDescriptor Registry 默认值 (private_default/group_default)
    2. force_enabled / force_disabled / force_disabled_group 初始硬约束
    3. 运行时预设 (none/lightweight/full)
    4. ToolOverride 表 (scope_type=chat_type/platform/group/user)，Sandbox 工具除外
    5. force_enabled / force_disabled / force_disabled_group 硬约束最终兜底
    6. Sandbox 硬上限、业务开关、canonical session grant 与硬配额最终门禁

    返回 (enabled: {tool_name: bool}, disabled_reasons: {tool_name: reason})
    """
    chat_type = normalize_tool_chat_type(chat_type)
    platform = normalize_tool_platform(platform)
    runtime_preset = normalize_runtime_preset(runtime_preset)
    profile = get_tool_profile(runtime_preset)
    enabled: dict[str, bool] = {}
    disabled: dict[str, str] = {}

    for descriptor in list_user_tool_descriptors():
        name = descriptor.name
        enabled[name] = resolve_tool_default(name, chat_type, db=db)

    for descriptor in list_user_tool_descriptors():
        name = descriptor.name
        td = descriptor.definition
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"
        elif td.force_enabled:
            enabled[name] = True
        if td.force_disabled_group and chat_type == "group":
            enabled[name] = False
            disabled[name] = "群聊强制禁用"

    if profile.mode == "force_only":
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if not (td and td.force_enabled):
                enabled[name] = False
                disabled[name] = "运行时预设=none"
    elif profile.mode == "allowlist":
        lightweight = _load_lightweight_set(db=db)
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if td and td.force_enabled:
                continue
            if name not in lightweight:
                enabled[name] = False
                disabled[name] = "运行时轻量预设"

    if db is not None and profile.allow_scope_overrides:
        try:
            from core.database import ToolOverride
            from sqlalchemy import or_

            conditions = [
                (ToolOverride.scope_type == "chat_type") & (ToolOverride.scope_id == chat_type),
            ]
            if platform:
                conditions.append((ToolOverride.scope_type == "platform") & (ToolOverride.scope_id == platform))
            if group_id:
                conditions.append((ToolOverride.scope_type == "group") & (ToolOverride.scope_id == group_id))
            if user_id:
                conditions.append((ToolOverride.scope_type == "user") & (ToolOverride.scope_id == user_id))

            rows = db.query(ToolOverride).filter(or_(*conditions)).all()
            for row in sorted(rows, key=lambda r: {
                "chat_type": 1, "platform": 2, "group": 3, "user": 4,
            }.get(r.scope_type, 9)):
                if row.tool_name in SANDBOX_TOOL_NAMES:
                    continue
                descriptor = get_tool_descriptor(row.tool_name)
                if descriptor is None or descriptor.framework_owned:
                    continue
                td = get_tool_def(row.tool_name)
                if td and (
                    td.force_enabled
                    or td.force_disabled
                    or (td.force_disabled_group and chat_type == "group")
                ):
                    continue
                enabled[row.tool_name] = bool(row.enabled)
                if not row.enabled:
                    disabled[row.tool_name] = row.reason or f"被 {row.scope_type}:{row.scope_id} 覆盖禁用"
                elif row.tool_name in disabled:
                    del disabled[row.tool_name]
        except Exception as e:
            logger.warning("Failed to load ToolOverride: %s", e)

    for descriptor in list_user_tool_descriptors():
        name = descriptor.name
        td = descriptor.definition
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"
        elif td.force_enabled:
            enabled[name] = True
            disabled.pop(name, None)
        if td.force_disabled_group and chat_type == "group":
            enabled[name] = False
            disabled[name] = "群聊强制禁用"

    if profile.mode == "ceiling":
        ceiling = set(profile.default_tool_names)
        for name in list(enabled):
            if name in ceiling:
                continue
            enabled[name] = False
            disabled[name] = "研究预设固定权限上限"

    for descriptor in list_user_tool_descriptors():
        name = descriptor.name
        td = descriptor.definition
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"

    _apply_sandbox_policy(
        enabled,
        disabled,
        chat_type=chat_type,
        platform=platform,
        session_id=session_id,
        profile=profile,
        db=db,
    )

    return enabled, disabled


@lru_cache(maxsize=8)
def _profile_toolchain(profile_id: str) -> tuple[str, ...]:
    """从实际镜像构建契约提取模型可见工具链。"""

    if profile_id == "developer":
        manifest = (
            _REPO_ROOT
            / "docker"
            / "sandbox"
            / "developer"
            / "toolchain-manifest.json"
        )
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
            commands = parsed.get("required_commands")
            if isinstance(commands, list):
                normalized = tuple(
                    str(item).strip()
                    for item in commands
                    if str(item).strip()
                )
                if normalized:
                    return normalized
        except (OSError, TypeError, ValueError):
            logger.warning(
                "Failed to read developer Sandbox toolchain manifest",
                exc_info=True,
            )
    if profile_id == "restricted":
        requirements = (
            _REPO_ROOT
            / "docker"
            / "sandbox"
            / "python"
            / "requirements.in"
        )
        packages: list[str] = []
        try:
            for line in requirements.read_text(encoding="utf-8").splitlines():
                value = line.split("#", 1)[0].strip()
                if value:
                    packages.append(value.split("==", 1)[0])
        except OSError:
            logger.warning(
                "Failed to read restricted Sandbox requirements",
                exc_info=True,
            )
        return tuple(dict.fromkeys(("sh", "python", *packages)))
    return ()


def build_sandbox_profile_guidance(
    profile_id: str,
    *,
    enabled_tool_names: set[str] | frozenset[str] = frozenset(),
) -> str:
    """按 canonical Profile 生成模型可见环境说明。"""

    from core.sandbox.execution_profiles import (
        load_execution_profile_registry,
    )

    descriptor = load_execution_profile_registry().descriptor(profile_id)
    tools = sorted(set(enabled_tool_names) & set(SANDBOX_TOOL_NAMES))
    toolchain = _profile_toolchain(descriptor.profile_id)
    lines = [f"Sandbox 环境（当前 Profile：{descriptor.profile_id}）："]
    if descriptor.network_policy_id == "none":
        lines.append(
            "- 网络：无网络（network=none）；公网、内网和宿主服务均不可达。"
        )
    else:
        lines.append(
            "- 网络：只能经受控代理访问以下域名："
            + "、".join(descriptor.network_allowlist)
            + "；其他公网域名、IP 直连、私网、宿主和其他容器均不可达。"
        )
        lines.append(
            "- HTTP_PROXY/HTTPS_PROXY 只负责应用选路，不是安全边界；"
            "清空或篡改代理变量不会获得三层直连路径。"
        )
    if tools:
        lines.append("- 当前模型工具：" + "、".join(tools) + "。")
    if toolchain:
        lines.append("- 镜像工具链：" + "、".join(toolchain) + "。")
    lines.extend([
        f"- 单条命令最大时长：{int(descriptor.max_timeout_seconds)} 秒；"
        "静态 wire schema 的 3600 秒只是协议绝对上限。",
        "- 权限：容器内 Docker 不可用，没有 root 或 sudo；"
        "不能选择镜像、网络、挂载、设备或 capability。",
        "- 并发：命令之间可并发；workspace_write 与 "
        "workspace_edit 互斥。命令与文件写并发时不保证一致快照。",
    ])

    if descriptor.execution_mode == "lease":
        lines.extend([
            "- 长进程："
            + ("支持" if descriptor.allow_long_running_processes else "不支持")
            + "；detached 后台进程："
            + ("支持" if descriptor.allow_detached_processes else "不支持")
            + "；stdin："
            + ("支持" if descriptor.allow_stdin else "不支持")
            + "。",
            "- dev server 必须以前台命令启动，并用 yield_time_ms 快速返回后"
            "通过 sandbox_poll 轮询；不要使用 `cmd &`。",
            "- terminate、命令硬超时、输出硬上限或 controller 重启都会回收"
            "当前 Lease，并终止其中全部活动进程。/workspace 与 /runtime 保留；"
            "/tmp 和全部旧 process_id 失效；下一次已授权执行会透明创建新 Lease。",
            f"- 每条命令都是新的 `{descriptor.shell_path} -lc`。"
            "`cd`、`export`、`alias`、`source activate` 不跨命令保留；"
            "使用 cwd、内联 `FOO=bar cmd` 或直接调用 `.venv/bin/python`。",
            "- HOME=/runtime/home 持久，登录 shell 会读取 `~/.profile`；"
            "/workspace 与 /runtime 跨命令及 Lease 重建持久，"
            "/tmp 只在同一 Lease 内持久。",
        ])
    else:
        lines.extend([
            "- 当前是一次性执行 Profile：不支持长进程、detached、stdin、"
            "sandbox_poll 或 sandbox_terminate；yield_time_ms 不会把命令转成"
            "可续接进程。",
            f"- 每条命令都是新的 `{descriptor.shell_path} -lc`。"
            "`cd`、`export`、`alias` 和环境激活不跨命令保留；"
            "使用 cwd、内联环境变量或直接调用虚拟环境解释器。",
            "- /workspace 与 /runtime 跨命令和容器重建持久；"
            "/tmp 随每次一次性容器删除。",
        ])
    return "\n".join(lines)


def _sandbox_prompt_profile(
    enabled: dict[str, bool],
    *,
    chat_type: str,
    platform: str,
    session_id: str,
    db,
) -> str:
    if db is None or not session_id:
        return ""
    from core.sandbox.access_policy import SandboxAccessPolicy

    policy = SandboxAccessPolicy(db)
    preferred = (
        "sandbox_exec",
        "workspace_edit",
        "workspace_read",
        "workspace_search",
    )
    for tool_name in preferred:
        if not enabled.get(tool_name):
            continue
        decision = policy.evaluate(
            tool_name,
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )
        if decision.allowed:
            return decision.execution_profile
    return ""


def build_sandbox_tool_schema_guidance(
    enabled: dict[str, bool],
    chat_type: str = "group",
    *,
    platform: str = "",
    session_id: str = "",
    db=None,
) -> str:
    """为已启用的 Sandbox 工具 schema 生成当前授权 Profile 说明。"""

    profile_id = _sandbox_prompt_profile(
        enabled,
        chat_type=chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )
    if not profile_id:
        return ""
    return build_sandbox_profile_guidance(
        profile_id,
        enabled_tool_names=frozenset(
            name for name, allowed in enabled.items() if allowed
        ),
    )


def build_runtime_tool_prompt(
    enabled: dict[str, bool],
    disabled: dict[str, str],
    chat_type: str = "group",
    *,
    platform: str = "",
    session_id: str = "",
    db=None,
) -> str:
    """兼容旧管理接口；工具事实只通过 API tools schema 暴露给模型。"""

    return ""


def record_runtime_tool_decision(
    session_id: str = "",
    message_id: str = "",
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    enabled: dict | None = None,
    disabled: dict | None = None,
    effective_tools: list | None = None,
    db=None,
) -> bool:
    """写入 RuntimeToolDecision 记录——供 WebUI 排查工具可用性。"""
    try:
        from core.database import RuntimeToolDecision

        def _write(session) -> None:
            session.add(RuntimeToolDecision(
                session_id=session_id,
                message_id=message_id,
                chat_type=chat_type,
                platform=normalize_tool_platform(platform),
                group_id=group_id,
                user_id=user_id,
                runtime_preset=runtime_preset,
                enabled_tools_json=json.dumps(
                    sorted([k for k, v in (enabled or {}).items() if v]),
                    ensure_ascii=False),
                disabled_tools_json=json.dumps(
                    sorted([k for k, v in (enabled or {}).items() if not v]),
                    ensure_ascii=False),
                disabled_reasons_json=json.dumps(disabled or {}, ensure_ascii=False),
                effective_tools_json=json.dumps(
                    list(effective_tools or []), ensure_ascii=False),
            ))
            # 惰性清理：概率 1/50 清理 30 天前旧记录（与写入同一事务）
            import random as _random
            if _random.randint(1, 50) == 1:
                cutoff = db_now_naive() - timedelta(days=30)
                deleted = session.query(RuntimeToolDecision).filter(
                    RuntimeToolDecision.created_at < cutoff
                ).delete()
                if deleted:
                    logger.info("Cleaned %d old runtime_preset_decisions", deleted)

        if db is not None:
            try:
                _write(db)
                db.flush()
                return True
            except Exception as e:
                logger.warning("Failed to record runtime_preset_decision: %s", e)
                return False

        from core import database

        session = database.SessionLocal()
        try:
            _write(session)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.warning("Failed to record runtime_preset_decision: %s", e)
            return False
        finally:
            session.close()
    except Exception as e:
        logger.warning("Failed to record runtime_preset_decision: %s", e)
        return False
