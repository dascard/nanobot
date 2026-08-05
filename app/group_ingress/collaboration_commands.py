"""群聊中显式、严格且不进入模型的多 Agent 协作命令。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class GroupCollaborationCommandKind(StrEnum):
    INVITE = "invite"
    STATUS = "status"
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class GroupCollaborationCommand:
    kind: GroupCollaborationCommandKind
    board_id: str
    task_id: str = ""
    client_id: str = ""
    delivery_id: str = ""
    delivery_sha256: str = ""
    reason_code: str = ""


_CLIENT = r"(?P<client>[a-z][a-z0-9_-]{0,31})"
_BOARD = r"(?P<board>\S{1,160})"
_TASK = r"(?P<task>\S{1,128})"
_DELIVERY = r"(?P<delivery>\S{1,160})"
_SHA256 = r"(?P<sha>[0-9a-f]{64})"
_REASON = r"(?P<reason>[A-Za-z0-9_.:-]{1,128})"

_INVITE_RE = re.compile(rf"^@agent:{_CLIENT}\s+{_BOARD}\s+{_TASK}$")
_STATUS_RE = re.compile(rf"^@agent\s+状态\s+{_BOARD}$")
_APPROVE_RE = re.compile(
    rf"^@agent\s+审批\s+{_BOARD}\s+{_DELIVERY}\s+{_SHA256}$"
)
_REJECT_RE = re.compile(
    rf"^@agent\s+拒绝\s+{_BOARD}\s+{_DELIVERY}\s+{_SHA256}\s+{_REASON}$"
)


def parse_group_collaboration_command(
    text: str,
) -> GroupCollaborationCommand | None:
    """只接受完整匹配；普通 @agent 对话继续走既有单 Agent 路径。"""

    normalized = str(text or "").strip()
    match = _INVITE_RE.fullmatch(normalized)
    if match is not None:
        return GroupCollaborationCommand(
            GroupCollaborationCommandKind.INVITE,
            board_id=match.group("board"),
            task_id=match.group("task"),
            client_id=match.group("client"),
        )
    match = _STATUS_RE.fullmatch(normalized)
    if match is not None:
        return GroupCollaborationCommand(
            GroupCollaborationCommandKind.STATUS,
            board_id=match.group("board"),
        )
    match = _APPROVE_RE.fullmatch(normalized)
    if match is not None:
        return GroupCollaborationCommand(
            GroupCollaborationCommandKind.APPROVE,
            board_id=match.group("board"),
            delivery_id=match.group("delivery"),
            delivery_sha256=match.group("sha"),
        )
    match = _REJECT_RE.fullmatch(normalized)
    if match is not None:
        return GroupCollaborationCommand(
            GroupCollaborationCommandKind.REJECT,
            board_id=match.group("board"),
            delivery_id=match.group("delivery"),
            delivery_sha256=match.group("sha"),
            reason_code=match.group("reason"),
        )
    return None


__all__ = [
    "GroupCollaborationCommand",
    "GroupCollaborationCommandKind",
    "parse_group_collaboration_command",
]
