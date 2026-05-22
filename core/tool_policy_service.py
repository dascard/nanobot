"""工具策略服务——resolve_effective_tools + build_tool_policy_prompt + 审计记录。"""

import json
import logging

from core.tool_registry import TOOL_METADATA, get_tool_def

logger = logging.getLogger("nanobot.tool_policy")

_DEFAULT_LIMITED_SET = {
    "reply", "no_reply", "image_summary", "python_sandbox", "sticker_search",
}


def _load_limited_set() -> set[str]:
    from core.settings_service import settings
    raw = settings.get("tool.limited_set")
    if raw and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x) for x in parsed if str(x).strip()}
        except (json.JSONDecodeError, TypeError):
            pass
    return _DEFAULT_LIMITED_SET


def resolve_effective_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    tool_policy: str = "full",
    db=None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """解析实际生效的工具启用/禁用。

    合并顺序（后面覆盖前面）：
    1. TOOL_METADATA 默认值 (private_default/group_default)
    2. force_enabled / force_disabled_group
    3. ToolOverride 表 (scope_type=chat_type/group/user)
    4. tool_policy 安全兜底 (none/limited/full)

    返回 (enabled: {tool_name: bool}, disabled_reasons: {tool_name: reason})
    """
    enabled: dict[str, bool] = {}
    disabled: dict[str, str] = {}

    for name, td in TOOL_METADATA.items():
        default = td.group_default if chat_type == "group" else td.private_default
        # 读取 SystemSetting 覆盖默认值
        if db is not None:
            try:
                from core.database import SystemSetting
                field = "group_default" if chat_type == "group" else "private_default"
                row = db.query(SystemSetting).filter(
                    SystemSetting.key == f"tool.defaults.{name}.{field}"
                ).first()
                if row:
                    default = str(row.value).lower() in ("1", "true", "yes", "on")
            except Exception:
                pass
        enabled[name] = default

    for name, td in TOOL_METADATA.items():
        if td.force_enabled:
            enabled[name] = True
        if td.force_disabled_group and chat_type == "group":
            enabled[name] = False
            disabled[name] = "群聊强制禁用"

    if db is not None:
        try:
            from core.database import ToolOverride
            rows = db.query(ToolOverride).filter(
                (ToolOverride.scope_type == "chat_type") & (ToolOverride.scope_id == chat_type)
                | (ToolOverride.scope_type == "group") & (ToolOverride.scope_id == group_id)
                | (ToolOverride.scope_type == "user") & (ToolOverride.scope_id == user_id)
            ).all()
            for row in sorted(rows, key=lambda r: {
                "chat_type": 1, "group": 2, "user": 3,
            }.get(r.scope_type, 9)):
                if row.tool_name not in TOOL_METADATA:
                    continue
                td = get_tool_def(row.tool_name)
                if td and (td.force_enabled or (td.force_disabled_group and chat_type == "group")):
                    continue
                enabled[row.tool_name] = bool(row.enabled)
                if not row.enabled:
                    disabled[row.tool_name] = row.reason or f"被 {row.scope_type}:{row.scope_id} 覆盖禁用"
                elif row.tool_name in disabled:
                    del disabled[row.tool_name]
        except Exception as e:
            logger.warning("Failed to load ToolOverride: %s", e)

    if tool_policy == "none":
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if not (td and td.force_enabled):
                enabled[name] = False
                disabled[name] = "tool_policy=none"
    elif tool_policy == "limited":
        limited = _load_limited_set()
        for name in list(enabled.keys()):
            td = get_tool_def(name)
            if td and td.force_enabled:
                continue
            if name not in limited:
                enabled[name] = False
                disabled[name] = f"tool_policy=limited"

    return enabled, disabled


def build_tool_policy_prompt(
    enabled: dict[str, bool],
    disabled: dict[str, str],
    chat_type: str = "group",
) -> str:
    """生成动态 [ToolPolicy] 系统消息。"""
    inactive = [n for n, v in enabled.items() if not v]

    lines = ["[ToolPolicy]"]
    scope = "本群" if chat_type == "group" else "本轮"
    lines.append(f"{scope}真实可调用工具以 API tools schema 为准，本段只做说明和审计。")

    if inactive:
        lines.append(f"已禁用工具（{len(inactive)}个）：")
        for name in sorted(inactive):
            reason = disabled.get(name, "未指定")
            lines.append(f"  - {name}：{reason}")

    lines.append("规则：不要声称调用未出现在 tools schema 中的工具，也不要声称调用已禁用工具。")
    lines.append("如需回复，必须真实调用 reply(content)；不回复则调用 no_reply(reason)。")
    return "\n".join(lines)


def record_tool_policy_decision(
    session_id: str = "",
    message_id: str = "",
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    tool_policy: str = "full",
    enabled: dict | None = None,
    disabled: dict | None = None,
    effective_tools: list | None = None,
):
    """写入 ToolPolicyDecision 记录——供 WebUI 排查工具可用性。"""
    try:
        from core.database import SessionLocal, ToolPolicyDecision
        db = SessionLocal()
        try:
            db.add(ToolPolicyDecision(
                session_id=session_id,
                message_id=message_id,
                chat_type=chat_type,
                group_id=group_id,
                user_id=user_id,
                tool_policy=tool_policy,
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
                from datetime import datetime as _dt, timedelta as _td
                cutoff = _dt.now() - _td(days=30)
                deleted = db.query(ToolPolicyDecision).filter(
                    ToolPolicyDecision.created_at < cutoff
                ).delete()
                if deleted:
                    logger.info("Cleaned %d old tool_policy_decisions", deleted)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to record tool_policy_decision: %s", e)
