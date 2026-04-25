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
            else:
                logger.warning(f"Model registry file not found at {MODEL_DATA_PATH}")
        except Exception as e:
            logger.error(f"Failed to load model registry: {e}")

    def get_models_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        models_list: List[Dict[str, Any]] = self.data.get("models", [])
        return [m for m in models_list if m.get("provider") == provider]

    def select_model(self, 
                     provider: str, 
                     tier: str = "smart", 
                     max_cost: Optional[float] = None,
                     min_intelligence: int = 0,
                     required_tags: Optional[List[str]] = None,
                     avoid_tags: Optional[List[str]] = None) -> Optional[str]:
        """
        根据厂商、层级、成本上限和最小智能得分选择模型。
        支持根据成本自动降级 (Smart -> Fast)
        """
        all_candidates = self.get_models_by_provider(provider)
        if not all_candidates:
            return None

        # Tier progression: smart -> fast -> any
        tiers_to_try = [tier] if tier else ["smart", "fast"]
        if tier == "smart":
            tiers_to_try.append("fast")
        
        required_tags = [x.lower() for x in (required_tags or []) if x]
        avoid_tags = [x.lower() for x in (avoid_tags or []) if x]

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
            # sort desc by tag/intelligence, asc by cost
            return (tag_hit, -avoid_hit, intel, -cost)

        for t in tiers_to_try:
            candidates = [m for m in all_candidates if m.get("tier") == t]
            
            # Apply cost filter
            if max_cost is not None:
                candidates = [m for m in candidates if m.get("cost_input_1m", 999) <= max_cost]
            
            # Apply intelligence filter
            if min_intelligence > 0:
                candidates = [m for m in candidates if m.get("intelligence", 0) >= min_intelligence]

            # Apply tag constraints (soft requirement if possible)
            if required_tags:
                tagged = [m for m in candidates if any(rt in _tags_of(m) for rt in required_tags)]
                if tagged:
                    candidates = tagged

            if avoid_tags:
                non_avoid = [m for m in candidates if not any(at in _tags_of(m) for at in avoid_tags)]
                if non_avoid:
                    candidates = non_avoid
            
            if candidates:
                # Found suitable candidates in this tier
                candidates.sort(key=_score, reverse=True)
                return candidates[0].get("id")

        # Ultimate Fallback: return the cheapest model if still no candidates
        all_candidates.sort(key=lambda x: x.get("cost_input_1m", 999))
        cheap_model = all_candidates[0]
        
        target_id = cheap_model.get("id")
        if max_cost is not None and cheap_model.get("cost_input_1m", 999) > max_cost:
            logger.warning(f"No model found for {provider} under budget {max_cost}. Using cheapest: {target_id}")
            
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

        updated = 0
        for m in models:
            model_id = m.get("id")
            if not model_id:
                continue
            if model_id in index:
                models_list[index[model_id]] = m
            else:
                index[model_id] = len(models_list)
                models_list.append(m)
            updated += 1

        self.data["models"] = models_list
        self.data["last_updated"] = __import__("datetime").datetime.now().isoformat()
        self.save_registry()
        return updated

    def remove_model(self, model_id: str):
        models_list = self.data.get("models", [])
        self.data["models"] = [m for m in models_list if m.get("id") != model_id]
        self.save_registry()

# Global instance
registry = ModelRegistry()
