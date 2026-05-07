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
    """过滤无意义的常见词。"""
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
    return phrase in noise or len(phrase) < MIN_PHRASE_LEN


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
                seen_terms.add(key)
                candidates.append({
                    "term": term,
                    "meaning": meaning,
                    "examples": [text[:120]],
                })
    return candidates


def _to_stream_id(session_id: str) -> str:
    """兼容 group_<id> 和 qq:<id>:group 两种 session_id 格式。"""
    from core.expression_memory import normalize_chat_stream_id
    sid = str(session_id or "").strip()
    if sid.startswith("qq:") and sid.endswith(":group"):
        raw = sid.removeprefix("qq:").removesuffix(":group")
    elif sid.startswith("group_"):
        raw = sid.removeprefix("group_")
    else:
        raw = sid
    return normalize_chat_stream_id(raw, chat_type="group", platform="qq")


def run_learning_cycle():
    """执行一轮学习扫描——从 ChatLog 查最近 ambient 消息，提取候选并 upsert。"""
    from sqlalchemy import or_
    from core.database import SessionLocal, ChatLog
    from core.expression_memory import upsert_expression, upsert_jargon

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

        # 按群分组
        group_msgs: dict[str, list[dict]] = {}
        for row in rows:
            stream_id = _to_stream_id(row.session_id)
            group_msgs.setdefault(stream_id, []).append({
                "content": row.content or "",
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
        if expr_new or jargon_new:
            logger.info("[ExpressionLearner] scanned=%d groups=%d "
                         "expr_new=%d expr_upd=%d jargon_new=%d jargon_upd=%d",
                         total, len(group_msgs),
                         expr_new, expr_upd, jargon_new, jargon_upd)
        return {
            "scanned": total, "groups": len(group_msgs),
            "expression_new": expr_new, "expression_updated": expr_upd,
            "jargon_new": jargon_new, "jargon_updated": jargon_upd,
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
