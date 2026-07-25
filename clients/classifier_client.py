"""
Private chat classifier guardrail — 4-layer defense.

L1: 模型注入检测 (prompt-injection-sentinel, transformers pipeline)
L2: Qwen model call (llama.cpp server)
L3: Output validation (strict format)
L4: Timeout fallback
"""

import hashlib
import logging
import os
import re
import urllib.request
from config import CLASSIFIER_API_URL
from clients.provider_adapter import adapter_from_route, registry_from_provider_configs
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
            base["provider_id"] = provider_id
            base["provider_enabled"] = _as_bool(provider.get("enabled", True), default=True)

    return base


def ensure_model_route_enabled(route_key: str, route: dict | None = None) -> dict:
    """实际调用前强制检查 provider.enabled。展示/目录解析不调用此函数。"""
    route = route or resolve_model_route(route_key)
    provider_id = str(route.get("provider_id") or "").strip()
    if provider_id and route.get("provider_enabled") is False:
        raise ModelRouteProviderUnavailableError(provider_id)
    return route


# Pattern for Qwen output validation: 是/否 + comma + number (optional negative)
OUTPUT_PATTERN = re.compile(r"^(是|否)[,，](-?\d+)$")

# Pattern to strip think/thought blocks from Qwen response
THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


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
    route = ensure_model_route_enabled(route_key)
    logger.info(
        "[call_model_route] route=%s provider=%s model=%s",
        route_key,
        route.get("provider_id", ""),
        route.get("model", ""),
    )

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

    adapter = adapter_from_route(
        route,
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
    response = provider.complete(
        ModelProviderRequest(
            messages=tuple(messages),
            model=str(route.get("model") or ""),
            max_tokens=(
                max_tokens if max_tokens is not None else int(route["max_tokens"])
            ),
            temperature=(
                temperature
                if temperature is not None
                else float(route["temperature"])
            ),
            timeout_seconds=timeout or float(route.get("timeout", 15)),
            enable_thinking=str(route.get("enable_thinking") or "auto"),
            trace_source=descriptor.trace_source,
            metadata={"route_key": route_key},
        )
    )
    return ModelRouteResponse(
        content=response.content,
        reasoning_content=response.reasoning_content,
        finish_reason=response.finish_reason,
        usage=dict(response.usage),
        raw_response=dict(response.raw_response),
    )


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
    """读取 provider 内部配置（含 api_key，仅内部使用）。

    对已知 provider 使用 config 常量作为 fallback，避免 settings 空值覆盖 env。
    旧 provider 名通过 canonical_provider_id 映射到 canonical 名。
    优先读 DB 中实际存在的 key（canonical 或旧 alias），再回退到 settings 默认值。
    """
    from core.settings_service import settings
    from core.database import SessionLocal, SystemSetting
    from config import NEW_API_BASE_URL, NEW_API_KEY, CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL
    from core.route_metadata import canonical_provider_id, PROVIDER_ALIASES

    raw_id = provider_id
    provider_id = canonical_provider_id(provider_id)

    # 找到此 canonical 名对应的旧 alias
    alias_key = None
    for old, new in PROVIDER_ALIASES.items():
        if new == provider_id and old != raw_id:
            alias_key = old
            break
    if not alias_key and provider_id == "local_llama":
        alias_key = "local_qwen"
    if not alias_key and provider_id == "local_vision":
        alias_key = "vision_qwen"

    # 一次性查询 DB，判断哪些 key 实际存在
    db_keys: set[str] = set()
    db = SessionLocal()
    try:
        rows = db.query(SystemSetting.key).filter(
            SystemSetting.key.like("model.providers.%")
        ).all()
        db_keys = {r.key for r in rows}
    except Exception:
        pass
    finally:
        db.close()

    def _get_with_fallback(field: str):
        """读取配置：DB 中有 canonical key → 用 canonical；
        DB 中只有 alias key → 用 alias；否则用 settings 默认值。"""
        ck = f"model.providers.{provider_id}.{field}"
        if ck in db_keys:
            return settings.get(ck, None)
        if alias_key:
            ak = f"model.providers.{alias_key}.{field}"
            if ak in db_keys:
                return settings.get(ak, None)
            alias_value = settings.get(ak, None)
            if alias_value is not None and alias_value != "":
                return alias_value
        v = settings.get(ck, None)
        return v

    base_url = str(_get_with_fallback("base_url") or "")
    api_key = str(_get_with_fallback("api_key") or "")

    if provider_id == "newapi":
        base_url = base_url or str(NEW_API_BASE_URL or "")
        api_key = api_key or str(NEW_API_KEY or "")
    elif provider_id == "local_llama":
        base_url = base_url or str(CLASSIFIER_API_URL or "")
    elif provider_id == "local_vision":
        base_url = base_url or str(IMAGE_SUMMARY_API_URL or "")

    if not base_url:
        return None
    enabled = _get_with_fallback("enabled")
    if enabled is None or enabled == "":
        enabled = True
    registry_provider = str(_get_with_fallback("registry_provider") or "").strip()
    return {
        "id": provider_id,
        "base_url": base_url,
        "api_key": api_key,
        "enabled": _as_bool(enabled, default=True),
        "registry_provider": registry_provider or None,
    }


def provider_public(p: dict) -> dict:
    """脱敏返回：不暴露 api_key 明文。"""
    return {
        "id": p["id"],
        "base_url": p.get("base_url", ""),
        "api_key_configured": bool(p.get("api_key")),
        "enabled": bool(p.get("enabled")),
        "legacy_aliases": p.get("legacy_aliases", []),
        "registry_provider": p.get("registry_provider") or None,
    }


def list_providers() -> list[dict]:
    """列出所有已配置的 provider（仅返回 canonical 名，跳过 deprecated alias）。"""
    from core.config_registry import SETTING_DEFS
    from core.route_metadata import (
        is_deprecated_provider, canonical_provider_id, normalize_base_url, PROVIDER_ALIASES,
    )
    from config import CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL

    providers: list[dict] = []
    seen_canonical: set[str] = set()
    deprecated_pids: list[str] = []
    for key in SETTING_DEFS:
        if not key.startswith("model.providers.") or not key.endswith(".base_url"):
            continue
        pid = key.removeprefix("model.providers.").removesuffix(".base_url")
        if is_deprecated_provider(pid):
            deprecated_pids.append(pid)
            continue
        # local_vision 仅在 endpoint 不同时条件性添加，不在此处扫描
        if pid == "local_vision":
            continue
        canonical = canonical_provider_id(pid)
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        cfg = _get_provider_config(canonical)
        if cfg:
            # 附上此 canonical 名对应的旧别名
            aliases = [old for old, new in PROVIDER_ALIASES.items() if new == canonical]
            if aliases:
                cfg["legacy_aliases"] = aliases
            providers.append(cfg)

    # local_vision 仅在 IMAGE_SUMMARY_API_URL != CLASSIFIER_API_URL 时出现
    normalized_classifier = normalize_base_url(str(CLASSIFIER_API_URL or ""))
    normalized_vision = normalize_base_url(str(IMAGE_SUMMARY_API_URL or ""))
    if normalized_vision and normalized_vision != normalized_classifier:
        if "local_vision" not in seen_canonical:
            cfg = _get_provider_config("local_vision")
            if cfg:
                cfg["legacy_aliases"] = ["vision_qwen"]
                providers.append(cfg)

    # 将兼容配置目录投影为类型化 Registry：重复 canonical id、alias 冲突或
    # 非法 capability 描述会在控制面列举/启动校验时立即失败。
    registry_from_provider_configs(providers)
    return providers


def provider_registry_introspection() -> tuple[dict[str, object], ...]:
    """返回当前 Provider Registry 的无密钥、无 endpoint 状态快照。"""

    registry = registry_from_provider_configs(list_providers())
    return tuple(dict(item) for item in registry.introspect())


def resolve_model_route(route_key: str) -> dict:
    """三层模型路由解析：provider → model → route params。

    返回 {route_key, provider_id, base_url, api_key, api_key_configured,
          model, timeout, temperature, max_tokens, source, inherited_from,
          overridden_fields}
    """
    from core.settings_service import settings
    from core.route_metadata import canonical_provider_id

    descriptor = require_model_route_descriptor(route_key)
    route_key = descriptor.route_key
    route = _resolve_classifier_route(route_key)

    # 确定 provider（使用 canonical 名）
    provider_id = canonical_provider_id(
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
        "model": model or "未指定",
        "timeout": route.get("timeout", 15),
        "temperature": route.get("temperature", 0),
        "max_tokens": route.get("max_tokens", 30),
        "enable_thinking": normalize_enable_thinking(route.get("enable_thinking", "auto")),
        "source": route.get("source", "provider"),
        "route_registry_generation": route_registry_snapshot.generation,
        "route_registry_sha256": route_registry_snapshot.sha256,
    }

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
                items.append({
                    "id": f"{provider}::{m}",
                    "provider": provider,
                    "model": m,
                    "capabilities": ["vision"] if ("vl" in m.lower() or "vision" in m.lower()) else [],
                    "stale": not data.get("last_refresh_ok", True),
                    "source": "provider_catalog",
                    "verified": True,
                })
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
