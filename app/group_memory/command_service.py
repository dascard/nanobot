"""群体记忆显式事务命令服务。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from core.chat_stream_identity import identity_storage_aliases
from core.db.group_memory_contracts import (
    ChatStreamConfigRecord,
    GroupMemoryRepositoryConflict,
    GroupMemoryRepositoryPort,
    GroupMemoryRecord,
)
from core.group_memory import (
    _cluster_key,
    _content_hash,
    resolve_group_memory_identity,
    validate_group_memory_row_identity,
)
from core.time_utils import db_now_naive


_ALLOWED_STATUSES = frozenset({
    "review",
    "active",
    "disabled",
    "archived",
    "rejected",
})
_ALLOWED_INJECT_POLICIES = frozenset({
    "auto",
    "manual_only",
    "never",
})
_ALLOWED_PROFILE_MODES = frozenset({"off", "preview", "on"})


class GroupMemoryCommandError(RuntimeError):
    """群体记忆命令无法完成。"""


class GroupMemoryNotFound(GroupMemoryCommandError):
    """目标群体记忆不存在。"""


class GroupMemoryDuplicate(GroupMemoryCommandError):
    """同一群、类型和内容的记忆已存在。"""


class GroupMemoryCommandService:
    def __init__(self, repository: GroupMemoryRepositoryPort) -> None:
        self.repository = repository

    def set_injection_mode(
        self,
        *,
        chat_stream_id: str,
        mode: str,
    ) -> ChatStreamConfigRecord:
        normalized_mode = str(mode or "").strip()
        if normalized_mode not in _ALLOWED_PROFILE_MODES:
            raise ValueError("group_profile_mode 无效")
        try:
            row = self.repository.set_injection_mode(
                str(chat_stream_id or "").strip(),
                normalized_mode,
            )
            self.repository.commit()
            return row
        except BaseException:
            self.repository.rollback()
            raise

    def update_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        status: str | None = None,
        inject_policy: str | None = None,
        disabled_reason: str | None = None,
        rejected_reason: str | None = None,
    ) -> GroupMemoryRecord:
        row = self.repository.get_memory(int(memory_id))
        if row is None:
            raise GroupMemoryNotFound("group memory not found")
        identity = resolve_group_memory_identity(
            row.chat_stream_id or row.group_id,
            platform="qq",
        )
        validate_group_memory_row_identity(row, identity)

        values: dict[str, object] = {}
        if content is not None:
            normalized_content = str(content).strip()
            if not normalized_content:
                raise ValueError("content is empty")
            content_hash = _content_hash(normalized_content)
            duplicate = self.repository.find_duplicate(
                chat_stream_id=identity.chat_stream_id,
                legacy_group_ids=identity_storage_aliases(
                    identity,
                    include_raw_group_id=True,
                ),
                memory_type=row.memory_type,
                content_hash=content_hash,
                exclude_id=row.id,
                # 治理管线写入的行使用原文完整 sha256,必须一并匹配。
                alternate_hashes=(
                    hashlib.sha256(
                        normalized_content.encode("utf-8")
                    ).hexdigest(),
                ),
            )
            if duplicate is not None:
                raise GroupMemoryDuplicate("已有相同记忆，可合并或归档")
            # 管理端改写正文属于人工治理动作:必须同步 approved_content_hash
            # 与治理来源字段(对齐 governance adapter 的 _mark_human_target
            # 契约),否则未经审核的新文本会顶着指向旧内容的审批哈希继续
            # 注入,后续同 key 冲突判定也会基于失效哈希。
            values.update({
                "content": normalized_content,
                "content_hash": content_hash,
                "cluster_key": _cluster_key(normalized_content),
                "approved_content_hash": hashlib.sha256(
                    normalized_content.encode("utf-8")
                ).hexdigest(),
                "approval_source": "human",
                "governance_mode": "human_managed",
                "human_reviewer_id": "admin",
                "human_reviewed_at": db_now_naive(),
                "human_action": "update",
            })
        if status is not None:
            normalized_status = str(status).strip()
            if normalized_status not in _ALLOWED_STATUSES:
                raise ValueError("status 无效")
            values["status"] = normalized_status
        if inject_policy is not None:
            normalized_policy = str(inject_policy).strip()
            if normalized_policy not in _ALLOWED_INJECT_POLICIES:
                raise ValueError("inject_policy 无效")
            values["inject_policy"] = normalized_policy
        if disabled_reason is not None:
            values["disabled_reason"] = str(disabled_reason).strip()
        if rejected_reason is not None:
            values["rejected_reason"] = str(rejected_reason).strip()
        if not values:
            return row

        try:
            updated = self.repository.update_memory(row.id, **values)
            self.repository.commit()
            return updated
        except GroupMemoryRepositoryConflict as exc:
            self.repository.rollback()
            raise GroupMemoryDuplicate(
                "已有相同记忆，可合并或归档"
            ) from exc
        except BaseException:
            self.repository.rollback()
            raise

    def record_injected(self, memory_ids: Sequence[int]) -> int:
        normalized = tuple(
            dict.fromkeys(
                int(item)
                for item in memory_ids
                if int(item) > 0
            )
        )
        if not normalized:
            return 0
        try:
            count = self.repository.mark_injected(
                normalized,
                injected_at=db_now_naive(),
            )
            self.repository.commit()
            return int(count)
        except BaseException:
            self.repository.rollback()
            raise


__all__ = [
    "GroupMemoryCommandError",
    "GroupMemoryCommandService",
    "GroupMemoryDuplicate",
    "GroupMemoryNotFound",
]
