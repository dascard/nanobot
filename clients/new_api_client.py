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
from typing import Dict, Any, List, Optional, AsyncIterator

from config import (
    NEW_API_BASE_URL,
    NEW_API_TIMEOUT,
    LLM_BUDGET_CAP,
    NEW_API_AUTO_MODEL_SYNC,
    NEW_API_MODEL_SYNC_INTERVAL_MINUTES,
    AUTO_MODEL_ROUTING_MODE,
)
from clients.model_registry import registry

logger = logging.getLogger("nanobot.new_api")

MODEL_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "data", "model_overrides.json")

# Retryable HTTP status codes
_RETRYABLE_STATUS = {429, 502, 503, 504}


class NewAPIClient:
    _last_model_sync_ts: float = 0.0
    _model_sync_lock = asyncio.Lock()
    _model_overrides_cache: Dict[str, Any] | None = None

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model_map: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = (base_url or NEW_API_BASE_URL).rstrip("/")
        self.timeout = timeout or NEW_API_TIMEOUT
        self.max_retries = max_retries

        avoid_tags = ["unstable"]
        self.model_map = {
            "smart": registry.select_model("new-api", "smart", max_cost=LLM_BUDGET_CAP, avoid_tags=avoid_tags) or "gpt-4o",
            "fast": registry.select_model("new-api", "fast", max_cost=LLM_BUDGET_CAP, avoid_tags=avoid_tags) or "gpt-4o-mini",
            "reasoning": registry.select_model("new-api", "reasoning", max_cost=LLM_BUDGET_CAP, avoid_tags=avoid_tags) or "o1-mini",
        }
        if model_map:
            for key, value in model_map.items():
                if value:
                    self.model_map[key] = value

        # Token usage tracking (updated after each call)
        self.last_usage: Dict[str, int] = {}

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
        if any(k in mid for k in ["qwen", "glm", "yi", "deepseek", "kimi", "claude", "gpt", "gemini"]):
            tags.append("general")

        if is_free:
            tags.append("free")

        tier = "smart"
        if "reasoning" in tags:
            tier = "reasoning"
        elif "fast" in tags:
            tier = "fast"

        # Generate per-model description from ID parts
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
        desc_parts.append("(free)" if is_free else "(paid)")
        desc = " ".join(desc_parts)

        if "reasoning" in tags:
            intelligence = 9
        elif "fast" in tags:
            intelligence = 6
        else:
            intelligence = 7

        return {
            "tier": tier,
            "tags": sorted(set(tags)) or ["general"],
            "description": desc,
            "intelligence": intelligence,
        }

    def _apply_model_override(self, model_id: str, base: Dict[str, Any]) -> Dict[str, Any]:
        overrides = self._load_model_overrides()
        if not overrides:
            return base

        candidates: List[str] = []
        candidates.append(model_id)

        # Treat `xxx` and `xxx:free` as alias forms.
        if model_id.endswith(":free"):
            candidates.append(model_id[:-5])
        else:
            candidates.append(f"{model_id}:free")

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
        return merged

    async def fetch_models(self) -> List[Dict[str, Any]]:
        """从 new-api `/models` 拉取模型列表。"""
        if not self.api_key:
            return []

        url = f"{self.base_url}/models"
        headers = self._build_headers()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=min(self.timeout, 60)),
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
                            "provider": "new-api",
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
            except aiohttp.ClientError as e:
                logger.warning(f"new-api model list network error: {e}")
                return []

    async def sync_models_to_registry(self, force: bool = False) -> int:
        """按时间窗自动同步模型列表到本地 registry。"""
        if not NEW_API_AUTO_MODEL_SYNC and not force:
            return 0

        interval_sec = max(60, NEW_API_MODEL_SYNC_INTERVAL_MINUTES * 60)
        now = time.time()
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

            # Post-sync summary: list all models by tier with free/paid breakdown
            all_models = registry.get_models_by_provider("new-api")
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

    def _resolve_model(self, model_tier: str, manual_model: str = "") -> str:
        return manual_model or self.model_map.get(model_tier, self.model_map["smart"])

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
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _route_model_tier(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], requested_tier: str) -> str:
        """根据任务复杂度自动路由 tier。"""
        mode = (AUTO_MODEL_ROUTING_MODE or "off").lower().strip()
        if mode == "off":
            return requested_tier

        # 当前版本：code heuristic；model planner 后续扩展。
        if mode not in {"code", "model"}:
            return requested_tier

        user_text = "\n".join(
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "user"
        ).lower()
        has_tools = bool(tools)

        hard_markers = ["设计", "证明", "推导", "架构", "审计", "优化", "debug", "reason", "analyze", "复杂"]
        easy_markers = ["翻译", "润色", "摘要", "改写", "hello", "解释一下"]

        if any(k in user_text for k in hard_markers) or (has_tools and len(user_text) > 800):
            return "reasoning"
        # Strip URLs before measuring text length so link-heavy messages
        # don't falsely trigger the reasoning threshold.
        text_without_urls = re.sub(r'https?://\S+', '', user_text)
        if len(text_without_urls) > 1800:
            return "reasoning"
        if any(k in user_text for k in easy_markers) and len(text_without_urls) < 400 and not has_tools:
            return "fast"
        return "smart"

    def _infer_task_tags(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> List[str]:
        text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user").lower()
        tags: List[str] = ["general"]

        if tools:
            tags.append("tool_use")
        if any(k in text for k in ["代码", "code", "debug", "bug", "sql", "python"]):
            tags.append("coding")
        if any(k in text for k in ["分析", "proof", "推导", "reason", "架构", "审计", "优化"]):
            tags.append("reasoning")
        if any(k in text for k in ["总结", "summary", "摘要", "提炼"]):
            tags.append("summarization")
        if any(k in text for k in ["翻译", "translate"]):
            tags.append("translation")

        return sorted(set(tags))

    def _resolve_model_for_task(self, model_tier: str, task_tags: Optional[List[str]] = None, manual_model: str = "", exclude_models: Optional[List[str]] = None) -> str:
        if manual_model:
            return manual_model

        avoid_tags = ["unstable"]
        task_tags = task_tags or []

        logger.debug(
            f"_resolve_model_for_task: tier={model_tier}, task_tags={task_tags}, "
            f"exclude={exclude_models}, budget={LLM_BUDGET_CAP}"
        )

        selected = registry.select_model(
            provider="new-api",
            tier=model_tier,
            max_cost=LLM_BUDGET_CAP,
            required_tags=task_tags,
            avoid_tags=avoid_tags,
            exclude_models=exclude_models,
        )
        if selected:
            logger.info(f"Model resolved: {selected} (tier={model_tier})")
            return selected

        # Fallback without required tags but still avoid unstable models.
        selected = registry.select_model(
            provider="new-api",
            tier=model_tier,
            max_cost=LLM_BUDGET_CAP,
            avoid_tags=avoid_tags,
            exclude_models=exclude_models,
        )
        if selected:
            logger.info(f"Model resolved (fallback, no required tags): {selected} (tier={model_tier})")
            return selected
        fallback = self._resolve_model(model_tier, manual_model="")
        logger.warning(f"Model resolution fell back to hardcoded default: {fallback}")
        return fallback

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        stream: bool = False,
        model_tier: str = "smart",
        manual_model: str = "",
    ) -> Dict[str, Any]:
        """Non-streaming chat completion with retry."""
        if not self.api_key:
            return {"error": "NEW_API_KEY is missing"}

        await self.sync_models_to_registry(force=False)

        routed_tier = self._route_model_tier(messages, tools, model_tier)
        task_tags = self._infer_task_tags(messages, tools)
        target_model = self._resolve_model_for_task(routed_tier, task_tags=task_tags, manual_model=manual_model)
        logger.info(
            f"chat_completion routing: requested_tier={model_tier}, "
            f"routed_tier={routed_tier}, task_tags={task_tags}, "
            f"target_model={target_model}"
        )
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        payload = self._build_payload(messages, tools, temperature, False, target_model)

        last_error: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as resp:
                        if resp.status in _RETRYABLE_STATUS and attempt < self.max_retries:
                            detail = await resp.text()
                            delay = min(2 ** attempt, 16)
                            logger.warning(
                                f"new-api retryable error: status={resp.status}, "
                                f"attempt={attempt}/{self.max_retries}, retry in {delay}s"
                            )
                            last_error = f"API Error {resp.status}: {detail[:200]}"
                            await asyncio.sleep(delay)
                            continue

                        if resp.status != 200:
                            detail = await resp.text()
                            logger.error(f"new-api error: status={resp.status}, detail={detail}")
                            return {"error": f"API Error {resp.status}", "detail": detail}

                        result = await resp.json()
                        # Track token usage
                        self.last_usage = result.get("usage", {})
                        result["_nanobot_model_tier"] = routed_tier
                        result["_nanobot_model_id"] = target_model
                        result["_nanobot_task_tags"] = task_tags
                        return result

                except aiohttp.ClientError as e:
                    last_error = str(e)
                    if attempt < self.max_retries:
                        delay = min(2 ** attempt, 16)
                        logger.warning(f"new-api network error (attempt {attempt}): {e}, retry in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"new-api network error (final): {e}")
                    return {"error": "NetworkError", "detail": str(e)}

        return {"error": "MaxRetriesExceeded", "detail": last_error or "Unknown"}

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        model_tier: str = "smart",
        manual_model: str = "",
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming chat completion. Yields parsed SSE chunks."""
        if not self.api_key:
            yield {"error": "NEW_API_KEY is missing"}
            return

        target_model = self._resolve_model(model_tier, manual_model)
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        payload = self._build_payload(messages, tools, temperature, True, target_model)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        detail = await resp.text()
                        yield {"error": f"API Error {resp.status}", "detail": detail}
                        return

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
                            yield chunk
                        except json.JSONDecodeError:
                            continue

            except aiohttp.ClientError as e:
                logger.error(f"new-api stream error: {e}")
                yield {"error": "NetworkError", "detail": str(e)}


def format_openai_messages(system_prompt: str, persona: str, context: str, query: str) -> List[Dict[str, str]]:
    full_system = f"{system_prompt}\n\n[USER PERSONA]\n{persona}"
    return [
        {"role": "system", "content": full_system},
        {"role": "user", "content": f"[HISTORY]\n{context}\n\n[USER QUERY]\n{query}"},
    ]
