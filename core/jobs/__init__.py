"""Durable Job Kernel 的稳定公开接口。"""

from core.jobs.contracts import (
    JobClaim,
    JobCorrelation,
    JobExecutionContext,
    JobFailure,
    JobHandler,
    JobLease,
    JobLeaseLost,
    JobLifecycle,
    JobLifecycleError,
    JobOutcome,
    JobRecord,
    JobRepositoryMode,
    JobRepositoryPort,
    JobResult,
    JobStatus,
)
from core.jobs.kernel import DurableJobKernel
from core.jobs.adapters import (
    JobLeaseAdapterBinding,
    JobLeaseAdapterDescriptor,
    JobLeaseAdapterPort,
    JobLeaseAdapterRegistry,
)
from core.jobs.policies import (
    JobRetryPolicy,
    JobSchedulePolicy,
    job_retry_policy_snapshot,
    job_schedule_policy_snapshot,
    require_job_retry_policy,
    require_job_schedule_policy,
)
from core.jobs.registry import (
    JOB_DESCRIPTOR_REGISTRY,
    JobDescriptor,
    JobDescriptorRegistry,
)
from core.jobs.repository import InMemoryJobRepository

__all__ = [
    "DurableJobKernel",
    "InMemoryJobRepository",
    "JOB_DESCRIPTOR_REGISTRY",
    "JobClaim",
    "JobCorrelation",
    "JobDescriptor",
    "JobDescriptorRegistry",
    "JobExecutionContext",
    "JobFailure",
    "JobHandler",
    "JobLease",
    "JobLeaseAdapterBinding",
    "JobLeaseAdapterDescriptor",
    "JobLeaseAdapterPort",
    "JobLeaseAdapterRegistry",
    "JobLeaseLost",
    "JobLifecycle",
    "JobLifecycleError",
    "JobOutcome",
    "JobRecord",
    "JobRepositoryMode",
    "JobRepositoryPort",
    "JobResult",
    "JobRetryPolicy",
    "JobSchedulePolicy",
    "JobStatus",
    "job_retry_policy_snapshot",
    "job_schedule_policy_snapshot",
    "require_job_retry_policy",
    "require_job_schedule_policy",
]
