"""模型 Provider Port 的应用组合根。"""

from __future__ import annotations

from clients.chat_completion_adapter import NewAPIChatCompletionAdapter
from clients.decision_model_adapter import ClassifierDecisionModelAdapter
from clients.model_catalog_adapter import RegistryModelCatalogAdapter
from clients.new_api_client import NewAPIClient
from clients.route_model_adapter import ClassifierRouteModelAdapter
from clients.task_runtime_adapter import RouteTaskModelAdapter
from config import NEW_API_BASE_URL, NEW_API_KEY
from core.model_provider.chat_runtime import (
    start_chat_completion_runtime,
    stop_chat_completion_runtime,
)
from core.model_provider.catalog_runtime import (
    start_model_catalog_runtime,
    stop_model_catalog_runtime,
)
from core.model_provider.decision_runtime import (
    start_decision_model_runtime,
    stop_decision_model_runtime,
)
from core.model_provider.route_runtime import (
    start_route_model_runtime,
    stop_route_model_runtime,
)
from core.model_provider.route_registry import (
    validate_model_route_task_contracts,
)
from core.task_runtime import start_task_runtime, stop_task_runtime


_route_adapter: ClassifierRouteModelAdapter | None = None
_chat_adapter: NewAPIChatCompletionAdapter | None = None
_decision_adapter: ClassifierDecisionModelAdapter | None = None
_catalog_adapter: RegistryModelCatalogAdapter | None = None
_task_adapter: RouteTaskModelAdapter | None = None


def start_model_runtime() -> None:
    global _catalog_adapter, _chat_adapter, _decision_adapter, _route_adapter
    global _task_adapter
    adapters = (
        _route_adapter,
        _task_adapter,
        _chat_adapter,
        _decision_adapter,
        _catalog_adapter,
    )
    if all(adapter is not None for adapter in adapters):
        return
    if any(adapter is not None for adapter in adapters):
        raise RuntimeError("模型运行时处于不一致的部分启动状态")

    validate_model_route_task_contracts()
    route_adapter = ClassifierRouteModelAdapter()
    task_adapter = RouteTaskModelAdapter()
    chat_adapter = NewAPIChatCompletionAdapter(NewAPIClient(
        api_key=NEW_API_KEY,
        base_url=NEW_API_BASE_URL,
    ))
    decision_adapter = ClassifierDecisionModelAdapter()
    catalog_adapter = RegistryModelCatalogAdapter()
    start_route_model_runtime(route_adapter)
    try:
        start_task_runtime(task_adapter)
        start_chat_completion_runtime(chat_adapter)
        start_decision_model_runtime(decision_adapter)
        start_model_catalog_runtime(catalog_adapter)
    except Exception:
        stop_model_catalog_runtime()
        stop_decision_model_runtime()
        stop_chat_completion_runtime()
        stop_task_runtime()
        stop_route_model_runtime()
        raise
    _route_adapter = route_adapter
    _task_adapter = task_adapter
    _chat_adapter = chat_adapter
    _decision_adapter = decision_adapter
    _catalog_adapter = catalog_adapter


def stop_model_runtime() -> None:
    global _catalog_adapter, _chat_adapter, _decision_adapter, _route_adapter
    global _task_adapter
    stop_model_catalog_runtime()
    stop_decision_model_runtime()
    stop_chat_completion_runtime()
    stop_task_runtime()
    stop_route_model_runtime()
    _chat_adapter = None
    _decision_adapter = None
    _catalog_adapter = None
    _route_adapter = None
    _task_adapter = None


__all__ = ["start_model_runtime", "stop_model_runtime"]
