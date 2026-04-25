"""
New-api OpenAI-compatible inference client.
Supports retry with exponential backoff, streaming, and token usage tracking.
"""
import asyncio
import aiohttp
import json
import logging
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

        self.model_map = {
            "smart": registry.select_model("new-api", "smart", max_cost=LLM_BUDGET_CAP) or "gpt-4o",
            "fast": registry.select_model("new-api", "fast", max_cost=LLM_BUDGET_CAP) or "gpt-4o-mini",
            "reasoning": registry.select_model("new-api", "reasoning", max_cost=LLM_BUDGET_CAP) or "o1-mini",
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

        tier = "smart"
        if "reasoning" in tags:
            tier = "reasoning"
        elif "fast" in tags:
            tier = "fast"

        if "reasoning" in tags:
            desc = "Strong at long-chain reasoning and complex planning tasks"
            intelligence = 9
        elif "fast" in tags:
            desc = "Optimized for speed/cost and short straightforward tasks"
            intelligence = 6
        else:
            desc = "Balanced general-purpose model for everyday assistant tasks"
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
                    models: List[Dict[str, Any]] = []
                    for item in items:
                        model_id = item.get("id")
                        if not model_id:
                            continue
                        profile = self._infer_model_profile(str(model_id))
                        base_model = {
                            "id": model_id,
                            "provider": "new-api",
                            "intelligence": profile["intelligence"],
                            "cost_input_1m": 9.99,
                            "cost_output_1m": 9.99,
                            "tier": profile["tier"],
                            "tags": profile["tags"],
                            "description": profile["description"],
                            "reasoning": "Auto-discovered from new-api /models",
                        }
                        models.append(self._apply_model_override(str(model_id), base_model))
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

            models = await self.fetch_models()
            if not models:
                self.__class__._last_model_sync_ts = now
                return 0

            updated = registry.add_or_update_many(models)
            self.__class__._last_model_sync_ts = now
            if updated:
                logger.info(f"new-api model sync updated {updated} models")
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
        msg_len = len(user_text)
        has_tools = bool(tools)

        hard_markers = ["设计", "证明", "推导", "架构", "审计", "优化", "debug", "reason", "analyze", "复杂"]
        easy_markers = ["翻译", "润色", "摘要", "改写", "hello", "解释一下"]

        if any(k in user_text for k in hard_markers) or msg_len > 1800 or (has_tools and msg_len > 800):
            return "reasoning"
        if any(k in user_text for k in easy_markers) and msg_len < 400 and not has_tools:
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

    def _resolve_model_for_task(self, model_tier: str, task_tags: List[str], manual_model: str = "") -> str:
        if manual_model:
            return manual_model

        avoid_tags = ["unstable", "rate_limited", "limited"]

        selected = registry.select_model(
            provider="new-api",
            tier=model_tier,
            max_cost=LLM_BUDGET_CAP,
            required_tags=task_tags,
            avoid_tags=avoid_tags,
        )
        if selected:
            return selected

        # Fallback without required tags but still avoid unstable models.
        selected = registry.select_model(
            provider="new-api",
            tier=model_tier,
            max_cost=LLM_BUDGET_CAP,
            avoid_tags=avoid_tags,
        )
        if selected:
            return selected
        return self._resolve_model(model_tier, manual_model="")

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
