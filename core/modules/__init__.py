"""模块化单体的稳定公开合同。"""

from core.modules.composition import (
    CompositionError,
    CompositionRoot,
    CompositionSnapshot,
    CompositionStartError,
    CompositionStopError,
    CompositionValidationError,
    ModuleContributionDescriptor,
)
from core.modules.contracts import (
    ApplicationModule,
    CompositionState,
    ModuleContributionRef,
    ModuleHealth,
    ModuleHealthCheck,
    ModuleManifest,
    ModuleRegistrationPort,
    ModuleRuntimeContext,
)


__all__ = [
    "ApplicationModule",
    "CompositionError",
    "CompositionRoot",
    "CompositionSnapshot",
    "CompositionStartError",
    "CompositionState",
    "CompositionStopError",
    "CompositionValidationError",
    "ModuleContributionDescriptor",
    "ModuleContributionRef",
    "ModuleHealth",
    "ModuleHealthCheck",
    "ModuleManifest",
    "ModuleRegistrationPort",
    "ModuleRuntimeContext",
]
