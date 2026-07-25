"""群分析数据结构——应用层纯逻辑只依赖这些类型。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class GroupRef:
    group_id: str           # "group_123456"
    legacy_group_id: str    # "123456"
    name: str


@dataclass
class RawChatLog:
    id: int
    role: str
    user_id: str
    sender_name: str
    content: str
    created_at: datetime | None = None
    created_at_ts: float | None = None
    message_id: str = ""
    source_message_ids_json: str = ""
    session_id: str = ""
    meta_json: str = "{}"


@dataclass
class GroupLogBatch:
    group: GroupRef
    logs: list[RawChatLog]
    latest_log_id: int | None = None
    raw_count: int = 0
