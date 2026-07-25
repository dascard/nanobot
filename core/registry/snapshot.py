"""不可变 Registry 快照。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Generic

from core.registry.contracts import RegistryDescriptorT


@dataclass(frozen=True, slots=True)
class RegistrySnapshot(Generic[RegistryDescriptorT]):
    """一次完整构建产生的只读、可寻址 Registry 内容。"""

    namespace: str
    generation: int
    sha256: str
    canonical_json: str
    ordered_ids: tuple[str, ...]
    _items: Mapping[str, RegistryDescriptorT] = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_items",
            MappingProxyType(dict(self._items)),
        )

    @property
    def items(self) -> Mapping[str, RegistryDescriptorT]:
        return self._items

    def __len__(self) -> int:
        return len(self.ordered_ids)

    def __iter__(self) -> Iterator[RegistryDescriptorT]:
        for descriptor_id in self.ordered_ids:
            yield self._items[descriptor_id]

    def get(
        self,
        descriptor_id: str,
    ) -> RegistryDescriptorT | None:
        return self._items.get(descriptor_id)

    def require(self, descriptor_id: str) -> RegistryDescriptorT:
        return self._items[descriptor_id]
