"""模型供应商 Port、描述符与不可变注册表。"""

from core.model_provider.contracts import (
    AsyncModelCompletionPort,
    ModelProviderPort,
    ModelProviderRequest,
    ModelProviderResponse,
    ProviderAvailability,
    ProviderCapability,
    ProviderDescriptor,
    SyncModelCompletionPort,
)
from core.model_provider.chat_runtime import (
    ChatCompletionPort,
    ChatCompletionRequest,
    ChatCompletionRuntime,
    ChatCompletionRuntimeState,
)
from core.model_provider.catalog_runtime import (
    ModelCatalogRuntime,
    ModelCatalogRuntimeState,
    ModelCatalogWriterPort,
)
from core.model_provider.decision_runtime import (
    DecisionModelPort,
    DecisionModelRuntime,
    DecisionModelRuntimeState,
)
from core.model_provider.registry import (
    DuplicateProviderError,
    ModelProviderRegistry,
    OverridePolicy,
    ProviderCapabilityError,
    ProviderNotFoundError,
    ProviderRegistryFrozenError,
    ProviderUnavailableError,
)

__all__ = [
    "ChatCompletionPort",
    "ChatCompletionRequest",
    "ChatCompletionRuntime",
    "ChatCompletionRuntimeState",
    "DecisionModelPort",
    "DecisionModelRuntime",
    "DecisionModelRuntimeState",
    "ModelCatalogRuntime",
    "ModelCatalogRuntimeState",
    "ModelCatalogWriterPort",
    "DuplicateProviderError",
    "AsyncModelCompletionPort",
    "ModelProviderPort",
    "ModelProviderRegistry",
    "ModelProviderRequest",
    "ModelProviderResponse",
    "OverridePolicy",
    "ProviderAvailability",
    "ProviderCapability",
    "ProviderCapabilityError",
    "ProviderDescriptor",
    "ProviderNotFoundError",
    "ProviderRegistryFrozenError",
    "ProviderUnavailableError",
    "SyncModelCompletionPort",
]
