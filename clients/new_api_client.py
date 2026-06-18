"""
New-api OpenAI-compatible inference client.
Supports retry with exponential backoff, streaming, and token usage tracking.
"""
import asyncio
import aiohttp
import json
import logging
import re
import time
import os
import fnmatch
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, AsyncIterator

from config import (
    NEW_API_BASE_URL,
    LLM_BUDGET_CAP,
    NEW_API_AUTO_MODEL_SYNC,
    NEW_API_MODEL_SYNC_INTERVAL_MINUTES,
    AUTO_MODEL_ROUTING_MODE,
)
from clients.model_registry import (
    registry,
    model_cost_value,
    model_intelligence_value,
    model_supports_capabilities,
    normalize_model_record,
)
from core.final_tools import filter_payload_tools
from core.llm_stream_trace import LLMStreamTraceAccumulator
from core.llm_request_sanitizer import sanitize_payload_messages
from core.model_route_options import apply_enable_thinking_to_payload

logger = logging.getLogger("nanobot.new_api")

def _new_api_timeout():
    from core.settings_service import settings
    return settings.get_int("new_api.timeout", 180)

MODEL_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "data", "model_overrides.json")

# Retryable HTTP status codes
_RETRYABLE_STATUS = {429, 502, 503, 504}


class NewAPIClient:
    _last_model_sync_ts: float | None = None  # lazy-init from runtime_state
    _model_sync_lock = asyncio.Lock()
    _model_overrides_cache: Dict[str, Any] | None = None
    _failure_tracker: "ModelFailureTracker | None" = None
    _background_tasks: set[asyncio.Task] = set()
    _shared_session: aiohttp.ClientSession | None = None

    @classmethod
    def _track_background_task(cls, awaitable, *, label: str = "background") -> asyncio.Task:
        task = asyncio.create_task(awaitable, name=f"new-api:{label}")
        cls._background_tasks.add(task)

        def _done(done: asyncio.Task) -> None:
            cls._background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                logger.debug("NewAPI background task cancelled: %s", label)
            except Exception as exc:
                logger.warning(
                    "NewAPI background task failed [%s]: %s",
                    label,
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_done)
        return task

    @classmethod
    def set_shared_session(cls, session: aiohttp.ClientSession | None) -> None:
        cls._shared_session = session

    @classmethod
    def get_failure_tracker(cls) -> "ModelFailureTracker":
        if cls._failure_tracker is None:
            from core.settings_service import settings
            from clients.model_registry import ModelFailureTracker
            cls._failure_tracker = ModelFailureTracker(
                max_failures=settings.get_int("model.max_consecutive_failures", 3),
                cooldown_base_s=settings.get_int("model.cooldown_base_seconds", 300),
                cooldown_max_s=settings.get_int("model.cooldown_max_seconds", 1800),
            )
        return cls._failure_tracker

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        timeout: Optional[int] = None,
        max_retries: int = 3,
        registry_provider: str = "new-api",
        session: aiohttp.ClientSession | None = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or NEW_API_BASE_URL).rstrip("/")
        self.registry_provider = (registry_provider or "new-api").strip() or "new-api"
        self._timeout_override = timeout
        self.max_retries = max_retries
        self.last_usage: Dict[str, int] = {}
        self._session = session

    @property
    def _timeout(self) -> int:
        return self._timeout_override or _new_api_timeout()

    @asynccontextmanager
    async def _request_session(self) -> AsyncIterator[aiohttp.ClientSession]:
        session = self._session or self.__class__._shared_session
        if session is not None:
            yield session
            return
        async with aiohttp.ClientSession() as session:
            yield session

    @classmethod
    def _load_model_overrides(cls) -> Dict[str, Any]:
        if cls._model_overrides_cache is not None:
            return cls._model_overrides_cache

        try:
            if os.path.exists(MODEL_OVERRIDES_PATH):
                with open(MODEL_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        cls._model_overrides_cache = raw
                        return raw
        except Exception as e:
            logger.warning(f"Failed to load model overrides: {e}")

        cls._model_overrides_cache = {}
        return cls._model_overrides_cache

    def _infer_model_profile(self, model_id: str) -> Dict[str, Any]:
        mid = (model_id or "").lower()
        tags: List[str] = []
        is_free = mid.endswith(":free") or mid.endswith("-free")

        if any(k in mid for k in ["reason", "o1", "r1", "think"]):
            tags.extend(["reasoning", "analysis"])
        if any(k in mid for k in ["code", "coder"]):
            tags.extend(["coding", "tool_use"])
        if any(k in mid for k in ["mini", "flash", "lite", "turbo"]):
            tags.extend(["fast", "cheap"])
        if any(k in mid for k in ["vision", "vl", "omni"]):
            tags.extend(["vision", "multimodal"])
        if any(k in mid for k in ["qwen", "glm", "yi", "deepseek", "kimi", "claude", "gpt", "gemini", "gemma"]):
            tags.append("general")

        if is_free:
            tags.append("free")

        tier = "smart"
        if "reasoning" in tags:
            tier = "reasoning"
        elif "fast" in tags:
            tier = "fast"

        tags_list = sorted(set(tags)) or ["general"]
        desc = self._build_description(model_id, tags_list)

        if "reasoning" in tags:
            intelligence = 9
        elif "fast" in tags:
            intelligence = 6
        else:
            intelligence = 7

        return {
            "tier": tier,
            "tags": tags_list,
            "description": desc,
            "intelligence": intelligence,
            "supports_image": "vision" in tags_list or "multimodal" in tags_list,
            "supports_tools": True,
            "supports_stream": True,
        }

    def _apply_model_override(self, model_id: str, base: Dict[str, Any]) -> Dict[str, Any]:
        base = normalize_model_record(base)
        overrides = self._load_model_overrides()
        if not overrides:
            return base

        candidates: List[str] = []
        candidates.append(model_id)

        # Strip provider prefix (e.g. "deepseek/deepseek-v4-pro-free" → "deepseek-v4-pro-free")
        if "/" in model_id:
            bare = model_id.split("/")[-1]
        else:
            bare = model_id

        # Build candidate forms: with/without :free and -free
        for mid in {model_id, bare}:
            candidates.append(mid)
            if mid.endswith(":free"):
                candidates.append(mid[:-5])
                candidates.append(f"{mid[:-5]}-free")
            elif mid.endswith("-free"):
                candidates.append(mid[:-5])
                candidates.append(f"{mid[:-5]}:free")
            else:
                candidates.append(f"{mid}:free")
                candidates.append(f"{mid}-free")

        lower_model_id = model_id.lower()
        wildcard_keys = [k for k in overrides.keys() if "*" in k]
        for pattern in wildcard_keys:
            if fnmatch.fnmatch(lower_model_id, pattern.lower()):
                candidates.append(pattern)

        override = None
        for key in candidates:
            val = overrides.get(key)
            if isinstance(val, dict):
                override = val
                break

        if not override:
            return base

        merged = dict(base)
        merged.update(override)
        if "tags" in base and "tags" in override and isinstance(base["tags"], list) and isinstance(override["tags"], list):
            merged["tags"] = sorted(set([*base["tags"], *override["tags"]]))
        # Clean up contradictory free/paid tags and regenerate description
        final_tags = merged.get("tags", [])
        if isinstance(final_tags, list):
            if "free" in final_tags and "paid" in final_tags:
                final_tags.remove("paid")
                merged["tags"] = final_tags
            # Regenerate description to match final tag state
            merged["description"] = self._build_description(model_id, final_tags)
        return normalize_model_record(merged, fallback=base)

    def _build_description(self, model_id: str, tags: List[str]) -> str:
        """Build a human-readable description from model ID and tags."""
        mid = (model_id or "").lower()
        parts = mid.replace(":free", "").replace("-free", "").replace("/", " ").replace("-", " ").split()
        family = next((p.capitalize() for p in parts if p in ["deepseek", "qwen", "gemma", "nemotron", "gpt", "claude", "kimi", "glm", "yi"]), "")
        variant = next((p.upper() for p in parts if p in ["flash", "pro", "max", "mini", "lite", "turbo", "high"]), "")
        size = next((p.upper() for p in parts if p.endswith("b") and p[:-1].isdigit()), "")
        model_type = next((p.capitalize() for p in parts if p in ["coder", "oss", "reasoning"]), "")

        desc_parts = [family] if family else [model_id]
        if size:
            desc_parts.append(size)
        if variant:
            desc_parts.append(variant)
        if model_type:
            desc_parts.append(model_type)
        is_free = "free" in tags
        desc_parts.append("(free)" if is_free else "(paid)")
        return " ".join(desc_parts)

    async def fetch_models(self) -> List[Dict[str, Any]]:
        """从 new-api `/models` 拉取模型列表。"""
        if not self.api_key:
            return []

        url = f"{self.base_url}/models"
        headers = self._build_headers()
        async with self._request_session() as session:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=min(self._timeout, 60)),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"new-api model list failed: status={resp.status}")
                        return []
                    payload = await resp.json()
                    items = payload.get("data", []) if isinstance(payload, dict) else []
                    logger.info(f"new-api /models returned {len(items)} model entries")
                    # Log first item keys for debugging what fields the gateway provides
                    if items:
                        first = items[0]
                        logger.debug(f"new-api /models first item keys: {list(first.keys())}, sample={ {k: first[k] for k in list(first.keys())[:5]} }")
                    models: List[Dict[str, Any]] = []
                    free_count = 0
                    for item in items:
                        model_id = item.get("id")
                        if not model_id:
                            continue
                        profile = self._infer_model_profile(str(model_id))
                        is_free = "free" in profile["tags"]

                        # Use API-provided metadata when available, fall back to inferred values
                        api_desc = (item.get("description") or "").strip()
                        api_owned_by = (item.get("owned_by") or "").strip()
                        # Only trust API pricing if explicitly present (0.0 is falsy, so use `in` check)
                        api_cost_input = item.get("cost_input_1m") if "cost_input_1m" in item else None
                        if api_cost_input is None and isinstance(item.get("pricing"), dict):
                            api_cost_input = item["pricing"].get("input")

                        base_model = {
                            "id": model_id,
                            "provider": self.registry_provider,
                            "intelligence": profile["intelligence"],
                            "cost_input_1m": api_cost_input if api_cost_input is not None else (0.0 if is_free else 9.99),
                            "cost_output_1m": (0.0 if is_free else 9.99),
                            "tier": profile["tier"],
                            "tags": profile["tags"],
                            "description": api_desc or profile["description"],
                            "reasoning": api_owned_by or "Auto-discovered from new-api /models",
                        }
                        models.append(self._apply_model_override(str(model_id), base_model))

                    tiers = {}
                    for m in models:
                        t = m.get("tier", "unknown")
                        tiers[t] = tiers.get(t, 0) + 1
                    logger.info(
                        f"fetch_models: {len(models)} models parsed "
                        f"(free={free_count}, paid={len(models) - free_count}), "
                        f"tiers={tiers}"
                    )
                    return models
            except asyncio.TimeoutError:
                logger.warning("new-api model list timed out")
                return []
            except aiohttp.ClientError as e:
                logger.warning(f"new-api model list network error: {e}")
                return []

    async def sync_models_to_registry(self, force: bool = False) -> int:
        """按时间窗自动同步模型列表到本地 registry。"""
        if not NEW_API_AUTO_MODEL_SYNC and not force:
            return 0

        from clients.model_registry import runtime_state as _rs

        interval_sec = max(60, NEW_API_MODEL_SYNC_INTERVAL_MINUTES * 60)
        now = time.time()

        # Load last sync time from persisted state (survives restarts)
        if self.__class__._last_model_sync_ts is None:
            async with self.__class__._model_sync_lock:
                if self.__class__._last_model_sync_ts is None:
                    saved = await _rs.get("last_model_sync_ts", 0)
                    self.__class__._last_model_sync_ts = saved

        if not force and now - self.__class__._last_model_sync_ts < interval_sec:
            return 0

        async with self.__class__._model_sync_lock:
            now = time.time()
            if not force and now - self.__class__._last_model_sync_ts < interval_sec:
                return 0

            logger.info(f"Starting model sync from {self.base_url}/models (force={force})")
            models = await self.fetch_models()
            if not models:
                logger.warning("Model sync: no models fetched from API")
                self.__class__._last_model_sync_ts = now
                return 0

            updated = registry.add_or_update_many(models)
            self.__class__._last_model_sync_ts = now
            # Persist sync timestamp so restart doesn't re-sync
            self.__class__._track_background_task(
                _rs.set("last_model_sync_ts", now),
                label="persist_model_sync_ts",
            )

            # Post-sync summary: list all models by tier with free/paid breakdown
            all_models = registry.get_models_by_provider(self.registry_provider)
            tiers = {}
            free_ids = []
            for m in all_models:
                t = m.get("tier", "unknown")
                if t not in tiers:
                    tiers[t] = {"free": 0, "paid": 0}
                if "free" in (m.get("tags") or []):
                    tiers[t]["free"] += 1
                    free_ids.append(m.get("id"))
                else:
                    tiers[t]["paid"] += 1

            tier_summary = ", ".join(
                f"{t}={c['free']}F/{c['paid']}P" for t, c in sorted(tiers.items())
            )
            logger.info(
                f"Model sync complete: {updated} models processed, "
                f"total={len(all_models)}, tiers=[{tier_summary}], "
                f"free_models={free_ids}"
            )
            return updated

    def _resolve_model(self, _model_tier: str = "", manual_model: str = "") -> str:
        """Simple fallback: cheapest available model."""
        if manual_model:
            return manual_model
        models = registry.get_models_by_provider(self.registry_provider)
        if models:
            models.sort(key=lambda m: model_cost_value(m.get("cost_input_1m")))
            return models[0]["id"]
        return "gpt-4o"

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        stream: bool,
        model: str,
        max_tokens: int | None = None,
        enable_thinking: Any = "auto",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload = sanitize_payload_messages(payload)
        payload = filter_payload_tools(payload)
        return apply_enable_thinking_to_payload(payload, model, enable_thinking)

    def _safe_get_failure_tracker(self):
        try:
            return self.get_failure_tracker()
        except Exception as e:
            logger.warning(f"failure tracker unavailable: {e}")
            return None

    # ── New Model Router ──

    def estimate_complexity(self, messages: List[Dict[str, Any]],
                            tools: Optional[List[Dict[str, Any]]] = None) -> int:
        """Return complexity 1-10. Used to derive the intel_floor."""
        user_text = "\n".join(
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "user"
        ).lower()
        text = re.sub(r'https?://\S+', '', user_text)
        length = len(text)
        has_tools = bool(tools)

        score = 2  # baseline
        if length > 400:   score += 1
        if length > 800:   score += 1
        if length > 1800:  score += 1
        if has_tools:      score += 1

        hard_markers = ["设计", "证明", "推导", "架构", "审计", "优化",
                        "debug", "reason", "analyze", "复杂", "proof"]
        if any(k in text for k in hard_markers):
            score += 2

        coding_markers = ["代码", "code", "python", "sql", "bug",
                          "javascript", "typescript", "前端", "后端"]
        if any(k in text for k in coding_markers):
            score += 1

        easy_markers = ["翻译", "润色", "摘要", "改写", "hello",
                        "hi", "你好", "解释一下"]
        if any(k in text for k in easy_markers) and length < 300:
            score -= 1

        return max(1, min(10, score))

    def get_ordered_candidates(self, provider: str, intel_floor: int,
                               exclude_models: Optional[List[str]] = None,
                               max_cost: Optional[float] = None,
                               avoid_tags: Optional[List[str]] = None,
                               required_capabilities: Optional[Dict[str, bool]] = None,
                               ) -> List[Dict[str, Any]]:
        """Return healthy models ordered by priority.

        Phase 1: models meeting intel_floor, priority-score first.
        Phase 2: lower-intelligence fallback models, priority-score first.
        """
        all_models = registry.get_models_by_provider(provider)
        if not all_models:
            return []

        exclude_lower = [em.lower() for em in (exclude_models or [])]
        avoid_tags_set = set(t.lower() for t in (avoid_tags or []))
        tracker = self._safe_get_failure_tracker()
        max_cost_val = model_cost_value(
            max_cost if max_cost is not None else LLM_BUDGET_CAP,
            default=LLM_BUDGET_CAP,
        )

        candidates = []
        for m in all_models:
            mid = m.get("id", "")
            raw_tags = m.get("tags") or []
            tags = [str(t).lower() for t in raw_tags] if isinstance(raw_tags, list) else []
            if mid.lower() in exclude_lower:
                continue
            if "unstable" in tags:
                continue
            if m.get("enabled", True) is False:
                continue
            if avoid_tags_set and any(at in tags for at in avoid_tags_set):
                continue
            if not model_supports_capabilities(m, required_capabilities):
                continue
            if model_cost_value(m.get("cost_input_1m")) > max_cost_val:
                continue
            if tracker is not None and tracker.sync_is_disabled(mid):
                continue
            candidates.append(m)

        if not candidates:
            return []

        qualified = [
            m for m in candidates
            if model_intelligence_value(m.get("intelligence")) >= intel_floor
        ]
        fallback = [
            m for m in candidates
            if model_intelligence_value(m.get("intelligence")) < intel_floor
        ]

        qualified.sort(key=lambda m: registry.compute_priority_score(m))
        fallback.sort(key=lambda m: registry.compute_priority_score(m))
        return qualified + fallback

    def resolve_model(self, messages: List[Dict[str, Any]],
                      tools: Optional[List[Dict[str, Any]]] = None,
                      manual_model: str = "",
                      exclude_models: Optional[List[str]] = None,
                      ) -> str:
        """Single entry point: complexity → intel_floor → ordered candidates → first healthy."""
        if manual_model:
            return manual_model

        complexity = self.estimate_complexity(messages, tools)
        intel_floor = max(1, complexity - 1)

        candidates = self.get_ordered_candidates(
            provider=self.registry_provider,
            intel_floor=intel_floor,
            exclude_models=exclude_models,
            avoid_tags=None,
        )

        if not candidates:
            all_models = registry.get_models_by_provider(self.registry_provider)
            all_models.sort(key=lambda m: model_cost_value(m.get("cost_input_1m")))
            if all_models:
                fallback = all_models[0]["id"]
                logger.warning(f"No healthy candidates, using cheapest: {fallback}")
                return fallback
            return "gpt-4o"

        selected = candidates[0]
        logger.info(
            f"Model resolved: {selected['id']} "
            f"(complexity={complexity}, intel_floor={intel_floor}, "
            f"intel={selected.get('intelligence')}, "
            f"cost={selected.get('cost_input_1m')})"
        )
        return selected["id"]

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        stream: bool = False,
        model_tier: str = "smart",
        manual_model: str = "",
        max_tokens: int | None = None,
        trace_id: str = "",
        run_id: str = "",
        llm_source: str = "",
        enable_thinking: Any = "auto",
    ) -> Dict[str, Any]:
        """Non-streaming chat completion with retry."""
        if not self.api_key:
            return {"error": "NEW_API_KEY is missing"}

        await self.sync_models_to_registry(force=False)

        complexity = self.estimate_complexity(messages, tools)
        if manual_model:
            info = registry.get_model_info(manual_model)
            if info and info.get("enabled", True) is False:
                return {"error": f"Model disabled: {manual_model}"}
            candidates = [{
                "id": manual_model,
                "intelligence": 0,
                "cost_input_1m": 0.0,
            }]
        else:
            intel_floor = max(1, complexity - 1)
            candidates = self.get_ordered_candidates(
                provider=self.registry_provider,
                intel_floor=intel_floor,
            )
            if not candidates:
                return {"error": "No candidates available"}

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        last_error: Optional[str] = None
        tracker = self._safe_get_failure_tracker()

        # Iterate candidates in priority order, with configurable retry per model.
        # 429 → switch immediately; 502/503/504 → retry then switch.
        from core.settings_service import settings
        _attempts = max(1, settings.get_int("new_api.max_retries", 3))
        for i, model in enumerate(candidates):
            target_model = str(model.get("id", ""))
            if i == 0:
                logger.info(f"chat_completion: {target_model} "
                            f"(complexity={complexity}, intel={model.get('intelligence')})")

            for attempt in range(_attempts):  # per-model retry from settings
                payload = self._build_payload(
                    messages,
                    tools,
                    temperature,
                    False,
                    target_model,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                )
                started = time.time()
                log_id = 0
                # 兜底从 contextvars 读取 trace 上下文
                _trace_id = trace_id
                _run_id = run_id
                _source = llm_source
                if not _trace_id or not _run_id or not _source:
                    try:
                        from core.llm_trace_context import get_llm_trace_vars
                        _t, _r, _s = get_llm_trace_vars()
                        _trace_id = _trace_id or _t
                        _run_id = _run_id or _r
                        _source = _source or _s
                    except Exception:
                        pass
                try:
                    from core.tracing import LLMRequestTracer
                    log_id = LLMRequestTracer.record_request(
                        trace_id=_trace_id,
                        run_id=_run_id,
                        source=_source or "unknown",
                        provider=self.registry_provider,
                        model=target_model,
                        url=url,
                        method="POST",
                        headers=headers,
                        request=payload,
                        status="created",
                    )
                except Exception as _e:
                    logger.warning("record llm api request failed: %s", _e)
                async with self._request_session() as session:
                    try:
                        async with session.post(
                            url, headers=headers, json=payload,
                            timeout=aiohttp.ClientTimeout(total=self._timeout),
                        ) as resp:
                            if resp.status == 200:
                                result = await resp.json()
                                try:
                                    from core.tracing import LLMRequestTracer
                                    LLMRequestTracer.finish_request(
                                        log_id=log_id,
                                        response=result,
                                        response_status=resp.status,
                                        status="success",
                                        latency_ms=int((time.time() - started) * 1000),
                                    )
                                except Exception as _e:
                                    logger.warning("finish llm api request failed: %s", _e)
                                if tracker is not None:
                                    self.__class__._track_background_task(
                                        tracker.record_success(target_model),
                                        label="record_success",
                                    )
                                self.last_usage = result.get("usage", {})
                                result["_nanobot_model_id"] = target_model
                                result["_nanobot_complexity"] = complexity
                                return result

                            detail = await resp.text()
                            last_error = f"API Error {resp.status}: {detail[:200]}"
                            try:
                                from core.tracing import LLMRequestTracer
                                LLMRequestTracer.finish_request(
                                    log_id=log_id,
                                    response={"detail": detail[:4000]},
                                    response_status=resp.status,
                                    status="failed",
                                    error=last_error,
                                    latency_ms=int((time.time() - started) * 1000),
                                )
                            except Exception as _e:
                                logger.warning("finish llm api request failed: %s", _e)
                            if tracker is not None:
                                self.__class__._track_background_task(
                                    tracker.record_failure(target_model),
                                    label="record_failure",
                                )

                            if resp.status == 429:
                                logger.warning(f"new-api: {target_model} 429 rate-limited, switching")
                                break  # break inner loop, move to next model
                            elif attempt == 0:
                                logger.warning(f"new-api: {target_model} {resp.status}, retrying ({last_error})")
                                await asyncio.sleep(1)
                                continue  # retry same model
                            else:
                                logger.warning(f"new-api: {target_model} failed ({last_error}), switching")
                                break  # exhausted retries, move to next model

                    except asyncio.TimeoutError as e:
                        last_error = f"timeout: {e}"
                        try:
                            from core.tracing import LLMRequestTracer
                            LLMRequestTracer.finish_request(
                                log_id=log_id,
                                response={},
                                response_status=0,
                                status="error",
                                error=last_error,
                                latency_ms=int((time.time() - started) * 1000),
                            )
                        except Exception:
                            pass
                        if tracker is not None:
                            self.__class__._track_background_task(
                                tracker.record_failure(target_model),
                                label="record_failure",
                            )
                        logger.warning(f"new-api timeout: {target_model}, switching")
                        break
                    except aiohttp.ClientError as e:
                        last_error = str(e)
                        try:
                            from core.tracing import LLMRequestTracer
                            LLMRequestTracer.finish_request(
                                log_id=log_id,
                                response={},
                                response_status=0,
                                status="error",
                                error=last_error,
                                latency_ms=int((time.time() - started) * 1000),
                            )
                        except Exception:
                            pass
                        if tracker is not None:
                            self.__class__._track_background_task(
                                tracker.record_failure(target_model),
                                label="record_failure",
                            )
                        logger.warning(f"new-api network error: {target_model}, {e}, switching")
                        break  # network error → next model immediately

        return {"error": "AllModelsFailed", "detail": last_error or "Unknown"}

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        model_tier: str = "smart",
        manual_model: str = "",
        max_tokens: int | None = None,
        trace_id: str = "",
        run_id: str = "",
        llm_source: str = "",
        enable_thinking: Any = "auto",
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming chat completion. Yields parsed SSE chunks."""
        if not self.api_key:
            yield {"error": "NEW_API_KEY is missing"}
            return

        await self.sync_models_to_registry(force=False)

        complexity = self.estimate_complexity(messages, tools)
        if manual_model:
            info = registry.get_model_info(manual_model)
            if info and info.get("enabled", True) is False:
                yield {"error": f"Model disabled: {manual_model}"}
                return
            target_model = manual_model
        else:
            intel_floor = max(1, complexity - 1)
            candidates = self.get_ordered_candidates(
                provider=self.registry_provider,
                intel_floor=intel_floor,
            )
            if candidates:
                target_model = str(candidates[0].get("id", ""))
            else:
                target_model = self._resolve_model(model_tier, manual_model)

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        payload = self._build_payload(
            messages,
            tools,
            temperature,
            True,
            target_model,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        )
        started = time.time()
        log_id = 0
        # 记录 stream LLM API 请求
        _trace_id = trace_id
        _run_id = run_id
        _source = llm_source
        if not _trace_id or not _run_id or not _source:
            try:
                from core.llm_trace_context import get_llm_trace_vars
                _t, _r, _s = get_llm_trace_vars()
                _trace_id = _trace_id or _t
                _run_id = _run_id or _r
                _source = _source or _s
            except Exception:
                pass
        try:
            from core.tracing import LLMRequestTracer
            log_id = LLMRequestTracer.record_request(
                trace_id=_trace_id,
                run_id=_run_id,
                source=_source or "unknown",
                provider=self.registry_provider,
                model=target_model,
                url=url,
                method="POST",
                headers=headers,
                request=payload,
                status="created",
            )
        except Exception as _e:
            logger.warning("record llm api stream request failed: %s", _e)
        tracker = self._safe_get_failure_tracker()

        async with self._request_session() as session:
            try:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    if resp.status != 200:
                        detail = await resp.text()
                        try:
                            from core.tracing import LLMRequestTracer
                            LLMRequestTracer.finish_request(
                                log_id=log_id,
                                response={"detail": detail[:4000]},
                                response_status=resp.status,
                                status="failed",
                                error=f"API Error {resp.status}",
                                latency_ms=int((time.time() - started) * 1000),
                            )
                        except Exception:
                            pass
                        if tracker is not None:
                            self.__class__._track_background_task(
                                tracker.record_failure(target_model),
                                label="stream_record_failure",
                            )
                        yield {"error": f"API Error {resp.status}", "detail": detail}
                        return

                    stream_trace = LLMStreamTraceAccumulator(started=started)
                    async for line in resp.content:
                        line_str = line.decode("utf-8").strip()
                        if not line_str or not line_str.startswith("data: "):
                            continue
                        data_str = line_str[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            # Track usage from final chunk if present
                            if chunk.get("usage"):
                                self.last_usage = chunk["usage"]
                                if tracker is not None:
                                    self.__class__._track_background_task(
                                        tracker.record_success(target_model),
                                        label="stream_record_success",
                                    )
                            stream_trace.record_chunk(chunk)
                            yield chunk
                        except json.JSONDecodeError:
                            continue
                    try:
                        from core.tracing import LLMRequestTracer
                        LLMRequestTracer.finish_request(
                            log_id=log_id,
                            response=stream_trace.build_response(),
                            response_status=resp.status,
                            status="stream_success",
                            latency_ms=int((time.time() - started) * 1000),
                        )
                    except Exception:
                        pass

            except asyncio.TimeoutError:
                logger.error("new-api stream timed out")
                try:
                    from core.tracing import LLMRequestTracer
                    LLMRequestTracer.finish_request(
                        log_id=log_id,
                        response={},
                        response_status=0,
                        status="stream_error",
                        error="stream timed out",
                        latency_ms=int((time.time() - started) * 1000),
                    )
                except Exception:
                    pass
                if tracker is not None:
                    self.__class__._track_background_task(
                        tracker.record_failure(target_model),
                        label="stream_record_failure",
                    )
                yield {"error": "Timeout", "detail": "stream timed out"}
            except aiohttp.ClientError as e:
                logger.error(f"new-api stream error: {e}")
                try:
                    from core.tracing import LLMRequestTracer
                    LLMRequestTracer.finish_request(
                        log_id=log_id,
                        response={},
                        response_status=0,
                        status="stream_error",
                        error=str(e),
                        latency_ms=int((time.time() - started) * 1000),
                    )
                except Exception:
                    pass
                if tracker is not None:
                    self.__class__._track_background_task(
                        tracker.record_failure(target_model),
                        label="stream_record_failure",
                    )
                yield {"error": "NetworkError", "detail": str(e)}


def format_openai_messages(system_prompt: str, persona: str, context: str, query: str) -> List[Dict[str, str]]:
    full_system = f"{system_prompt}\n\n[USER PERSONA]\n{persona}"
    return [
        {"role": "system", "content": full_system},
        {"role": "user", "content": f"[HISTORY]\n{context}\n\n[USER QUERY]\n{query}"},
    ]
