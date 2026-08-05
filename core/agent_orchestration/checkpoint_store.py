"""有界多 Agent checkpoint Store。"""

from __future__ import annotations

import asyncio

from core.agent_orchestration.contracts import (
    AgentOrchestrationCheckpoint,
)


class InMemoryAgentOrchestrationCheckpointStore:
    """严格单调的测试 Store；生产启用前必须替换为持久实现。"""

    def __init__(self, *, max_records: int = 1024) -> None:
        if type(max_records) is not int or not 1 <= max_records <= 100_000:
            raise ValueError("max_records 必须是 1..100000 的整数")
        self._max_records = max_records
        self._records: dict[str, AgentOrchestrationCheckpoint] = {}
        self._order: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def save(
        self,
        checkpoint: AgentOrchestrationCheckpoint,
    ) -> AgentOrchestrationCheckpoint:
        if not isinstance(checkpoint, AgentOrchestrationCheckpoint):
            raise TypeError("checkpoint 必须是 AgentOrchestrationCheckpoint")
        async with self._lock:
            existing = self._records.get(checkpoint.checkpoint_id)
            if existing is not None:
                if existing == checkpoint:
                    return existing
                raise ValueError("checkpoint_id 已绑定不同内容")
            if len(self._records) >= self._max_records:
                raise RuntimeError("checkpoint Store 容量已满")
            ordered = self._order.setdefault(checkpoint.orchestration_id, [])
            if ordered:
                previous = self._records[ordered[-1]]
                if checkpoint.identity.owner != previous.identity.owner:
                    raise ValueError("同一编排不能切换 checkpoint owner")
                if checkpoint.plan_sha256 != previous.plan_sha256:
                    raise ValueError("同一编排不能切换冻结计划")
                if checkpoint.sequence != previous.sequence + 1:
                    raise ValueError("checkpoint sequence 必须连续递增")
                if checkpoint.parent_checkpoint_id != previous.checkpoint_id:
                    raise ValueError("checkpoint 必须引用当前最新状态")
            elif checkpoint.sequence != 1 or checkpoint.parent_checkpoint_id:
                raise ValueError("首个 checkpoint 边界无效")
            self._records[checkpoint.checkpoint_id] = checkpoint
            ordered.append(checkpoint.checkpoint_id)
            return checkpoint

    async def load_latest(
        self,
        orchestration_id: str,
        *,
        owner_id: str,
    ) -> AgentOrchestrationCheckpoint | None:
        normalized_id = str(orchestration_id or "").strip()
        normalized_owner = str(owner_id or "").strip()
        if not normalized_id or not normalized_owner:
            raise ValueError("orchestration_id 和 owner_id 不能为空")
        async with self._lock:
            ordered = self._order.get(normalized_id, ())
            if not ordered:
                return None
            checkpoint = self._records[ordered[-1]]
            if checkpoint.identity.owner.canonical_id != normalized_owner:
                return None
            return checkpoint


__all__ = ["InMemoryAgentOrchestrationCheckpointStore"]
