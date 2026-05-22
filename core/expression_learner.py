"""群聊自动学习——后台任务周期性扫描 ChatLog ambient 消息，提取表达/黑话候选。

不依赖 LLM，使用轻量启发式规则：
1. 短词重复检测 → ExpressionMemory 候选
2. 定义句式检测 → JargonMemory 候选
"""

import json
import logging
import re
import time as _time
from collections import Counter
from datetime import datetime, timedelta

logger = logging.getLogger("nanobot.expression_learner")

SCAN_INTERVAL_SEC = 600       # 每 10 分钟扫描
SCAN_WINDOW_MIN = 15          # 扫描最近 15 分钟
MIN_REPEAT_COUNT = 2          # 短词最少重复次数
MIN_PHRASE_LEN = 2            # 短词最少 CJK 字符数
MAX_PHRASE_LEN = 8            # 短词最多 CJK 字符数

# 绝对不能学习为群表达/黑话的系统词和工具名
BAD_LEARN_TERMS: set[str] = {
    "tool_error", "reply", "no_reply", "RuntimeTool", "AvailableFunctions",
    "memory_read", "memory_write", "sql_analysis", "group_analysis",
    "python_sandbox", "image_summary", "sticker_search", "ai_daily", "news_search",
    "news_daily", "Traceback", "Exception", "HTTPError",
    "request_json", "response_json", "skill", "subagent",
    "AgentRun", "PromptRender", "conversation",
    "nanobot", "runtime", "prompt", "home", "end",
}

# 消息内容中的内部标记——出现则不学习
_INTERNAL_CONTENT_MARKERS = (
    "[Tool",
    "Tool completed",
    "tool_error",
    "Traceback",
    "Exception",
    "HTTPError",
    "response_json",
    "request_json",
    "AgentRun",
    "PromptRender",
    "RuntimeTool",
    "Available Functions",
    "Available Sub-Agents",
    "Background Execution",
    "reply/no_reply",
    "<runtime_context>",
    "<user_input>",
    "<history_context>",
    "<conversation_context>",
    "<persona_reference>",
    "[CQ:image,file=http://127.0.0.1",
)

# 定义句式正则
_DEFINITION_PATTERNS = [
    re.compile(r"(.{1,10})就是(.{1,30})"),
    re.compile(r"(.{1,10})的意思是(.{1,30})"),
    re.compile(r"什么叫(.{1,10}).{0,4}就是(.{1,30})"),
    re.compile(r"(.{1,10})[=＝](.{1,20})"),
]


def _cjk_chars(text: str) -> str:
    return "".join(ch for ch in text if "一" <= ch <= "鿿")


def _is_noise_phrase(phrase: str) -> bool:
    """过滤无意义的常见词、纯符号/数字串。"""
    noise = {
        "什么", "怎么", "为什么", "不知道", "我觉得", "我也是",
        "哈哈哈", "就是", "那个", "这个", "可以", "没有", "不是",
        "好的", "确实", "真的", "还行", "还行吧", "没问题",
        "今天", "一下", "感觉", "问题", "但是", "然后", "现在",
        "还是", "应该", "可能", "已经", "有点", "不过", "所以",
        "如果", "因为", "的话", "比较", "特别", "一起", "好像",
        "其实", "最近", "之前", "以后", "直接", "真的假", "怎么办",
        "不知道怎", "这个问题",
    }
    if phrase in noise or len(phrase) < MIN_PHRASE_LEN:
        return True
    import re
    if re.fullmatch(r"[\d\s\.\,\+\-\*\/\=×xX%％\(\)（）\$€¥₩]+", phrase):
        return True
    return False


def _short_cjk_phrases(text: str) -> list[str]:
    """从单条消息提取完整短句（≤8 CJK 字符），不滑窗切 n-gram。"""
    cjk = _cjk_chars(text)
    if not cjk:
        return []
    # 按标点/空格/拉丁字符天然分段，避免跨句拼接
    parts = re.split(r"[，。！？、；：\s\.,!?;:\"'()（）\[\]{}「」『』\n]+", text)
    phrases: list[str] = []
    for part in parts:
        c = _cjk_chars(part)
        if MIN_PHRASE_LEN <= len(c) <= MAX_PHRASE_LEN and not _is_noise_phrase(c):
            phrases.append(c)
    return phrases


