"""Registry 的隔离构建、冻结和原子 generation 发布。"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from threading import RLock
from typing import Generic, Self

from core.registry.contracts import RegistryDescriptorT
from core.registry.snapshot import RegistrySnapshot
from core.registry.validation import (
    RegistryConflictError,
    RegistryFrozenError,
    RegistryPublishConflictError,
    RegistryValidationError,
    canonical_json,
    resolve_dependency_order,
    validate_descriptor,
    validate_identifier,
)


class RegistryBuilder(Generic[RegistryDescriptorT]):
    """只在构建期可写；成功冻结后不再接受注册。"""

    def __init__(self, namespace: str) -> None:
        self._namespace = validate_identifier(
            namespace,
            field_name="registry.namespace",
        )
        self._descriptors: dict[str, RegistryDescriptorT] = {}
        self._dependencies: dict[str, tuple[str, ...]] = {}
        self._payloads: dict[str, object] = {}
        self._snapshot: RegistrySnapshot[RegistryDescriptorT] | None = None

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def frozen(self) -> bool:
        return self._snapshot is not None

    def register(self, descriptor: RegistryDescriptorT) -> Self:
        if self._snapshot is not None:
            raise RegistryFrozenError(
                f"Registry {self._namespace} 已冻结"
            )
        descriptor_id, dependencies, payload = validate_descriptor(
            descriptor,
            expected_namespace=self._namespace,
        )
        if descriptor_id in self._descriptors:
            raise RegistryConflictError(
                f"Registry {self._namespace} 重复注册 ID: {descriptor_id}"
            )
        self._descriptors[descriptor_id] = descriptor
        self._dependencies[descriptor_id] = dependencies
        self._payloads[descriptor_id] = payload
        return self

    def freeze(
        self,
        *,
        generation: int = 1,
    ) -> RegistrySnapshot[RegistryDescriptorT]:
        if self._snapshot is not None:
            return self._snapshot
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation <= 0
        ):
            raise RegistryValidationError(
                "registry generation 必须是正整数"
            )

        ordered_ids = resolve_dependency_order(self._dependencies)
        content = {
            "schema_version": 1,
            "namespace": self._namespace,
            "descriptors": [
                {
                    "id": descriptor_id,
                    "dependencies": sorted(
                        self._dependencies[descriptor_id]
                    ),
                    "payload": self._payloads[descriptor_id],
                }
                for descriptor_id in sorted(self._descriptors)
            ],
        }
        encoded = canonical_json(content)
        snapshot = RegistrySnapshot(
            namespace=self._namespace,
            generation=generation,
            sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            canonical_json=encoded,
            ordered_ids=ordered_ids,
            _items=self._descriptors,
        )
        self._snapshot = snapshot
        return snapshot


RegistryBuild = Callable[
    [RegistryBuilder[RegistryDescriptorT]],
    object,
]


class RegistryGeneration(Generic[RegistryDescriptorT]):
    """完整构建候选后，以比较并交换方式发布新快照。"""

    def __init__(self, namespace: str) -> None:
        self._namespace = validate_identifier(
            namespace,
            field_name="registry.namespace",
        )
        self._current: RegistrySnapshot[RegistryDescriptorT] | None = None
        self._lock = RLock()

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def current(self) -> RegistrySnapshot[RegistryDescriptorT] | None:
        with self._lock:
            return self._current

    def rebuild(
        self,
        configure: RegistryBuild[RegistryDescriptorT],
    ) -> RegistrySnapshot[RegistryDescriptorT]:
        if not callable(configure):
            raise RegistryValidationError(
                "Registry generation configure 必须可调用"
            )

        with self._lock:
            previous = self._current
            next_generation = (
                1 if previous is None else previous.generation + 1
            )

        builder = RegistryBuilder[RegistryDescriptorT](self._namespace)
        configure(builder)
        candidate = builder.freeze(generation=next_generation)

        with self._lock:
            if self._current is not previous:
                raise RegistryPublishConflictError(
                    f"Registry {self._namespace} 发布期间 generation 已变化"
                )
            self._current = candidate
        return candidate
