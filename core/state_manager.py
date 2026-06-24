"""
Terrarium State Manager.
Tracks non-persistent or semi-persistent "world state" that doesn't fit in ChatLog.
"""
import json
import os
import logging
from typing import Any

logger = logging.getLogger("nanobot.state")

STATE_FILE = "./data/terrarium_state.json"

class StateManager:
    def __init__(self):
        self.state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        return {
            "active_tasks": {},
            "group_sentiments": {},
            "global_flags": {}
        }

    def save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def get_group_state(self, session_id: str) -> dict[str, Any]:
        """获取特定会话场的活跃状态"""
        return self.state.get("group_sentiments", {}).get(session_id, {"vibe": "neutral", "last_event": None})

    def update_group_state(self, session_id: str, updates: dict[str, Any]):
        """更新会话场状态"""
        if "group_sentiments" not in self.state:
            self.state["group_sentiments"] = {}
        if session_id not in self.state["group_sentiments"]:
            self.state["group_sentiments"][session_id] = {}
        self.state["group_sentiments"][session_id].update(updates)
        self.save()
