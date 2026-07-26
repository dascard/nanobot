"""块式会话记忆:块边界判定与 open 块状态维护(私聊)。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md 机制 1。

本模块只负责把新写入的 ConversationTurn 归入正确的块,并在时间间隔/尺寸
超限时封口开新块。系统 A(块内滚动压缩)与系统 B(历史块 episode)分别在
P2/P3 落地,此处只维护块的存在与边界。

并发:``assign_turns_to_block`` 始终在写路径的 ``run_sqlite_locked_retry``
事务内、且在两条对话 turn flush(已持有 SQLite 写锁)之后调用,因此读-改-写
不会与其他写者交错;``open_key`` 唯一约束是双开的兜底防线。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.session_memory import config
from core.db.models.session_memory import ConversationBlock
from core.token_utils import estimate_tokens

logger = logging.getLogger("nanobot.session_memory.blocks")


def is_block_memory_enabled(session_id: str = "") -> bool:
    """块式会话记忆是否对该会话启用(P5 灰度)。

    优先级:settings 托管配置(运行时可改,管理端 system_settings)>
    ``config.BLOCK_SESSION_MEMORY_ENABLED`` 常量默认。
    ``block_memory.session_allowlist``(逗号分隔 session_id)非空时为灰度
    白名单,仅名单内会话启用;为空表示全量。settings 读取失败时退回常量。
    """

    try:
        from core.settings_service import settings

        if not settings.get_bool(
            "block_memory.enabled", config.BLOCK_SESSION_MEMORY_ENABLED
        ):
            return False
        allowlist = str(
            settings.get_str("block_memory.session_allowlist", "") or ""
        )
    except Exception:
        return bool(config.BLOCK_SESSION_MEMORY_ENABLED)
    entries = {item.strip() for item in allowlist.split(",") if item.strip()}
    if not entries:
        return True
    return str(session_id or "") in entries


def _gap_seconds(new_created_at: datetime, last_turn_at: datetime | None) -> float:
    """新 user 消息与块内上一条消息的时间间隔(秒);时钟回拨/同秒记 0。"""

    if last_turn_at is None:
        return 0.0
    return max(0.0, (new_created_at - last_turn_at).total_seconds())


def _next_block_seq(db: Any, session_id: str) -> int:
    current = (
        db.query(ConversationBlock.block_seq)
        .filter(ConversationBlock.session_id == session_id)
        .order_by(ConversationBlock.block_seq.desc())
        .first()
    )
    return int(current[0]) + 1 if current and current[0] is not None else 1


def _seal_block(
    db: Any,
    block: ConversationBlock,
    *,
    reason: str,
    now: datetime,
) -> None:
    """封口 open 块:固化 episode(系统 B)后置 closed 并 flush。

    先产出 episode(读取该块滚动摘要、归档之),再清空 open_key 置 closed,
    确保清空 open_key 先于新块 INSERT(避免唯一冲突)。
    """

    from app.session_memory.block_episodes import seal_block_to_episode

    seal_block_to_episode(db, block, reason=reason, now=now)
    block.status = "closed"
    block.open_key = None
    block.closed_at = now
    block.closed_reason = reason
    block.updated_at = now
    db.flush()
    logger.info(
        "[Block] sealed session=%s block_id=%s seq=%s reason=%s turns=%s tokens=%s",
        block.session_id, block.id, block.block_seq, reason,
        block.turn_count, block.token_estimate,
    )


def get_open_block(db: Any, session_id: str) -> ConversationBlock | None:
    """返回该 session 当前唯一的 open 块;无则 None。"""

    return (
        db.query(ConversationBlock)
        .filter(
            ConversationBlock.session_id == session_id,
            ConversationBlock.open_key.isnot(None),
        )
        .first()
    )


def assign_turns_to_block(
    db: Any,
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
    turns: list[Any],
    now: datetime | None = None,
) -> ConversationBlock | None:
    """把一次 persist 新增的 ConversationTurn 归入块。

    - ``turns`` 是同一 exchange 已 flush 的 ConversationTurn(有 id/created_at),
      user 在前 assistant 在后;整批必进同一块。
    - kill-switch 关闭或非私聊直接返回 None(不建块)。
    - gap/尺寸超限则封口旧块开新块;否则并入当前 open 块。
    """

    if not is_block_memory_enabled(session_id):
        return None
    if chat_type != "private":
        return None

    eligible = [
        turn
        for turn in turns
        if turn is not None and getattr(turn, "id", None) is not None
    ]
    if not eligible:
        return None

    now = now or datetime.now()
    new_created_at = eligible[0].created_at or now
    first_turn_id = min(int(turn.id) for turn in eligible)
    last_turn_id = max(int(turn.id) for turn in eligible)
    last_turn_at = max((turn.created_at or now) for turn in eligible)
    delta_tokens = sum(estimate_tokens(getattr(turn, "content", "")) for turn in eligible)
    turn_count = len(eligible)

    open_block = get_open_block(db, session_id)
    if open_block is not None:
        gap = _gap_seconds(new_created_at, open_block.last_turn_at)
        gap_exceeded = gap >= config.BLOCK_GAP_SECONDS
        size_exceeded = (
            int(open_block.turn_count or 0) + turn_count > config.BLOCK_MAX_TURNS
            or int(open_block.token_estimate or 0) + delta_tokens
            > config.BLOCK_MAX_TOKENS
        )
        if not (gap_exceeded or size_exceeded):
            open_block.last_turn_id = last_turn_id
            open_block.last_turn_at = last_turn_at
            open_block.turn_count = int(open_block.turn_count or 0) + turn_count
            open_block.token_estimate = (
                int(open_block.token_estimate or 0) + delta_tokens
            )
            open_block.updated_at = now
            return open_block
        _seal_block(
            db,
            open_block,
            reason="gap" if gap_exceeded else "size",
            now=now,
        )

    new_block = ConversationBlock(
        session_id=session_id,
        user_id=user_id,
        chat_type=chat_type,
        block_seq=_next_block_seq(db, session_id),
        status="open",
        open_key=session_id,
        first_turn_id=first_turn_id,
        last_turn_id=last_turn_id,
        started_at=new_created_at,
        last_turn_at=last_turn_at,
        turn_count=turn_count,
        token_estimate=delta_tokens,
        created_at=now,
        updated_at=now,
    )
    db.add(new_block)
    db.flush()
    logger.info(
        "[Block] opened session=%s block_id=%s seq=%s first_turn=%s",
        session_id, new_block.id, new_block.block_seq, first_turn_id,
    )
    return new_block


__all__ = [
    "assign_turns_to_block",
    "get_open_block",
    "is_block_memory_enabled",
]
