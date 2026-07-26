#!/usr/bin/env python3
"""把存量私聊 ConversationTurn 一次性回填成会话块与 episode。

见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md P5。

用法(服务器上,部署含 20260726_block_session_memory_schema 迁移后执行):

    python scripts/backfill_session_blocks.py --dry-run   # 预览统计
    python scripts/backfill_session_blocks.py             # 实际写入

行为:
- 只处理私聊会话(身份经 parse_compatibility_chat_stream_identity 判定);
  群聊与无法解析的 session 一律跳过。
- 幂等:已有块的会话只补"最早块之前"的未覆盖前缀;无未覆盖 turn 则跳过。
- 按与在线写路径相同的 gap/尺寸规则切块;历史块建为 closed(reason=backfill)
  并同步产出 deterministic episode;仅当会话无任何块且尾段仍在 gap 窗口内时,
  尾段保留为 open 块交给在线路径续写。
- 与在线写路径并发冲突(open_key 唯一冲突)时回滚该会话并跳过,在线优先。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# 直接 `python scripts/backfill_session_blocks.py` 运行时把仓库根加入 sys.path,
# 使 core/app/foundation 可导入;作为模块导入(tests)时无副作用。
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_REQUIRED_MIGRATION = "20260726_block_session_memory_schema"


@dataclass
class BackfillStats:
    sessions_scanned: int = 0
    sessions_backfilled: int = 0
    sessions_skipped_covered: int = 0
    sessions_skipped_non_private: int = 0
    sessions_conflict: int = 0
    blocks_created: int = 0
    blocks_left_open: int = 0
    episodes_created: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions_scanned": self.sessions_scanned,
            "sessions_backfilled": self.sessions_backfilled,
            "sessions_skipped_covered": self.sessions_skipped_covered,
            "sessions_skipped_non_private": self.sessions_skipped_non_private,
            "sessions_conflict": self.sessions_conflict,
            "blocks_created": self.blocks_created,
            "blocks_left_open": self.blocks_left_open,
            "episodes_created": self.episodes_created,
            "errors": list(self.errors),
        }


def _is_private_session(session_id: str) -> bool:
    from foundation.identity import parse_compatibility_chat_stream_identity

    identity = parse_compatibility_chat_stream_identity(
        str(session_id or ""), legacy_platform="qq"
    )
    return identity is not None and identity.chat_type == "private"


def _split_runs(turns: list[Any], *, gap_seconds: int, max_turns: int, max_tokens: int) -> list[list[Any]]:
    """按在线写路径相同规则(gap/尺寸)把有序 turn 切成连续段。"""

    from core.token_utils import estimate_tokens

    runs: list[list[Any]] = []
    current: list[Any] = []
    current_tokens = 0
    prev_at: datetime | None = None
    for turn in turns:
        turn_tokens = estimate_tokens(getattr(turn, "content", "") or "")
        gap = 0.0
        if prev_at is not None and turn.created_at is not None:
            gap = max(0.0, (turn.created_at - prev_at).total_seconds())
        needs_new = bool(current) and (
            gap >= gap_seconds
            or len(current) + 1 > max_turns
            or current_tokens + turn_tokens > max_tokens
        )
        if needs_new:
            runs.append(current)
            current = []
            current_tokens = 0
        current.append(turn)
        current_tokens += turn_tokens
        if turn.created_at is not None:
            prev_at = turn.created_at
    if current:
        runs.append(current)
    return runs


def _backfill_one_session(
    db: Session,
    session_id: str,
    *,
    now: datetime,
    stats: BackfillStats,
) -> None:
    from app.session_memory import config
    from app.session_memory.block_episodes import seal_block_to_episode
    from core.db.models.chat import ConversationTurn
    from core.db.models.session_memory import ConversationBlock
    from core.token_utils import estimate_tokens

    existing = (
        db.query(ConversationBlock)
        .filter(ConversationBlock.session_id == session_id)
        .order_by(ConversationBlock.first_turn_id.asc())
        .all()
    )
    boundary_id = int(existing[0].first_turn_id or 0) if existing else 0
    min_seq = min(int(b.block_seq or 0) for b in existing) if existing else 0

    turn_query = db.query(ConversationTurn).filter(
        ConversationTurn.session_id == session_id
    )
    if existing:
        turn_query = turn_query.filter(ConversationTurn.id < boundary_id)
    turns = turn_query.order_by(ConversationTurn.id.asc()).all()
    if not turns:
        stats.sessions_skipped_covered += 1
        return

    runs = _split_runs(
        turns,
        gap_seconds=config.BLOCK_GAP_SECONDS,
        max_turns=config.BLOCK_MAX_TURNS,
        max_tokens=config.BLOCK_MAX_TOKENS,
    )
    # 已有块时新段编号排在其前(可为 0/负数,仅用于排序);否则从 1 起。
    start_seq = (min_seq - len(runs)) if existing else 1

    for index, run in enumerate(runs):
        seq = start_seq + index
        first_turn = run[0]
        last_turn = run[-1]
        last_turn_at = max((t.created_at or now) for t in run)
        is_tail = index == len(runs) - 1
        keep_open = (
            not existing
            and is_tail
            and (now - last_turn_at).total_seconds() < config.BLOCK_GAP_SECONDS
        )
        block = ConversationBlock(
            session_id=session_id,
            user_id=str(first_turn.user_id or ""),
            chat_type="private",
            block_seq=seq,
            status="open" if keep_open else "closed",
            open_key=session_id if keep_open else None,
            first_turn_id=int(first_turn.id),
            last_turn_id=int(last_turn.id),
            started_at=first_turn.created_at or now,
            last_turn_at=last_turn_at,
            closed_at=None if keep_open else now,
            turn_count=len(run),
            token_estimate=sum(
                estimate_tokens(getattr(t, "content", "") or "") for t in run
            ),
            closed_reason="" if keep_open else "backfill",
            created_at=now,
            updated_at=now,
        )
        db.add(block)
        db.flush()
        stats.blocks_created += 1
        if keep_open:
            stats.blocks_left_open += 1
        else:
            seal_block_to_episode(db, block, reason="backfill", now=now)
            stats.episodes_created += 1
    stats.sessions_backfilled += 1


def backfill_session_blocks(
    db: Session,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    sessions: list[str] | None = None,
    limit_sessions: int = 0,
) -> BackfillStats:
    """回填全部(或指定)私聊会话;返回统计。可重复执行,幂等。"""

    from core.db.models.chat import ConversationTurn

    now = now or datetime.now()
    stats = BackfillStats()

    rows = (
        db.query(ConversationTurn.session_id)
        .distinct()
        .order_by(ConversationTurn.session_id.asc())
        .all()
    )
    session_ids = [str(row[0]) for row in rows if row[0]]
    if sessions:
        wanted = {str(item) for item in sessions}
        session_ids = [sid for sid in session_ids if sid in wanted]
    if limit_sessions > 0:
        session_ids = session_ids[:limit_sessions]

    for session_id in session_ids:
        stats.sessions_scanned += 1
        if not _is_private_session(session_id):
            stats.sessions_skipped_non_private += 1
            continue
        try:
            _backfill_one_session(db, session_id, now=now, stats=stats)
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except IntegrityError:
            db.rollback()
            stats.sessions_conflict += 1
        except Exception as exc:  # 单会话失败不拖垮整体;记录后继续。
            db.rollback()
            stats.errors.append(f"{session_id}: {type(exc).__name__}: {exc}")
    return stats


def _assert_migration_applied(engine: Any) -> None:
    with engine.connect() as conn:
        try:
            rows = conn.execute(
                text("SELECT version FROM schema_migrations WHERE version = :v"),
                {"v": _REQUIRED_MIGRATION},
            ).fetchall()
        except Exception as exc:
            raise SystemExit(
                f"无法读取 schema_migrations(数据库未初始化?): {exc}"
            ) from exc
    if not rows:
        raise SystemExit(
            f"缺少迁移 {_REQUIRED_MIGRATION};请先部署新版本并启动一次服务再回填。"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填存量私聊会话块与 episode")
    parser.add_argument(
        "--database-url",
        default="",
        help="SQLAlchemy 数据库 URL;默认取 core.database.DATABASE_URL",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument(
        "--session", action="append", default=[], help="只处理指定 session_id(可重复)",
    )
    parser.add_argument(
        "--limit-sessions", type=int, default=0, help="最多处理的会话数(0=不限)",
    )
    args = parser.parse_args(argv)

    if args.database_url:
        database_url = args.database_url
    else:
        from core.database import DATABASE_URL

        database_url = DATABASE_URL

    engine = create_engine(database_url)
    _assert_migration_applied(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        stats = backfill_session_blocks(
            db,
            dry_run=args.dry_run,
            sessions=list(args.session) or None,
            limit_sessions=int(args.limit_sessions),
        )
    finally:
        db.close()
        engine.dispose()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] 会话块回填完成:")
    for key, value in stats.as_dict().items():
        print(f"  {key}: {value}")
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
