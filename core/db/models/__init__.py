"""按子域组织的 ORM 模型；`core.database` 仅保留兼容 re-export。"""

from core.db.models.chat import ChatLog, ConversationTurn, SensitiveData, User
from core.db.models.inbound import ChatDeliveryOutbox, InboundMessageClaim
from core.db.models.outbound import (
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
)
from core.db.models.persona import (
    Persona,
    PersonaBehavior,
    PersonaFact,
    SystemPrompt,
)
from core.db.models.proactive import ProactiveOutreachLease, ProactiveOutreachLog
from core.db.models.session_memory import (
    MemoryDigest,
    MemoryDigestJob,
    RollingSessionSummary,
    SessionSummaryJob,
)
from core.db.models.scheduling import ScheduledTask
from core.db.models.sandbox import (
    Asset,
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxProjectSequence,
    SandboxRun,
    Workspace,
    WorkspaceAsset,
    WorkspaceQuotaBinding,
)

__all__ = [
    "Asset",
    "ChatLog",
    "ChatDeliveryOutbox",
    "ConversationTurn",
    "InboundMessageClaim",
    "MemoryDigest",
    "MemoryDigestJob",
    "OutboundDeliveryAttempt",
    "OutboundDeliveryCircuit",
    "OutboundDeliveryControl",
    "OutboundDeliveryOutbox",
    "OutboundGenerationAttempt",
    "OutboundRun",
    "Persona",
    "PersonaBehavior",
    "PersonaFact",
    "ProactiveOutreachLease",
    "ProactiveOutreachLog",
    "SensitiveData",
    "RollingSessionSummary",
    "SessionSummaryJob",
    "ScheduledTask",
    "SandboxAccessGrant",
    "SandboxAdminOperation",
    "SandboxProjectSequence",
    "SandboxRun",
    "SystemPrompt",
    "User",
    "Workspace",
    "WorkspaceAsset",
    "WorkspaceQuotaBinding",
]
