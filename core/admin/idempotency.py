"""Admin 写操作的数据库唯一幂等账本。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models import AdminIdempotencyRecord
from core.registry.validation import canonical_json
from core.time_utils import db_now_naive


class AdminIdempotencyError(RuntimeError):
    code = "admin_idempotency_error"


class AdminIdempotencyConflict(AdminIdempotencyError):
    code = "admin_idempotency_conflict"


class AdminIdempotencyInProgress(AdminIdempotencyError):
    code = "admin_idempotency_in_progress"


class AdminIdempotencyPreviousFailure(AdminIdempotencyError):
    code = "admin_idempotency_previous_failure"


class AdminIdempotencyCorrupt(AdminIdempotencyError):
    code = "admin_idempotency_record_corrupt"


@dataclass(frozen=True, slots=True)
class AdminIdempotencyBegin:
    claimed: bool
    replay_result: dict[str, object] | None = None


def admin_request_sha256(payload: Mapping[str, object]) -> str:
    encoded = canonical_json(dict(payload)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AdminIdempotencyService:
    """以 request_id 主键提供跨进程 at-most-once 语义。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _resolve_existing(
        existing: AdminIdempotencyRecord,
        *,
        action: str,
        target_id: str,
        request_sha256: str,
    ) -> AdminIdempotencyBegin:
        if (
            str(existing.action) != action
            or str(existing.target_id) != target_id
            or str(existing.request_sha256) != request_sha256
        ):
            raise AdminIdempotencyConflict(
                "同一 request_id 已用于不同 Admin 写请求"
            )
        if existing.status == "running":
            raise AdminIdempotencyInProgress(
                "相同 Admin 写请求仍在执行"
            )
        if existing.status == "failed":
            raise AdminIdempotencyPreviousFailure(
                "相同 Admin 写请求此前已失败；请核对状态后使用新 request_id"
            )
        if existing.status != "succeeded":
            raise AdminIdempotencyCorrupt("Admin 幂等账本状态无效")
        try:
            result = json.loads(str(existing.result_json or "{}"))
        except json.JSONDecodeError as exc:
            raise AdminIdempotencyCorrupt(
                "Admin 幂等账本结果损坏"
            ) from exc
        if not isinstance(result, dict):
            raise AdminIdempotencyCorrupt(
                "Admin 幂等账本结果必须是对象"
            )
        return AdminIdempotencyBegin(
            claimed=False,
            replay_result=result,
        )

    def begin(
        self,
        *,
        request_id: str,
        action: str,
        target_id: str,
        request_sha256: str,
    ) -> AdminIdempotencyBegin:
        existing = self.db.get(AdminIdempotencyRecord, request_id)
        if existing is not None:
            return self._resolve_existing(
                existing,
                action=action,
                target_id=target_id,
                request_sha256=request_sha256,
            )
        now = db_now_naive()
        row = AdminIdempotencyRecord(
            request_id=request_id,
            action=action,
            target_id=target_id,
            request_sha256=request_sha256,
            status="running",
            result_json="{}",
            error_code="",
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        try:
            self.db.commit()
            return AdminIdempotencyBegin(claimed=True)
        except IntegrityError:
            self.db.rollback()

        existing = self.db.get(AdminIdempotencyRecord, request_id)
        if existing is None:
            raise AdminIdempotencyCorrupt(
                "幂等唯一约束冲突后找不到账本记录"
            )
        return self._resolve_existing(
            existing,
            action=action,
            target_id=target_id,
            request_sha256=request_sha256,
        )

    def succeed(
        self,
        *,
        request_id: str,
        result: Mapping[str, object],
    ) -> None:
        now = db_now_naive()
        changed = self.db.execute(
            update(AdminIdempotencyRecord)
            .where(
                AdminIdempotencyRecord.request_id == request_id,
                AdminIdempotencyRecord.status == "running",
            )
            .values(
                status="succeeded",
                result_json=json.dumps(
                    dict(result),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                error_code="",
                updated_at=now,
            )
        )
        if int(changed.rowcount or 0) != 1:
            self.db.rollback()
            raise AdminIdempotencyCorrupt(
                "Admin 幂等账本终态写入失败"
            )
        self.db.commit()

    def fail(
        self,
        *,
        request_id: str,
        error_code: str,
    ) -> None:
        self.db.rollback()
        now = db_now_naive()
        self.db.execute(
            update(AdminIdempotencyRecord)
            .where(
                AdminIdempotencyRecord.request_id == request_id,
                AdminIdempotencyRecord.status == "running",
            )
            .values(
                status="failed",
                result_json="{}",
                error_code=str(error_code or "operation_failed")[:128],
                updated_at=now,
            )
        )
        self.db.commit()


__all__ = [
    "AdminIdempotencyBegin",
    "AdminIdempotencyConflict",
    "AdminIdempotencyCorrupt",
    "AdminIdempotencyError",
    "AdminIdempotencyInProgress",
    "AdminIdempotencyPreviousFailure",
    "AdminIdempotencyService",
    "admin_request_sha256",
]
