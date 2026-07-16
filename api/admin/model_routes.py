"""Admin Models 路由。"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from core.database import SystemSetting, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-models"])


# ═══════════════════════════════════════════
# Model status / tests
# ═══════════════════════════════════════════

@router.get("/models/status")
def models_status(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from clients.classifier_client import (
        Guardrail, resolve_model_route, list_providers,
    )
    from config import NEW_API_BASE_URL, NEW_API_KEY, CLASSIFIER_API_URL

    # ── Providers (脱敏) ──
    from clients.classifier_client import provider_public
    raw_providers = list_providers()
    # 确保内置 provider 始终存在（即使无 DB 配置时通过 env fallback 出现）
    if not any(p["id"] == "newapi" for p in raw_providers):
        raw_providers.append({
            "id": "newapi", "base_url": str(NEW_API_BASE_URL or ""),
            "api_key": str(NEW_API_KEY or ""), "enabled": bool(NEW_API_BASE_URL),
        })
    if not any(p["id"] in ("local_llama", "local_qwen") for p in raw_providers):
        raw_providers.append({
            "id": "local_llama", "base_url": str(CLASSIFIER_API_URL or ""),
            "api_key": "", "enabled": bool(CLASSIFIER_API_URL),
        })
    providers = [provider_public(p) for p in raw_providers]

    # ── Routes ──
    from core.route_metadata import ROUTE_METADATA, route_label_for
    routes = {}
    for rk in ROUTE_METADATA:
        r = resolve_model_route(rk)
        entry = {
            "route_key": rk,
            "label": route_label_for(rk),
            "route_type": r.get("route_type", "unknown"),
            "provider_id": r["provider_id"],
            "model": r["model"],
            "api_key_configured": r["api_key_configured"],
            "route_api_key_configured": r.get("route_api_key_configured", False),
            "provider_enabled": r.get("provider_enabled", True),
            "timeout": r["timeout"], "temperature": r["temperature"],
            "max_tokens": r["max_tokens"],
            "enable_thinking": r.get("enable_thinking", "auto"),
            "source": r.get("source", "provider"),
            "editable": True,
        }
        if r.get("inherited_from"):
            entry["inherited_from"] = r["inherited_from"]
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

    return {
        "providers": providers,
        "routes": routes,
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
        })
    return {"models": models, "last_updated": registry.data.get("last_updated", "never")}


# ── Provider 管理 ──

@router.get("/models/providers")
def list_model_providers(_auth=Depends(verify_admin)):
    """列出所有已配置的供应商（api_key 脱敏）。"""
    from clients.classifier_client import list_providers, provider_public
    return {"providers": [provider_public(p) for p in list_providers()]}


class ProviderUpdateBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    registry_provider: str | None = None


@router.put("/models/providers/{provider_id}")
def update_model_provider(
    provider_id: str, body: ProviderUpdateBody,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """更新供应商配置——写入 SystemSetting。旧 provider 名自动 canonicalize。"""
    _ALLOWED_PROVIDERS = {"newapi", "local_llama", "local_vision", "local_qwen", "vision_qwen"}
    if provider_id not in _ALLOWED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider_id}")
    from core.settings_service import settings
    from core.route_metadata import canonical_provider_id

    raw_provider_id = provider_id
    provider_id = canonical_provider_id(provider_id)

    prefix = f"model.providers.{provider_id}"
    written = {}
    fields = {"base_url": body.base_url, "api_key": body.api_key}
    for field, value in fields.items():
        if value is None:
            continue
        key = f"{prefix}.{field}"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=str(value), description=f"provider {provider_id} {field}")
            db.add(row)
        else:
            row.value = str(value)
        written[key] = str(value)
    if body.enabled is not None:
        key = f"{prefix}.enabled"
        val = "1" if body.enabled else "0"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val, description=f"provider {provider_id} enabled")
            db.add(row)
        else:
            row.value = val
        written[key] = val
    if body.registry_provider is not None:
        key = f"{prefix}.registry_provider"
        val = str(body.registry_provider).strip()
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val, description=f"provider {provider_id} registry_provider")
            db.add(row)
        else:
            row.value = val
        written[key] = val
    db.commit()
    audit(db, "update_provider", "provider", provider_id, _redact(written), ip_address=client_ip(request))
    settings.invalidate()
    return {
        "ok": True,
        "provider_id": provider_id,
        "input_provider_id": raw_provider_id if raw_provider_id != provider_id else None,
        "version": settings.version,
    }


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
    """从各 provider 的 /models 端点刷新模型列表，持久化到 SystemSetting。"""
    import urllib.request as _ur
    from datetime import datetime, timezone
    from clients.classifier_client import list_providers, build_provider_catalog

    results = []
    for p in list_providers():
        base_url = p["base_url"].rstrip("/")
        if not base_url:
            continue
        if p.get("enabled") is False:
            results.append({"provider": p["id"], "models": [], "ok": False, "error": "provider disabled"})
            continue
        headers = {"Content-Type": "application/json"}
        if p.get("api_key"):
            headers["Authorization"] = f"Bearer {p['api_key']}"
        try:
            req = _ur.Request(f"{base_url}/models", headers=headers, method="GET")
            proxy_handler = _ur.ProxyHandler({})
            opener = _ur.build_opener(proxy_handler)
            with opener.open(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            items = body.get("data", []) if isinstance(body, dict) else []
            models = sorted(set(m["id"] for m in items if isinstance(m, dict) and m.get("id")))
            key = f"model.catalog.{p['id']}"
            val = json.dumps({
                "models": models, "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_refresh_ok": True, "last_error": "",
            }, ensure_ascii=False)
            row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if not row:
                row = SystemSetting(key=key, value=val, description=f"model catalog for {p['id']}")
                db.add(row)
            else:
                row.value = val
            results.append({"provider": p["id"], "models": models, "ok": True})
        except Exception as e:
            key = f"model.catalog.{p['id']}"
            old_models = []
            old_row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if old_row:
                try:
                    old_models = json.loads(old_row.value or "{}").get("models", [])
                except Exception:
                    pass
            val = json.dumps({
                "models": old_models, "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_refresh_ok": False, "last_error": str(e)[:300],
            }, ensure_ascii=False)
            if not old_row:
                row = SystemSetting(key=key, value=val, description=f"model catalog for {p['id']}")
                db.add(row)
            else:
                old_row.value = val
            results.append({"provider": p["id"], "models": old_models, "ok": False, "error": str(e)[:300]})
    db.commit()
    return {"results": results, "catalog": build_provider_catalog(db)}


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
    "main_chat":       {"key": "model.reply",              "field": "model",   "env": "LLM_MODEL_REPLY"},
    "fast_chat":       {"key": "model.fast",               "field": "model",   "env": "LLM_MODEL_FAST"},
    "smart_chat":      {"key": "model.smart",              "field": "model",   "env": "LLM_MODEL_SMART"},
    "timing_gate":     {"key": "model.route.timing_gate",  "field": "api_url", "env": "CLASSIFIER_API_URL"},
    "sticker_describe":{"key": "model.route.sticker_describe","field": "api_url","env": "IMAGE_SUMMARY_API_URL"},
}


def _resolve_route_value(stage: str, db: Session) -> tuple[str, str, str]:
    """Return (value, source, is_overridden). source 准确反映值来源。"""
    from core.settings_service import settings
    meta = _STAGE_META[stage]
    key = meta["key"]

    # 检查 DB 是否有覆盖
    db_row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if db_row and db_row.value is not None:
        return db_row.value, "db_override", True

    # 没有 DB 覆盖，查 config 值
    env_name = meta["env"]
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

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        row = SystemSetting(key=key, value=body.value, description=defn.description)
        db.add(row)
    else:
        row.value = body.value
    db.commit()
    audit(db, "update_model_route", "route", stage, {"value": body.value},
           ip_address=client_ip(request))
    settings.invalidate()
    return {"ok": True, "stage": stage, "value": body.value, "version": settings.version}


# ── 模型路由编辑（完整字段）──

# route_key → setting prefix 映射（reply/fast/smart 使用 model.*；classifier 使用 model.route.*）
_ROUTE_SETTING_MAP: dict[str, str] = {
    "reply": "model.reply",
    "fast": "model.fast",
    "smart": "model.smart",
    "session_summary": "model.session_summary",
    "memory_digest": "model.memory_digest",
}
# classifier routes: route_key 直接对应 model.route.<key>
_CLASSIFIER_ROUTE_KEYS = {
    "timing_gate",
    "timing_proactive",
    "outreach_extract",
    "outreach_judge",
    "outreach_generate",
    "private_decision",
    "classifier_legacy",
    "sticker_describe",
}
# frontend 友好名称 → 后端 route_key
_ROUTE_ALIAS: dict[str, str] = {
    "vision": "sticker_describe",
}

_CHAT_ROUTES = {"reply", "fast", "smart", "session_summary", "memory_digest"}


def _resolve_route_key(route_key: str) -> tuple[str, str, bool]:
    """解析前端 route_key → (prefix, db_key, is_classifier)。

    返回 (setting_prefix, route_key_for_db, is_classifier_route)。
    """
    route_key = _ROUTE_ALIAS.get(route_key, route_key)
    if route_key in _CHAT_ROUTES:
        return _ROUTE_SETTING_MAP[route_key], route_key, False
    return f"model.route.{route_key}", route_key, True


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

    prefix, db_key, is_classifier = _resolve_route_key(route_key)
    if is_classifier:
        if db_key not in _CLASSIFIER_ROUTE_KEYS:
            raise HTTPException(404, f"unknown route: {route_key}")
    elif prefix not in SETTING_DEFS:
        raise HTTPException(404, f"unknown route: {route_key}")

    written = {}
    fields = {
        "provider": body.provider,
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

    prepared: list[tuple[str, str, str]] = []
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
        prepared.append((key, stored_value, desc))

    for key, stored_value, desc in prepared:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=stored_value, description=desc)
            db.add(row)
        else:
            row.value = stored_value
        written[key] = stored_value
    db.commit()
    audit(db, "edit_model_route", "route", route_key, _redact(written), ip_address=client_ip(request))
    settings.invalidate()
    # 清除 image_summary 30s route cache（invalidate 后清理，避免并发重建旧缓存）
    if db_key == "sticker_describe":
        try:
            from creatures.nanobot.prompts.skills.image_summary.tool import _get_image_summary_route
            if hasattr(_get_image_summary_route, "_cache"):
                delattr(_get_image_summary_route, "_cache")
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
    route_key = _ROUTE_ALIAS.get(route_key, route_key)
    route = resolve_model_route(route_key)
    try:
        ensure_model_route_enabled(route_key, route)
    except RuntimeError as e:
        return {"ok": False, "route_key": route_key, "error": str(e)[:500]}

    if route_key in _CHAT_ROUTES:
        from clients.new_api_client import NewAPIClient
        from nanobot_kt.bridge import _registry_provider_for_route
        model = route.get("model", "") or route_key
        client = NewAPIClient(
            api_key=route["api_key"],
            base_url=route["base_url"],
            registry_provider=_registry_provider_for_route(route.get("provider_id", "")),
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
    from nanobot_kt.bridge import _registry_provider_for_route

    route = resolve_model_route(route_key)
    return {
        "route_key": route_key,
        "provider_id": route.get("provider_id", ""),
        "registry_provider": _registry_provider_for_route(route.get("provider_id", "")),
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

    route_key = _ROUTE_ALIAS.get(route_key, route_key)

    from clients.classifier_client import resolve_model_route, _resolve_classifier_route
    effective_route_key = route_key or "timing_gate"
    if effective_route_key in _CHAT_ROUTES or effective_route_key in _CLASSIFIER_ROUTE_KEYS:
        route = resolve_model_route(effective_route_key)
    else:
        route = _resolve_classifier_route(effective_route_key)

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
