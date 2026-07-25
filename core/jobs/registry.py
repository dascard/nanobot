"""内建 Durable Job 类型的代码所有 Registry。"""

from __future__ import annotations

from dataclasses import dataclass

from core.jobs.contracts import JobLifecycle, JobRepositoryMode
from core.registry import RegistryBuilder, RegistrySnapshot


@dataclass(frozen=True, slots=True)
class JobDescriptor:
    job_type: str
    version: str
    owner_module: str
    domain: str
    handler_id: str
    schedule_policy_id: str
    retry_policy_id: str
    repository_mode: JobRepositoryMode
    lifecycle: JobLifecycle
    external_side_effects: bool

    @property
    def registry_namespace(self) -> str:
        return "durable_job"

    @property
    def registry_id(self) -> str:
        return self.job_type

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "owner_module": self.owner_module,
            "domain": self.domain,
            "handler_id": self.handler_id,
            "schedule_policy_id": self.schedule_policy_id,
            "retry_policy_id": self.retry_policy_id,
            "repository_mode": self.repository_mode.value,
            "lifecycle": self.lifecycle.value,
            "external_side_effects": self.external_side_effects,
        }


class JobDescriptorRegistry:
    def __init__(self, descriptors: tuple[JobDescriptor, ...]) -> None:
        builder = RegistryBuilder[JobDescriptor]("durable_job")
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(self) -> RegistrySnapshot[JobDescriptor]:
        return self._snapshot

    def require(self, job_type: str) -> JobDescriptor:
        try:
            return self._snapshot.require(str(job_type or "").strip())
        except KeyError as exc:
            raise ValueError(
                f"未登记的 Durable Job：{job_type or '<empty>'}"
            ) from exc

    def descriptors(self) -> tuple[JobDescriptor, ...]:
        return tuple(self._snapshot)


def _descriptor(
    job_type: str,
    *,
    owner: str,
    domain: str,
    schedule: str,
    retry: str,
    lifecycle: JobLifecycle = JobLifecycle.ACTIVE,
    repository_mode: JobRepositoryMode = JobRepositoryMode.KERNEL,
    external_side_effects: bool = False,
) -> JobDescriptor:
    return JobDescriptor(
        job_type=job_type,
        version="1.0.0",
        owner_module=owner,
        domain=domain,
        handler_id=f"{job_type}.handler",
        schedule_policy_id=schedule,
        retry_policy_id=retry,
        repository_mode=repository_mode,
        lifecycle=lifecycle,
        external_side_effects=external_side_effects,
    )


JOB_DESCRIPTOR_REGISTRY = JobDescriptorRegistry((
    _descriptor(
        "group_memory_learning",
        owner="app.group_learning",
        domain="group_memory_learning",
        schedule="background.long.v1",
        retry="group_memory_learning.v1",
        repository_mode=JobRepositoryMode.PORT_ADAPTER,
    ),
    _descriptor(
        "memory_digest",
        owner="app.memory_digest",
        domain="memory_digest",
        schedule="background.long.v1",
        retry="memory_digest.v1",
    ),
    _descriptor(
        "outbound_delivery",
        owner="core.outbound",
        domain="delivery",
        schedule="outbound.adapter.v1",
        retry="outbound_delivery.v1",
        repository_mode=JobRepositoryMode.PORT_ADAPTER,
        external_side_effects=True,
    ),
    _descriptor(
        "sandbox_admin_operation",
        owner="core.sandbox",
        domain="sandbox",
        schedule="background.standard.v1",
        retry="sandbox_admin_operation.v1",
        external_side_effects=True,
    ),
    _descriptor(
        "semantic_index",
        owner="core.semantic",
        domain="semantic_index",
        schedule="background.long.v1",
        retry="semantic_index.v1",
    ),
    _descriptor(
        "session_summary",
        owner="app.session_memory",
        domain="session_memory",
        schedule="background.long.v1",
        retry="session_summary.v1",
    ),
))


__all__ = [
    "JOB_DESCRIPTOR_REGISTRY",
    "JobDescriptor",
    "JobDescriptorRegistry",
]
