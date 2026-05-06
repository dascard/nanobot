"""私聊三态分类 Gate——先判断对话意图，再决定是否回复。"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("nanobot.private_timing")

_NO_REPLY_SET = {"嗯", "哦", "ok", "OK", "Ok", "收到", "好", "好的", "哈哈", "草", "。。", "…", "..."}
_WAIT_MARKERS = ("等下", "等等", "我发你", "我发图", "还有", "就是然后", "这个是", "你看这个")

# 纯传输内容——无对话意图
_TRANSPORT_PATTERNS = (
    re.compile(r"^https?://\S+$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^[A-Za-z0-9_\-+/=]{32,}$"),
    re.compile(r"^```[\s\S]*```$"),
)

# 明确任务请求——有对话意图
_TASK_PATTERNS = (
    re.compile(r"(?:帮我|请|帮忙|麻烦).{0,20}(?:总结|分析|解释|翻译|写|搜索|看看|检查|查)"),
    re.compile(r"(?:总结|分析|解释|翻译|搜索|写).{0,12}(?:日报|新闻|代码|这段|这个|一下)"),
    re.compile(r"(?:怎么|为什么|哪里错|能不能|如何|怎么办|有人知道|求推荐|怎么修)"),
    re.compile(r"[?？]$"),
)
_REQUEST_MARKERS = ("帮我", "解释", "看看", "分析", "总结", "翻译", "怎么", "为什么",
                    "哪里错", "报错", "能不能", "请", "求推荐", "有人知道", "如何", "怎么办")


def _looks_transport_only(text: str, has_files: bool) -> bool:
    if has_files and not text.strip():
        return True
    t = text.strip()
    if not t:
        return False
    if any(p.match(t) for p in _TRANSPORT_PATTERNS):
        return True
    if len(t) > 500 and not any(k in t for k in _REQUEST_MARKERS) and "?" not in t and "？" not in t:
        return True
    return False


def _looks_task_request(text: str) -> bool:
    return any(p.search(text) for p in _TASK_PATTERNS)


@dataclass
class PrivateDecision:
    action: str
    reason: str = ""
    confidence: float = 0.0
    raw_label: str = ""
    complexity: int = 0


@dataclass
class PrivateTimingGate:
    """先判传输意图，再决定回复。"""

    classifier: object | None = None
    stats: dict = field(default_factory=lambda: {"no_reply": 0, "wait": 0, "reply_now": 0, "total": 0})

    async def classify(self, text: str, *, user_id: str = "", has_files: bool = False) -> PrivateDecision:
        text = (text or "").strip()
        self.stats["total"] += 1
        logger.info("[PrivateDecision] start user=%s len=%d has_files=%s", user_id, len(text), has_files)

        if not text and not has_files:
            self.stats["no_reply"] += 1
            return _log("no_reply", "empty message", 1.0, "rule_empty", user_id)
        if text in _NO_REPLY_SET:
            self.stats["no_reply"] += 1
            return _log("no_reply", "short acknowledgement", 0.9, "rule_ack", user_id)
        if any(m in text for m in _WAIT_MARKERS) and len(text) < 30:
            self.stats["wait"] += 1
            return _log("wait", "looks incomplete", 0.8, "rule_wait", user_id)
        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log("no_reply", "transport_only", 0.95, "rule_transport", user_id)
        if _looks_task_request(text):
            c = 5 if ("日报" in text or "新闻" in text) else 4
            self.stats["reply_now"] += 1
            d = PrivateDecision(action="reply_now", reason="rule_task_request",
                                confidence=1.0, raw_label="rule_task_request", complexity=c)
            logger.info("[PrivateDecision] fast_path user=%s action=reply_now complexity=%s", user_id, c)
            return d

        try:
            from clients.classifier_client import get_private_decision_classifier
            from config import CLASSIFIER_TIMEOUT
            import asyncio
            result = await asyncio.wait_for(
                asyncio.to_thread(get_private_decision_classifier().classify, text, has_files),
                timeout=CLASSIFIER_TIMEOUT + 1,
            )
            action = result.get("action", "reply_now")
            complexity = int(result.get("complexity", 0) or 0)
            self.stats[action] += 1
            decision = PrivateDecision(action=action, reason=result.get("reason", ""),
                                       confidence=1.0, raw_label=result.get("raw", ""),
                                       complexity=complexity)
            logger.info("[PrivateDecision] result user=%s action=%s complexity=%s reason=%s",
                        user_id, action, complexity, decision.reason[:120])
            return decision
        except asyncio.TimeoutError:
            logger.warning("[PrivateDecision] timeout user=%s", user_id)
        except Exception as e:
            logger.warning("[PrivateDecision] failed user=%s: %s", user_id, e)

        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log("no_reply", "fallback transport", 0.6, "fallback_transport", user_id)
        self.stats["reply_now"] += 1
        return _log("reply_now", "fallback default", 0.5, "fallback", user_id)


def _log(action: str, reason: str, confidence: float, raw: str, user_id: str) -> PrivateDecision:
    d = PrivateDecision(action, reason, confidence, raw)
    logger.info("[PrivateDecision] rule user=%s action=%s conf=%.2f reason=%s raw=%s",
                user_id, action, confidence, reason[:80], raw)
    return d


_gate: PrivateTimingGate | None = None


def get_private_gate() -> PrivateTimingGate:
    global _gate
    if _gate is None:
        _gate = PrivateTimingGate()
        logger.info("[PrivateGate] initialized")
    return _gate
