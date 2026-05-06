"""私聊三态分类 Gate——先判断对话意图，再决定 effort + tool_policy。"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("nanobot.private_timing")

_NO_REPLY_SET = {"嗯", "哦", "ok", "OK", "Ok", "收到", "好", "好的", "哈哈", "草", "。。", "…", "..."}
_WAIT_MARKERS = ("等下", "等等", "我发你", "我发图", "还有", "就是然后", "这个是", "你看这个")

_TRANSPORT_PATTERNS = (
    re.compile(r"^https?://\S+$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^[A-Za-z0-9_\-+/=]{32,}$"),
    re.compile(r"^```[\s\S]*```$"),
)

_TASK_PATTERNS = (
    re.compile(r"(?:帮我|请|帮忙|麻烦).{0,20}(?:总结|分析|解释|翻译|写|搜索|看看|检查|查)"),
    re.compile(r"(?:总结|分析|解释|翻译|搜索|写).{0,12}(?:日报|新闻|代码|这段|这个|一下)"),
    re.compile(r"(?:怎么|为什么|哪里错|能不能|如何|怎么办|有人知道|求推荐|怎么修)"),
    re.compile(r"[?？]$"),
)
_REQUEST_MARKERS = ("帮我", "解释", "看看", "分析", "总结", "翻译", "怎么", "为什么",
                    "哪里错", "报错", "能不能", "请", "求推荐", "有人知道", "如何", "怎么办")

_IDENTITY_PROBE_WORDS = ("你是谁", "你是啥", "你是？", "你是?", "你叫啥", "你叫什么")
_CHECK_CAPABILITY_WORDS = ("你能干嘛", "你能做什么", "你会什么", "有什么功能")
_IS_BOT_WORDS = ("机器人", "bot", "Bot", "是不是人")
_PERSONAL_PROBE_WORDS = ("哪里人", "多大", "男的女的", "真人吗", "在哪", "住哪")
_MISSING_MATERIAL_WORDS = ("帮我看", "帮我查", "帮我看下", "帮我看报错", "帮我看代码")
_TOO_BROAD_WORDS = ("整个项目", "全部代码", "完整方案", "全部改", "全改", "帮我审")


_EFFORT_CONSTRAINTS = {
    "casual": "本轮只随口接一句。不要分析、不要列步骤、不要调用工具、不要解释能力。回复控制在1句话，最好2-12个字。如果缺材料，只让对方发材料。",
    "short": "本轮简短处理。先给判断，最多补1-3个要点，最多3句话。不要用Markdown标题，不要列表超过3条。不要写报告。",
    "serious": "本轮认真处理。可以使用工具，可以分步骤分析。先给结论，再给依据、修改点和验收方式。",
}


def get_effort_constraint(effort: str) -> str:
    return _EFFORT_CONSTRAINTS.get(effort, "")


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


def _has_inline_material(text: str) -> bool:
    """判断消息里是否已包含具体材料（报错、日志、代码等）。"""
    strong = ("Traceback", "Error", "Exception", "No module named",
              "KeyError", "TypeError", "SyntaxError", "HTTPError",
              "ImportError", "ModuleNotFoundError")
    if any(m in text for m in strong):
        return True
    if len(text) < 40:
        return False
    weak = ("\n", "```", "报错如下", "错误信息", "status code")
    return any(m in text for m in weak)


def _infer_effort(text: str, is_superuser: bool = False) -> tuple[str, str, str]:
    t = text.strip()
    if is_superuser:
        if _looks_task_request(t):
            return "serious", "full", "superuser_task"
        return "short", "limited", "superuser_query"
    if any(w in t for w in _IDENTITY_PROBE_WORDS):
        return "casual", "none", "identity_probe"
    if any(w in t for w in _CHECK_CAPABILITY_WORDS):
        return "casual", "none", "check_capability"
    if any(w in t for w in _IS_BOT_WORDS) and len(t) < 30:
        return "casual", "none", "is_bot_probe"
    if any(w in t for w in _PERSONAL_PROBE_WORDS):
        return "casual", "none", "personal_probe"
    if any(w in t for w in _MISSING_MATERIAL_WORDS) and not _looks_task_request(t):
        if _has_inline_material(t):
            return "short", "limited", "specific_task"
        return "casual", "none", "missing_material"
    if any(w in t for w in _TOO_BROAD_WORDS):
        return "casual", "none", "too_broad"
    if _looks_task_request(t):
        if ("日报" in t or "新闻" in t):
            if is_superuser:
                return "serious", "full", "daily_request"
            return "casual", "none", "daily_request_casual"
        return "short", "limited", "specific_task"
    return "short", "limited", "general_query"


@dataclass
class PrivateDecision:
    action: str  # "no_reply" | "wait" | "reply_now"
    reason: str = ""
    confidence: float = 0.0
    raw_label: str = ""
    complexity: int = 0
    effort: str = "short"       # "ignore" | "casual" | "short" | "serious"
    tool_policy: str = "limited"  # "none" | "limited" | "full"


@dataclass
class PrivateTimingGate:
    """先判对话意图，再决定 effort + tool_policy。"""

    classifier: object | None = None
    stats: dict = field(default_factory=lambda: {"no_reply": 0, "wait": 0, "reply_now": 0, "total": 0})

    async def classify(self, text: str, *, user_id: str = "", has_files: bool = False,
                       is_superuser: bool = False) -> PrivateDecision:
        text = (text or "").strip()
        self.stats["total"] += 1
        logger.info("[PrivateDecision] start user=%s len=%d has_files=%s superuser=%s",
                    user_id, len(text), has_files, is_superuser)

        if not text and not has_files:
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "empty message", 1.0, "rule_empty", "ignore", "none", user_id)
        if text in _NO_REPLY_SET:
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "short acknowledgement", 0.9, "rule_ack", "ignore", "none", user_id)
        if any(m in text for m in _WAIT_MARKERS) and len(text) < 30:
            self.stats["wait"] += 1
            return _log_d("wait", "looks incomplete", 0.8, "rule_wait", "ignore", "none", user_id)
        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "transport_only", 0.95, "rule_transport", "ignore", "none", user_id)

        effort, tool_policy, intent = _infer_effort(text, is_superuser)

        # casual 直接规则返回
        if effort == "casual":
            self.stats["reply_now"] += 1
            d = PrivateDecision(action="reply_now", reason=intent, confidence=1.0,
                                raw_label=intent, complexity=2, effort=effort, tool_policy=tool_policy)
            logger.info("[PrivateDecision] fast_path user=%s effort=%s tool=%s intent=%s",
                        user_id, effort, tool_policy, intent)
            return d

        # Qwen 分类（仅 short/serious）
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
            d = PrivateDecision(action=action, reason=result.get("reason", ""),
                                confidence=1.0, raw_label=result.get("raw", ""),
                                complexity=complexity, effort=effort, tool_policy=tool_policy)
            logger.info("[PrivateDecision] result user=%s action=%s effort=%s tool=%s complexity=%s",
                        user_id, action, effort, tool_policy, complexity)
            return d
        except asyncio.TimeoutError:
            logger.warning("[PrivateDecision] timeout user=%s", user_id)
        except Exception as e:
            logger.warning("[PrivateDecision] failed user=%s: %s", user_id, e)

        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "fallback transport", 0.6, "fallback_transport", "ignore", "none", user_id)
        self.stats["reply_now"] += 1
        return _log_d("reply_now", "fallback default", 0.5, "fallback", effort, tool_policy, user_id)


def _log_d(action: str, reason: str, confidence: float, raw: str,
           effort: str, tool_policy: str, user_id: str,
           complexity: int = 0) -> PrivateDecision:
    d = PrivateDecision(action, reason, confidence, raw,
                        complexity=complexity, effort=effort, tool_policy=tool_policy)
    logger.info("[PrivateDecision] rule user=%s action=%s effort=%s tool=%s conf=%.2f reason=%s",
                user_id, action, effort, tool_policy, confidence, reason[:80])
    return d


_gate: PrivateTimingGate | None = None


def get_private_gate() -> PrivateTimingGate:
    global _gate
    if _gate is None:
        _gate = PrivateTimingGate()
        logger.info("[PrivateGate] initialized")
    return _gate
