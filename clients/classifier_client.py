"""
Private chat classifier guardrail — 4-layer defense.

L1: 模型注入检测 (prompt-injection-sentinel, transformers pipeline)
L2: Qwen model call (llama.cpp server)
L3: Output validation (strict format)
L4: Timeout fallback
"""

import asyncio
import hashlib
import logging
import os
import re
import urllib.error
import urllib.request
from config import CLASSIFIER_API_URL
from clients.provider_adapter import adapter_from_route, registry_from_provider_configs
from core.async_bridge import run_awaitable_sync
from core.model_provider import (
    ModelProviderRegistry,
    ModelProviderRequest,
    ModelProviderResponse,
    SyncModelCompletionPort,
)
from core.model_provider.response_normalization import strip_think_blocks
from core.model_provider.route_registry import (
    list_model_route_descriptors,
    model_route_registry_snapshot,
    require_model_route_descriptor,
)
from foundation.llm.model_options import normalize_enable_thinking

logger = logging.getLogger("nanobot.classifier")


_MISSING = object()


ModelRouteResponse = ModelProviderResponse


class ModelRouteProviderUnavailableError(RuntimeError):
    """配置明确禁用 route provider；供上层按类型处理。"""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = str(provider_id or "").strip()
        super().__init__(
            "模型路由 Provider 已禁用"
            f"（provider disabled: {self.provider_id}）"
        )


class ModelRouteProviderUnsupportedError(RuntimeError):
    """Provider 驱动尚未接入当前 Nanobot 业务 Route。"""

    def __init__(self, provider_id: str, driver_type: str) -> None:
        self.provider_id = str(provider_id or "").strip()
        self.driver_type = str(driver_type or "").strip()
        super().__init__(
            "模型路由 Provider 驱动尚未接入"
            f"（provider={self.provider_id}, driver={self.driver_type}）"
        )


def _get_db_setting_value(key: str) -> tuple[bool, str | None]:
    """读取单个 SystemSetting，兼容尚未进入 SETTING_DEFS 的旧/实验键。"""
    try:
        from core.database import SessionLocal, SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting.value).filter(SystemSetting.key == key).first()
            if row is None:
                return False, None
            return True, row[0]
        finally:
            db.close()
    except Exception:
        return False, None


def _get_setting_value(key: str, default=None):
    """读取设置；settings 不认识的实验键再从 DB 兜底读一次。"""
    from core.settings_service import settings

    value = settings.get(key, default)
    if value not in (None, ""):
        return value
    exists, db_value = _get_db_setting_value(key)
    if exists:
        return db_value
    return value


def _configured_model_for_route(route_key: str) -> str:
    """只通过 SettingSpec 目录解析路由模型及兼容回退。"""

    descriptor = require_model_route_descriptor(route_key)
    value = _get_setting_value(descriptor.model_setting_key, "")
    if not value:
        fallback_key = descriptor.model_fallback_setting_key
        if fallback_key is not None:
            value = _get_setting_value(fallback_key, "")
    return str(value or "").strip()


