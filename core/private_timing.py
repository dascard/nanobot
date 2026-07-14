"""私聊三态分类 Gate——先判断对话意图，再决定 effort + runtime_preset。"""

import logging
import re
from dataclasses import asdict, dataclass, field

from core.timing_score import TimingDecision, TimingModelHint, decide_timing

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
    re.compile(r"(?:哪里错|有人知道|求推荐|怎么修)"),
)
_DIAGNOSTIC_QUESTION_PATTERN = re.compile(r"(?:怎么|为什么|能不能|如何|怎么办)")
_DAILY_REQUEST_PATTERNS = (
    re.compile(
        r"(?:(?:请|麻烦)(?:帮我)?|帮我)?"
        r"(?:给我|发我|来份|来一份|生成|整理|总结|搜索|查看|看看|看下|看一下|查下|查一下)"
        r".{0,40}(?:日报|新闻|简报)[?？。！!]*"
    ),
    re.compile(r"(?:最新|今日|今天|本周|近期).{0,20}(?:日报|新闻|简报)[?？。！!]*"),
)
_INLINE_MATERIAL_STRONG = (
    "traceback",
    "error",
    "exception",
    "no module named",
    "keyerror",
    "typeerror",
    "syntaxerror",
    "httperror",
    "importerror",
    "modulenotfounderror",
)
_INLINE_MATERIAL_WEAK = ("\n", "```", "报错如下", "错误信息", "status code")
_DIAGNOSTIC_MATERIAL_MARKERS = _INLINE_MATERIAL_STRONG + (
    "```",
    "报错如下",
    "错误信息",
    "status code",
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
        return False
    t = text.strip()
    if not t:
        return False
    if any(p.match(t) for p in _TRANSPORT_PATTERNS):
        return True
    if len(t) > 500 and not any(k in t for k in _REQUEST_MARKERS) and "?" not in t and "？" not in t:
        return True
    return False


def _looks_task_request(text: str) -> bool:
    if any(p.search(text) for p in _TASK_PATTERNS):
        return True
    return bool(_DIAGNOSTIC_QUESTION_PATTERN.search(text)) and _has_diagnostic_material(text)


def _looks_daily_request(text: str) -> bool:
    value = text.strip()
    return any(pattern.fullmatch(value) for pattern in _DAILY_REQUEST_PATTERNS)


def _has_diagnostic_material(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _DIAGNOSTIC_MATERIAL_MARKERS)


def _has_inline_material(text: str) -> bool:
    """判断消息里是否已包含具体材料（报错、日志、代码等）。"""
    lowered = text.lower()
    if any(marker in lowered for marker in _INLINE_MATERIAL_STRONG):
        return True
    if len(text) < 40:
        return False
    return any(marker in lowered for marker in _INLINE_MATERIAL_WEAK)


def _infer_effort(text: str, is_superuser: bool = False) -> tuple[str, str, str]:
    t = text.strip()
    if any(w in t for w in _IDENTITY_PROBE_WORDS):
        return "casual", "none", "identity_probe"
    if any(w in t for w in _CHECK_CAPABILITY_WORDS):
        return "casual", "none", "check_capability"
    if any(w in t for w in _IS_BOT_WORDS) and len(t) < 30:
        return "casual", "none", "is_bot_probe"
    if any(w in t for w in _PERSONAL_PROBE_WORDS):
        return "casual", "none", "personal_probe"
    if any(w in t for w in _TOO_BROAD_WORDS):
        return "casual", "none", "too_broad"
    if _looks_daily_request(t):
        if is_superuser:
            return "serious", "full", "daily_request"
        return "casual", "none", "daily_request_casual"
    if any(w in t for w in _MISSING_MATERIAL_WORDS) and not _looks_task_request(t):
        if _has_inline_material(t):
            return "short", "lightweight", "specific_task"
        return "casual", "none", "missing_material"
    if _looks_task_request(t):
        # 超级用户只定义权限上限，不能让每一轮请求自动扩大到完整工具集。
        if is_superuser:
            return "serious", "full", "superuser_task"
        return "short", "lightweight", "specific_task"
    if is_superuser:
        return "short", "lightweight", "superuser_query"
    return "short", "lightweight", "general_query"


@dataclass
class PrivateDecision:
    action: str  # "no_reply" | "wait" | "reply_now"
    reason: str = ""
    confidence: float = 0.0
    raw_label: str = ""
    complexity: int = 0
    effort: str = "short"       # "ignore" | "casual" | "short" | "serious"
    runtime_preset: str = "lightweight"  # "none" | "lightweight" | "full"
    timing_scoring: dict | None = None


@dataclass
class PrivateTimingGate:
    """先判对话意图，再决定 effort + runtime_preset。"""

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

        scoring = _score_private_timing(text, has_files=has_files)
        if scoring.stage == "rule_shortcut":
            return self._decision_from_scoring_shortcut(
                scoring,
                text,
                user_id=user_id,
                is_superuser=is_superuser,
            )

        if text in _NO_REPLY_SET:
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "short acknowledgement", 0.9, "rule_ack", "ignore", "none",
                          user_id, timing_scoring=asdict(scoring))
        if any(m in text for m in _WAIT_MARKERS) and len(text) < 30:
            self.stats["wait"] += 1
            return _log_d("wait", "looks incomplete", 0.8, "rule_wait", "ignore", "none",
                          user_id, timing_scoring=asdict(scoring))
        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "transport_only", 0.95, "rule_transport", "ignore", "none",
                          user_id, timing_scoring=asdict(scoring))
        if has_files and not text:
            self.stats["reply_now"] += 1
            return _log_d("reply_now", "image_only", 0.95, "rule_image_only", "short", "lightweight",
                          user_id, complexity=3, timing_scoring=asdict(scoring))

        effort, runtime_preset, intent = _infer_effort(text, is_superuser)

        # casual 直接规则返回
        if effort == "casual":
            self.stats["reply_now"] += 1
            d = PrivateDecision(action="reply_now", reason=intent, confidence=1.0,
                                raw_label=intent, complexity=2, effort=effort, runtime_preset=runtime_preset)
            logger.info("[PrivateDecision] fast_path user=%s effort=%s tool=%s intent=%s",
                        user_id, effort, runtime_preset, intent)
            return d

        # Qwen 分类（仅 short/serious）
        try:
            from clients.classifier_client import get_private_decision_classifier
            from config import CLASSIFIER_TIMEOUT
            import asyncio
            classifier = self.classifier or get_private_decision_classifier()
            result = await asyncio.wait_for(
                asyncio.to_thread(classifier.classify, text, has_files),
                timeout=CLASSIFIER_TIMEOUT + 1,
            )
            scoring = _score_private_timing(text, has_files=has_files, model_result=result)
            action = _private_action_from_timing(scoring.action)
            complexity = int(result.get("complexity", 0) or 0)
            self.stats[action] += 1
            d = PrivateDecision(action=action, reason=result.get("reason", ""),
                                confidence=scoring.model_confidence, raw_label=result.get("raw", ""),
                                complexity=complexity, effort=effort, runtime_preset=runtime_preset,
                                timing_scoring=asdict(scoring))
            logger.info("[PrivateDecision] result user=%s action=%s effort=%s tool=%s complexity=%s",
                        user_id, action, effort, runtime_preset, complexity)
            return d
        except asyncio.TimeoutError:
            logger.warning("[PrivateDecision] timeout user=%s", user_id)
        except Exception as e:
            logger.warning("[PrivateDecision] failed user=%s: %s", user_id, e)

        if _looks_transport_only(text, has_files):
            self.stats["no_reply"] += 1
            return _log_d("no_reply", "fallback transport", 0.6, "fallback_transport", "ignore", "none",
                          user_id, timing_scoring=asdict(scoring))
        action = _private_action_from_timing(scoring.action)
        if action == "reply_now":
            self.stats["reply_now"] += 1
            return _log_d("reply_now", "fallback default", 0.5, "fallback", effort, runtime_preset,
                          user_id, timing_scoring=asdict(scoring))
        self.stats[action] += 1
        return _log_d(action, f"fallback scoring: {scoring.reason}", 0.5, "fallback_scoring", "ignore",
                      "none", user_id, timing_scoring=asdict(scoring))

    def _decision_from_scoring_shortcut(
        self,
        scoring: TimingDecision,
        text: str,
        *,
        user_id: str,
        is_superuser: bool,
    ) -> PrivateDecision:
        action = _private_action_from_timing(scoring.action)
        if action == "reply_now":
            effort, runtime_preset, intent = _infer_effort(text, is_superuser)
            complexity = _complexity_for_effort(effort)
            reason = intent
        elif action == "wait":
            effort = "short"
            runtime_preset = "lightweight"
            complexity = 0
            reason = scoring.reason
        else:
            effort = "ignore"
            runtime_preset = "none"
            complexity = 0
            reason = scoring.reason

        self.stats[action] += 1
        return _log_d(
            action,
            reason,
            1.0,
            "scoring_rule_shortcut",
            effort,
            runtime_preset,
            user_id,
            complexity=complexity,
            timing_scoring=asdict(scoring),
        )


