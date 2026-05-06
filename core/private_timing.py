"""私聊三态分类 Gate——每条私聊消息先分类再路由。"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("nanobot.private_timing")

_NO_REPLY_SET = {"嗯", "哦", "ok", "OK", "Ok", "收到", "好", "好的", "哈哈", "草", "。。", "…", "..."}
_WAIT_MARKERS = ("等下", "等等", "我发你", "我发图", "还有", "就是然后", "这个是", "你看这个")
_TASK_KEYWORDS = ("日报", "总结", "新闻", "搜索", "分析", "帮我", "解释", "代码", "报错", "翻译", "写")


@dataclass
class PrivateDecision:
    action: str  # "no_reply" | "wait" | "reply_now"
    reason: str = ""
    confidence: float = 0.0
    raw_label: str = ""
    complexity: int = 0


@dataclass
class PrivateTimingGate:
    """私聊三态分类器——NO_REPLY / WAIT / REPLY_NOW。"""

    classifier: object | None = None
    stats: dict = field(default_factory=lambda: {"no_reply": 0, "wait": 0, "reply_now": 0, "total": 0})

    async def classify(self, text: str, *, user_id: str = "", has_files: bool = False) -> PrivateDecision:
        text = (text or "").strip()
        self.stats["total"] += 1

        logger.info("[PrivateDecision] start user=%s len=%d has_files=%s", user_id, len(text), has_files)

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

        # 规则 fast path：明确任务请求直接 reply_now，不等 Qwen 超时
        if any(k in text for k in _TASK_KEYWORDS):
            c = 5 if ("日报" in text or "新闻" in text) else 4
            self.stats["reply_now"] += 1
            d = PrivateDecision(action="reply_now", reason="rule_task_request",
                                confidence=1.0, raw_label="rule_task_request", complexity=c)
            logger.info("[PrivateDecision] fast_path user=%s action=reply_now complexity=%s", user_id, c)
            return d

        # Qwen 一次调用输出 action + complexity
        try:
            from clients.classifier_client import get_private_decision_classifier
            from config import CLASSIFIER_TIMEOUT
            import asyncio

            result = await asyncio.wait_for(
                asyncio.to_thread(
                    get_private_decision_classifier().classify, text, has_files,
                ),
                timeout=CLASSIFIER_TIMEOUT + 1,
            )
            action = result.get("action", "reply_now")
            complexity = int(result.get("complexity", 0) or 0)
            self.stats[action] += 1
            decision = PrivateDecision(
                action=action,
                reason=result.get("reason", ""),
                confidence=1.0,
                raw_label=result.get("raw", ""),
                complexity=complexity,
            )
            logger.info(
                "[PrivateDecision] result user=%s action=%s complexity=%s reason=%s raw=%s",
                user_id, action, complexity, decision.reason[:120], decision.raw_label[:120],
            )
            return decision
        except asyncio.TimeoutError:
            logger.warning("[PrivateDecision] timeout user=%s", user_id)
        except Exception as e:
            logger.warning("[PrivateDecision] failed user=%s: %s", user_id, e)

        # fallback
        self.stats["reply_now"] += 1
        return _log("reply_now", "fallback default", 0.5, "fallback", user_id)


def _log(action: str, reason: str, confidence: float, raw: str, user_id: str) -> PrivateDecision:
    d = PrivateDecision(action, reason, confidence, raw)
    logger.info("[PrivateDecision] rule user=%s action=%s conf=%.2f reason=%s raw=%s",
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
