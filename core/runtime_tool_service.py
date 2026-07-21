"""运行时工具服务——解析最终工具集、生成说明并记录决策。"""

import json
import logging
from datetime import timedelta

from core.time_utils import db_now_naive
from core.tool_registry import SANDBOX_TOOL_NAMES, TOOL_METADATA, get_tool_def

logger = logging.getLogger("nanobot.runtime_tools")

_DEFAULT_LIGHTWEIGHT_SET = {
    "reply", "no_reply", "image_summary", "image_generation", "sticker_search",
}
RESEARCH_TOOL_NAMES = frozenset({
    "web_search",
    "reply",
    "no_reply",
})

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
    normalized = str(value or "full").strip().lower()
    if normalized in {"none", "off", "disabled"}:
        return "none"
    if normalized in {"lightweight", "light", "lite"}:
        return "lightweight"
    if normalized in {"research", "research_companion"}:
        return "research"
    return "full"


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
    if db is not None:
        try:
            from core.database import SystemSetting
            row = db.query(SystemSetting).filter(
                SystemSetting.key == "tool.lightweight_set"
            ).first()
            parsed = _parse_lightweight_set(row.value if row else None)
            if parsed is not None:
                return parsed
            return _DEFAULT_LIGHTWEIGHT_SET
        except Exception:
            pass
    from core.settings_service import settings
    parsed = _parse_lightweight_set(settings.get("tool.lightweight_set"))
    if parsed is not None:
        return parsed
    return _DEFAULT_LIGHTWEIGHT_SET


def resolve_lightweight_default(tool_name: str, db=None) -> bool:
    """读取自动降档轻量预设中的工具启用状态。"""
    td = get_tool_def(tool_name)
    if td and td.force_disabled:
        return False
    if td and td.force_enabled:
        return True
    return tool_name in _load_lightweight_set(db=db)


def _sandbox_setting_enabled(key: str, *, db=None, default: bool = False) -> bool:
    """读取 Sandbox 安全开关；请求级数据库会话优先于全局缓存。"""

    if db is not None:
        try:
            from core.database import SystemSetting

            row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if row is not None and row.value is not None:
                return str(row.value).strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            pass
    try:
        from core.settings_service import settings

        return settings.get_bool(key, default)
    except Exception:
        return bool(default)


def _has_user_sandbox_allowlist(db, *, user_id: str, tool_name: str) -> bool:
    """首期只把显式 user 级开启视为普通用户 allowlist。"""

    if db is None or not user_id:
        return False
    try:
        from core.database import ToolOverride

        row = db.query(ToolOverride).filter(
            ToolOverride.tool_name == tool_name,
            ToolOverride.scope_type == "user",
            ToolOverride.scope_id == user_id,
            ToolOverride.enabled == 1,
        ).first()
        return row is not None
    except Exception:
        return False


def _apply_sandbox_policy(
    enabled: dict[str, bool],
    disabled: dict[str, str],
    *,
    chat_type: str,
    user_id: str,
    db=None,
) -> None:
    """最终 Sandbox 门禁，任何默认值或 ToolOverride 都不能绕过总开关。"""

    sandbox_enabled = _sandbox_setting_enabled("sandbox.enabled", db=db)
    exec_enabled = _sandbox_setting_enabled("sandbox.exec_enabled", db=db)
    group_enabled = _sandbox_setting_enabled("sandbox.group_enabled", db=db)

    for tool_name in SANDBOX_TOOL_NAMES:
        if not sandbox_enabled:
            enabled[tool_name] = False
            disabled[tool_name] = "Sandbox 总开关未启用"
            continue
        if tool_name == "sandbox_exec" and not exec_enabled:
            enabled[tool_name] = False
            disabled[tool_name] = "Sandbox 执行能力未启用"
            continue
        if chat_type == "group" and not group_enabled:
            enabled[tool_name] = False
            disabled[tool_name] = "群聊 Workspace 未启用"
            continue
        authorized_identity = (
            chat_type == "private_superuser"
            or _has_user_sandbox_allowlist(
                db,
                user_id=user_id,
                tool_name=tool_name,
            )
        )
        if not authorized_identity:
            enabled[tool_name] = False
            disabled[tool_name] = "仅限私聊超级用户或显式用户 allowlist"


