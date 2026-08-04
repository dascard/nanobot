"""Agent Runtime 稳定错误边界。"""

from __future__ import annotations


class AgentRuntimeError(RuntimeError):
    """Agent Runtime 端口的基础错误。"""

    code = "agent_runtime_error"

    def __init__(self, message: str, *, runtime_id: str = "") -> None:
        super().__init__(message)
        self.runtime_id = str(runtime_id or "").strip()


class AgentRuntimeStateError(AgentRuntimeError):
    """当前生命周期状态不允许执行请求的操作。"""

    code = "agent_runtime_invalid_state"


class AgentRuntimeCapabilityError(AgentRuntimeError):
    """底层 Runtime 不具备端口要求的能力。"""

    code = "agent_runtime_capability_unavailable"


class AgentRuntimeAdapterError(AgentRuntimeError):
    """底层框架调用失败。"""

    code = "agent_runtime_adapter_error"


class AgentRuntimeExecutionError(AgentRuntimeAdapterError):
    """单轮 Agent 执行失败。"""

    code = "agent_runtime_execution_failed"


class AgentRuntimeAmbiguousError(AgentRuntimeExecutionError):
    """外部副作用或部分输出已经发生，结果不能安全确认或重放。"""

    code = "agent_runtime_ambiguous"


class AgentRuntimeRecoveryError(AgentRuntimeError):
    """Checkpoint 恢复前检或 lineage 操作失败。"""

    code = "agent_runtime_recovery_failed"


class AgentRuntimeBudgetExceededError(AgentRuntimeExecutionError):
    """统一预算已经耗尽或本次操作超出声明范围。"""

    code = "agent_runtime_budget_exceeded"


class AgentRuntimePermissionError(AgentRuntimeExecutionError):
    """统一 PermissionPort 拒绝或仍要求审批。"""

    code = "agent_runtime_permission_denied"
