"""私聊三态分类 Gate——每条私聊消息先分类再路由。"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("nanobot.private_timing")

_NO_REPLY_SET = {"嗯", "哦", "ok", "OK", "Ok", "收到", "好", "好的", "哈哈", "草", "。。", "…", "..."}
_WAIT_MARKERS = ("等下", "等等", "我发你", "我发图", "还有", "就是然后", "这个是", "你看这个")


@dataclass
class PrivateDecision:
    action: str  # "no_reply" | "wait" | "reply_now"
    reason: str = ""
    confidence: float = 0.0
    raw_label: str = ""


@dataclass
class PrivateTimingGate:
    """私聊三态分类器——NO_REPLY / WAIT / REPLY_NOW。"""

    classifier: object | None = None
    stats: dict = field(default_factory=lambda: {"no_reply": 0, "wait": 0, "reply_now": 0, "total": 0})

    async def classify(self, text: str, *, user_id: str = "", has_files: bool = False) -> PrivateDecision:
        text = (text or "").strip()
        self.stats["total"] += 1

        logger.info("[PrivateGate] classify_start user=%s len=%d has_files=%s", user_id, len(text), has_files)

        # 规则兜底：空消息/极短应答 → NO_REPLY
        if not text and not has_files:
            self.stats["no_reply"] += 1
            return _log("no_reply", "empty message", 1.0, "rule_empty", user_id)
        if text in _NO_REPLY_SET:
            self.stats["no_reply"] += 1
            return _log("no_reply", "short acknowledgement", 0.9, "rule_ack", user_id)

        # 规则兜底：明显没说完 → WAIT
        if any(m in text for m in _WAIT_MARKERS) and len(text) < 30:
            self.stats["wait"] += 1
            return _log("wait", "looks incomplete", 0.8, "rule_wait", user_id)

        # Qwen 独立三态分类——不复用 Guardrail.classify()
        try:
            from clients.classifier_client import call_qwen_private_timing
            import asyncio
            result = await asyncio.to_thread(call_qwen_private_timing, text, has_files)
            label = result.get("label", "REPLY_NOW")
            action = "no_reply" if label == "NO_REPLY" else ("wait" if label == "WAIT" else "reply_now")
            self.stats[action] += 1
            decision = PrivateDecision(action, label, result.get("confidence", 1.0), label)
            logger.info("[PrivateClassify] qwen_result user=%s action=%s raw=%s",
                        user_id, action, label)
            return decision
        except Exception as e:
            logger.warning("[PrivateGate] classifier failed user=%s: %s", user_id, e)

        # fallback
        self.stats["reply_now"] += 1
        return _log("reply_now", "fallback default", 0.5, "fallback", user_id)


def _log(action: str, reason: str, confidence: float, raw: str, user_id: str) -> PrivateDecision:
    d = PrivateDecision(action, reason, confidence, raw)
    logger.info("[PrivateGate] route user=%s action=%s conf=%.2f reason=%s raw=%s",
                user_id, action, confidence, reason[:80], raw)
    return d


# 全局单例
_gate: PrivateTimingGate | None = None


def get_private_gate() -> PrivateTimingGate:
    global _gate
    if _gate is None:
        _gate = PrivateTimingGate()
        logger.info("[PrivateGate] initialized")
    return _gate
