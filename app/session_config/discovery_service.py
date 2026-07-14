"""从运行记录和配置表发现可管理的 canonical 会话。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import case, func

from core.chat_stream_identity import (
    ChatStreamIdentity,
    ChatStreamIdentityError,
    canonicalize_legacy_chat_stream_id,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)
from core.database import (
    AgentRun,
    ChatLog,
    ChatStreamConfig,
    ConversationTurn,
    User,
)


IdentityStatus = Literal["canonical", "legacy_alias", "unresolved", "invalid"]


@dataclass(frozen=True)
class DiscoveredChatStream:
    """供 Admin 列表使用的会话身份和来源摘要。"""

    chat_stream_id: str
    platform: str
    chat_type: str
    session_id: str
    runtime_session_id: str
    session_name: str
    identity_status: IdentityStatus
    identity_conflict: bool
    legacy_aliases: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass
class _DiscoveryBuilder:
    chat_stream_id: str
    platform: str = ""
    chat_type: str = ""
    session_id: str = ""
    runtime_session_id: str = ""
    session_name: str = ""
    identity_status: IdentityStatus = "canonical"
    sources: set[str] = field(default_factory=set)
    legacy_aliases: set[str] = field(default_factory=set)
    canonical_config: bool = False
    name_priority: int = -1
    runtime_id_priority: int = -1


_NAME_PRIORITY = {
    "chat_stream_config": 0,
    "user": 1,
    "conversation_turn": 2,
    "agent_run": 3,
    "chat_log": 4,
    "runtime": 5,
}


def _safe_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _raw_identity_status(raw: str) -> IdentityStatus:
    if ":" in raw or raw.startswith(("group_", "private_")):
        return "invalid"
    return "unresolved"


def _set_session_name(builder: _DiscoveryBuilder, name: Any, source: str) -> None:
    normalized = str(name or "").strip()
    priority = _NAME_PRIORITY.get(source, 0)
    if normalized and priority > builder.name_priority:
        builder.session_name = normalized
        builder.name_priority = priority


def _set_runtime_session_id(
    builder: _DiscoveryBuilder,
    runtime_session_id: Any,
    source: str,
) -> None:
    normalized = str(runtime_session_id or "").strip()
    priority = _NAME_PRIORITY.get(source, 0)
    if normalized and priority > builder.runtime_id_priority:
        builder.runtime_session_id = normalized
        builder.runtime_id_priority = priority


def _add_identity(
    builders: dict[str, _DiscoveryBuilder],
    identity: ChatStreamIdentity,
    *,
    source: str,
    session_name: Any = "",
    runtime_session_id: Any = "",
    legacy_alias: str = "",
    canonical_config: bool = False,
) -> None:
    builder = builders.setdefault(
        identity.chat_stream_id,
        _DiscoveryBuilder(
            chat_stream_id=identity.chat_stream_id,
            platform=identity.platform,
            chat_type=identity.chat_type,
            session_id=identity.external_session_id,
        ),
    )
    builder.sources.add(source)
    builder.canonical_config = builder.canonical_config or canonical_config
    if legacy_alias:
        builder.legacy_aliases.add(legacy_alias)
    _set_session_name(builder, session_name, source)
    _set_runtime_session_id(builder, runtime_session_id, source)


def _add_raw_identity(
    builders: dict[str, _DiscoveryBuilder],
    raw: Any,
    *,
    source: str,
    session_name: Any = "",
    status: IdentityStatus | None = None,
) -> None:
    value = str(raw or "").strip()
    if not value:
        return
    builder = builders.setdefault(
        value,
        _DiscoveryBuilder(
            chat_stream_id=value,
            session_id=value,
            identity_status=status or _raw_identity_status(value),
        ),
    )
    builder.sources.add(source)
    if builder.identity_status == "unresolved" and status == "invalid":
        builder.identity_status = "invalid"
    _set_session_name(builder, session_name, source)
    _set_runtime_session_id(builder, value, source)


def _add_unknown_session(
    builders: dict[str, _DiscoveryBuilder],
    raw: Any,
    *,
    source: str,
    session_name: Any = "",
) -> None:
    value = str(raw or "").strip()
    if not value:
        return
    try:
        identity = parse_canonical_chat_stream_id(value)
    except ChatStreamIdentityError:
        canonical = canonicalize_legacy_chat_stream_id(value)
        if canonical is None:
            _add_raw_identity(
                builders,
                value,
                source=source,
                session_name=session_name,
            )
            return
        identity = parse_canonical_chat_stream_id(canonical)
    _add_identity(
        builders,
        identity,
        source=source,
        session_name=session_name,
        runtime_session_id=value,
    )


def _add_known_session(
    builders: dict[str, _DiscoveryBuilder],
    raw: Any,
    *,
    platform: Any,
    chat_type: Any,
    source: str,
    session_name: Any = "",
) -> None:
    value = str(raw or "").strip()
    if not value:
        return
    try:
        identity = resolve_chat_stream_identity(
            platform=str(platform or ""),
            chat_type=str(chat_type or ""),
            session_id=value,
        )
    except ChatStreamIdentityError:
        _add_raw_identity(
            builders,
            value,
            source=source,
            session_name=session_name,
            status="invalid",
        )
        return
    _add_identity(
        builders,
        identity,
        source=source,
        session_name=session_name,
        runtime_session_id=value,
    )


def _discover_config_rows(db: Any, builders: dict[str, _DiscoveryBuilder]) -> None:
    rows = db.query(ChatStreamConfig.chat_stream_id).all()
    for (raw_id,) in rows:
        value = str(raw_id or "").strip()
        if not value:
            continue
        try:
            identity = parse_canonical_chat_stream_id(value)
        except ChatStreamIdentityError:
            canonical = canonicalize_legacy_chat_stream_id(value)
            if canonical is None:
                _add_raw_identity(
                    builders,
                    value,
                    source="chat_stream_config",
                )
                continue
            identity = parse_canonical_chat_stream_id(canonical)
            _add_identity(
                builders,
                identity,
                source="chat_stream_config",
                legacy_alias=value,
            )
            continue
        _add_identity(
            builders,
            identity,
            source="chat_stream_config",
            canonical_config=True,
        )


def _discover_agent_runs(db: Any, builders: dict[str, _DiscoveryBuilder]) -> None:
    valid_platform = func.coalesce(
        func.nullif(
            func.lower(
                func.trim(func.json_extract(AgentRun.meta_json, "$.platform")),
            ),
            "",
        ),
        "qq",
    )
    platform = case(
        (func.json_valid(AgentRun.meta_json) == 1, valid_platform),
        else_="qq",
    )
    rank = func.row_number().over(
        partition_by=(AgentRun.session_id, AgentRun.chat_type, platform),
        order_by=(AgentRun.started_at.desc(), AgentRun.run_id.desc()),
    ).label("discovery_rank")
    ranked = db.query(
        AgentRun.session_id.label("session_id"),
        AgentRun.chat_type.label("chat_type"),
        AgentRun.meta_json.label("meta_json"),
        platform.label("platform"),
        rank,
    ).filter(
        AgentRun.session_id != "",
        AgentRun.chat_type.in_(("group", "private")),
    ).subquery()
    rows = db.query(
        ranked.c.session_id,
        ranked.c.chat_type,
        ranked.c.meta_json,
        ranked.c.platform,
    ).filter(ranked.c.discovery_rank == 1).all()
    for session_id, chat_type, meta_json, platform_name in rows:
        meta = _safe_json_object(meta_json)
        _add_known_session(
            builders,
            session_id,
            platform=platform_name,
            chat_type=chat_type,
            source="agent_run",
            session_name=meta.get("session_name"),
        )


def _discover_chat_logs(db: Any, builders: dict[str, _DiscoveryBuilder]) -> None:
    latest_ids = db.query(
        ChatLog.session_id.label("session_id"),
        func.max(ChatLog.id).label("latest_id"),
    ).filter(
        ChatLog.session_id.is_not(None),
        ChatLog.session_id != "",
    ).group_by(ChatLog.session_id).subquery()
    rows = db.query(
        ChatLog.session_id,
        ChatLog.session_name,
    ).join(
        latest_ids,
        ChatLog.id == latest_ids.c.latest_id,
    ).all()
    for session_id, session_name in rows:
        _add_unknown_session(
            builders,
            session_id,
            source="chat_log",
            session_name=session_name,
        )


def _discover_conversation_turns(
    db: Any,
    builders: dict[str, _DiscoveryBuilder],
) -> None:
    rows = db.query(ConversationTurn.session_id).filter(
        ConversationTurn.session_id.is_not(None)
    ).distinct().all()
    for (session_id,) in rows:
        _add_unknown_session(
            builders,
            session_id,
            source="conversation_turn",
        )


def _discover_legacy_users(db: Any, builders: dict[str, _DiscoveryBuilder]) -> None:
    rows = db.query(User.id, User.name).filter(User.id.like("group_%")).all()
    for session_id, session_name in rows:
        _add_known_session(
            builders,
            session_id,
            platform="qq",
            chat_type="group",
            source="user",
            session_name=session_name,
        )


def _discover_runtime_snapshot(
    runtime_snapshot: dict[str, Any],
    builders: dict[str, _DiscoveryBuilder],
) -> None:
    for session_id, state in runtime_snapshot.items():
        state_object = state if isinstance(state, dict) else {}
        chat_type = state_object.get("chat_type") or "group"
        if chat_type not in {"group", "private"}:
            continue
        _add_known_session(
            builders,
            session_id,
            platform=state_object.get("platform") or "qq",
            chat_type=chat_type,
            source="runtime",
            session_name=(
                state_object.get("session_name")
                or state_object.get("group_name")
            ),
        )


def discover_chat_streams(
    db: Any,
    *,
    runtime_snapshot: dict[str, Any],
) -> list[DiscoveredChatStream]:
    """聚合配置与运行记录，不读取聊天正文，也不修改任何历史数据。"""
    builders: dict[str, _DiscoveryBuilder] = {}
    _discover_config_rows(db, builders)
    _discover_agent_runs(db, builders)
    _discover_runtime_snapshot(runtime_snapshot, builders)
    _discover_chat_logs(db, builders)
    _discover_conversation_turns(db, builders)
    _discover_legacy_users(db, builders)

    discovered: list[DiscoveredChatStream] = []
    for builder in builders.values():
        status = builder.identity_status
        if builder.platform:
            if builder.canonical_config:
                status = "canonical"
            elif builder.legacy_aliases:
                status = "legacy_alias"
            else:
                status = "canonical"
        discovered.append(DiscoveredChatStream(
            chat_stream_id=builder.chat_stream_id,
            platform=builder.platform,
            chat_type=builder.chat_type,
            session_id=builder.session_id,
            runtime_session_id=builder.runtime_session_id,
            session_name=builder.session_name,
            identity_status=status,
            identity_conflict=(
                builder.canonical_config and bool(builder.legacy_aliases)
            ),
            legacy_aliases=tuple(sorted(builder.legacy_aliases)),
            sources=tuple(sorted(builder.sources)),
        ))
    return sorted(discovered, key=lambda item: item.chat_stream_id)
