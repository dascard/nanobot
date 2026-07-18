"""
New-api OpenAI-compatible inference client.
Supports retry with exponential backoff, streaming, and token usage tracking.
"""
import asyncio
import aiohttp
import hashlib
import json
import logging
import re
import time
import os
import fnmatch
from contextlib import asynccontextmanager
from typing import Any
from collections.abc import AsyncIterator, Mapping

from config import (
    NEW_API_BASE_URL,
    LLM_BUDGET_CAP,
    NEW_API_AUTO_MODEL_SYNC,
    NEW_API_MODEL_SYNC_INTERVAL_MINUTES,
)
from clients.model_registry import (
    ModelFailureTracker,
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
_RAW_BODY_PREVIEW_LIMIT = 4096


class _InvalidModelJSON(ValueError):
    def __init__(self, message: str, audit: dict[str, Any]):
        super().__init__(message)
        self.audit = audit


def _raw_body_audit(raw_body: bytes) -> dict[str, Any]:
    text = raw_body.decode("utf-8", errors="replace")
    return {
        "raw_body_preview": text[:_RAW_BODY_PREVIEW_LIMIT],
        "raw_body_chars": len(text),
        "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
    }


def _validate_chat_completion_payload(
    payload: Any,
    *,
    raw_body: bytes,
) -> dict[str, Any]:
    """校验 HTTP 200 的 Chat Completions 根契约，避免把畸形响应记为成功。"""

    if not isinstance(payload, dict):
        reason = "root must be an object"
    else:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            reason = "choices must be a non-empty list"
        elif not isinstance(choices[0], dict):
            reason = "choices[0] must be an object"
        else:
            message = choices[0].get("message")
            if not isinstance(message, dict):
                reason = "choices[0].message must be an object"
            else:
                content = message.get("content")
                tool_calls = message.get("tool_calls")
                finish_reason = choices[0].get("finish_reason")
                reasoning_content = message.get("reasoning_content")
                reasoning = message.get("reasoning")
                usage = payload.get("usage")
                valid_content = isinstance(content, str)
                valid_tool_calls = _valid_completion_tool_calls(tool_calls)
                if "content" in message and content is not None and not valid_content:
                    reason = "choices[0].message.content must be a string or null"
                elif tool_calls is not None and not valid_tool_calls:
                    reason = "choices[0].message.tool_calls must be a non-empty object list"
                elif not valid_content and not valid_tool_calls:
                    reason = "choices[0].message must contain content or tool_calls"
                elif finish_reason is not None and not isinstance(finish_reason, str):
                    reason = "choices[0].finish_reason must be a string or null"
                elif reasoning_content is not None and not isinstance(reasoning_content, str):
                    reason = (
                        "choices[0].message.reasoning_content must be a string or null"
                    )
                elif reasoning is not None and not isinstance(reasoning, str):
                    reason = "choices[0].message.reasoning must be a string or null"
                elif usage is not None and not isinstance(usage, Mapping):
                    reason = "usage must be an object or null"
                else:
                    return payload
    raise _InvalidModelJSON(
        f"model response invalid completion contract: {reason}",
        _raw_body_audit(raw_body),
    )


def _valid_completion_tool_calls(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            return False
        if item.get("type") != "function":
            return False
        function = item.get("function")
        if not isinstance(function, dict):
            return False
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name.strip():
            return False
        if not isinstance(arguments, str):
            return False
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed_arguments, dict):
            return False
    return True


async def _read_response_json(response: Any) -> Any:
    read = getattr(response, "read", None)
    if not callable(read):
        payload = await response.json()
        raw_body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return _validate_chat_completion_payload(payload, raw_body=raw_body)
    raw_body = await read()
    if isinstance(raw_body, str):
        raw_bytes = raw_body.encode("utf-8")
    elif isinstance(raw_body, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(raw_body)
    else:
        payload = await response.json()
        raw_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return _validate_chat_completion_payload(payload, raw_body=raw_bytes)
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _InvalidModelJSON(
            f"model response invalid JSON: {exc}",
            _raw_body_audit(raw_bytes),
        ) from exc
    return _validate_chat_completion_payload(payload, raw_body=raw_bytes)


def messages_have_image_url(messages: list[dict[str, Any]]) -> bool:
    for message in messages or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def required_capabilities_for_request(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
) -> dict[str, bool]:
    required: dict[str, bool] = {}
    if messages_have_image_url(messages):
        required["supports_image"] = True
    if tools:
        required["supports_tools"] = True
    if stream:
        required["supports_stream"] = True
    return required


def validate_payload_capabilities(
    *,
    model_info: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
) -> None:
    if not model_info:
        return
    required = required_capabilities_for_request(messages, tools=tools, stream=stream)
    missing = [
        field
        for field, required_value in required.items()
        if required_value and not model_supports_capabilities(model_info, {field: True})
    ]
    if missing:
        model_id = str(model_info.get("id") or "(unknown)")
        raise ValueError(
            f"model lacks required capabilities: {model_id} missing={','.join(missing)}"
        )


class NewAPIClient:
    _last_model_sync_ts: float | None = None  # lazy-init from runtime_state
    _model_sync_lock = asyncio.Lock()
    _model_overrides_cache: dict[str, Any] | None = None
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
    def _session_usable_in_current_loop(cls, session: aiohttp.ClientSession | None) -> bool:
        if session is None or getattr(session, "closed", False):
            return False
        session_loop = getattr(session, "_loop", None)
        if session_loop is None:
            return True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        is_closed = getattr(session_loop, "is_closed", None)
        if callable(is_closed) and is_closed():
            return False
        return session_loop is current_loop

    @classmethod
    def get_failure_tracker(cls) -> "ModelFailureTracker":
        if cls._failure_tracker is None:
            from core.settings_service import settings
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
        timeout: int | None = None,
        max_retries: int = 3,
        registry_provider: str = "new-api",
        session: aiohttp.ClientSession | None = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or NEW_API_BASE_URL).rstrip("/")
        self.registry_provider = (registry_provider or "new-api").strip() or "new-api"
        self._timeout_override = timeout
        self.max_retries = max_retries
        self.last_usage: dict[str, int] = {}
        self._session = session

    @property
    def _timeout(self) -> int:
        return self._timeout_override or _new_api_timeout()

    @asynccontextmanager
    async def _request_session(self) -> AsyncIterator[aiohttp.ClientSession]:
        session = self._session
        if self.__class__._session_usable_in_current_loop(session):
            yield session
            return

        shared_session = self.__class__._shared_session
        if self.__class__._session_usable_in_current_loop(shared_session):
            yield shared_session
            return

        async with aiohttp.ClientSession() as session:
            yield session

    @classmethod
    def _load_model_overrides(cls) -> dict[str, Any]:
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

    def _infer_model_profile(self, model_id: str) -> dict[str, Any]:
        mid = (model_id or "").lower()
        tags: list[str] = []
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

    def _apply_model_override(self, model_id: str, base: dict[str, Any]) -> dict[str, Any]:
        base = normalize_model_record(base)
        overrides = self._load_model_overrides()
        if not overrides:
            return base

        candidates: list[str] = []
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

    def _build_description(self, model_id: str, tags: list[str]) -> str:
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

    async def fetch_models(self) -> list[dict[str, Any]]:
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
                    models: list[dict[str, Any]] = []
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

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        stream: bool,
        model: str,
        max_tokens: int | None = None,
        enable_thinking: Any = "auto",
        model_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
        validate_payload_capabilities(
            model_info=model_info,
            messages=payload["messages"],
            tools=payload.get("tools"),
            stream=bool(payload.get("stream")),
        )
        return apply_enable_thinking_to_payload(payload, model, enable_thinking)

    def _safe_get_failure_tracker(self):
        try:
            return self.get_failure_tracker()
        except Exception as e:
            logger.warning(f"failure tracker unavailable: {e}")
            return None

    # ── New Model Router ──

    def estimate_complexity(self, messages: list[dict[str, Any]],
                            tools: list[dict[str, Any]] | None = None) -> int:
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
        if length > 400:
            score += 1
        if length > 800:
            score += 1
        if length > 1800:
            score += 1
        if has_tools:
            score += 1

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
                               exclude_models: list[str] | None = None,
                               max_cost: float | None = None,
                               avoid_tags: list[str] | None = None,
                               required_capabilities: dict[str, bool] | None = None,
                               ) -> list[dict[str, Any]]:
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

    def resolve_model(self, messages: list[dict[str, Any]],
                      tools: list[dict[str, Any]] | None = None,
                      manual_model: str = "",
                      exclude_models: list[str] | None = None,
                      ) -> str:
        """Single entry point: complexity → intel_floor → ordered candidates → first healthy."""
        circuit_disabled_manual = ""
        if manual_model:
            tracker = self._safe_get_failure_tracker()
            if tracker is None or tracker.sync_is_disabled(manual_model) is not True:
                return manual_model
            circuit_disabled_manual = manual_model
            logger.warning(
                "Manual model circuit-disabled, falling back to healthy candidates: %s",
                manual_model,
            )

        complexity = self.estimate_complexity(messages, tools)
        intel_floor = max(1, complexity - 1)
        effective_excludes = list(exclude_models or [])
        if circuit_disabled_manual:
            effective_excludes.append(circuit_disabled_manual)

        candidates = self.get_ordered_candidates(
            provider=self.registry_provider,
            intel_floor=intel_floor,
            exclude_models=effective_excludes,
            avoid_tags=None,
        )

        if not candidates:
            logger.warning("No healthy model candidates available")
            return ""

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
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        model_tier: str = "smart",
        manual_model: str = "",
        max_tokens: int | None = None,
        trace_id: str = "",
        run_id: str = "",
        llm_source: str = "",
        enable_thinking: Any = "auto",
    ) -> dict[str, Any]:
        """Non-streaming chat completion with retry."""
        if not self.api_key:
            return {"error": "NEW_API_KEY is missing"}

        await self.sync_models_to_registry(force=False)

        complexity = self.estimate_complexity(messages, tools)
        required_capabilities = required_capabilities_for_request(
            messages,
            tools=tools,
            stream=False,
        )
        if manual_model:
            info = registry.get_model_info(manual_model)
            if info and info.get("enabled", True) is False:
                return {"error": f"Model disabled: {manual_model}"}
            if info and not model_supports_capabilities(info, required_capabilities):
                return {"error": f"Model lacks required capabilities: {manual_model}"}
            manual_candidate = dict(info or {})
            manual_candidate.setdefault("id", manual_model)
            manual_candidate.setdefault("intelligence", 0)
            manual_candidate.setdefault("cost_input_1m", 0.0)
            candidates = [manual_candidate]
        else:
            intel_floor = max(1, complexity - 1)
            candidates = self.get_ordered_candidates(
                provider=self.registry_provider,
                intel_floor=intel_floor,
                required_capabilities=required_capabilities,
            )
            if not candidates:
                return {"error": "No candidates available"}

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        last_error: str | None = None
        tracker = self._safe_get_failure_tracker()
        attempted_models: list[str] = []
        attempt_log_ids: list[int] = []

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
                if target_model and target_model not in attempted_models:
                    attempted_models.append(target_model)
                payload = self._build_payload(
                    messages,
                    tools,
                    temperature,
                    False,
                    target_model,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                    model_info=model,
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
                if int(log_id or 0) > 0:
                    attempt_log_ids.append(int(log_id))
                async with self._request_session() as session:
                    try:
                        async with session.post(
                            url, headers=headers, json=payload,
                            timeout=aiohttp.ClientTimeout(total=self._timeout),
                        ) as resp:
                            if resp.status == 200:
                                try:
                                    result = await _read_response_json(resp)
                                except _InvalidModelJSON as exc:
                                    last_error = str(exc)
                                    try:
                                        from core.tracing import LLMRequestTracer
                                        LLMRequestTracer.finish_request(
                                            log_id=log_id,
                                            response=exc.audit,
                                            response_status=resp.status,
                                            status="error",
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
                                    logger.warning(
                                        "new-api: %s returned invalid JSON, %s",
                                        target_model,
                                        "retrying" if attempt + 1 < _attempts else "switching",
                                    )
                                    if attempt + 1 < _attempts:
                                        await asyncio.sleep(1)
                                        continue
                                    break
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
                                result["_nanobot_requested_model"] = target_model
                                result["_nanobot_request_log_id"] = (
                                    int(log_id) if int(log_id or 0) > 0 else None
                                )
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
                                logger.warning(
                                    "new-api: %s status=%s, retrying",
                                    target_model,
                                    resp.status,
                                )
                                await asyncio.sleep(1)
                                continue  # retry same model
                            else:
                                logger.warning(
                                    "new-api: %s status=%s failed, switching",
                                    target_model,
                                    resp.status,
                                )
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
                        logger.warning(
                            "new-api network error: model=%s error_type=%s, switching",
                            target_model,
                            type(e).__name__,
                        )
                        break  # network error → next model immediately

        return {
            "error": "AllModelsFailed",
            "detail": last_error or "Unknown",
            "_nanobot_requested_models": attempted_models,
            "_nanobot_request_log_ids": list(dict.fromkeys(attempt_log_ids)),
        }

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model_tier: str = "smart",
        manual_model: str = "",
        max_tokens: int | None = None,
        trace_id: str = "",
        run_id: str = "",
        llm_source: str = "",
        enable_thinking: Any = "auto",
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming chat completion. Yields parsed SSE chunks."""
        if not self.api_key:
            yield {"error": "NEW_API_KEY is missing"}
            return

        await self.sync_models_to_registry(force=False)

        complexity = self.estimate_complexity(messages, tools)
        required_capabilities = required_capabilities_for_request(
            messages,
            tools=tools,
            stream=True,
        )
        if manual_model:
            info = registry.get_model_info(manual_model)
            if info and info.get("enabled", True) is False:
                yield {"error": f"Model disabled: {manual_model}"}
                return
            if info and not model_supports_capabilities(info, required_capabilities):
                yield {"error": f"Model lacks required capabilities: {manual_model}"}
                return
            target_model_info = dict(info or {})
            target_model_info.setdefault("id", manual_model)
            target_model = manual_model
        else:
            intel_floor = max(1, complexity - 1)
            candidates = self.get_ordered_candidates(
                provider=self.registry_provider,
                intel_floor=intel_floor,
                required_capabilities=required_capabilities,
            )
            if candidates:
                target_model_info = candidates[0]
                target_model = str(target_model_info.get("id", ""))
            else:
                yield {"error": "No candidates available"}
                return

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
            model_info=target_model_info,
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


def format_openai_messages(system_prompt: str, persona: str, context: str, query: str) -> list[dict[str, str]]:
    full_system = f"{system_prompt}\n\n[USER PERSONA]\n{persona}"
    return [
        {"role": "system", "content": full_system},
        {"role": "user", "content": f"[HISTORY]\n{context}\n\n[USER QUERY]\n{query}"},
    ]
