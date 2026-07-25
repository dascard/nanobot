"""现有领域任务状态机到 Durable Job 租约合同的显式 Adapter Registry。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from core.jobs.contracts import JobLease
from core.jobs.registry import JOB_DESCRIPTOR_REGISTRY
from core.registry import RegistryBuilder, RegistrySnapshot


@runtime_checkable
class JobLeaseAdapterPort(Protocol):
    """只投影租约身份，不复制或接管领域任务状态。"""

    job_type: str

    def project_lease(self, source: object) -> JobLease: ...


@dataclass(frozen=True, slots=True)
class JobLeaseAdapterDescriptor:
    job_type: str
    owner_module: str
    source_type_name: str
    projector_id: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        descriptor = JOB_DESCRIPTOR_REGISTRY.require(self.job_type)
        if descriptor.owner_module != self.owner_module:
            raise ValueError(
                f"Job Adapter owner 漂移：{self.job_type}"
            )
        if not str(self.source_type_name or "").strip():
            raise ValueError("source_type_name 不能为空")
        if not str(self.projector_id or "").strip():
            raise ValueError("projector_id 不能为空")

    @property
    def registry_namespace(self) -> str:
        return "job_lease_adapter"

    @property
    def registry_id(self) -> str:
        return self.job_type

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return self.depends_on

    def registry_payload(self) -> dict[str, object]:
        return {
            "owner_module": self.owner_module,
            "source_type": self.source_type_name,
            "projector_id": self.projector_id,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class JobLeaseAdapterBinding:
    descriptor: JobLeaseAdapterDescriptor
    source_type: type
    _projector: Callable[[object], JobLease] = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, type):
            raise TypeError("source_type 必须是类型")
        expected = (
            f"{self.source_type.__module__}."
            f"{self.source_type.__qualname__}"
        )
        if expected != self.descriptor.source_type_name:
            raise ValueError(
                f"Job Adapter source type 漂移："
                f"{self.descriptor.job_type}"
            )
        if not callable(self._projector):
            raise TypeError("_projector 必须可调用")

    @property
    def job_type(self) -> str:
        return self.descriptor.job_type

    def project_lease(self, source: object) -> JobLease:
        if not isinstance(source, self.source_type):
            raise TypeError(
                f"{self.job_type} 租约来源类型无效"
            )
        lease = self._projector(source)
        if not isinstance(lease, JobLease):
            raise TypeError(
                f"{self.job_type} Adapter 必须返回 JobLease"
            )
        return lease


class JobLeaseAdapterRegistry:
    def __init__(
        self,
        bindings: tuple[JobLeaseAdapterBinding, ...],
    ) -> None:
        builder = RegistryBuilder[JobLeaseAdapterDescriptor](
            "job_lease_adapter"
        )
        runtime_bindings: dict[str, JobLeaseAdapterBinding] = {}
        for binding in bindings:
            builder.register(binding.descriptor)
            if binding.job_type in runtime_bindings:
                raise ValueError(
                    f"重复 Job Lease Adapter：{binding.job_type}"
                )
            runtime_bindings[binding.job_type] = binding
        self._snapshot = builder.freeze()
        self._bindings = runtime_bindings

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[JobLeaseAdapterDescriptor]:
        return self._snapshot

    def require(self, job_type: str) -> JobLeaseAdapterBinding:
        normalized = str(job_type or "").strip()
        try:
            self._snapshot.require(normalized)
            return self._bindings[normalized]
        except KeyError as exc:
            raise ValueError(
                f"未绑定的 Job Lease Adapter：{job_type or '<empty>'}"
            ) from exc

    def job_types(self) -> tuple[str, ...]:
        return tuple(self._snapshot.ordered_ids)


__all__ = [
    "JobLeaseAdapterBinding",
    "JobLeaseAdapterDescriptor",
    "JobLeaseAdapterPort",
    "JobLeaseAdapterRegistry",
]
