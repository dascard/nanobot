"""Run Ledger 的权威只读模型。

读取器先固定 stream head 的 high-water，再完整分页读取并校验摘要链。调用方只能在
得到完整快照后使用投影，不能把部分事件或 legacy 状态当作当前运行事实。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.run_ledger.contracts import (
    RunEventLedgerPort,
    RunLedgerEventRecord,
    RunLedgerHead,
    RunLedgerIntegrityError,
)
from core.run_ledger.projection import RunLedgerProjection, project_run_ledger


@dataclass(frozen=True, slots=True)
class AuthoritativeRunLedgerView:
    """固定 high-water 后得到的完整、可重建 Run 视图。"""

    head: RunLedgerHead
    records: tuple[RunLedgerEventRecord, ...]
    projection: RunLedgerProjection

    @property
    def accepted(self) -> RunLedgerEventRecord:
        return self.records[0]


def load_authoritative_run_view(
    ledger: RunEventLedgerPort,
    run_id: str,
    *,
    page_size: int = 1000,
) -> AuthoritativeRunLedgerView | None:
    """读取一个 Run 的完整权威快照；不存在 Ledger 时返回 ``None``。

    ``head`` 是本次读取的 high-water。读取期间追加的新事件不会混入该快照；如果
    high-water 以内的事实不完整、摘要链损坏或协调头与事实不一致，则拒绝投影。
    """

    if type(page_size) is not int or not 0 < page_size <= 5000:
        raise ValueError("page_size 必须在 1 到 5000 之间")
    head = ledger.head(run_id)
    if head is None:
        return None
    high_water = head.last_sequence
    if high_water <= 0:
        raise RunLedgerIntegrityError("Run Ledger head 没有对应事实")

    records: list[RunLedgerEventRecord] = []
    after_sequence = 0
    while after_sequence < high_water:
        page = ledger.read(
            run_id,
            after_sequence=after_sequence,
            through_sequence=high_water,
            limit=min(page_size, high_water - after_sequence),
        )
        if not page:
            raise RunLedgerIntegrityError(
                "Run Ledger high-water 以内存在缺失事实"
            )
        records.extend(page)
        after_sequence = page[-1].sequence

    frozen_records = tuple(records)
    if len(frozen_records) != high_water:
        raise RunLedgerIntegrityError(
            "Run Ledger 事实数量与 high-water 不一致"
        )
    projection = project_run_ledger(frozen_records)
    if projection is None:
        raise RunLedgerIntegrityError("Run Ledger 无法生成权威投影")
    if frozen_records[0].event_type != "run.accepted":
        raise RunLedgerIntegrityError("Run Ledger 首条事实不是 run.accepted")
    if frozen_records[-1].event_id != head.last_event_id:
        raise RunLedgerIntegrityError("Run Ledger head 的 last_event_id 不一致")
    if frozen_records[-1].event_sha256 != head.last_event_sha256:
        raise RunLedgerIntegrityError("Run Ledger head 的摘要不一致")
    terminal_sequences = tuple(
        record.sequence
        for record in frozen_records
        if record.event_type == "run.terminated"
    )
    if len(terminal_sequences) > 1:
        raise RunLedgerIntegrityError("Run Ledger 包含多个终止事实")
    expected_terminal = terminal_sequences[0] if terminal_sequences else None
    if head.terminal_sequence != expected_terminal:
        raise RunLedgerIntegrityError("Run Ledger head 的终态位置不一致")
    return AuthoritativeRunLedgerView(
        head=head,
        records=frozen_records,
        projection=projection,
    )


__all__ = [
    "AuthoritativeRunLedgerView",
    "load_authoritative_run_view",
]