def _as_bool(value, default: bool = True) -> bool:
    """把 settings/DB/env 里的 bool-like 值统一解析为 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _setting_is_explicit(key: str, value: object = _MISSING) -> bool:
    """判断配置是否来自 DB/env/非默认值，而不是 SettingDef 默认值。"""
    from core.config_registry import SETTING_DEFS

    exists, db_value = _get_db_setting_value(key)
    if exists:
        return bool(str(db_value or "").strip())

    defn = SETTING_DEFS.get(key)
    if value is _MISSING:
        value = _get_setting_value(key)
    if value is None or not str(value).strip():
        return False
    if defn is None:
        return True
    if defn.env_name and os.environ.get(defn.env_name):
        return True
    return str(value).strip() != str(defn.default).strip()


def _classifier_timeout() -> float:
    from core.settings_service import settings
    return settings.get_float("classifier.timeout", 15.0)


def _resolve_classifier_route(route_key: str) -> dict:
    """按冻结 Descriptor 解析模型路由配置。

    返回 {provider, base_url, api_key, model, timeout, temperature, max_tokens}。
    继承、默认值、SettingSpec 和 fallback 均由 ModelRouteDescriptor 声明。
    """

    descriptor = require_model_route_descriptor(route_key)
    defaults = {
        "provider": "llama.cpp",
        "provider_id": descriptor.default_provider_id,
        "base_url": str(CLASSIFIER_API_URL or "http://172.17.0.1:9999/v1"),
        "api_key": "",
        "model": "",
        "timeout": descriptor.default_timeout_seconds,
        "temperature": descriptor.default_temperature,
        "max_tokens": descriptor.default_max_tokens,
        "enable_thinking": descriptor.default_enable_thinking,
    }

    base = (
        _resolve_classifier_route(descriptor.inherits_from)
        if descriptor.inherits_from is not None
        else dict(defaults)
    )

    prefix = descriptor.setting_prefix
    raw = _get_setting_value(prefix)

    if _setting_is_explicit(prefix, raw) and raw and isinstance(raw, str) and raw.strip():
        # 旧写法：直接写 base_url 字符串，覆盖继承值
        base["base_url"] = str(raw)

    route_provider = str(_get_setting_value(f"{prefix}.provider", "") or "").strip()
    route_base_url = str(_get_setting_value(f"{prefix}.base_url", "") or "").strip()
    route_api_key = str(_get_setting_value(f"{prefix}.api_key", "") or "").strip()

    # 字段级覆盖：只覆盖 route 自己显式设置了值的字段
    if route_provider:
        base["provider"] = route_provider
    if route_base_url and _setting_is_explicit(f"{prefix}.base_url", route_base_url):
        base["base_url"] = route_base_url
    if route_api_key:
        base["api_key"] = route_api_key
    v = _get_setting_value(f"{prefix}.model")
    if v:
        base["model"] = str(v)
    route_defaults = {
        "timeout": descriptor.default_timeout_seconds,
        "temperature": descriptor.default_temperature,
        "max_tokens": descriptor.default_max_tokens,
    }
    for k, route_default in route_defaults.items():
        v = _get_setting_value(f"{prefix}.{k}", route_default)
        if v is not None:
            base[k] = float(v) if k == "temperature" else (int(v) if k == "max_tokens" else float(v))

    if not base.get("model"):
        base["model"] = _configured_model_for_route(route_key)

    enable_thinking_key = f"{prefix}.enable_thinking"
    enable_thinking = _get_setting_value(enable_thinking_key, "")
    if not descriptor.inherit_thinking_when_unset:
        base["enable_thinking"] = normalize_enable_thinking(
            enable_thinking or descriptor.default_enable_thinking
        )
    elif _setting_is_explicit(enable_thinking_key, enable_thinking):
        base["enable_thinking"] = normalize_enable_thinking(enable_thinking)

    # 合并 provider 配置：route.provider → provider base_url/api_key
    inherited_provider_id = str(base.get("provider_id") or "").strip()
    provider_id = route_provider or inherited_provider_id

    # 检测 base_url 是显式配置还是继承/默认来的
    explicit_base_url = bool(
        (_setting_is_explicit(prefix, raw) and raw and isinstance(raw, str) and raw.strip())
        or (route_base_url and _setting_is_explicit(f"{prefix}.base_url", route_base_url))
    )

    if provider_id:
        # 即使 Provider 配置暂时不可见，也保留 Route 的显式引用，避免诊断和
        # 删除保护把它错误回退成父 Route 的 Provider。
        base["provider_id"] = provider_id
        provider = _get_provider_config(provider_id)
        if provider:
            if explicit_base_url:
                # 用户显式配置了 base_url → route 优先
                base["base_url"] = base.get("base_url") or provider.get("base_url", "")
                base["source"] = "route_override"
            else:
                # 无显式 base_url（继承/默认） → provider 优先
                base["base_url"] = provider.get("base_url") or base.get("base_url", "")
                base["source"] = "provider"
            # api_key: route 自己配置优先；否则使用当前 provider，不沿用继承 provider 的 key
            if route_api_key:
                base["api_key"] = route_api_key
                base["api_key_source"] = "route"
            elif route_provider:
                base["api_key"] = provider.get("api_key", "")
                base["api_key_source"] = "provider"
            else:
                base["api_key"] = base.get("api_key") or provider.get("api_key", "")
                base["api_key_source"] = "inherited"
            base["provider_enabled"] = _as_bool(provider.get("enabled", True), default=True)

    return base


def ensure_model_route_enabled(route_key: str, route: dict | None = None) -> dict:
    """实际调用前强制检查 provider.enabled。展示/目录解析不调用此函数。"""
    route = route or resolve_model_route(route_key)
    provider_id = str(route.get("provider_id") or "").strip()
    if provider_id and route.get("provider_enabled") is False:
        raise ModelRouteProviderUnavailableError(provider_id)
    if provider_id and route.get("route_completion_supported") is False:
        raise ModelRouteProviderUnsupportedError(
            provider_id,
            str(route.get("driver_type") or "unknown"),
        )
    return route


# Pattern for Qwen output validation: 是/否 + comma + number (optional negative)
OUTPUT_PATTERN = re.compile(r"^(是|否)[,，](-?\d+)$")

# Pattern to strip think/thought blocks from Qwen response
THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def _track_route_model_health(model: str, *, success: bool) -> None:
    """同步 Route 复用全局模型熔断器，不阻塞已有事件循环。"""

    try:
        from clients.new_api_client import NewAPIClient

        tracker = NewAPIClient.get_failure_tracker()
        operation = (
            tracker.record_success(model)
            if success
            else tracker.record_failure(model)
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run_awaitable_sync(operation)
        else:
            loop.create_task(operation)
    except Exception as exc:
        logger.warning(
            "[call_model_route] 更新模型健康状态失败 model=%s error=%s",
            model,
            type(exc).__name__,
        )


def _bound_route_completion_attempts(
    route_key: str,
    resolved_route: dict,
) -> list[dict]:
    """把有效 Binding 候选展开成同步 completion 的逐次调用配置。"""

    if not resolved_route.get("binding_candidates"):
        return []

    from clients.new_api_client import NewAPIClient
    from core.model_provider.preset_config import resolve_route_binding_candidates

    candidates = resolve_route_binding_candidates(route_key)
    tracker = NewAPIClient.get_failure_tracker()
    attempts: list[dict] = []
    unavailable: list[str] = []
    for candidate_index, (candidate, resolved) in enumerate(candidates):
        model = resolved.preset
        provider = _get_provider_config(model.provider_id)
        identity = candidate.identity or f"{model.provider_id}/{model.model}"
        if provider is None:
            unavailable.append(f"{identity}: Provider 不存在")
            continue
        if not model.enabled:
            unavailable.append(f"{identity}: 模型已禁用")
            continue
        if not provider.get("enabled", True):
            unavailable.append(f"{identity}: Provider 已禁用")
            continue
        if not provider.get("route_completion_supported", True):
            unavailable.append(f"{identity}: Provider 不支持同步 Route")
            continue
        if not provider.get("base_url"):
            unavailable.append(f"{identity}: Base URL 未配置")
            continue
        if tracker.sync_is_disabled(model.model):
            unavailable.append(f"{identity}: 熔断冷却中")
            continue
        attempts.append({
            **resolved_route,
            "profile_id": model.id,
            "provider_id": model.provider_id,
            "base_url": provider.get("base_url", ""),
            "api_key": provider.get("api_key", ""),
            "provider_enabled": True,
            "driver_type": provider.get("driver_type", "openai"),
            "route_completion_supported": True,
            "model": model.model,
            "max_context": model.max_context,
            "max_tokens": model.max_output,
            "temperature": model.temperature,
            "timeout": model.timeout,
            "enable_thinking": model.enable_thinking,
            "reasoning_effort": model.reasoning_effort,
            "service_tier": model.service_tier,
            "extra_headers": dict(model.extra_headers),
            "extra_body": dict(model.extra_body),
            "candidate_index": candidate_index,
        })
    if not attempts:
        detail = "；".join(unavailable) or "没有满足硬能力约束的候选"
        raise RuntimeError(f"Route {route_key} 没有可调用候选：{detail}")
    if unavailable:
        logger.warning(
            "[call_model_route] route=%s 跳过不可用候选：%s",
            route_key,
            "；".join(unavailable),
        )
    return attempts


def resolve_model_route_attempts(route_key: str) -> list[dict]:
    """展开 Route 的健康候选，供同一次语义任务按 attempt 顺序切换模型。"""

    route = resolve_model_route(route_key)
    attempts = _bound_route_completion_attempts(route_key, route)
    if attempts:
        return attempts
    return [ensure_model_route_enabled(route_key, route)]


def call_model_route_response(
    route_key: str = "timing_gate",
    messages: list[dict] | None = None,
    *,
    system_prompt: str = "",
    user_message: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> ModelRouteResponse:
    """调用分类器模型路由并保留停止原因、推理正文和 usage。

    从 settings.get(f"model.route.{route_key}") 读取完整路由配置
    （provider/base_url/api_key/model/timeout/temperature/max_tokens），
    支持 OpenAI-compatible API / New API / 本地 llama.cpp。
    调用 /chat/completions，返回 cleaned text。
    """
    descriptor = require_model_route_descriptor(route_key)
    route = resolve_model_route(route_key)
    binding_managed = bool(route.get("binding_candidates"))
    if not binding_managed:
        route = ensure_model_route_enabled(route_key, route)

    if not messages:
        fallback_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        messages = fallback_messages
        from core.prompt_v2.task_templates import render_task_messages

        prompt_key = descriptor.runtime_task_key
        if prompt_key:
            messages = render_task_messages(
                prompt_key,
                {
                    "message": user_message,
                    "system_prompt": system_prompt,
                    "pending_text": user_message,
                    "recent_context": "",
                    "bot_name": "",
                    "group_profile": "",
                },
                fallback_messages=fallback_messages,
            )

    attempts = _bound_route_completion_attempts(route_key, route) or [route]
    last_error: Exception | None = None
    for candidate_index, attempt_route in enumerate(attempts):
        target_model = str(attempt_route.get("model") or "")
        logger.info(
            "[call_model_route] route=%s candidate=%d/%d provider=%s model=%s",
            route_key,
            candidate_index + 1,
            len(attempts),
            attempt_route.get("provider_id", ""),
            target_model,
        )
        adapter = adapter_from_route(
            attempt_route,
            opener_factory=urllib.request.build_opener,
        )
        provider_registry = ModelProviderRegistry()
        provider_registry.register(adapter)
        provider_registry.freeze()
        provider = provider_registry.require(
            adapter.descriptor.id,
            capabilities=descriptor.required_provider_capabilities,
        )
        if not isinstance(provider, SyncModelCompletionPort):
            raise TypeError(
                f"Provider {adapter.descriptor.id} 未实现同步 completion Port"
            )
        try:
            response = provider.complete(
                ModelProviderRequest(
                    messages=tuple(messages),
                    model=target_model,
                    max_tokens=(
                        max_tokens
                        if max_tokens is not None
                        else int(attempt_route["max_tokens"])
                    ),
                    temperature=(
                        temperature
                        if temperature is not None
                        else float(attempt_route["temperature"])
                    ),
                    timeout_seconds=(
                        timeout or float(attempt_route.get("timeout", 15))
                    ),
                    enable_thinking=str(
                        attempt_route.get("enable_thinking") or "auto"
                    ),
                    reasoning_effort=str(
                        attempt_route.get("reasoning_effort") or ""
                    ),
                    service_tier=str(attempt_route.get("service_tier") or ""),
                    extra_headers=dict(attempt_route.get("extra_headers") or {}),
                    extra_body=dict(attempt_route.get("extra_body") or {}),
                    trace_source=descriptor.trace_source,
                    metadata={
                        "route_key": route_key,
                        "candidate_index": candidate_index,
                    },
                )
            )
        except urllib.error.HTTPError as exc:
            last_error = exc
            status = int(getattr(exc, "code", 0) or 0)
            if binding_managed and status not in {400, 413, 422, 401, 403}:
                _track_route_model_health(target_model, success=False)
            if status in {401, 403} or candidate_index + 1 >= len(attempts):
                raise
            logger.warning(
                "[call_model_route] route=%s model=%s status=%s，切换候选",
                route_key,
                target_model,
                status,
            )
            continue
        except Exception as exc:
            last_error = exc
            if binding_managed:
                _track_route_model_health(target_model, success=False)
            if candidate_index + 1 >= len(attempts):
                raise
            logger.warning(
                "[call_model_route] route=%s model=%s error=%s，切换候选",
                route_key,
                target_model,
                type(exc).__name__,
            )
            continue

        if binding_managed:
            _track_route_model_health(target_model, success=True)
        return ModelRouteResponse(
            content=response.content,
            reasoning_content=response.reasoning_content,
            finish_reason=response.finish_reason,
            usage=dict(response.usage),
            raw_response=dict(response.raw_response),
        )
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Route {route_key} 没有执行任何模型候选")


def call_model_route(
    route_key: str = "timing_gate",
    messages: list[dict] | None = None,
    *,
    system_prompt: str = "",
    user_message: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> str:
    """兼容旧调用方，只返回清洗后的正文字符串。"""

    return call_model_route_response(
        route_key=route_key,
        messages=messages,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    ).content


# ── 模型路由解析（provider + model）──

def _get_provider_config(provider_id: str) -> dict | None:
    """兼容入口：由 Provider 控制面返回包含凭据的内部配置。"""

    from core.model_provider.provider_config import get_provider_instance

    provider = get_provider_instance(provider_id)
    return provider.internal_view() if provider is not None else None


def provider_public(p: dict) -> dict:
    """脱敏返回：不暴露 api_key 明文。"""
    return {
        "id": p["id"],
        "display_name": p.get("display_name") or p["id"],
        "driver_type": p.get("driver_type") or "openai",
        "base_url": p.get("base_url", ""),
        "api_key_configured": bool(p.get("api_key")),
        "credential_configured": bool(
            p.get("credential_configured", p.get("api_key"))
        ),
        "credential_source": p.get("credential_source") or (
            "configured" if p.get("api_key") else "none"
        ),
        "credential_mode": p.get("credential_mode") or (
            "oauth" if p.get("driver_type") == "codex" else "api_key"
        ),
        "enabled": bool(p.get("enabled")),
        "builtin": bool(p.get("builtin")),
        "legacy_aliases": p.get("legacy_aliases", []),
        "registry_provider": p.get("registry_provider") or None,
        "model_discovery_enabled": bool(
            p.get("model_discovery_enabled", True)
        ),
        "kt_driver_available": p.get("driver_type", "openai")
        in {"openai", "anthropic", "codex"},
        "route_completion_supported": bool(
            p.get("route_completion_supported", p.get("driver_type", "openai") == "openai")
        ),
        "agent_runtime_supported": bool(
            p.get("agent_runtime_supported", p.get("driver_type", "openai") == "openai")
        ),
        "model_discovery_supported": bool(
            p.get("model_discovery_supported", p.get("driver_type", "openai") == "openai")
        ),
        "catalog": dict(p.get("catalog") or {}),
    }


def list_providers(db=None) -> list[dict]:
    """兼容入口：列出所有 canonical Provider 的内部配置。"""

    from core.model_provider.provider_config import list_provider_instances

    providers = [provider.internal_view() for provider in list_provider_instances(db)]
    # 同步业务 Route 仍只注册已经接入的 OpenAI-compatible Adapter。
    registry_from_provider_configs([
        provider
        for provider in providers
        if provider.get("route_completion_supported")
    ])
    return providers


def provider_registry_introspection() -> tuple[dict[str, object], ...]:
    """返回当前 Provider Registry 的无密钥、无 endpoint 状态快照。"""

    registry = registry_from_provider_configs([
        provider
        for provider in list_providers()
        if provider.get("route_completion_supported")
    ])
    return tuple(dict(item) for item in registry.introspect())


def resolve_model_route(route_key: str) -> dict:
    """三层模型路由解析：provider → model → route params。

    返回 {route_key, provider_id, base_url, api_key, api_key_configured,
          model, timeout, temperature, max_tokens, source, inherited_from,
          overridden_fields}
    """
    from core.settings_service import settings
    from core.model_provider.provider_config import canonical_provider_instance_id

    descriptor = require_model_route_descriptor(route_key)
    route_key = descriptor.route_key
    route = _resolve_classifier_route(route_key)

    # 确定 provider（使用 canonical 名）
    provider_id = canonical_provider_instance_id(
        str(route.get("provider_id") or settings.get(f"model.route.{route_key}.provider") or "")
    )
    if not provider_id:
        provider_id = descriptor.default_provider_id
    if not provider_id and descriptor.inherits_from is not None:
        provider_id = str(
            resolve_model_route(descriptor.inherits_from).get(
                "provider_id",
                "",
            )
        )

    provider = _get_provider_config(provider_id) or {
        "id": provider_id, "base_url": route.get("base_url", ""),
        "api_key": route.get("api_key", ""), "enabled": True,
    }

    # 确定 model
    model = route.get("model", "")
    if not model:
        model = _configured_model_for_route(route_key)

    route_registry_snapshot = model_route_registry_snapshot()
    result = {
        "route_key": route_key,
        "route_type": descriptor.route_type,
        "provider_id": provider_id,
        "base_url": route.get("base_url") or provider["base_url"],
        "api_key": route.get("api_key") or provider["api_key"],
        "api_key_configured": bool(route.get("api_key") or provider.get("api_key")),
        "route_api_key_configured": route.get("api_key_source") == "route",
        "provider_enabled": _as_bool(provider.get("enabled", True), default=True),
        "driver_type": provider.get("driver_type", "openai"),
        "route_completion_supported": bool(
            provider.get("route_completion_supported", True)
        ),
        "model": model or "未指定",
        "timeout": route.get("timeout", 15),
        "temperature": route.get("temperature", 0),
        "max_tokens": route.get("max_tokens", 30),
        "enable_thinking": normalize_enable_thinking(route.get("enable_thinking", "auto")),
        "source": route.get("source", "provider"),
        "route_registry_generation": route_registry_snapshot.generation,
        "route_registry_sha256": route_registry_snapshot.sha256,
    }

    # 新控制面：Route 优先绑定目录模型并叠加局部覆盖；未绑定时保留旧解析。
    binding_applied = False
    try:
        from core.model_provider.preset_config import (
            get_effective_route_binding,
            resolve_route_binding_candidates,
        )

        binding = get_effective_route_binding(route_key)
        bound_candidates = resolve_route_binding_candidates(route_key)
        if binding is not None and bound_candidates:
            _candidate, resolved_model = bound_candidates[0]
            model_config = resolved_model.preset
            bound_provider = _get_provider_config(model_config.provider_id)
            if bound_provider is None:
                raise ValueError(
                    "模型默认配置引用的 Provider 不存在: "
                    f"{model_config.provider_id}"
                )
            binding_applied = True
            runtime_supported = (
                bool(bound_provider.get("agent_runtime_supported"))
                if route_key == "reply"
                else bool(bound_provider.get("route_completion_supported"))
            )
            result.update({
                "profile_id": model_config.id,
                "provider_id": model_config.provider_id,
                "base_url": bound_provider.get("base_url", ""),
                "api_key": bound_provider.get("api_key", ""),
                "api_key_configured": bool(
                    bound_provider.get("credential_configured")
                ),
                "route_api_key_configured": False,
                "provider_enabled": bool(bound_provider.get("enabled", True)),
                "driver_type": bound_provider.get("driver_type", "openai"),
                "route_completion_supported": runtime_supported,
                "model": model_config.model,
                "max_context": model_config.max_context,
                "max_tokens": model_config.max_output,
                "temperature": model_config.temperature,
                "timeout": model_config.timeout,
                "enable_thinking": model_config.enable_thinking,
                "reasoning_effort": model_config.reasoning_effort,
                "service_tier": model_config.service_tier,
                "selected_variations": dict(
                    resolved_model.selected_variations
                ),
                "route_overrides": dict(resolved_model.route_overrides),
                "binding_candidates": [
                    {
                        **entry.to_dict(),
                        "profile_id": item.preset.id,
                        "provider_id": item.preset.provider_id,
                        "model": item.preset.model,
                        "driver_type": (
                            (_get_provider_config(item.preset.provider_id) or {}).get(
                                "driver_type", "openai"
                            )
                        ),
                        "route_overrides": dict(item.route_overrides),
                        "intelligence": item.preset.intelligence,
                        "fallback_only": item.preset.fallback_only,
                        "cost_input_1m": item.preset.cost_input_1m,
                        "cost_output_1m": item.preset.cost_output_1m,
                    }
                    for entry, item in bound_candidates
                ],
                "binding_inherited_from": binding.inherited_from or None,
                "source": "model_binding",
            })
    except ValueError as exc:
        result["binding_error"] = str(exc)

    # 继承信息完全来自 Descriptor。
    if descriptor.inherits_from is not None:
        inherited_from = descriptor.inherits_from
        parent = resolve_model_route(inherited_from)
        overrides = {}
        for k in ("max_tokens", "timeout", "temperature", "model", "provider_id", "enable_thinking"):
            if result[k] != parent.get(k) and result[k] not in ("", "未指定", 30):
                overrides[k] = result[k]
        result["inherited_from"] = inherited_from
        result["overridden_fields"] = overrides
        if not binding_applied:
            result["source"] = f"inherited_from_{inherited_from}"

    result.update({
        "domain": descriptor.domain,
        "owner": descriptor.owner,
        "required_provider_capabilities": sorted(
            capability.value
            for capability in descriptor.required_provider_capabilities
        ),
        "setting_prefix": descriptor.setting_prefix,
        "model_setting_key": descriptor.model_setting_key,
        "model_fallback_setting_key": (
            descriptor.model_fallback_setting_key
        ),
        "fallback_route": descriptor.fallback_route,
        "fallback_scope": descriptor.fallback_scope,
        "candidate_policy_id": descriptor.candidate_policy_id,
        "circuit_breaker_policy_id": (
            descriptor.circuit_breaker_policy_id
        ),
        "task_contract_keys": list(descriptor.task_contract_keys),
        "output_contract_id": descriptor.output_contract_id,
        "trace_policy_id": descriptor.trace_policy_id,
        "lifecycle": descriptor.lifecycle.value,
        "execution_mode": descriptor.execution_mode.value,
        "slo": descriptor.slo.metadata(),
    })
    return result


def build_provider_catalog(db=None) -> list[dict]:
    """返回从 /v1/models 同步的真实模型列表（仅 provider_catalog）。

    不包含 route 引用。用于「模型列表」Tab。
    """
    from core.route_metadata import canonical_provider_id, is_deprecated_provider
    import json

    if db is None:
        from core.database import SessionLocal
        db = SessionLocal()
        _close_db = True
    else:
        _close_db = False
    try:
        from core.database import SystemSetting
        from core.model_provider.preset_config import list_model_defaults

        defaults = {
            (item.provider_id, item.model): item
            for item in list_model_defaults(db)
        }
        rows = db.query(SystemSetting).filter(
            SystemSetting.key.like("model.catalog.%")
        ).all()
        items: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row.value or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            raw_provider = row.key.removeprefix("model.catalog.")
            if is_deprecated_provider(raw_provider):
                continue
            provider = canonical_provider_id(raw_provider)
            for m in data.get("models", []):
                model_default = defaults.get((provider, m))
                entry = {
                    "id": f"{provider}::{m}",
                    "provider": provider,
                    "model": m,
                    "configured": model_default is not None,
                    "stale": not data.get("last_refresh_ok", True),
                    "updated_at": str(data.get("updated_at") or ""),
                    "last_refresh_ok": data.get("last_refresh_ok", True),
                    "last_error": str(data.get("last_error") or ""),
                    "source": "provider_catalog",
                    "verified": True,
                }
                if model_default is not None:
                    entry["default_config"] = model_default.public_view()
                    entry["capabilities"] = [
                        name.removeprefix("supports_")
                        for name, enabled in model_default.capabilities.items()
                        if enabled
                    ]
                else:
                    entry["default_config"] = None
                    entry["capabilities"] = []
                items.append(entry)
        return sorted(items, key=lambda x: x["model"])
    finally:
        if _close_db:
            db.close()


def build_route_references() -> list[dict]:
    """返回当前所有 route 引用的模型，标记是否在 provider_catalog 中确认。

    用于「路由引用异常」展示——未确认的条目会高亮。
    """
    from core.route_metadata import canonical_provider_id, is_deprecated_provider
    from core.database import SessionLocal, SystemSetting
    import json

    # 先收集 provider_catalog 中已确认的 key
    catalog_keys: set[str] = set()
    db = SessionLocal()
    try:
        rows = db.query(SystemSetting).filter(
            SystemSetting.key.like("model.catalog.%")
        ).all()
        for row in rows:
            try:
                data = json.loads(row.value or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            raw_provider = row.key.removeprefix("model.catalog.")
            if is_deprecated_provider(raw_provider):
                continue
            provider = canonical_provider_id(raw_provider)
            for m in data.get("models", []):
                catalog_keys.add(f"{provider}::{m}")
    finally:
        db.close()

    items: list[dict] = []
    seen: set[str] = set()
    for descriptor in list_model_route_descriptors():
        rk = descriptor.route_key
        r = resolve_model_route(rk)
        m = r.get("model", "")
        if not m or m == "未指定":
            continue
        pid = r.get("provider_id", "")
        k = f"{pid}::{m}" if pid else m
        if k not in seen:
            seen.add(k)
            items.append({
                "id": k,
                "provider": pid,
                "model": m,
                "route_key": rk,
                "route_type": r.get("route_type", "unknown"),
                "verified": k in catalog_keys,
                "source": "route",
            })
    items.sort(key=lambda x: (x["verified"], x["model"]))
    return items


def build_model_catalog(db=None, *,
                        provider_filter: str = "",
                        query: str = "",
                        limit: int = 0,
                        offset: int = 0) -> list[dict]:
    """组合 catalog（兼容旧接口，诊断页用）。"""
    catalog = build_provider_catalog(db)
    refs = build_route_references()
    # 合并：provider_catalog 条目 + route-only 条目
    cat_keys = {e["id"] for e in catalog}
    merged = list(catalog)
    for ref in refs:
        if ref["id"] not in cat_keys:
            ref["capabilities"] = []
            ref["used_by"] = [ref["route_key"]]
            ref["stale"] = False
            merged.append(ref)
        else:
            for e in merged:
                if e["id"] == ref["id"]:
                    e.setdefault("used_by", []).append(ref["route_key"])
                    break
    items = sorted(merged, key=lambda x: x["model"])
    if provider_filter:
        items = [e for e in items if e["provider"] == provider_filter]
    if query:
        q = query.lower()
        items = [e for e in items if q in e["model"].lower() or q in e["provider"]]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    return items

# Control characters to strip (exclude \n, \t, \r)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Guardrail:
    """4-layer guardrail for private message classification."""

    _sentinel: object | None = None  # 类级别缓存，所有实例共享

    def __init__(self):
        self._system_prompt = (
            "判断是否需要回复。\n"
            "疑问、请求、讨论、任何带对话文字的 → 是,\n"
            "即使消息中含链接/密钥/路径，只要有人类对话文字就判是,\n"
            "只有纯链接/密钥/文件路径/空白 → 否,\n"
            "不确定就回 是,\n\n"
            "逗号后跟复杂度 1-10。1=你好谢谢 3=简单 5=普通 7=分析 9=很难 10=推理题。\n\n"
            "示例: 你好 → 是,1\n"
            "... → 是,1\n"
            "[图片] → 是,3\n"
            "sk-abc → 否,0\n"
            "   → 否,0\n"
            "帮我写代码 → 是,6\n"
            "sk-abc过期了怎么办 → 是,5\n"
            "总结群聊讨论了什么 → 是,7\n\n"
            "只输出 是,数字 或 否,数字。禁止思考。"
        )

    # ── L0: Message Preprocessing ──

    # Prefixes that confuse the model into thinking it's a system instruction
    _CONFUSING_PREFIXES = re.compile(
        r"^\s*[\[<]\s*(?:SYSTEM|system|INST|PROMPT|INSTRUCTION|CMD)[\s\]>]+",
    )

    # ── L1: Model-based Injection Detection ──

    @classmethod
    def _load_sentinel(cls):
        """Lazy-load sentinel model from local ./sentinel (class-level cache)."""
        if cls._sentinel is not None:
            return cls._sentinel
        try:
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )

            from config import get_sentinel_model_path

            model_path = get_sentinel_model_path()
            logger.info(
                "Loading sentinel configured=%s",
                str(bool(model_path)).lower(),
            )
            tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True,
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                model_path,
                torch_dtype="float16",
                trust_remote_code=True,
            )
            cls._sentinel = pipeline(
                "text-classification",
                model=model,
                tokenizer=tokenizer,
                device=-1,
                max_length=512,
                truncation=True,
            )
            logger.info("Sentinel loaded, labels=%s", model.config.id2label)
        except ImportError:
            logger.warning("transformers not installed, injection detection disabled")
            cls._sentinel = False
        except Exception as e:
            logger.error(
                "Failed to load sentinel: error_type=%s",
                type(e).__name__,
            )
            cls._sentinel = False
        return cls._sentinel

    @classmethod
    def _detect_injection(cls, message: str) -> bool:
        """Run sentinel model on message. Returns True if injection detected."""
        sentinel = cls._load_sentinel()
        if sentinel is False or sentinel is None:
            return False  # model unavailable → fail open

        try:
            # Normalize
            text = message.replace("\r\n", "\n").replace("\r", "\n")
            text = CONTROL_CHAR_PATTERN.sub("", text)
            if not text.strip():
                return False

            result = sentinel(text[:1024])  # truncate to avoid excess tokens
            # result is list of dicts: [{"label": "INJECTION", "score": 0.97}]
            label = result[0]["label"].upper() if result else ""
            score = result[0]["score"] if result else 0.0

            is_injection = "JAILBREAK" in label and score >= 0.5
            if is_injection:
                logger.warning(
                    "Sentinel detected injection: label=%s score=%.3f", label, score
                )
            return is_injection
        except Exception as e:
            logger.error("Sentinel inference failed: %s", e)
            return False  # fail open

    # ── L2: Qwen Call ──

    def _call_qwen(self, message: str) -> str:
        """调用分类器模型路由（同步）。"""
        logger.info("  [classifier] >> message: %.80s", message)
        content = call_model_route(
            route_key="classifier_legacy",
            system_prompt=self._system_prompt,
            user_message=message,
            max_tokens=30,
        )
        logger.info("  [classifier] << cleaned: %.120s", content)
        return content

    # ── L3: Output Validation ──

    def _validate_output(self, text: str) -> tuple:
        """Validate and parse Qwen output.

        Returns (is_valid, type_str, complexity):
          is_valid: whether the output matches the expected format.
          type_str: "是" or "否" (empty if invalid).
          complexity: parsed complexity, clamped to [1, 10].
                      Forced to 0 when type="否" and raw_complexity > 2
                      (model is confused).
        """
        stripped = text.strip()
        # Allow bare "是" (no complexity) — default to 5
        if stripped in ("是", "是，"):
            return (True, "是", 5)
        if stripped in ("否", "否，"):
            return (True, "否", 0)

        match = OUTPUT_PATTERN.match(stripped)
        if not match:
            return (False, "", 0)

        type_str = match.group(1)
        complexity = int(match.group(2))

        # Clamp complexity to [1, 10]
        if complexity > 10:
            complexity = 10
        elif complexity < 1:
            complexity = 1

        # If model says "no" with high complexity, it's confused -> treat as silent
        if type_str == "否" and complexity > 2:
            return (True, "否", 0)

        return (True, type_str, complexity)

    # ── Public API ──

    def detect_injection(
        self, message: str, *, allow_passthrough: bool = False
    ) -> dict:
        """Sentinel 注入检测——不做 Qwen 调用。"""
        if not message or not message.strip():
            return {"status": "safe", "injection": False}
        if self._detect_injection(message):
            if allow_passthrough:
                logger.info("[Guardrail] injection detected but passthrough enabled")
                return {"status": "safe", "injection": True, "passthrough": True}
            return {"status": "injection", "injection": True}
        return {"status": "safe", "injection": False}

    def classify_reply_legacy(self, message: str) -> dict:
        """旧 Qwen 二分类——输出 status=reply/silent + complexity。"""
        message = self._CONFUSING_PREFIXES.sub("", message).strip()
        if not message:
            return {"status": "silent", "complexity": 0}
        try:
            response_text = self._call_qwen(message)
        except Exception as exc:
            logger.warning(
                "Qwen call failed, fallback to reply: error_type=%s",
                type(exc).__name__,
            )
            return {"status": "reply", "complexity": 5}
        is_valid, type_str, complexity = self._validate_output(response_text)
        if not is_valid:
            return {"status": "injection", "complexity": 0}
        if type_str == "否":
            return {"status": "silent", "complexity": 0}
        return {"status": "reply", "complexity": complexity}

    def classify(
        self, message: str, *, allow_injection_passthrough: bool = False
    ) -> dict:
        """Classify a private chat message (保持兼容)。

        Returns dict with:
          status: "reply" | "silent" | "injection"
          complexity: int (0 for silent/injection, 1-10 for reply)
        """
        if not message or not message.strip():
            return {"status": "silent", "complexity": 0}

        injection = self.detect_injection(
            message, allow_passthrough=allow_injection_passthrough
        )
        if injection["status"] == "injection":
            return {"status": "injection", "complexity": 0}

        return self.classify_reply_legacy(message)


# ── Module-level singleton ──

_guardrail_instance: Guardrail | None = None


def get_guardrail() -> Guardrail:
    """Return the module-level Guardrail singleton."""
    global _guardrail_instance
    if _guardrail_instance is None:
        _guardrail_instance = Guardrail()
    return _guardrail_instance


# ── PrivateDecisionClassifier（私聊三态决策，一次 Qwen 调用输出 action + complexity）──


class PrivateDecisionClassifier:
    """私聊决策兼容 façade；实际执行与解析统一由 TaskRuntime 负责。"""

    def classify(self, message: str, has_files: bool = False) -> dict:
        from clients.decision_model_adapter import (
            execute_private_decision_task,
        )

        return execute_private_decision_task(message, has_files)


_private_decision_instance: PrivateDecisionClassifier | None = None


def get_private_decision_classifier() -> PrivateDecisionClassifier:
    global _private_decision_instance
    if _private_decision_instance is None:
        _private_decision_instance = PrivateDecisionClassifier()
    return _private_decision_instance


# ── Timing Gate（群聊回复节奏判断，独立于 Guardrail）──

TIMING_GATE_MAX_TOKENS = 80


class TimingGate:
    """群聊节奏判断器——Qwen 三态输出，与 Guardrail 完全独立。"""

    def _call_qwen(self, message: str) -> str:
        return call_model_route(
            route_key="timing_gate",
            user_message=message,
            max_tokens=TIMING_GATE_MAX_TOKENS,
        )

    def _parse_output(self, raw: str) -> dict:
        # 去 think
        cleaned = raw
        for _ in range(5):
            prev = cleaned
            cleaned = THINK_PATTERN.sub("", cleaned).strip()
            if cleaned == prev:
                break

        try:
            from core.prompt_v2.task_contracts import parse_task_output

            parsed = parse_task_output("timing_gate", cleaned)
            return {
                **parsed,
                "raw": raw[:200],
                "error_type": None,
                "parse_quality": "json",
                "model_confidence": 0.8,
            }
        except ValueError:
            pass

        # 非法 → no_reply
        logger.warning(
            "[TimingGate] invalid output chars=%s sha256=%s",
            len(raw),
            hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
        )
        return {
            "action": "no_reply",
            "delay_seconds": None,
            "reason": "非法输出",
            "raw": raw[:200],
            "error_type": "parse_error",
            "parse_quality": "invalid",
            "model_confidence": 0.0,
        }

    def judge(self, context: str) -> dict:
        import time as _t

        t0 = _t.time()
        try:
            raw = self._call_qwen(context)
            result = self._parse_output(raw)
            if result.get("error_type") == "parse_error":
                retry_context = (
                    f"{context}\n\n"
                    "[输出契约修正] 只输出一个 JSON object。action 只能是 "
                    "continue、wait 或 no_reply。wait 必须给 3-15 的整数 delay_seconds。"
                )
                raw = self._call_qwen(retry_context)
                result = self._parse_output(raw)
            result["context"] = context
            result["raw"] = raw
            elapsed_ms = int((_t.time() - t0) * 1000)
            logger.info(
                "[TimingGate] action=%s delay=%s latency=%dms reason=%.60s error=%s",
                result["action"],
                result.get("delay_seconds"),
                elapsed_ms,
                str(result.get("reason", ""))[:60],
                result.get("error_type"),
            )
            return result
        except Exception as e:
            elapsed_ms = int((_t.time() - t0) * 1000)
            logger.warning("[TimingGate] failed latency=%dms: %s", elapsed_ms, e)
            return {
                "action": "no_reply",
                "delay_seconds": None,
                "reason": f"Qwen不可用: {e}",
                "raw": "",
                "error_type": "network_error",
                "parse_quality": "network_error",
                "model_confidence": 0.0,
            }


_timing_gate_instance: "TimingGate | None" = None


def get_timing_gate() -> TimingGate:
    global _timing_gate_instance
    if _timing_gate_instance is None:
        _timing_gate_instance = TimingGate()
    return _timing_gate_instance


# ── 群聊主动发言裁判（独立 route timing_proactive，默认倾向沉默）──

def judge_proactive(context: str) -> dict:
    """群聊主动发言语义裁判。走独立 route timing_proactive，解析失败保守沉默。"""
    import time as _t

    t0 = _t.time()
    try:
        response = call_model_route_response(
            route_key="timing_proactive",
            user_message=context,
        )
    except Exception as e:
        logger.warning("[Proactive] failed latency=%dms: %s", int((_t.time() - t0) * 1000), e)
        return {"should_speak": False, "reason": f"裁判不可用: {e}", "raw": "", "error_type": "network_error"}

    raw = response.content
    if response.finish_reason not in (None, "stop"):
        error_type = (
            "model_truncated"
            if response.finish_reason == "length"
            else "model_finish_error"
        )
        logger.warning(
            "[Proactive] abnormal finish_reason=%s",
            response.finish_reason,
        )
        return {
            "should_speak": False,
            "reason": f"模型未正常结束: {response.finish_reason or 'missing'}",
            "raw": str(raw)[:200],
            "error_type": error_type,
        }

    cleaned = strip_think_blocks(raw).strip()
    try:
        from core.prompt_v2.task_contracts import parse_task_output

        parsed = parse_task_output("timing_proactive", cleaned)
        return {
            **parsed,
            "raw": raw[:200],
            "error_type": None,
        }
    except ValueError:
        pass

    logger.warning("[Proactive] invalid output: %s", str(raw)[:100])
    return {
        "should_speak": False,
        "reason": "输出不满足主动发言契约",
        "raw": str(raw)[:200],
        "error_type": "contract_error",
    }
