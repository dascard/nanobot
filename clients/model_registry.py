import json
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("nanobot.registry")

MODEL_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "models.json")

class ModelRegistry:
    def __init__(self):
        self.data: Dict[str, Any] = {"models": [], "last_updated": "never"}
        self._load_registry()

    def _load_registry(self):
        try:
            if os.path.exists(MODEL_DATA_PATH):
                with open(MODEL_DATA_PATH, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content.strip():
                        self.data = json.loads(content)
                        self._log_all_models("loaded")
            else:
                logger.warning(f"Model registry file not found at {MODEL_DATA_PATH}")
        except Exception as e:
            logger.error(f"Failed to load model registry: {e}")

    def _log_all_models(self, event: str = "") -> None:
        """Log all registry models grouped by tier with key attributes."""
        models_list: List[Dict[str, Any]] = self.data.get("models", [])
        if not models_list:
            logger.info(f"Model registry is empty (event={event})")
            return

        tiers: Dict[str, List[Dict[str, Any]]] = {}
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
                lines.append(
                    f"  {m.get('id')} | intel={m.get('intelligence',0)} "
                    f"| cost=${m.get('cost_input_1m',0):.2f}/1M "
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

    def get_models_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        models_list: List[Dict[str, Any]] = self.data.get("models", [])
        return [m for m in models_list if m.get("provider") == provider]

    def select_model(self,
                     provider: str,
                     tier: str = "smart",
                     max_cost: Optional[float] = None,
                     min_intelligence: int = 0,
                     required_tags: Optional[List[str]] = None,
                     avoid_tags: Optional[List[str]] = None,
                     exclude_models: Optional[List[str]] = None,
                     prefer_free: bool = True) -> Optional[str]:
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

        def _tags_of(m: Dict[str, Any]) -> List[str]:
            tags = m.get("tags") or []
            if not isinstance(tags, list):
                return []
            return [str(t).lower() for t in tags]

        def _score(m: Dict[str, Any]) -> tuple:
            tags = _tags_of(m)
            tag_hit = sum(1 for t in required_tags if t in tags)
            avoid_hit = sum(1 for t in avoid_tags if t in tags)
            intel = m.get("intelligence", 0)
            cost = m.get("cost_input_1m", 999)
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
                candidates = [m for m in candidates if m.get("cost_input_1m", 999) <= max_cost]
                logger.debug(f"select_model: tier={t}, after_cost_filter={len(candidates)}")

            # Apply intelligence filter
            if min_intelligence > 0:
                candidates = [m for m in candidates if m.get("intelligence", 0) >= min_intelligence]
                logger.debug(f"select_model: tier={t}, after_intel_filter={len(candidates)}")

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
                    sel_intel = selected.get("intelligence", 0)
                    for c in all_candidates:
                        tags_c = _tags_of(c)
                        if "free" not in tags_c:
                            continue
                        if c.get("intelligence", 0) < sel_intel - 1:
                            continue
                        if max_cost is not None and c.get("cost_input_1m", 999) > max_cost:
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

        # Ultimate Fallback: return the cheapest model if still no candidates
        all_candidates.sort(key=lambda x: x.get("cost_input_1m", 999))
        cheap_model = all_candidates[0]

        target_id = cheap_model.get("id")
        if max_cost is not None and cheap_model.get("cost_input_1m", 999) > max_cost:
            logger.warning(f"No model found for {provider} under budget {max_cost}. Using cheapest: {target_id}")

        logger.info(
            f"Fallback model selected: id={target_id}, "
            f"cost_input_1m={cheap_model.get('cost_input_1m')}, "
            f"tier={cheap_model.get('tier')}"
        )
        return target_id

    def get_model_info(self, model_id: str) -> Optional[Dict[str, Any]]:
        models_list: List[Dict[str, Any]] = self.data.get("models", [])
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

    def add_or_update_model(self, model_data: Dict[str, Any]):
        model_id = model_data.get("id")
        if not model_id: return
        
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

    def add_or_update_many(self, models: List[Dict[str, Any]]) -> int:
        """批量更新模型，减少频繁磁盘写入。返回有效写入条数。"""
        if not models:
            return 0

        models_list = self.data.get("models", [])
        index = {m.get("id"): i for i, m in enumerate(models_list) if m.get("id")}

        new_count = 0
        updated_count = 0
        updated_ids = []
        new_ids = []

        for m in models:
            model_id = m.get("id")
            if not model_id:
                continue
            if model_id in index:
                old = models_list[index[model_id]]
                # Check if anything changed
                changed = (
                    old.get("tier") != m.get("tier") or
                    old.get("intelligence") != m.get("intelligence") or
                    old.get("cost_input_1m") != m.get("cost_input_1m") or
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

    def remove_model(self, model_id: str):
        models_list = self.data.get("models", [])
        self.data["models"] = [m for m in models_list if m.get("id") != model_id]
        self.save_registry()

# Global instance
registry = ModelRegistry()