def resolve_effective_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    db=None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """解析实际生效的工具启用/禁用。

    合并顺序（后面覆盖前面）：
    1. TOOL_METADATA 默认值 (private_default/group_default)
    2. force_enabled / force_disabled / force_disabled_group 初始硬约束
    3. 运行时预设 (none/lightweight/full)
    4. ToolOverride 表 (scope_type=chat_type/platform/group/user)，显式覆盖可放开轻量预设
    5. force_enabled / force_disabled / force_disabled_group 硬约束最终兜底
    6. Sandbox 总开关、执行开关、群聊开关与首期身份 allowlist 最终门禁

    返回 (enabled: {tool_name: bool}, disabled_reasons: {tool_name: reason})
    """
    chat_type = normalize_tool_chat_type(chat_type)
    platform = normalize_tool_platform(platform)
    runtime_preset = normalize_runtime_preset(runtime_preset)
    enabled: dict[str, bool] = {}
    disabled: dict[str, str] = {}

    for name, td in TOOL_METADATA.items():
        enabled[name] = resolve_tool_default(name, chat_type, db=db)

    for name, td in TOOL_METADATA.items():
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"
        elif td.force_enabled:
            enabled[name] = True
        if td.force_disabled_group and chat_type == "group":
            enabled[name] = False
            disabled[name] = "群聊强制禁用"

    if runtime_preset == "none":
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if not (td and td.force_enabled):
                enabled[name] = False
                disabled[name] = "运行时预设=none"
    elif runtime_preset == "lightweight":
        lightweight = _load_lightweight_set(db=db)
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if td and td.force_enabled:
                continue
            if name not in lightweight:
                enabled[name] = False
                disabled[name] = "运行时轻量预设"

    if db is not None and runtime_preset != "none":
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
                if row.tool_name not in TOOL_METADATA:
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

    for name, td in TOOL_METADATA.items():
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"
        elif td.force_enabled:
            enabled[name] = True
            disabled.pop(name, None)
        if td.force_disabled_group and chat_type == "group":
            enabled[name] = False
            disabled[name] = "群聊强制禁用"

    if runtime_preset == "research":
        for name in list(enabled):
            if name in RESEARCH_TOOL_NAMES:
                continue
            enabled[name] = False
            disabled[name] = "研究预设固定权限上限"

    for name, td in TOOL_METADATA.items():
        if td.force_disabled:
            enabled[name] = False
            disabled[name] = "系统安全硬禁用"

    _apply_sandbox_policy(
        enabled,
        disabled,
        chat_type=chat_type,
        user_id=user_id,
        db=db,
    )

    return enabled, disabled


def build_runtime_tool_prompt(
    enabled: dict[str, bool],
    disabled: dict[str, str],
    chat_type: str = "group",
) -> str:
    """生成动态 [RuntimeTool] 系统消息。"""
    inactive = [n for n, v in enabled.items() if not v]

    lines = ["[RuntimeTool]"]
    scope = "本群" if chat_type == "group" else "本轮"
    lines.append(f"{scope}真实可调用工具以 API tools schema 为准，本段只做说明和审计。")

    if inactive:
        lines.append(f"已禁用工具（{len(inactive)}个）：")
        for name in sorted(inactive):
            reason = disabled.get(name, "未指定")
            lines.append(f"  - {name}：{reason}")

    if enabled.get("sql_analysis"):
        lines.append(
            "工具边界：查聊天记录、上一句、刚才说过什么、会话日志或数据库统计时使用 "
            "sql_analysis；memory_query 只查询已生成的结构化摘要，不用于检索未摘要的 "
            "chat_logs/conversation_turns。"
        )

    lines.append("规则：不要声称调用未出现在 tools schema 中的工具，也不要声称调用已禁用工具。")
    return "\n".join(lines)


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
