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

        # Qwen 三态分类
        if self.classifier:
            try:
                import asyncio
                prompt = _build_prompt(text, has_files)
                result = await asyncio.to_thread(self.classifier.classify, prompt)
                decision = _map_result(result)
                self.stats[decision.action] += 1
                logger.info(
                    "[PrivateClassify] qwen_result user=%s action=%s conf=%.2f reason=%s raw=%s",
                    user_id, decision.action, decision.confidence, decision.reason[:120], decision.raw_label,
                )
                return decision
            except Exception as e:
                logger.warning("[PrivateGate] classifier failed user=%s: %s", user_id, e)

        # fallback
        self.stats["reply_now"] += 1
        return _log("reply_now", "fallback default", 0.5, "fallback", user_id)


def _build_prompt(text: str, has_files: bool) -> str:
    ctx = f"{text}\n[附带图片]" if has_files else text
    return (
        "判断这条私聊消息需要怎样处理。\n\n"
        "选项：\n"
        "NO_REPLY — 不需要回复（纯感叹词、简短应答、表情类）\n"
        "WAIT — 用户还没说完，需要等后续消息（半句话、碎片输入）\n"
        "REPLY_NOW — 明确问题/命令/请求，应该立即回复\n\n"
        f"消息：{ctx[:500]}\n\n"
        "只输出 NO_REPLY、WAIT 或 REPLY_NOW，不要解释。"
    )


def _map_result(result) -> "PrivateDecision":
    if isinstance(result, dict):
        label = str(result.get("label") or result.get("action") or result.get("status") or "").strip().upper()
        reason = str(result.get("reason") or "")
        confidence = float(result.get("confidence") or 0.0)
    else:
        label = str(result).strip().upper()
        reason = ""
        confidence = 0.0

    if label in ("NO_REPLY", "IGNORE", "SILENT", "否"):
        return PrivateDecision("no_reply", reason, confidence, label)
    if label in ("WAIT", "BUFFER", "NEED_MORE", "HOLD"):
        return PrivateDecision("wait", reason, confidence, label)
    return PrivateDecision("reply_now", reason, confidence, label)


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
        from clients.classifier_client import get_guardrail
        _gate = PrivateTimingGate(classifier=get_guardrail())
        logger.info("[PrivateGate] initialized with classifier")
    return _gate
