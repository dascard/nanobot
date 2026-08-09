"""Admin Models 路由。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from core.db import get_db, system_setting_repository
from core.model_provider.route_registry import (
    ModelRouteExecutionMode,
    ModelRouteNotFoundError,
    list_model_route_descriptors,
    model_route_registry_snapshot,
    require_model_route_descriptor,
    resolve_model_route_key,
)
from core.settings_admin_service import (
    SystemSettingCommandService,
    SystemSettingQueryService,
    SystemSettingWrite,
)

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-models"])


def _setting_query(db: Session) -> SystemSettingQueryService:
    return SystemSettingQueryService(system_setting_repository(db))


def _setting_command(db: Session) -> SystemSettingCommandService:
    return SystemSettingCommandService(system_setting_repository(db))


def _provider_public_views(db: Session) -> list[dict[str, object]]:
    """返回带近期运行证据的脱敏 Provider 视图。"""

    from core.model_provider.provider_config import list_provider_instances
    from clients.provider_runtime_evidence import (
        summarize_provider_runtime_evidence,
    )

    providers = list_provider_instances(db)
    runtime_evidence = summarize_provider_runtime_evidence(
        db,
        (provider.id for provider in providers),
        aliases_by_provider={
            provider.id: (
                provider.id,
                provider.registry_provider,
                *provider.aliases,
            )
            for provider in providers
        },
    )
    views: list[dict[str, object]] = []
    for provider in providers:
        view = provider.public_view()
        view["runtime_evidence"] = runtime_evidence[provider.id]
        views.append(view)
    return views


def _task_contract_views(
    task_contract_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    from core.prompt_v2.task_contracts import get_task_contract

    views: list[dict[str, object]] = []
    for task_key in task_contract_keys:
        contract = get_task_contract(task_key)
        if contract is None:
            raise RuntimeError(
                f"模型路由引用了未登记 Task Contract: {task_key}"
            )
        views.append({
            "task_key": contract.task_key,
            "owner_module": contract.owner_module,
            "domain": contract.domain,
            "output_contract_id": contract.output_contract_id,
            "output_schema": contract.output_schema,
            "output_failure_policy": contract.output_failure_policy,
            "template_failure_policy": contract.template_failure_policy,
            "source_precedence": list(contract.source_precedence),
        })
    return views


# ═══════════════════════════════════════════
# Model status / tests
# ═══════════════════════════════════════════

@router.get("/models/status")
def models_status(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from clients.classifier_client import Guardrail, resolve_model_route
    from core.model_provider.provider_config import list_provider_instances

    # ── Providers (脱敏) ──
    providers = [provider.public_view() for provider in list_provider_instances(db)]

    # ── Routes ──
    routes = {}
    for descriptor in list_model_route_descriptors():
        rk = descriptor.route_key
        r = resolve_model_route(rk)
        entry = {
            "route_key": rk,
            "label": descriptor.label,
            "route_type": r.get("route_type", "unknown"),
            "owner": descriptor.owner,
            "domain": descriptor.domain,
            "setting_prefix": descriptor.setting_prefix,
            "model_setting_key": descriptor.model_setting_key,
            "fallback_route": descriptor.fallback_route,
            "fallback_scope": descriptor.fallback_scope,
            "required_provider_capabilities": sorted(
                capability.value
                for capability
                in descriptor.required_provider_capabilities
            ),
            "candidate_policy_id": descriptor.candidate_policy_id,
            "circuit_breaker_policy_id": (
                descriptor.circuit_breaker_policy_id
            ),
            "task_contract_keys": list(descriptor.task_contract_keys),
            "task_contracts": _task_contract_views(
                descriptor.task_contract_keys
            ),
            "output_contract_id": descriptor.output_contract_id,
            "trace_policy_id": descriptor.trace_policy_id,
            "lifecycle": descriptor.lifecycle.value,
            "slo": descriptor.slo.metadata(),
            "provider_id": r["provider_id"],
            "profile_id": r.get("profile_id", ""),
            "model": r["model"],
            "api_key_configured": r["api_key_configured"],
            "route_api_key_configured": r.get("route_api_key_configured", False),
            "provider_enabled": r.get("provider_enabled", True),
            "driver_type": r.get("driver_type", "openai"),
            "route_completion_supported": r.get(
                "route_completion_supported",
                True,
            ),
            "timeout": r["timeout"], "temperature": r["temperature"],
            "max_tokens": r["max_tokens"],
            "max_context": r.get("max_context"),
            "reasoning_effort": r.get("reasoning_effort", ""),
            "service_tier": r.get("service_tier", ""),
            "enable_thinking": r.get("enable_thinking", "auto"),
            "binding_candidates": r.get("binding_candidates", []),
            "binding_error": r.get("binding_error", ""),
            "source": r.get("source", "provider"),
            "editable": True,
        }
        if descriptor.inherits_from:
            entry["inherited_from"] = descriptor.inherits_from
            entry["overridden_fields"] = r.get("overridden_fields", {})
        if rk == "classifier_legacy":
            entry["note"] = "兼容旧 reply/no_reply 分类路径；正常群聊优先使用 TimingGate"
        routes[rk] = entry

    # ── Local Components ──
    persona_configured = False
    persona_load_state = "not_loaded"
    persona_error = ""
    try:
        from core.persona_preprocess import _EMBEDDER_MODEL, embed_text  # noqa: F401
        persona_configured = True
    except Exception as e:
        persona_error = str(e)[:200]
        persona_load_state = "unavailable"

    nli_configured = False
    nli_load_state = "not_loaded"
    nli_error = ""
    try:
        from core.persona_preprocess import _NLI_MODEL  # noqa: F401
        nli_configured = True
    except Exception as e:
        nli_error = str(e)[:200]
        nli_load_state = "unavailable"
    try:
        from core.semantic.provider_factory import describe_reranker_provider_config
        rag_reranker = describe_reranker_provider_config()
    except Exception as e:
        rag_reranker = {
            "configured": False,
            "load_state": "unavailable",
            "model": "BAAI/bge-reranker-v2-m3",
            "model_path": "./models/bge-reranker-v2-m3",
            "error": str(e)[:200],
        }

    sentinel_configured = True
    sentinel_load_state = "not_loaded"
    try:
        g = Guardrail()
        if g._sentinel is not None:
            sentinel_load_state = "loaded"
    except Exception:
        sentinel_configured = False
        sentinel_load_state = "unavailable"

    route_registry = model_route_registry_snapshot()
    return {
        "providers": providers,
        "routes": routes,
        "route_registry": {
            "generation": route_registry.generation,
            "sha256": route_registry.sha256,
        },
        "local_components": {
            "persona_embed": {
                "model": "BAAI/bge-base-zh-v1.5",
                "loader": "sentence-transformers / HuggingFace",
                "configured": persona_configured,
                "load_state": persona_load_state,
                "error": persona_error,
                "role": "PersonaFact/PersonaBehavior 语义去重、聚类",
                "trigger": "首次画像候选处理 / 点击「测试 embedding」",
                "note": "按需懒加载；配置完成不等于已加载到内存",
            },
            "nli": {
                "model": "roberta-large-mnli",
                "loader": "transformers pipeline / HuggingFace",
                "configured": nli_configured,
                "load_state": nli_load_state,
                "error": nli_error,
                "role": "画像矛盾检测 (fallback: cosine)",
                "trigger": "首次矛盾检测 / 点击「测试 NLI」",
                "note": "按需懒加载；失败时降级为 cosine 检测",
            },
            "rag_reranker": {
                "model": rag_reranker.get("model") or "BAAI/bge-reranker-v2-m3",
                "model_path": rag_reranker.get("model_path") or "./models/bge-reranker-v2-m3",
                "resolved_model_path": rag_reranker.get("resolved_model_path"),
                "download_repo_id": rag_reranker.get("download_repo_id"),
                "loader": rag_reranker.get("loader") or "sentence-transformers CrossEncoder",
                "configured": bool(rag_reranker.get("configured")),
                "load_state": rag_reranker.get("load_state") or "not_loaded",
                "error": "" if rag_reranker.get("configured") else "本地 reranker 模型目录不存在或未配置",
                "role": "Memory / Sticker / Knowledge / GroupAnalysis RAG 候选重排",
                "trigger": "首次 RAG 查询 / 点击「测试 reranker」",
                "note": "本地模型组件，不走 new-api；默认下载 BAAI/bge-reranker-v2-m3 到 ./models/bge-reranker-v2-m3",
                "path_exists": rag_reranker.get("path_exists"),
                "source": rag_reranker.get("source"),
            },
            "sentinel": {
                "model": "prompt-injection-sentinel",
                "loader": "transformers pipeline",
                "configured": sentinel_configured,
                "load_state": sentinel_load_state,
                "role": "L1 prompt injection 检测",
                "trigger": "首次调用 _load_sentinel() 时加载",
                "note": "按需懒加载",
            },
        },
        "unsupported": {
            "rerank": {"implemented": True, "note": "通过本地 rag_reranker 组件接入 RAG rerank pipeline"},
        },
    }


class ChatModelTestRequest(BaseModel):
    model: str = ""
    prompt: str = Field(default="用一句话回复：Nanobot 模型连通性测试", max_length=4000)
    json_mode: bool = False


@router.post("/models/chat-test")
async def chat_model_test(body: ChatModelTestRequest, _auth=Depends(verify_admin)):
    import time
    from clients.new_api_client import NewAPIClient
    from config import NEW_API_KEY, NEW_API_BASE_URL

    client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
    user_prompt = body.prompt
    if body.json_mode:
        user_prompt = body.prompt + "\n只输出 JSON: {\"ok\": true, \"summary\": \"...\"}"
    t0 = time.time()
    from core.llm_trace_context import llm_trace_scope
    with llm_trace_scope(source="admin"):
        result = await client.chat_completion(
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0,
            manual_model=body.model,
            max_tokens=200,
        )
    latency_ms = int((time.time() - t0) * 1000)
    return {"latency_ms": latency_ms, "result": result}


# ── Model Catalog & Routes ──

@router.get("/model-catalog")
def get_model_catalog(_auth=Depends(verify_admin)):
    from clients.model_registry import registry

    models = []
    for m in registry.data.get("models", []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        models.append({
            "key": mid, "id": mid,
            "model": m.get("model") or mid,
            "provider": m.get("provider") or "",
            "tier": m.get("tier") or "",
            "intel": m.get("intelligence", 0),
            "intelligence": m.get("intelligence", 0),
            "input_cost": m.get("cost_input_1m", 0),
            "output_cost": m.get("cost_output_1m", 0),
            "cost_input_1m": m.get("cost_input_1m", 0),
            "cost_output_1m": m.get("cost_output_1m", 0),
            "tags": m.get("tags") or [],
            "description": m.get("description") or "",
            "enabled": bool(m.get("enabled", True)),
            "available": bool(m.get("available", True)),
            "supports_stream": bool(m.get("supports_stream", False)),
            "supports_tools": bool(m.get("supports_tools", False)),
            "supports_image": bool(m.get("supports_image", False)),
            "capability_evidence": dict(m.get("capability_evidence") or {}),
            "routing_verified": bool(m.get("routing_verified", False)),
            "routing_evidence": str(m.get("routing_evidence") or "unknown"),
        })
    return {"models": models, "last_updated": registry.data.get("last_updated", "never")}


# ── Provider 管理 ──

@router.get("/models/providers")
def list_model_providers(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """列出 Provider 实例和驱动能力（不返回原始凭据）。"""

    from core.model_provider.provider_config import provider_driver_catalog

    return {
        "providers": _provider_public_views(db),
        "driver_types": provider_driver_catalog(),
    }


class ProviderCreateBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=100)
    driver_type: Literal["openai", "anthropic", "codex"] = "openai"
    base_url: str = Field(default="", max_length=2048)
    enabled: bool = True
    registry_provider: str = Field(default="", max_length=64)
    model_discovery_enabled: bool = True
    provider_name: str = Field(default="", max_length=64)
    provider_native_tools: list[str] = Field(default_factory=list, max_length=32)
    credential_action: Literal["keep", "replace", "clear"] = "keep"
    api_key: SecretStr | None = None


class ProviderUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    driver_type: Literal["openai", "anthropic", "codex"] | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = None
    enabled: bool | None = None
    registry_provider: str | None = Field(default=None, max_length=64)
    model_discovery_enabled: bool | None = None
    provider_name: str | None = Field(default=None, max_length=64)
    provider_native_tools: list[str] | None = Field(default=None, max_length=32)
    credential_action: Literal["keep", "replace", "clear"] = "keep"


class ProviderDoctorBody(BaseModel):
    model: str = Field(default="", max_length=160)
    live_completion: bool = True
    probe_stream: bool = False
    probe_tools: bool = False
    probe_image: bool = False
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=60)


def _validated_provider_url(value: object) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise HTTPException(422, "Base URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "Base URL 必须是完整的 http/https 地址")
    if parsed.username or parsed.password:
        raise HTTPException(422, "Base URL 不允许包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise HTTPException(422, "Base URL 不允许包含查询参数或 fragment")
    return url


def _validated_registry_provider(value: object) -> str:
    registry_provider = str(value or "").strip()
    if not registry_provider:
        return ""
    from core.model_provider.contracts import (
        ProviderCapability,
        ProviderDescriptor,
    )

    try:
        ProviderDescriptor(
            id=registry_provider,
            display_name=registry_provider,
            capabilities=frozenset({ProviderCapability.CHAT_COMPLETION}),
        )
    except ValueError as exc:
        raise HTTPException(422, "Registry Provider 名称格式无效") from exc
    return registry_provider


def _validated_provider_name(value: object, fallback: str) -> str:
    provider_name = str(value or fallback).strip()
    if not provider_name or len(provider_name) > 64:
        raise HTTPException(422, "KT Provider Name 无效")
    if not all(char.isalnum() or char in {"_", "-"} for char in provider_name):
        raise HTTPException(
            422,
            "KT Provider Name 只能包含字母、数字、_、-",
        )
    return provider_name


def _validated_native_tools(values: list[str] | None) -> list[str]:
    normalized = list(dict.fromkeys(
        str(value or "").strip()
        for value in (values or [])
        if str(value or "").strip()
    ))
    try:
        from core.model_provider.admin_runtime import list_provider_native_tools

        known = {
            str(item.get("name") or "")
            for item in list_provider_native_tools()
            if isinstance(item, Mapping)
        }
    except Exception:
        known = {"image_gen"}
    unknown = sorted(set(normalized) - known)
    if unknown:
        raise HTTPException(
            422,
            f"未知 KT Provider Native Tool: {', '.join(unknown)}",
        )
    return normalized


def _credential_write(
    *,
    provider_id: str,
    driver_type: str,
    credential_action: str,
    api_key: SecretStr | None,
) -> SystemSettingWrite | None:
    from core.model_provider.provider_config import provider_setting_key

    raw_key = api_key.get_secret_value() if api_key is not None else ""
    if len(raw_key) > 8192:
        raise HTTPException(422, "API Key 超过长度上限")
    if credential_action == "keep":
        if raw_key:
            raise HTTPException(
                422,
                "提交 API Key 时必须显式设置 credential_action=replace",
            )
        return None
    if driver_type == "codex" and credential_action == "replace":
        raise HTTPException(
            422,
            "Codex 驱动使用 KT OAuth，不接受 Nanobot API Key",
        )
    if credential_action == "replace":
        value = raw_key.strip()
        if not value:
            raise HTTPException(422, "replace 操作必须提供非空 API Key")
    else:
        value = ""
    return SystemSettingWrite(
        key=provider_setting_key(provider_id, "api_key"),
        value=value,
        description=f"provider {provider_id} api_key",
    )


def _provider_public_or_404(provider_id: str, db: Session) -> dict:
    from core.model_provider.provider_config import get_provider_instance

    provider = get_provider_instance(provider_id, db)
    if provider is None:
        raise HTTPException(404, f"unknown provider: {provider_id}")
    return provider.public_view()


@router.post("/models/providers", status_code=201)
def create_model_provider(
    body: ProviderCreateBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """创建自定义 Provider 实例。"""

    from core.model_provider.provider_config import (
        get_provider_instance,
        provider_setting_key,
        validate_driver_type,
        validate_provider_id,
    )
    from core.settings_service import settings

    try:
        provider_id = validate_provider_id(body.id)
        driver_type = validate_driver_type(body.driver_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if get_provider_instance(provider_id, db) is not None:
        raise HTTPException(409, f"Provider 已存在: {provider_id}")
    if driver_type != "openai":
        references = _provider_route_references(provider_id)
        if references:
            raise HTTPException(
                409,
                detail={
                    "message": (
                        "已有模型 Route 引用该 Provider ID，"
                        "不能创建为尚未接入的驱动"
                    ),
                    "route_references": references,
                },
            )

    display_name = str(body.display_name or provider_id).strip()
    if not display_name:
        raise HTTPException(422, "Provider 显示名称不能为空")
    base_url = (
        "" if driver_type == "codex" else _validated_provider_url(body.base_url)
    )
    discovery_enabled = (
        body.model_discovery_enabled and driver_type == "openai"
    )
    registry_provider = _validated_registry_provider(
        body.registry_provider or provider_id
    )
    provider_name = _validated_provider_name(
        body.provider_name,
        "codex" if driver_type == "codex" else provider_id,
    )
    provider_native_tools = _validated_native_tools(body.provider_native_tools)
    prepared = [
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "display_name"),
            value=display_name,
            description=f"provider {provider_id} display_name",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "driver_type"),
            value=driver_type,
            description=f"provider {provider_id} driver_type",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "base_url"),
            value=base_url,
            description=f"provider {provider_id} base_url",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "enabled"),
            value="1" if body.enabled else "0",
            description=f"provider {provider_id} enabled",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "registry_provider"),
            value=registry_provider,
            description=f"provider {provider_id} registry_provider",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "model_discovery_enabled"),
            value="1" if discovery_enabled else "0",
            description=f"provider {provider_id} model_discovery_enabled",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "provider_name"),
            value=provider_name,
            description=f"provider {provider_id} provider_name",
        ),
        SystemSettingWrite(
            key=provider_setting_key(provider_id, "provider_native_tools"),
            value=json.dumps(provider_native_tools, ensure_ascii=False),
            description=f"provider {provider_id} provider_native_tools",
        ),
    ]
    credential = _credential_write(
        provider_id=provider_id,
        driver_type=driver_type,
        credential_action=body.credential_action,
        api_key=body.api_key,
    )
    if credential is not None:
        prepared.append(credential)
    _setting_command(db).upsert_many(prepared)
    settings.invalidate()
    audit(
        db,
        "create_provider",
        "provider",
        provider_id,
        {
            "fields": [write.key.rsplit(".", 1)[-1] for write in prepared],
            "credential_action": body.credential_action,
        },
        ip_address=client_ip(request),
    )
    return {
        "ok": True,
        "provider": _provider_public_or_404(provider_id, db),
        "version": settings.version,
    }


@router.put("/models/providers/{provider_id}")
def update_model_provider(
    provider_id: str, body: ProviderUpdateBody,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """更新 Provider；凭据必须明确选择 keep/replace/clear。"""

    from core.model_provider.provider_config import (
        canonical_provider_instance_id,
        get_provider_instance,
        provider_setting_key,
        validate_driver_type,
    )
    from core.settings_service import settings

    raw_provider_id = provider_id
    provider_id = canonical_provider_instance_id(provider_id)
    current = get_provider_instance(provider_id, db)
    if current is None:
        raise HTTPException(404, f"unknown provider: {raw_provider_id}")

    driver_type = (
        validate_driver_type(body.driver_type)
        if body.driver_type is not None
        else current.driver_type
    )
    if (
        body.driver_type is not None
        and driver_type != current.driver_type
        and driver_type != "openai"
    ):
        references = _provider_route_references(provider_id)
        if references:
            raise HTTPException(
                409,
                detail={
                    "message": (
                        "Provider 正被模型 Route 引用，不能切换为尚未接入的驱动"
                    ),
                    "route_references": references,
                },
            )
    prepared: list[SystemSettingWrite] = []
    if body.display_name is not None:
        display_name = str(body.display_name).strip()
        if not display_name:
            raise HTTPException(422, "Provider 显示名称不能为空")
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "display_name"),
            value=display_name,
            description=f"provider {provider_id} display_name",
        ))
    if body.driver_type is not None:
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "driver_type"),
            value=driver_type,
            description=f"provider {provider_id} driver_type",
        ))
        if driver_type == "codex" and current.api_key:
            prepared.append(SystemSettingWrite(
                key=provider_setting_key(provider_id, "api_key"),
                value="",
                description=f"provider {provider_id} api_key",
            ))
    if body.base_url is not None or (
        body.driver_type is not None and driver_type == "codex"
    ):
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "base_url"),
            value=(
                ""
                if driver_type == "codex"
                else _validated_provider_url(body.base_url)
            ),
            description=f"provider {provider_id} base_url",
        ))
    if body.enabled is not None:
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "enabled"),
            value="1" if body.enabled else "0",
            description=f"provider {provider_id} enabled",
        ))
    if body.registry_provider is not None:
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "registry_provider"),
            value=_validated_registry_provider(body.registry_provider),
            description=f"provider {provider_id} registry_provider",
        ))
    if body.model_discovery_enabled is not None or body.driver_type is not None:
        discovery_enabled = bool(
            (
                body.model_discovery_enabled
                if body.model_discovery_enabled is not None
                else current.model_discovery_enabled
            )
            and driver_type == "openai"
        )
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "model_discovery_enabled"),
            value="1" if discovery_enabled else "0",
            description=f"provider {provider_id} model_discovery_enabled",
        ))
    if body.provider_name is not None or body.driver_type is not None:
        provider_name = _validated_provider_name(
            body.provider_name,
            (
                "codex"
                if driver_type == "codex"
                else current.provider_name or provider_id
            ),
        )
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "provider_name"),
            value=provider_name,
            description=f"provider {provider_id} provider_name",
        ))
    if body.provider_native_tools is not None:
        prepared.append(SystemSettingWrite(
            key=provider_setting_key(provider_id, "provider_native_tools"),
            value=json.dumps(
                _validated_native_tools(body.provider_native_tools),
                ensure_ascii=False,
            ),
            description=f"provider {provider_id} provider_native_tools",
        ))
    credential = _credential_write(
        provider_id=provider_id,
        driver_type=driver_type,
        credential_action=body.credential_action,
        api_key=body.api_key,
    )
    if credential is not None:
        # 驱动切换到 Codex 时可能已添加同一个清理键，保持事务键唯一。
        prepared = [write for write in prepared if write.key != credential.key]
        prepared.append(credential)
    _setting_command(db).upsert_many(prepared)
    settings.invalidate()
    audit(
        db,
        "update_provider",
        "provider",
        provider_id,
        {
            "fields": [write.key.rsplit(".", 1)[-1] for write in prepared],
            "credential_action": body.credential_action,
        },
        ip_address=client_ip(request),
    )
    return {
        "ok": True,
        "provider_id": provider_id,
        "input_provider_id": raw_provider_id if raw_provider_id != provider_id else None,
        "provider": _provider_public_or_404(provider_id, db),
        "version": settings.version,
    }


def _provider_route_references(provider_id: str) -> list[str]:
    from clients.classifier_client import resolve_model_route
    from core.model_provider.provider_config import canonical_provider_instance_id

    references: list[str] = []
    for descriptor in list_model_route_descriptors():
        route = resolve_model_route(descriptor.route_key)
        if canonical_provider_instance_id(route.get("provider_id", "")) == provider_id:
            references.append(descriptor.route_key)
    return references


@router.delete("/models/providers/{provider_id}")
def delete_model_provider(
    provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """删除未被 Route 引用的自定义 Provider，并清理其目录与凭据。"""

    from core.model_provider.provider_config import (
        canonical_provider_instance_id,
        get_provider_instance,
        provider_config_keys,
    )
    from core.settings_service import settings

    provider_id = canonical_provider_instance_id(provider_id)
    provider = get_provider_instance(provider_id, db)
    if provider is None:
        raise HTTPException(404, f"unknown provider: {provider_id}")
    if provider.builtin:
        raise HTTPException(409, "内置 Provider 不允许删除，可以将其禁用")
    references = _provider_route_references(provider_id)
    from core.model_provider.preset_config import (
        list_model_defaults,
        list_model_presets,
    )

    preset_references = [
        preset.id
        for preset in list_model_presets(db)
        if preset.provider_id == provider_id
    ]
    model_default_references = [
        item.model
        for item in list_model_defaults(db)
        if item.provider_id == provider_id
    ]
    if references or preset_references or model_default_references:
        raise HTTPException(
            409,
            detail={
                "message": "Provider 正被模型默认配置或 Route 引用，不能删除",
                "route_references": references,
                "preset_references": preset_references,
                "model_default_references": model_default_references,
            },
        )
    keys = provider_config_keys(provider_id, db)
    deleted = _setting_command(db).delete_many(keys)
    settings.invalidate()
    audit(
        db,
        "delete_provider",
        "provider",
        provider_id,
        {
            "deleted_setting_count": deleted,
            "catalog_deleted": any(
                key == f"model.catalog.{provider_id}" for key in keys
            ),
        },
        ip_address=client_ip(request),
    )
    return {"ok": True, "provider_id": provider_id, "deleted": deleted}


@router.post("/models/providers/{provider_id}/test")
async def test_model_provider(
    provider_id: str,
    body: ProviderDoctorBody | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """执行配置、DNS、传输、TLS、认证、目录和 live 请求分层诊断。"""

    from clients.provider_doctor import (
        ProviderDoctorOptions,
        run_provider_doctor,
    )
    from core.model_provider.preset_config import (
        list_model_defaults,
        list_model_presets,
    )
    from core.model_provider.provider_config import get_provider_instance

    provider = get_provider_instance(provider_id, db)
    if provider is None:
        raise HTTPException(404, f"unknown provider: {provider_id}")
    request_body = body or ProviderDoctorBody()
    managed_models = [
        item
        for item in (*list_model_defaults(db), *list_model_presets(db))
        if item.provider_id == provider.id and item.enabled
    ]
    requested_model = str(request_body.model or "").strip()
    selected = next(
        (item for item in managed_models if item.model == requested_model),
        None,
    )
    if not requested_model and managed_models:
        selected = sorted(managed_models, key=lambda item: (item.model, item.id))[0]
        requested_model = selected.model
    model_capabilities = frozenset(
        name
        for name, enabled in dict(
            getattr(selected, "capabilities", {}) or {}
        ).items()
        if enabled
    )
    report = await asyncio.to_thread(
        run_provider_doctor,
        provider,
        ProviderDoctorOptions(
            model=requested_model,
            live_completion=request_body.live_completion,
            probe_stream=request_body.probe_stream,
            probe_tools=request_body.probe_tools,
            probe_image=request_body.probe_image,
            timeout_seconds=request_body.timeout_seconds,
            model_capabilities=model_capabilities,
        ),
    )
    payload = report.to_dict()
    checks = payload["checks"]
    failed = next(
        (item for item in checks if item["status"] == "failed"),
        None,
    )
    catalog = next(
        (item for item in checks if item["layer"] == "catalog"),
        {},
    )
    report_status = "ready" if report.ok else "failed"
    if not provider.enabled:
        report_status = "disabled"
    elif all(
        item["status"] in {"passed", "skipped", "unsupported"}
        for item in checks
    ) and any(item["status"] == "unsupported" for item in checks):
        report_status = "unsupported"
    payload.update({
        "status": report_status,
        "error": str((failed or {}).get("summary") or ""),
        "latency_ms": sum(int(item.get("latency_ms") or 0) for item in checks),
        "model_count": int(
            (catalog.get("metadata") or {}).get("model_count") or 0
        ),
        "model_descriptor_verified": selected is not None,
    })
    return payload


# ── 模型目录 ──

@router.get("/models/catalog")
def get_model_catalog_v2(provider: str = "", q: str = "", limit: int = 0, offset: int = 0,
                          db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """增强版模型目录：支持 provider/q/limit/offset 过滤。"""
    from clients.classifier_client import build_provider_catalog
    items = build_provider_catalog(db)
    if provider:
        items = [e for e in items if e["provider"] == provider]
    if q:
        ql = q.lower()
        items = [e for e in items if ql in e["model"].lower() or ql in e["provider"]]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    return {"catalog": items}


@router.get("/models/route-references")
def get_route_references(_auth=Depends(verify_admin)):
    """路由引用模型——标记是否在 provider_catalog 中确认存在。"""
    from clients.classifier_client import build_route_references
    return {"route_references": build_route_references()}


@router.post("/models/catalog/refresh")
def refresh_model_catalog(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """刷新所有启用且支持目录发现的 Provider。"""

    return _refresh_provider_catalogs(db)


def _previous_catalog_models(db: Session, provider_id: str) -> list[str]:
    row = _setting_query(db).get(f"model.catalog.{provider_id}")
    if row is None:
        return []
    try:
        data = json.loads(row.value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    models = data.get("models", []) if isinstance(data, dict) else []
    if not isinstance(models, list):
        return []
    return [str(model) for model in models if str(model or "").strip()]


def _refresh_provider_catalogs(
    db: Session,
    provider_id: str | None = None,
) -> dict:
    """执行目录同步；失败时保留最后一次成功或已知的模型集合。"""

    from datetime import datetime, timezone
    from clients.classifier_client import build_provider_catalog
    from clients.provider_catalog import discover_provider_models
    from core.model_provider.provider_config import (
        canonical_provider_instance_id,
        list_provider_instances,
    )
    from foundation.llm.safe_diagnostics import safe_response_summary

    providers = list_provider_instances(db)
    if provider_id is not None:
        canonical = canonical_provider_instance_id(provider_id)
        providers = [provider for provider in providers if provider.id == canonical]
        if not providers:
            raise HTTPException(404, f"unknown provider: {provider_id}")
    else:
        providers = [
            provider
            for provider in providers
            if provider.enabled
            and provider.model_discovery_enabled
            and provider.model_discovery_supported
        ]

    results: list[dict] = []
    writes: list[SystemSettingWrite] = []
    for provider in providers:
        previous_models = _previous_catalog_models(db, provider.id)
        updated_at = datetime.now(timezone.utc).isoformat()
        try:
            if not provider.enabled:
                raise RuntimeError("Provider 已禁用")
            if not provider.model_discovery_enabled:
                raise RuntimeError("Provider 模型目录同步已关闭")
            if not provider.model_discovery_supported:
                raise RuntimeError(
                    f"{provider.driver_type} 驱动尚未接入 Nanobot 模型目录发现"
                )
            models = discover_provider_models(provider.internal_view())
            ok = True
            error = ""
        except Exception as exc:
            models = previous_models
            ok = False
            error = safe_response_summary(exc, max_chars=300)

        payload = {
            "models": models,
            "updated_at": updated_at,
            "last_refresh_ok": ok,
            "last_error": error,
        }
        writes.append(SystemSettingWrite(
            key=f"model.catalog.{provider.id}",
            value=json.dumps(payload, ensure_ascii=False),
            description=f"model catalog for {provider.id}",
        ))
        result = {
            "provider": provider.id,
            "models": models,
            "model_count": len(models),
            "updated_at": updated_at,
            "ok": ok,
            "stale": not ok,
        }
        if error:
            result["error"] = error
        results.append(result)

    _setting_command(db).upsert_many(writes)
    return {
        "results": results,
        "catalog": build_provider_catalog(db),
    }


@router.post("/models/providers/{provider_id}/catalog/refresh")
def refresh_model_provider_catalog(
    provider_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """只刷新指定 Provider 的模型目录。"""

    return _refresh_provider_catalogs(db, provider_id)


_ALLOWED_TIERS = {"fast", "smart", "reasoning", "unknown"}


class ModelCatalogPatch(BaseModel):
    intelligence: int | None = Field(default=None, ge=0, le=15)
    cost_input_1m: float | None = Field(default=None, ge=0)
    cost_output_1m: float | None = Field(default=None, ge=0)
    tier: str | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


@router.patch("/model-catalog/{model_id}")
def patch_model_catalog(
    model_id: str, body: ModelCatalogPatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from clients.model_registry import registry

    m = registry.get_model_info(model_id)
    if not m:
        raise HTTPException(404, f"model '{model_id}' not found")
    if body.tier is not None and body.tier not in _ALLOWED_TIERS:
        raise HTTPException(422, f"invalid tier: {body.tier}")

    before = {
        "intelligence": m.get("intelligence"),
        "cost_input_1m": m.get("cost_input_1m"),
        "cost_output_1m": m.get("cost_output_1m"),
        "tier": m.get("tier"),
        "enabled": m.get("enabled", True),
        "tags": list(m.get("tags") or []),
    }
    updates = {}
    if body.intelligence is not None:
        m["intelligence"] = body.intelligence
        updates["intelligence"] = body.intelligence
    if body.cost_input_1m is not None:
        m["cost_input_1m"] = body.cost_input_1m
        updates["cost_input_1m"] = body.cost_input_1m
    if body.cost_output_1m is not None:
        m["cost_output_1m"] = body.cost_output_1m
        updates["cost_output_1m"] = body.cost_output_1m
    if body.tier is not None:
        m["tier"] = body.tier
        updates["tier"] = body.tier
    if body.enabled is not None:
        m["enabled"] = body.enabled
        updates["enabled"] = body.enabled
    if body.tags is not None:
        cleaned = []
        for t in body.tags:
            s = str(t).strip().lower()[:40]
            if s and s not in cleaned:
                cleaned.append(s)
        m["tags"] = cleaned[:20]
        updates["tags"] = cleaned

    registry.add_or_update_model(m)
    audit_request(db, request, "update_model_catalog", "model", model_id,
                   {"before": before, "after": updates})
    return {"ok": True, "model": model_id, "updates": updates}


# ── Stage Routes ──

_STAGE_META = {
    "main_chat": {"key": "model.reply", "field": "model"},
    "fast_chat": {"key": "model.fast", "field": "model"},
    "smart_chat": {"key": "model.smart", "field": "model"},
    "timing_gate": {"key": "model.route.timing_gate", "field": "api_url"},
    "sticker_describe": {
        "key": "model.route.sticker_describe",
        "field": "api_url",
    },
}


def _resolve_route_value(stage: str, db: Session) -> tuple[str, str, str]:
    """Return (value, source, is_overridden). source 准确反映值来源。"""
    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings

    meta = _STAGE_META[stage]
    key = meta["key"]

    # 检查 DB 是否有覆盖
    db_row = _setting_query(db).get(key)
    if db_row and db_row.value is not None:
        return db_row.value, "db_override", True

    # 没有 DB 覆盖，按 SettingSpec 解析；环境变量名也只从目录读取。
    env_name = SETTING_DEFS[key].env_name
    val = settings.get(key)
    if val:
        return str(val), env_name, True
    return "", "default", True


@router.get("/model-routes")
def get_model_routes(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    routes = {}
    for stage, meta in _STAGE_META.items():
        val, source, editable = _resolve_route_value(stage, db)
        field = meta["field"]
        entry = {"editable": editable, "source": source, field: val}
        if field == "api_url":
            entry["model"] = ""
        routes[stage] = entry
    return {"routes": routes}


class ModelRoutePatch(BaseModel):
    value: str = Field(default="", max_length=256)


@router.patch("/model-routes/{stage}")
def patch_model_route(
    stage: str, body: ModelRoutePatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if stage not in _STAGE_META:
        raise HTTPException(404, f"unknown stage: {stage}")

    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings
    from clients.model_registry import registry

    meta = _STAGE_META[stage]
    key = meta["key"]
    defn = SETTING_DEFS[key]

    # 模型路由：校验 value 是否存在于 registry
    if meta["field"] == "model" and body.value:
        if not registry.get_model_info(body.value):
            raise HTTPException(404, f"model not found in catalog: {body.value}")

    _setting_command(db).upsert(
        key=key,
        value=body.value,
        description=defn.description,
    )
    audit(db, "update_model_route", "route", stage, {"value": body.value},
           ip_address=client_ip(request))
    settings.invalidate()
    return {"ok": True, "stage": stage, "value": body.value, "version": settings.version}


# ── 模型路由编辑（完整字段）──

# 以下名称保留一个兼容周期，内容完全由冻结 Registry 投影，不能手工维护 route 集合。
_ROUTE_SETTING_MAP: dict[str, str] = {
    descriptor.route_key: descriptor.model_setting_key
    for descriptor in list_model_route_descriptors()
    if descriptor.execution_mode is ModelRouteExecutionMode.CHAT_COMPLETION
}
_CLASSIFIER_ROUTE_KEYS = frozenset(
    descriptor.route_key
    for descriptor in list_model_route_descriptors()
    if descriptor.execution_mode
    is ModelRouteExecutionMode.ROUTE_COMPLETION
)
_ROUTE_ALIAS: dict[str, str] = {
    alias: descriptor.route_key
    for descriptor in list_model_route_descriptors()
    for alias in descriptor.aliases
}
_CHAT_ROUTES = frozenset(_ROUTE_SETTING_MAP)


def _resolve_route_key(route_key: str) -> tuple[str, str, bool]:
    """解析前端 route_key → (prefix, db_key, is_classifier)。

    返回 (setting_prefix, route_key_for_db, is_classifier_route)。
    """
    canonical = resolve_model_route_key(route_key)
    descriptor = require_model_route_descriptor(canonical)
    is_classifier = (
        descriptor.execution_mode
        is ModelRouteExecutionMode.ROUTE_COMPLETION
    )
    prefix = (
        descriptor.setting_prefix
        if is_classifier
        else descriptor.model_setting_key
    )
    return prefix, canonical, is_classifier


def _redact(v: dict) -> dict:
    """脱敏：api_key → ***"""
    return {k: ("***" if k.endswith(".api_key") else v) for k, v in v.items()}


class ModelRouteEditBody(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    enable_thinking: str | None = None


@router.put("/models/routes/{route_key}")
def edit_model_route(
    route_key: str, body: ModelRouteEditBody,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """编辑模型路由配置——写入 SystemSetting 子字段。"""
    from core.config_registry import SETTING_DEFS
    from core.settings_service import coerce_setting_value, settings

    try:
        prefix, db_key, is_classifier = _resolve_route_key(route_key)
    except ModelRouteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown route: {route_key}",
        ) from exc
    if not is_classifier and prefix not in SETTING_DEFS:
        raise HTTPException(404, f"unknown route: {route_key}")

    normalized_provider = body.provider
    if body.provider is not None and str(body.provider).strip():
        from core.model_provider.provider_config import (
            canonical_provider_instance_id,
            get_provider_instance,
        )

        normalized_provider = canonical_provider_instance_id(body.provider)
        provider_instance = get_provider_instance(normalized_provider, db)
        if provider_instance is None:
            raise HTTPException(422, f"unknown provider: {body.provider}")
        if not provider_instance.route_completion_supported:
            raise HTTPException(
                422,
                f"Provider {provider_instance.id} 使用 {provider_instance.driver_type} "
                "驱动，尚不能绑定当前 Nanobot 业务 Route",
            )

    written = {}
    fields = {
        "provider": normalized_provider,
        "model": body.model,
        "api_key": body.api_key,
        "timeout": body.timeout,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "enable_thinking": body.enable_thinking,
    }
    if not is_classifier:
        allowed = {"model", "provider", "timeout", "temperature", "max_tokens", "enable_thinking"}
    else:
        allowed = {"provider", "model", "api_key", "timeout", "temperature", "max_tokens", "enable_thinking"}

    prepared: list[SystemSettingWrite] = []
    for field, value in fields.items():
        if value is None or field not in allowed:
            continue
        if not is_classifier:
            if field == "model":
                key = prefix
            elif field == "provider":
                key = f"model.route.{db_key}.provider"
            elif field in {"timeout", "temperature", "max_tokens", "enable_thinking"}:
                key = f"model.route.{db_key}.{field}"
            else:
                continue
        else:
            key = f"{prefix}.{field}"
        defn = SETTING_DEFS.get(key)
        desc = defn.description if defn else f"model route {route_key}.{field}"
        if field == "enable_thinking":
            from core.model_route_options import normalize_enable_thinking
            try:
                stored_value = normalize_enable_thinking(value)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        elif defn and defn.value_type == "int":
            try:
                stored_value = str(coerce_setting_value(value, defn))
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        elif defn and defn.value_type == "float":
            try:
                stored_value = str(coerce_setting_value(value, defn))
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        else:
            stored_value = str(value)
        prepared.append(SystemSettingWrite(
            key=key,
            value=stored_value,
            description=desc,
        ))
        written[key] = stored_value
    _setting_command(db).upsert_many(prepared)
    audit(db, "edit_model_route", "route", route_key, _redact(written), ip_address=client_ip(request))
    settings.invalidate()
    # 清除 image_summary 30s route cache（invalidate 后清理，避免并发重建旧缓存）
    if db_key == "sticker_describe":
        try:
            from core.media_tool_runtime import get_image_summary_provider

            get_image_summary_provider().invalidate_route_cache()
        except Exception as e:
            logger.warning("[models] clear image_summary route cache failed: %s", e, exc_info=True)
    # 不返回 written，只返回 api_key_configured
    resp: dict = {"ok": True, "route_key": route_key, "version": settings.version}
    api_key_written = any(k.endswith(".api_key") for k in written)
    if api_key_written:
        resp["api_key_configured"] = bool(body.api_key)
    return resp


_TINY_TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@router.post("/models/routes/{route_key}/test")
async def test_model_route(route_key: str, mode: str = "ping", _auth=Depends(verify_admin)):
    """测试某个模型路由的连通性。"""
    import time
    from clients.classifier_client import call_model_route, ensure_model_route_enabled, resolve_model_route
    from core.llm_trace_context import llm_trace_scope

    t0 = time.time()
    try:
        route_key = resolve_model_route_key(route_key)
    except ModelRouteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown route: {route_key}",
        ) from exc
    route = resolve_model_route(route_key)
    try:
        ensure_model_route_enabled(route_key, route)
    except RuntimeError as e:
        return {"ok": False, "route_key": route_key, "error": str(e)[:500]}

    if route_key in _CHAT_ROUTES:
        from clients.new_api_client import NewAPIClient
        from clients.classifier_client import registry_provider_for_route
        model = route.get("model", "") or route_key
        client = NewAPIClient(
            api_key=route["api_key"],
            base_url=route["base_url"],
            registry_provider=registry_provider_for_route(
                route.get("provider_id", "")
            ),
        )
        try:
            with llm_trace_scope(source="admin"):
                result = await client.chat_completion(
                    messages=[{"role": "user", "content": "回复OK"}],
                    manual_model=model, max_tokens=10, temperature=0,
                    enable_thinking=route.get("enable_thinking", "auto"),
                )
            return {
                "ok": True, "route_key": route_key, "model": model,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": str(result)[:300],
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}
    elif route_key == "sticker_describe":
        # vision route: ping 为文本连通性；vision 会真实发送 OpenAI-compatible 多模态 payload。
        try:
            if mode == "vision":
                messages = [
                    {"role": "system", "content": "你是视觉连通性测试模型。只回复 ok。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请确认你收到了这张 1x1 测试图片，只回复 ok。"},
                            {"type": "image_url", "image_url": {"url": _TINY_TEST_PNG}},
                        ],
                    },
                ]
                with llm_trace_scope(source="admin"):
                    raw = await asyncio.to_thread(
                        call_model_route,
                        route_key=route_key,
                        messages=messages,
                        max_tokens=20,
                        temperature=0,
                    )
                vision_payload_ok = True
                note = "真实视觉 payload 连通性测试"
            else:
                with llm_trace_scope(source="admin"):
                    raw = await asyncio.to_thread(
                        call_model_route,
                        route_key=route_key,
                        user_message="测试连通性",
                        system_prompt="你是一个视觉描述模型。收到图片时输出JSON描述。此消息仅测试连通性，回复 ok。",
                        max_tokens=20,
                    )
                vision_payload_ok = False
                note = "仅文本连通性测试，非完整视觉描述测试"
            return {
                "ok": True, "route_key": route_key,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "model": route.get("model", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": raw[:200],
                "vision_payload_ok": vision_payload_ok,
                "note": note,
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}
    else:
        try:
            with llm_trace_scope(source="admin"):
                raw = await asyncio.to_thread(
                    call_model_route,
                    route_key=route_key,
                    user_message="判断是否需要bot回复",
                    system_prompt="群聊节奏判断——是否需要bot回复。输出JSON",
                    max_tokens=60,
                )
            return {
                "ok": True, "route_key": route_key,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "model": route.get("model", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": raw[:500],
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}


@router.get("/models/routes/{route_key}/resolved")
def get_resolved_route(route_key: str, _auth=Depends(verify_admin)):
    """路由诊断——返回 resolve_model_route() 的脱敏完整结果。

    用于排查"页面显示 newapi，实际走不走 newapi"一类问题。
    """
    from clients.classifier_client import resolve_model_route
    from clients.classifier_client import registry_provider_for_route

    try:
        route_key = resolve_model_route_key(route_key)
    except ModelRouteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown route: {route_key}",
        ) from exc
    route = resolve_model_route(route_key)
    descriptor = require_model_route_descriptor(route_key)
    registry_snapshot = model_route_registry_snapshot()
    return {
        "route_key": route_key,
        "route_registry_generation": registry_snapshot.generation,
        "route_registry_sha256": registry_snapshot.sha256,
        "label": descriptor.label,
        "owner": descriptor.owner,
        "domain": descriptor.domain,
        "route_type": descriptor.route_type,
        "setting_prefix": descriptor.setting_prefix,
        "model_setting_key": descriptor.model_setting_key,
        "model_fallback_setting_key": (
            descriptor.model_fallback_setting_key
        ),
        "fallback_route": descriptor.fallback_route,
        "fallback_scope": descriptor.fallback_scope,
        "required_provider_capabilities": sorted(
            capability.value
            for capability in descriptor.required_provider_capabilities
        ),
        "candidate_policy_id": descriptor.candidate_policy_id,
        "circuit_breaker_policy_id": (
            descriptor.circuit_breaker_policy_id
        ),
        "task_contract_keys": list(descriptor.task_contract_keys),
        "task_contracts": _task_contract_views(
            descriptor.task_contract_keys
        ),
        "output_contract_id": descriptor.output_contract_id,
        "trace_policy_id": descriptor.trace_policy_id,
        "lifecycle": descriptor.lifecycle.value,
        "slo": descriptor.slo.metadata(),
        "provider_id": route.get("provider_id", ""),
        "driver_type": route.get("driver_type", "openai"),
        "route_completion_supported": route.get(
            "route_completion_supported",
            True,
        ),
        "registry_provider": registry_provider_for_route(
            route.get("provider_id", "")
        ),
        "base_url": route.get("base_url", ""),
        "model": route.get("model", ""),
        "api_key_configured": bool(route.get("api_key")),
        "api_key_source": route.get("api_key_source", ""),
        "timeout": route.get("timeout", 15),
        "temperature": route.get("temperature", 0),
        "max_tokens": route.get("max_tokens", 30),
        "enable_thinking": route.get("enable_thinking", "auto"),
        "source": route.get("source", ""),
        "provider_enabled": route.get("provider_enabled", True),
        "inherited_from": route.get("inherited_from", None),
        "overridden_fields": route.get("overridden_fields", None),
    }


@router.get("/models/available")
def list_available_models(route_key: str = "", base_url_override: str = "",
                          db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """获取某个 route 的可选模型列表——从 provider /models 端点拉取。"""
    import urllib.request as _ur
    import urllib.error as _ure

    from clients.classifier_client import resolve_model_route
    try:
        effective_route_key = resolve_model_route_key(
            route_key or "timing_gate"
        )
    except ModelRouteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown route: {route_key}",
        ) from exc
    route = resolve_model_route(effective_route_key)

    provider_id = str(route.get("provider_id", "") or "")
    if provider_id and route.get("provider_enabled") is False:
        return {"models": [], "error": f"provider disabled: {provider_id}", "source": "provider_disabled"}

    route_base_url = str(route.get("base_url", "")).rstrip("/")
    api_key = str(route.get("api_key", "") or "")
    if base_url_override:
        base_url = base_url_override.rstrip("/")
        if base_url != route_base_url:
            api_key = ""
    else:
        base_url = route_base_url

    if not base_url:
        return {"models": [], "error": "no base_url configured", "source": "none"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = _ur.Request(f"{base_url}/models", headers=headers, method="GET")
        proxy_handler = _ur.ProxyHandler({})
        opener = _ur.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        items = body.get("data", []) if isinstance(body, dict) else []
        ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        return {"models": sorted(ids)[:100], "source": f"{base_url}/models"}
    except _ure.HTTPError as e:
        b = e.read().decode("utf-8", errors="ignore")[:300] if e.fp else ""
        return {"models": [], "error": f"HTTP {e.code}: {b}", "source": base_url}
    except Exception as e:
        return {"models": [], "error": str(e)[:300], "source": base_url}


# ── 本地语义组件测试/预热 ──

def _test_nli_contradiction(a: str, b: str) -> dict:
    """模块级 NLI 矛盾检测——避免调用实例方法。"""
    from core.persona_preprocess import _get_nli
    nli = _get_nli()
    if nli is None:
        return {"available": False, "fallback": "cosine", "label": "nli_unavailable"}
    result = nli(f"{a} </s></s> {b}")
    if isinstance(result, list) and result:
        r = result[0]
        return {
            "available": True, "label": r.get("label", ""),
            "score": round(float(r.get("score", 0)), 4),
            "contradiction": r.get("label") == "CONTRADICTION",
        }
    return {"available": True, "raw": str(result)[:200]}


@router.post("/models/local/{component}/test")
async def test_local_component(component: str, _auth=Depends(verify_admin)):
    """测试本地语义组件。component: persona_embed | nli | rag_reranker"""
    import time
    t0 = time.time()
    if component == "persona_embed":
        try:
            from core.persona_preprocess import embed_text, _EMBEDDER_MODEL
            vec = embed_text("测试文本——用于验证embedding组件")
            return {
                "ok": True, "component": component, "model": str(_EMBEDDER_MODEL),
                "load_state": "loaded", "dim": len(vec),
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False, "component": component,
                "load_state": "failed", "error": str(e)[:500],
                "hint": "检查 HuggingFace 连接、磁盘缓存、sentence-transformers 安装",
            }
    elif component == "nli":
        try:
            from core.persona_preprocess import _NLI_MODEL
            result = _test_nli_contradiction("我喜欢苹果", "我不喜欢苹果")
            available = result.get("available", False)
            return {
                "ok": True, "component": component, "model": str(_NLI_MODEL),
                "load_state": "loaded" if available else "fallback",
                "fallback": "cosine" if not available else None,
                "result": result,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False, "component": component,
                "load_state": "failed", "error": str(e)[:500],
                "hint": "检查 HuggingFace 连接、磁盘缓存、transformers 安装",
            }
    elif component == "rag_reranker":
        try:
            from core.semantic.provider_factory import get_reranker_provider
            from core.semantic.reranker import SemanticCandidate

            provider = get_reranker_provider()
            if provider is None:
                return {
                    "ok": False,
                    "component": component,
                    "load_state": "unavailable",
                    "error": "本地 reranker 模型未配置或模型目录不存在",
                    "hint": "默认会自动下载 BAAI/bge-reranker-v2-m3 到 ./models/bge-reranker-v2-m3",
                }
            results = provider.rerank(
                "端口冲突怎么解决",
                [
                    SemanticCandidate(
                        candidate_id="test:1",
                        source_type="debug",
                        title="端口冲突",
                        text="8000 端口被占用时，使用 lsof 或 netstat 找到占用进程。",
                    ),
                    SemanticCandidate(
                        candidate_id="test:2",
                        source_type="debug",
                        title="无关内容",
                        text="今天的天气很好。",
                    ),
                ],
                top_k=2,
            )
            best = results[0] if results else None
            return {
                "ok": True,
                "component": component,
                "model": getattr(provider, "model_name", ""),
                "load_state": "loaded",
                "best_candidate_id": best.candidate_id if best else "",
                "best_score": best.score if best else None,
                "raw_score": best.raw_score if best else None,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False,
                "component": component,
                "load_state": "failed",
                "error": str(e)[:500],
                "hint": "检查本地 reranker 模型目录和 sentence-transformers 安装",
            }
    else:
        raise HTTPException(404, f"unknown component: {component}")


@router.post("/models/local/{component}/warmup")
async def warmup_local_component(component: str, _auth=Depends(verify_admin)):
    """预热本地语义组件——触发懒加载。"""
    import time
    t0 = time.time()
    if component == "persona_embed":
        try:
            from core.persona_preprocess import embed_text, _EMBEDDER_MODEL
            vec = embed_text("预热文本")
            return {
                "ok": True, "component": component, "model": str(_EMBEDDER_MODEL),
                "load_state": "loaded", "dim": len(vec),
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    elif component == "nli":
        try:
            from core.persona_preprocess import _NLI_MODEL
            result = _test_nli_contradiction("预热", "预热")
            available = result.get("available", False)
            return {
                "ok": True, "component": component, "model": str(_NLI_MODEL),
                "load_state": "loaded" if available else "fallback",
                "fallback": "cosine" if not available else None,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    elif component == "rag_reranker":
        try:
            return await test_local_component(component, _auth)
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    else:
        raise HTTPException(404, f"unknown component: {component}")


class TimingGateStabilityRequest(BaseModel):
    cases: list[dict] = Field(default_factory=list, max_length=10)
    runs: int = Field(default=20, ge=1, le=20)


@router.post("/models/timing-gate-stability-test")
async def timing_gate_stability_test(body: TimingGateStabilityRequest, _auth=Depends(verify_admin)):
    """TimingGate JSON 稳定性测试——连续跑 N 次，统计 parse_error 和延迟分布。

    不会写入真实 TimingGate 记录。
    """
    import time
    from clients.classifier_client import get_timing_gate

    gate = get_timing_gate()
    all_results: list[dict] = []
    default_cases = [
        {"name": "普通玩梗", "context": "<recent>\n[用户A]: 笑死我了\n[用户B]: 哈哈哈哈\n</recent>", "pending_count": 2},
        {"name": "技术求助", "context": "<recent>\n[用户C]: 问一下这个报错怎么修\n[用户D]: 贴代码看看\n</recent>", "pending_count": 1},
        {"name": "连续发材料", "context": "<recent>\n[用户G]: 等下我发日志\n[用户G]: 还有一张图\n</recent>", "pending_count": 2},
        {"name": "直接叫bot", "context": "<recent>\n[用户E]: @bot 你在吗\n</recent>", "pending_count": 1},
        {"name": "群命令", "context": "<recent>\n[用户F]: /status\n</recent>", "pending_count": 0},
    ]
    cases = body.cases if body.cases else default_cases

    for case in cases:
        name = case.get("name", "unknown")
        base_context = str(case.get("context", ""))
        pending = int(case.get("pending_count", 0))
        runs_list: list[dict] = []
        parse_errors = 0
        latencies: list[float] = []
        actions: dict[str, int] = {}
        errors: dict[str, int] = {}
        raw_samples: list[str] = []

        # 构造接近真实 TimingGate 输入的 context
        timing_context = (
            f"<timing_context>\n"
            f"pending_count: {pending}\n"
            f"talk_value: {case.get('talk_value', 0.5)}\n"
            f"msg_1m: {case.get('msg_1m', 0)}\n"
            f"msg_5m: {case.get('msg_5m', 0)}\n"
            f"{base_context}\n"
            f"</timing_context>"
        )

        for i in range(body.runs):
            t0 = time.time()
            result = await asyncio.to_thread(gate.judge, timing_context)
            lat = time.time() - t0
            latencies.append(lat)

            action = result.get("action", "no_reply")
            error_type = result.get("error_type")
            if error_type == "parse_error":
                parse_errors += 1
            actions[action] = actions.get(action, 0) + 1
            errors[error_type or "none"] = errors.get(error_type or "none", 0) + 1
            runs_list.append({
                "index": i,
                "action": action,
                "reason": result.get("reason", ""),
                "delay": result.get("delay_seconds"),
                "error_type": error_type,
                "latency_ms": int(lat * 1000),
            })

            if i < 3:
                raw_samples.append((result.get("raw") or "")[:300])

        n = body.runs
        all_results.append({
            "name": name,
            "run_count": n,
            "parse_error_count": parse_errors,
            "parse_error_ratio": round(parse_errors / n, 3),
            "avg_latency_ms": int(sum(latencies) / len(latencies) * 1000),
            "action_dist": actions,
            "error_dist": errors,
            "runs": runs_list,
            "raw_samples": raw_samples,
        })

    total_errors = sum(r["parse_error_count"] for r in all_results)
    total_runs = len(cases) * body.runs
    return {
        "dry_run": True,
        "cases": all_results,
        "overall_parse_error_count": total_errors,
        "overall_parse_error_ratio": round(total_errors / total_runs, 3) if total_runs else 0,
    }


# ── Model Health Check ──

@router.post("/models/health-check")
async def model_health_check(_auth=Depends(verify_admin)):
    """探测三个实际模型路由，返回结构化可达性与可用性。"""
    import aiohttp
    from clients import classifier_client
    from core import model_route_health

    targets = (
        ("new_api", "reply"),
        ("classifier", "timing_gate"),
        ("image_summary", "sticker_describe"),
    )
    results: dict[str, dict[str, object]] = {}
    async with aiohttp.ClientSession() as session:
        for name, route_key in targets:
            try:
                route = classifier_client.resolve_model_route(route_key)
                health = await model_route_health.probe_model_route(route, session)
            except Exception:
                health = model_route_health.ModelRouteHealth(
                    "network_error", False, False, None, 0
                )
            results[name] = health.as_dict()

    return {"endpoints": results}