def _extract_expression_candidates(messages: list[dict]) -> list[dict]:
    """从最近消息中检测重复短句——候选表达。

    要求至少来自 2 个不同 sender，避免单人刷屏污染。
    """
    phrase_counts: Counter = Counter()
    phrase_senders: dict[str, set[str]] = {}
    phrase_examples: dict[str, list[str]] = {}

    for msg in messages:
        text = msg.get("content", "")
        sender = msg.get("sender_name", "")
        for phrase in _short_cjk_phrases(text):
            if _is_noise_phrase(phrase):
                continue
            if phrase.lower() in BAD_LEARN_TERMS or phrase in BAD_LEARN_TERMS:
                continue
            phrase_counts[phrase] += 1
            if sender:
                phrase_senders.setdefault(phrase, set()).add(sender)
            if phrase not in phrase_examples:
                phrase_examples[phrase] = []
            if text not in phrase_examples[phrase]:
                phrase_examples[phrase].append(text[:120])

    candidates = []
    for phrase, count in phrase_counts.items():
        if count >= MIN_REPEAT_COUNT and len(phrase_senders.get(phrase, set())) >= 2:
            candidates.append({
                "expression": phrase,
                "expr_type": "phrase",
                "source_count": count,
                "examples": phrase_examples.get(phrase, [])[:3],
            })
    return candidates


def _extract_jargon_candidates(messages: list[dict]) -> list[dict]:
    """从最近消息中检测定义句式——黑话候选。"""
    candidates: list[dict] = []
    seen_terms: set[str] = set()

    for msg in messages:
        text = msg.get("content", "")
        for pat in _DEFINITION_PATTERNS:
            for m in pat.finditer(text):
                term = m.group(1).strip()
                meaning = m.group(2).strip()
                key = term.lower()
                if key in seen_terms or len(term) < 2 or len(meaning) < 2:
                    continue
                if _is_noise_phrase(term):
                    continue
                # 过滤系统词和内部标记
                if term.lower() in BAD_LEARN_TERMS or term in BAD_LEARN_TERMS:
                    continue
                meaning_lower = meaning.lower()
                if any(bad in meaning_lower for bad in ("error", "trace", "json", "http", "tool", "reply", "prompt")):
                    continue
                seen_terms.add(key)
                candidates.append({
                    "term": term,
                    "meaning": meaning,
                    "examples": [text[:120]],
                })
    return candidates


