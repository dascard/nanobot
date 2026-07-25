"""跨领域 Registry 的最小描述符合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeVar


class RegistryDescriptor(Protocol):
    """领域描述符接入 Registry Kernel 时必须暴露的稳定字段。"""

    @property
    def registry_namespace(self) -> str: ...

    @property
    def registry_id(self) -> str: ...

    @property
    def registry_dependencies(self) -> tuple[str, ...]: ...

    def registry_payload(self) -> Mapping[str, object]:
        """返回参与内容 Hash 的无副作用、可 JSON 序列化元数据。"""


RegistryDescriptorT = TypeVar(
    "RegistryDescriptorT",
    bound=RegistryDescriptor,
)
