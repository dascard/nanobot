import json
import math
import os
import time
import asyncio
import logging
from typing import Any

from core.runtime_paths import RUNTIME_PATHS

logger = logging.getLogger("nanobot.registry")

_DATA_DIR = os.fspath(RUNTIME_PATHS.data_dir / "model_registry")
MODEL_SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "models.json")
MODEL_DATA_PATH = os.path.join(_DATA_DIR, "models.json")
_FAILURE_STATE_PATH = os.path.join(_DATA_DIR, "model_failures.json")
_RUNTIME_STATE_PATH = os.path.join(_DATA_DIR, "runtime_state.json")
UNKNOWN_MODEL_COST = 999.0
CAPABILITY_FIELDS = ("supports_image", "supports_tools", "supports_stream")
UNKNOWN_CAPABILITY_EVIDENCE = "unknown"


def model_cost_value(value: Any, default: float = UNKNOWN_MODEL_COST) -> float:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(cost) or cost < 0:
        return float(default)
    return cost


def model_intelligence_value(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(score):
        return float(default)
    return score


def model_routing_sort_key(
    model: dict[str, Any],
    *,
    intel_floor: int,
    configured_index: int = 0,
) -> tuple[int, float, int, float, int]:
    """质量分层后按总价、额外模态和智能度排序。

    低于质量门槛的免费模型以及显式 ``fallback_only`` 模型永远位于
    正常候选之后，只承担最后兜底。能力不兼容应在调用本函数前硬过滤。
    """

    intelligence = model_intelligence_value(model.get("intelligence"))
    input_cost = model_cost_value(model.get("cost_input_1m"))
    output_cost = model_cost_value(model.get("cost_output_1m"))
    tags = {str(item).strip().lower() for item in (model.get("tags") or [])}
    is_free = input_cost == 0 and output_cost == 0
    below_floor = intelligence < max(0, int(intel_floor))
    if bool(model.get("fallback_only")) or (is_free and below_floor):
        quality_bucket = 2
    elif below_floor:
        quality_bucket = 1
    else:
        quality_bucket = 0

    input_modalities = model.get("input_modalities")
    if not isinstance(input_modalities, (list, tuple)):
        input_modalities = ["text", "image"] if model.get("supports_image") else ["text"]
    output_modalities = model.get("output_modalities")
    if not isinstance(output_modalities, (list, tuple)):
        output_modalities = ["text"]
    modality_count = max(0, len(set(input_modalities)) - 1) + max(
        0, len(set(output_modalities)) - 1
    )
    if "audio" in tags and "audio" not in input_modalities:
        modality_count += 1
    if "video" in tags and "video" not in input_modalities:
        modality_count += 1
    return (
        quality_bucket,
        input_cost + output_cost,
        modality_count,
        -intelligence,
        configured_index,
    )


def normalize_model_cost_fields(
    model: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(model)
    for field in ("cost_input_1m", "cost_output_1m"):
        value = normalized.get(field)
        if value is None and fallback is not None:
            value = fallback.get(field)
        normalized[field] = model_cost_value(value)
    return normalized


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _model_tags(model: dict[str, Any]) -> list[str]:
    tags = model.get("tags") or []
    if not isinstance(tags, list):
        return []
    return [str(t).lower() for t in tags]


def normalize_model_capability_fields(
    model: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(model)
    nested = normalized.get("capabilities")
    nested_map = nested if isinstance(nested, dict) else {}
    raw_evidence = normalized.get("capability_evidence")
    evidence_map = raw_evidence if isinstance(raw_evidence, dict) else {}
    fallback_evidence_raw = (
        fallback.get("capability_evidence") if fallback is not None else {}
    )
    fallback_evidence = (
        fallback_evidence_raw
        if isinstance(fallback_evidence_raw, dict)
        else {}
    )
    normalized_evidence: dict[str, str] = {}

    for field in CAPABILITY_FIELDS:
        short = field.removeprefix("supports_")
        direct = field in normalized and normalized.get(field) is not None
        nested_present = (
            short in nested_map or field in nested_map
        ) and nested_map.get(short, nested_map.get(field)) is not None
        raw = normalized.get(field) if direct else None
        if raw is None and nested_present:
            raw = nested_map.get(short, nested_map.get(field))
        used_fallback = False
        if raw is None and fallback is not None:
            raw = fallback.get(field)
            used_fallback = raw is not None

        value = _coerce_optional_bool(raw)
        if value is None:
            value = False
        normalized[field] = value
        if direct or nested_present:
            source = str(evidence_map.get(field) or "explicit_descriptor")
        elif used_fallback:
            source = str(
                fallback_evidence.get(field) or "inherited_descriptor"
            )
        else:
            source = UNKNOWN_CAPABILITY_EVIDENCE
        normalized_evidence[field] = source[:80]

    normalized["capability_evidence"] = normalized_evidence

    return normalized


def normalize_model_record(
    model: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_model_cost_fields(model, fallback=fallback)
    return normalize_model_capability_fields(normalized, fallback=fallback)


def model_supports_capabilities(
    model: dict[str, Any],
    required_capabilities: dict[str, bool] | None = None,
) -> bool:
    if not required_capabilities:
        return True
    normalized = normalize_model_capability_fields(model)
    evidence = normalized.get("capability_evidence") or {}
    for field, required in required_capabilities.items():
        if not required:
            continue
        if (
            normalized.get(field) is not True
            or evidence.get(field) == UNKNOWN_CAPABILITY_EVIDENCE
        ):
            return False
    return True


def _atomic_write(path: str, data: Any) -> None:
    """Write JSON to tmp file then atomic rename — safe against SIGKILL."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_read(path: str) -> dict | None:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return None


class ModelFailureTracker:
    """Per-model circuit breaker with exponential-backoff cooldown + disk persistence."""

    def __init__(self, max_failures: int = 3,
                 cooldown_base_s: float = 300.0,
                 cooldown_max_s: float = 1800.0):
        self._failures: dict[str, int] = {}
        self._disabled_until: dict[str, float] = {}
        self._max_failures = max_failures
        self._cooldown_base = cooldown_base_s
        self._cooldown_max = cooldown_max_s
        self._lock = asyncio.Lock()
        self._load()

    # ── persistence ──

    def _load(self) -> None:
        data = _atomic_read(_FAILURE_STATE_PATH)
        if data:
            self._failures = data.get("failures", {})
            self._disabled_until = data.get("disabled_until", {})
            now = time.time()
            expired = [mid for mid, until in self._disabled_until.items() if now >= until]
            for mid in expired:
                del self._disabled_until[mid]
                self._failures.pop(mid, None)
            if expired:
                logger.info(f"Expired {len(expired)} stale cooldowns on load")
            if self._failures:
                logger.info(f"Loaded failure state: {len(self._failures)} models, "
                            f"{len(self._disabled_until)} disabled")

    def _save(self) -> None:
        _atomic_write(_FAILURE_STATE_PATH, {
            "failures": dict(self._failures),
            "disabled_until": dict(self._disabled_until),
        })

    # ── public API ──

    async def record_failure(self, model_id: str) -> None:
        async with self._lock:
            count = self._failures.get(model_id, 0) + 1
            self._failures[model_id] = count
            if count >= self._max_failures:
                extra = count - self._max_failures
                cooldown = min(self._cooldown_base * (2 ** extra), self._cooldown_max)
                self._disabled_until[model_id] = time.time() + cooldown
                logger.warning(f"Model [{model_id}] disabled for {cooldown:.0f}s (failure #{count})")
            self._save()

    async def record_success(self, model_id: str) -> None:
        async with self._lock:
            changed = self._failures.pop(model_id, None) is not None
            self._disabled_until.pop(model_id, None)
            if changed:
                self._save()

    def sync_is_disabled(self, model_id: str) -> bool:
        """Sync check (no lock). Expired cooldowns auto-clear."""
        until = self._disabled_until.get(model_id)
        if until is None:
            return False
        if time.time() >= until:
            del self._disabled_until[model_id]
            self._failures.pop(model_id, None)
            self._save()
            logger.info(f"Model [{model_id}] re-enabled after cooldown")
            return False
        return True

    async def is_disabled(self, model_id: str) -> bool:
        """Async check with lock."""
        async with self._lock:
            return self.sync_is_disabled(model_id)


class RuntimeState:
    """General runtime state persisted across restarts.

    Stores lightweight key-value pairs that should survive
    container restarts (model sync timestamps, usage counters, etc).
    Uses atomic writes — safe against SIGKILL during save.
    """

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        data = _atomic_read(_RUNTIME_STATE_PATH)
        if data:
            self._data = data
            logger.debug(f"Loaded runtime state: {list(data.keys())}")

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = value
            _atomic_write(_RUNTIME_STATE_PATH, self._data)

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return self._data.get(key, default)

    async def incr(self, key: str) -> int:
        async with self._lock:
            v = int(self._data.get(key, 0)) + 1
            self._data[key] = v
            _atomic_write(_RUNTIME_STATE_PATH, self._data)
            return v

    async def keys(self) -> list[str]:
        async with self._lock:
            return list(self._data.keys())


# Global runtime state singleton
runtime_state = RuntimeState()


class ModelRegistry:
    @staticmethod
    def compute_priority_score(model: dict, max_cost: float = 10.0,
                               max_intel: float = 15.0) -> float:
        """Lower score = higher priority (cheaper + more capable first).

        Formula: cost_weight * (cost/max_cost) - intel_weight * (intel/max_intel)
                 + free_bonus + unstable_penalty
        """
        from core.settings_service import settings
        cost = model_cost_value(model.get("cost_input_1m"))
        intel = model_intelligence_value(model.get("intelligence"))
        raw_tags = model.get("tags") or []
        tags = [str(t).lower() for t in raw_tags] if isinstance(raw_tags, list) else []

        cost_norm = cost / max(model_cost_value(max_cost, default=10.0), 1e-9)
        intel_norm = intel / max(model_intelligence_value(max_intel, default=15.0), 1e-9)
        free_b = settings.get_float("router.free_bonus", -2.0) if "free" in tags else 0.0
        unstable_p = settings.get_float("router.unstable_penalty", 5.0) if "unstable" in tags else 0.0

        return (settings.get_float("router.cost_weight", 6.0) * cost_norm
                - settings.get_float("router.intel_weight", 5.0) * intel_norm
                + free_b + unstable_p)

    def __init__(self):
        self.data: dict[str, Any] = {"models": [], "last_updated": "never"}
        self._load_registry()

    def _load_registry(self):
        try:
            load_path = (
                MODEL_DATA_PATH
                if os.path.exists(MODEL_DATA_PATH)
                else MODEL_SEED_PATH
            )
            if os.path.exists(load_path):
                with open(load_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content.strip():
                        self.data = json.loads(content)
                        source = (
                            "checked_in_catalog"
                            if load_path == MODEL_SEED_PATH
                            else "runtime_catalog"
                        )
                        for model in self.data.get("models", []):
                            if not isinstance(model, dict):
                                continue
                            if source == "checked_in_catalog":
                                model.setdefault("routing_verified", True)
                                model.setdefault(
                                    "routing_evidence", source
                                )
                            elif "routing_verified" not in model:
                                verified = bool(model.get("metadata_source"))
                                model["routing_verified"] = verified
                                model["routing_evidence"] = (
                                    "curated_metadata"
                                    if verified
                                    else "legacy_unverified"
                                )
                        self._log_all_models("loaded")
            else:
                logger.warning(
                    "Model registry file not found at %s or %s",
                    MODEL_DATA_PATH,
                    MODEL_SEED_PATH,
                )
        except Exception as e:
            logger.error(f"Failed to load model registry: {e}")

    def _log_all_models(self, event: str = "") -> None:
        """Log all registry models grouped by tier with key attributes."""
        models_list: list[dict[str, Any]] = self.data.get("models", [])
        if not models_list:
            logger.info(f"Model registry is empty (event={event})")
            return

        tiers: dict[str, list[dict[str, Any]]] = {}
        for m in models_list:
            t = m.get("tier", "unknown")
            tiers.setdefault(t, []).append(m)

        lines = [f"=== Model Registry ({event}) total={len(models_list)} ==="]
        for t in ["reasoning", "smart", "fast", "unknown"]:
            tier_models = tiers.pop(t, [])
            if not tier_models:
                continue
            lines.append(f"-- {t} ({len(tier_models)} models) --")
            for m in tier_models:
                tags = m.get("tags") or []
                is_free = "FREE" if "free" in tags else "paid"
                unstable = " [UNSTABLE]" if "unstable" in tags else ""
                desc = (m.get("description") or "").strip()
                desc_suffix = f" — {desc[:80]}" if desc else ""
                cost = model_cost_value(m.get("cost_input_1m"), default=0.0)
                lines.append(
                    f"  {m.get('id')} | intel={m.get('intelligence',0)} "
                    f"| cost=${cost:.2f}/1M "
                    f"| {is_free}{unstable}{desc_suffix}"
                )
        for t, tier_models in sorted(tiers.items()):
            if not tier_models:
                continue
            lines.append(f"-- {t} ({len(tier_models)} models) --")
            for m in tier_models:
                lines.append(f"  {m.get('id')}")

        for line in lines:
            logger.info(line)

    def get_models_by_provider(self, provider: str) -> list[dict[str, Any]]:
        models_list: list[dict[str, Any]] = self.data.get("models", [])
        return [m for m in models_list if m.get("provider") == provider]

    def select_model(self,
                     provider: str,
                     tier: str = "smart",
                     max_cost: float | None = None,
                     min_intelligence: int = 0,
                     required_tags: list[str] | None = None,
                     avoid_tags: list[str] | None = None,
                     exclude_models: list[str] | None = None,
                     prefer_free: bool = True) -> str | None:
        """
        根据厂商、层级、成本上限和最小智能得分选择模型。
        支持根据成本自动降级 (Smart -> Fast)
        """
        all_candidates = self.get_models_by_provider(provider)
        if not all_candidates:
            logger.warning(f"No models found for provider={provider}")
            return None

        # Tier progression: smart -> fast -> any
        tiers_to_try = [tier] if tier else ["smart", "fast"]
        if tier == "smart":
            tiers_to_try.append("fast")

        required_tags = [x.lower() for x in (required_tags or []) if x]
        avoid_tags = [x.lower() for x in (avoid_tags or []) if x]

        logger.debug(
            f"select_model: provider={provider}, tier={tier}, "
            f"required_tags={required_tags}, avoid_tags={avoid_tags}, "
            f"exclude={exclude_models}, max_cost={max_cost}, prefer_free={prefer_free}"
        )

        def _tags_of(m: dict[str, Any]) -> list[str]:
            tags = m.get("tags") or []
            if not isinstance(tags, list):
                return []
            return [str(t).lower() for t in tags]

        def _score(m: dict[str, Any]) -> tuple:
            tags = _tags_of(m)
            tag_hit = sum(1 for t in required_tags if t in tags)
            avoid_hit = sum(1 for t in avoid_tags if t in tags)
            intel = model_intelligence_value(m.get("intelligence"))
            cost = model_cost_value(m.get("cost_input_1m"))
            is_free = 1 if (prefer_free and "free" in tags) else 0
            # sort desc by tag/avoid/is_free/intelligence, asc by cost
            return (tag_hit, -avoid_hit, is_free, intel, -cost)

        for t in tiers_to_try:
            candidates = [m for m in all_candidates if m.get("tier") == t]
            logger.debug(f"select_model: tier={t}, candidates_before_filter={len(candidates)}")

            if exclude_models:
                exclude_lower = [em.lower() for em in exclude_models]
                candidates = [m for m in candidates if m.get("id", "").lower() not in exclude_lower]
                logger.debug(f"select_model: tier={t}, after_exclude={len(candidates)}")

            # Apply cost filter
            if max_cost is not None:
                candidates = [m for m in candidates if model_cost_value(m.get("cost_input_1m")) <= max_cost]
                logger.debug(f"select_model: tier={t}, after_cost_filter={len(candidates)}")

            # Apply intelligence filter
            if min_intelligence > 0:
                candidates = [m for m in candidates if m.get("intelligence", 0) >= min_intelligence]
                logger.debug(f"select_model: tier={t}, after_intel_filter={len(candidates)}")

            # WebUI 禁用的模型不参与路由
            candidates = [m for m in candidates if m.get("enabled", True) is not False]

            # Apply tag constraints (soft requirement if possible)
            if required_tags:
                tagged = [m for m in candidates if any(rt in _tags_of(m) for rt in required_tags)]
                if tagged:
                    candidates = tagged
                    logger.debug(f"select_model: tier={t}, required_tags matched={len(candidates)}")

            if avoid_tags:
                non_avoid = [m for m in candidates if not any(at in _tags_of(m) for at in avoid_tags)]
                if non_avoid:
                    candidates = non_avoid
                    logger.debug(
                        f"select_model: tier={t}, after_avoid_filter={len(candidates)}, "
                        f"excluded_models_with_avoid_tags={len([m for m in all_candidates if m.get('tier') == t]) - len(candidates)}"
                    )

            if candidates:
                # Found suitable candidates in this tier
                candidates.sort(key=_score, reverse=True)
                selected = candidates[0]

                # 跨层免费优先：当前层最优是付费的，检查其他层有无智力接近的免费模型
                if prefer_free and "free" not in _tags_of(selected):
                    sel_intel = model_intelligence_value(selected.get("intelligence"))
                    for c in all_candidates:
                        tags_c = _tags_of(c)
                        if "free" not in tags_c:
                            continue
                        if c.get("enabled", True) is False:
                            continue
                        if model_intelligence_value(c.get("intelligence")) < sel_intel - 1:
                            continue
                        if max_cost is not None and model_cost_value(c.get("cost_input_1m")) > max_cost:
                            continue
                        if exclude_models and c.get("id", "").lower() in (em.lower() for em in exclude_models):
                            continue
                        if avoid_tags and any(at in tags_c for at in avoid_tags):
                            continue
                        logger.info(
                            f"Model selected (cross-tier free): prefer {c.get('id')} "
                            f"over {selected.get('id')} (tier={t})"
                        )
                        selected = c
                        break

                selected_tags = _tags_of(selected)
                logger.info(
                    f"Model selected: id={selected.get('id')}, tier={t}, "
                    f"intelligence={selected.get('intelligence')}, "
                    f"cost_input_1m={selected.get('cost_input_1m')}, "
                    f"tags={selected_tags}, is_free={'free' in selected_tags}, "
                    f"candidates_considered={len(candidates)}"
                )
                return selected.get("id")

        # Ultimate Fallback: return the cheapest enabled model
        # 如果全部 disabled，则退回最便宜的（总得给手动指定留后路）
        enabled = [m for m in all_candidates if m.get("enabled", True) is not False]
        pool = enabled if enabled else all_candidates
        pool.sort(key=lambda x: model_cost_value(x.get("cost_input_1m")))
        cheap_model = pool[0]

        target_id = cheap_model.get("id")
        if max_cost is not None and model_cost_value(cheap_model.get("cost_input_1m")) > max_cost:
            logger.warning(f"No model found for {provider} under budget {max_cost}. Using cheapest: {target_id}")

        logger.info(
            f"Fallback model selected: id={target_id}, "
            f"cost_input_1m={cheap_model.get('cost_input_1m')}, "
            f"tier={cheap_model.get('tier')}"
        )
        return target_id

    def get_model_info(self, model_id: str) -> dict[str, Any] | None:
        models_list: list[dict[str, Any]] = self.data.get("models", [])
        for m in models_list:
            if m.get("id") == model_id:
                return m
        return None

    def save_registry(self):
        try:
            os.makedirs(os.path.dirname(MODEL_DATA_PATH), exist_ok=True)
            with open(MODEL_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logger.info(f"Registry saved to {MODEL_DATA_PATH}")
        except Exception as e:
            logger.error(f"Failed to save registry: {e}")

    def add_or_update_model(self, model_data: dict[str, Any]):
        model_data = normalize_model_record(model_data)
        model_id = model_data.get("id")
        if not model_id:
            return
        
        models_list = self.data.get("models", [])
        found = False
        for i, m in enumerate(models_list):
            if m.get("id") == model_id:
                models_list[i] = model_data
                found = True
                break
        
        if not found:
            models_list.append(model_data)
        
        self.data["models"] = models_list
        self.data["last_updated"] = __import__("datetime").datetime.now().isoformat()
        self.save_registry()

    def add_or_update_many(self, models: list[dict[str, Any]]) -> int:
        """批量更新模型，减少频繁磁盘写入。返回有效写入条数。"""
        if not models:
            return 0

        models_list = self.data.get("models", [])
        index = {m.get("id"): i for i, m in enumerate(models_list) if m.get("id")}

        new_count = 0
        updated_count = 0
        updated_ids = []
        new_ids = []

        for raw_model in models:
            model_id = raw_model.get("id")
            if not model_id:
                continue
            old = models_list[index[model_id]] if model_id in index else None
            m = normalize_model_record(raw_model, fallback=old)
            if model_id in index:
                # Check if anything changed
                changed = (
                    old.get("tier") != m.get("tier") or
                    old.get("intelligence") != m.get("intelligence") or
                    old.get("cost_input_1m") != m.get("cost_input_1m") or
                    old.get("cost_output_1m") != m.get("cost_output_1m") or
                    old.get("context_window") != m.get("context_window") or
                    old.get("supports_image") != m.get("supports_image") or
                    old.get("supports_tools") != m.get("supports_tools") or
                    old.get("supports_stream") != m.get("supports_stream") or
                    sorted(old.get("tags") or []) != sorted(m.get("tags") or [])
                )
                models_list[index[model_id]] = m
                if changed:
                    updated_count += 1
                    updated_ids.append(model_id)
            else:
                index[model_id] = len(models_list)
                models_list.append(m)
                new_count += 1
                new_ids.append(model_id)

        total = new_count + updated_count
        self.data["models"] = models_list
        self.data["last_updated"] = __import__("datetime").datetime.now().isoformat()

        if total > 0:
            logger.info(
                f"Registry batch update: {total} models processed "
                f"(new={new_count}, updated={updated_count}, unchanged={len(models) - total})"
            )
            if new_ids:
                logger.info(f"New models added: {new_ids}")
            if updated_ids:
                logger.info(f"Models updated: {updated_ids}")
            self._log_all_models("post-sync")

        self.save_registry()
        return total

    def replace_provider_models(
        self,
        provider: str,
        models: list[dict[str, Any]],
    ) -> int:
        """以一次上游目录快照替换指定 Provider，淘汰已下线模型。"""

        normalized_provider = str(provider or "").strip()
        if not normalized_provider:
            return 0

        existing = self.data.get("models", [])
        old_provider_models = {
            str(model.get("id")): model
            for model in existing
            if model.get("provider") == normalized_provider and model.get("id")
        }
        other_provider_models = [
            model
            for model in existing
            if model.get("provider") != normalized_provider
        ]

        replacement: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_model in models:
            model_id = str(raw_model.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            candidate = dict(raw_model)
            candidate["provider"] = normalized_provider
            replacement.append(normalize_model_record(
                candidate,
                fallback=old_provider_models.get(model_id),
            ))

        replacement.sort(key=lambda item: str(item.get("id") or ""))
        new_provider_models = {
            str(model.get("id")): model
            for model in replacement
        }
        added = sorted(set(new_provider_models) - set(old_provider_models))
        removed = sorted(set(old_provider_models) - set(new_provider_models))
        updated = sorted(
            model_id
            for model_id in set(old_provider_models) & set(new_provider_models)
            if old_provider_models[model_id] != new_provider_models[model_id]
        )

        self.data["models"] = other_provider_models + replacement
        self.data["last_updated"] = __import__("datetime").datetime.now().isoformat()
        self.save_registry()

        total = len(added) + len(updated) + len(removed)
        if total:
            logger.info(
                "Provider model snapshot replaced: provider=%s added=%s "
                "updated=%s removed=%s",
                normalized_provider,
                added,
                updated,
                removed,
            )
        return total

    def remove_model(self, model_id: str):
        models_list = self.data.get("models", [])
        self.data["models"] = [m for m in models_list if m.get("id") != model_id]
        self.save_registry()

# Global instance
registry = ModelRegistry()