def should_learn_from_chatlog(row) -> tuple[bool, str]:
    """判断 ChatLog 行是否可以作为群表达/黑话学习素材。返回 (可学习, 拒绝原因)。"""
    if not row or row.role != "ambient":
        return False, "not_ambient"

    content = str(row.content or "").strip()
    if not content or len(content) > 2000:
        return False, "empty_or_too_long"

    # meta 标记检查
    try:
        meta = json.loads(row.meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if meta.get("no_learn") or meta.get("internal") or meta.get("control"):
        return False, "meta_no_learn"

    sender = str(row.sender_name or "").lower()
    if sender in ("nanobot", "bot", "self", ""):
        return False, "system_sender"

    # 内部标记硬过滤
    content_lower = content.lower()
    for marker in _INTERNAL_CONTENT_MARKERS:
        if marker.lower() in content_lower:
            return False, f"internal_marker:{marker[:30]}"

    # 拒绝纯 URL / CQ 码 / JSON / stack trace
    stripped = content.strip()
    if stripped.startswith("http://") or stripped.startswith("https://") or stripped.startswith("[CQ:"):
        return False, "cq_or_url"
    if stripped.startswith("{") and stripped.endswith("}"):
        return False, "json_object"
    if "File " in content and 'line ' in content:
        return False, "stack_trace"

    return True, "ok"


def sanitize_learnable_group_text(text: str) -> str:
    """清洗可学习文本——去掉 CQ 码、URL、时间戳、code block、JSON。"""
    import re as _re
    cleaned = str(text or "")
    # 去掉 CQ 码
    cleaned = _re.sub(r"\[CQ:[^\]]+\]", "", cleaned)
    # 去掉 URL
    cleaned = _re.sub(r"https?://\S+", "", cleaned)
    # 去掉时间戳
    cleaned = _re.sub(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}", "", cleaned)
    cleaned = _re.sub(r"\d{2}:\d{2}:\d{2}", "", cleaned)
    # 去掉 markdown code block
    cleaned = _re.sub(r"```[\s\S]*?```", "", cleaned)
    # 去掉 JSON 大对象
    cleaned = _re.sub(r"\{[^}]{50,}\}", "", cleaned)
    # 去掉多余空白
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= MIN_PHRASE_LEN else ""


def run_learning_cycle():
    """执行一轮学习扫描——从 ChatLog 查最近 ambient 消息，提取候选并 upsert。"""
    from sqlalchemy import or_
    from core.database import SessionLocal, ChatLog
    from core.expression_memory import upsert_expression, upsert_jargon
    from core.group_runtime.ids import normalize_group_stream_id

    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(minutes=SCAN_WINDOW_MIN)
        rows = (
            db.query(ChatLog)
            .filter(
                ChatLog.role == "ambient",
                ChatLog.created_at >= cutoff,
                or_(
                    ChatLog.session_id.like("group_%"),
                    ChatLog.session_id.like("qq:%:group"),
                ),
            )
            .all()
        )
        if not rows:
            return {"scanned": 0, "expression_new": 0, "jargon_new": 0}

        # 硬过滤不可学习的消息
        accepted_rows: list = []
        reject_reasons: Counter = Counter()
        for r in rows:
            ok, reason = should_learn_from_chatlog(r)
            if ok:
                accepted_rows.append(r)
            else:
                reject_reasons[reason] += 1
        rows = accepted_rows

        # 按 stream_id 分组，content 剥离 sender 前缀
        from core.context_builder import _strip_speaker_prefix

        group_msgs: dict[str, list[dict]] = {}
        for row in rows:
            stream_id = normalize_group_stream_id(row.session_id)
            clean_content = sanitize_learnable_group_text(
                _strip_speaker_prefix(row.content or "", row.sender_name or "")
            )
            if not clean_content:
                continue
            group_msgs.setdefault(stream_id, []).append({
                "content": clean_content,
                "sender_name": row.sender_name or "",
            })

        expr_new, expr_upd, jargon_new, jargon_upd = 0, 0, 0, 0

        for stream_id, msgs in group_msgs.items():
            for c in _extract_expression_candidates(msgs):
                r = upsert_expression(stream_id, c["expression"],
                                       expr_type=c["expr_type"],
                                       source_count=c["source_count"],
                                       examples=c["examples"])
                if r == "new":
                    expr_new += 1
                elif r and r.startswith("updated"):
                    expr_upd += 1

            for c in _extract_jargon_candidates(msgs):
                r = upsert_jargon(stream_id, c["term"],
                                   meaning=c["meaning"],
                                   examples=c["examples"])
                if r == "new":
                    jargon_new += 1
                elif r and r.startswith("updated"):
                    jargon_upd += 1

        total = len(rows)
        # 记录本轮拒绝原因 Top-5
        top_reject = reject_reasons.most_common(5) if reject_reasons else []
        if expr_new or jargon_new or top_reject:
            logger.info("[ExpressionLearner] scanned=%d accepted=%d groups=%d "
                         "expr_new=%d expr_upd=%d jargon_new=%d jargon_upd=%d "
                         "reject_top=%s",
                         total + sum(reject_reasons.values()), total, len(group_msgs),
                         expr_new, expr_upd, jargon_new, jargon_upd,
                         json.dumps(top_reject, ensure_ascii=False))
        return {
            "scanned": total + sum(reject_reasons.values()),
            "accepted": total,
            "groups": len(group_msgs),
            "expression_new": expr_new, "expression_updated": expr_upd,
            "jargon_new": jargon_new, "jargon_updated": jargon_upd,
            "reject_reasons": dict(reject_reasons.most_common(10)),
        }
    except Exception:
        logger.exception("[ExpressionLearner] cycle failed")
        return {"error": True}
    finally:
        db.close()


def expression_learner_scheduler(stop_event):
    """后台线程——周期性运行学习扫描。"""
    logger.info("[ExpressionLearner] started, interval=%ds window=%dmin",
                SCAN_INTERVAL_SEC, SCAN_WINDOW_MIN)
    while not stop_event.wait(timeout=SCAN_INTERVAL_SEC):
        t0 = _time.time()
        result = run_learning_cycle()
        elapsed = (_time.time() - t0) * 1000
        if not result.get("error"):
            logger.debug("[ExpressionLearner] cycle done in %.0fms result=%s",
                         elapsed, json.dumps(result, ensure_ascii=False))
