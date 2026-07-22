"""现有模型注册表到核心 ModelCatalogWriterPort 的 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from clients.model_registry import registry


class RegistryModelCatalogAdapter:
    @property
    def adapter_id(self) -> str:
        return "new_api_model_catalog"

    def upsert_models(self, models: tuple[Mapping[str, Any], ...]) -> int:
        count = 0
        for model in models:
            model_data = dict(model)
            if not model_data.get("id"):
                continue
            registry.add_or_update_model(model_data)
            count += 1
        return count


__all__ = ["RegistryModelCatalogAdapter"]
