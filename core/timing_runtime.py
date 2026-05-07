"""TimingGate 状态机——已迁移至 core/group_runtime/runtime.py，此处保留向后兼容重导出。"""
from core.group_runtime.runtime import (  # noqa: F401
    MIN_INTERVAL, MAX_PENDING, MAX_WAIT_SEC, MAX_RETRIES, MAX_AGE_SEC,
    IDLE_CLEANUP_SEC, BOT_REPLY_COOLDOWN_SEC, _DIRECT_TRIGGERS,
    GroupPendingMessage as PendingMessage,
    GroupChatState as GateState,
    GroupRuntime,
    _pending_payload,
    get_group_runtime,
)