def _log_d(action: str, reason: str, confidence: float, raw: str,
           effort: str, runtime_preset: str, user_id: str,
           complexity: int = 0, timing_scoring: dict | None = None) -> PrivateDecision:
    d = PrivateDecision(action, reason, confidence, raw,
                        complexity=complexity, effort=effort, runtime_preset=runtime_preset,
                        timing_scoring=timing_scoring)
    logger.info("[PrivateDecision] rule user=%s action=%s effort=%s tool=%s conf=%.2f reason=%s",
                user_id, action, effort, runtime_preset, confidence, reason[:80])
    return d


def _score_private_timing(
    text: str,
    *,
    has_files: bool = False,
    model_result: dict | None = None,
) -> TimingDecision:
    model_hint = _model_hint_from_private_result(model_result) if model_result else None
    return decide_timing(
        text,
        is_group=False,
        is_private=True,
        has_files=has_files,
        model_hint=model_hint,
    )


def _model_hint_from_private_result(result: dict) -> TimingModelHint:
    reason = str(result.get("reason", ""))
    confidence = _private_model_confidence(reason)
    return TimingModelHint(
        action=str(result.get("action", "reply_now")),
        confidence=confidence,
        raw=str(result.get("raw", "")),
        reason=reason,
    )


def _private_model_confidence(reason: str) -> float:
    lowered = str(reason or "").lower()
    if any(marker in lowered for marker in ("invalid output fallback", "classifier fallback", "parse_error")):
        return 0.0
    if _is_low_confidence_private_parse(reason):
        return 0.5
    return 0.8


def _is_low_confidence_private_parse(reason: str) -> bool:
    lowered = str(reason or "").lower()
    return any(marker in lowered for marker in ("fallback parse", "legacy"))


def _private_action_from_timing(action: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"continue", "reply_now"}:
        return "reply_now"
    if normalized in {"wait", "no_reply"}:
        return normalized
    return "reply_now"


def _complexity_for_effort(effort: str) -> int:
    if effort == "serious":
        return 6
    if effort == "casual":
        return 2
    if effort == "short":
        return 3
    return 0


_gate: PrivateTimingGate | None = None


def get_private_gate() -> PrivateTimingGate:
    global _gate
    if _gate is None:
        _gate = PrivateTimingGate()
        logger.info("[PrivateGate] initialized")
    return _gate
