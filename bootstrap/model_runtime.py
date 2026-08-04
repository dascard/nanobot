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
from core.model_provider.admin_runtime import (
    start_model_provider_admin_runtime,
    stop_model_provider_admin_runtime,
)
from core.model_provider.catalog_runtime import (
    start_model_catalog_runtime,
    stop_model_catalog_runtime,
)
from core.model_provider.decision_runtime import (
    start_decision_model_runtime,
    stop_decision_model_runtime,
)
from core.model_provider.credential_runtime import (
    start_provider_credential_status_runtime,
    stop_provider_credential_status_runtime,
)
from core.model_provider.preset_runtime import (
    start_model_preset_resolver_runtime,
    stop_model_preset_resolver_runtime,
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


class OptionalCodexCredentialStatusAdapter:
    """Codex 可选依赖未安装时返回明确的未配置状态。"""

    def resolve(self, driver_type: str) -> tuple[bool, str]:
        if str(driver_type or "") != "codex":
            return False, "none"
        try:
            from nanobot_kt.codex_oauth_adapter import codex_status
        except ModuleNotFoundError as exc:
            if not str(exc.name or "").startswith("kohakuterrarium"):
                raise
            return False, "none"
        status = codex_status()
        configured = bool(status.get("authenticated"))
        return configured, "kt_oauth" if configured else "none"


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

    from core.model_provider.variation_resolver import (
        ModelPresetVariationResolver,
    )
    from nanobot_kt.model_provider_admin_adapter import (
        KtModelProviderAdminAdapter,
    )
    from nanobot_kt.codex_admin_adapter import KtCodexAdminAdapter
    from bootstrap.media_tool_runtime import bind_media_tool_runtime
    from bootstrap.news_search_runtime import bind_news_search_runtime

    validate_model_route_task_contracts()
    try:
        bind_media_tool_runtime()
        bind_news_search_runtime()
        start_model_preset_resolver_runtime(ModelPresetVariationResolver())
        admin_adapter = KtModelProviderAdminAdapter()
        codex_admin_adapter = KtCodexAdminAdapter()
        start_model_provider_admin_runtime(
            native_tools=admin_adapter,
            connectivity=admin_adapter,
            codex_admin=codex_admin_adapter,
        )
        start_provider_credential_status_runtime(
            OptionalCodexCredentialStatusAdapter()
        )
        route_adapter = ClassifierRouteModelAdapter()
        task_adapter = RouteTaskModelAdapter()
        chat_adapter = NewAPIChatCompletionAdapter(NewAPIClient(
            api_key=NEW_API_KEY,
            base_url=NEW_API_BASE_URL,
        ))
        decision_adapter = ClassifierDecisionModelAdapter()
        catalog_adapter = RegistryModelCatalogAdapter()
        start_route_model_runtime(route_adapter)
        start_task_runtime(task_adapter)
        start_chat_completion_runtime(chat_adapter)
        start_decision_model_runtime(decision_adapter)
        start_model_catalog_runtime(catalog_adapter)
    except Exception:
        from bootstrap.media_tool_runtime import clear_media_tool_runtime
        from bootstrap.news_search_runtime import clear_news_search_runtime

        stop_model_catalog_runtime()
        stop_decision_model_runtime()
        stop_chat_completion_runtime()
        stop_task_runtime()
        stop_route_model_runtime()
        stop_provider_credential_status_runtime()
        stop_model_provider_admin_runtime()
        stop_model_preset_resolver_runtime()
        clear_news_search_runtime()
        clear_media_tool_runtime()
        raise
    _route_adapter = route_adapter
    _task_adapter = task_adapter
    _chat_adapter = chat_adapter
    _decision_adapter = decision_adapter
    _catalog_adapter = catalog_adapter


def stop_model_runtime() -> None:
    global _catalog_adapter, _chat_adapter, _decision_adapter, _route_adapter
    global _task_adapter
    from bootstrap.media_tool_runtime import clear_media_tool_runtime
    from bootstrap.news_search_runtime import clear_news_search_runtime

    stop_model_catalog_runtime()
    stop_decision_model_runtime()
    stop_chat_completion_runtime()
    stop_task_runtime()
    stop_route_model_runtime()
    stop_provider_credential_status_runtime()
    stop_model_provider_admin_runtime()
    stop_model_preset_resolver_runtime()
    clear_news_search_runtime()
    clear_media_tool_runtime()
    _chat_adapter = None
    _decision_adapter = None
    _catalog_adapter = None
    _route_adapter = None
    _task_adapter = None


__all__ = ["start_model_runtime", "stop_model_runtime"]
