"""跨领域 Registry Kernel 的稳定公开接口。"""

from core.registry.builder import RegistryBuilder, RegistryGeneration
from core.registry.contracts import RegistryDescriptor
from core.registry.snapshot import RegistrySnapshot
from core.registry.validation import (
    RegistryConflictError,
    RegistryDependencyError,
    RegistryError,
    RegistryFrozenError,
    RegistryPublishConflictError,
    RegistryValidationError,
)

__all__ = [
    "RegistryBuilder",
    "RegistryConflictError",
    "RegistryDependencyError",
    "RegistryDescriptor",
    "RegistryError",
    "RegistryFrozenError",
    "RegistryGeneration",
    "RegistryPublishConflictError",
    "RegistrySnapshot",
    "RegistryValidationError",
]
