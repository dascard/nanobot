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
    }
    return phrase in noise or len(phrase) < MIN_PHRASE_LEN


def _extract_expression_candidates(messages: list[dict]) -> list[dict]:
    """从最近消息中检测重复短词——候选表达。"""
    phrase_counts: Counter = Counter()
    phrase_examples: dict[str, list[str]] = {}

    for msg in messages:
        text = msg.get("content", "")
        cjk = _cjk_chars(text)
        if not cjk:
            continue
        seen: set[str] = set()
        for length in range(MIN_PHRASE_LEN, min(MAX_PHRASE_LEN + 1, len(cjk) + 1)):
            for start in range(len(cjk) - length + 1):
                phrase = cjk[start:start + length]
                if phrase in seen or _is_noise_phrase(phrase):
                    continue
                seen.add(phrase)
                phrase_counts[phrase] += 1
                if phrase not in phrase_examples:
                    phrase_examples[phrase] = []
                if text not in phrase_examples[phrase]:
                    phrase_examples[phrase].append(text[:120])

    candidates = []
    for phrase, count in phrase_counts.items():
        if count >= MIN_REPEAT_COUNT:
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


def run_learning_cycle():
    """执行一轮学习扫描——从 ChatLog 查最近 ambient 消息，提取候选并 upsert。"""
    from core.database import SessionLocal, ChatLog
    from core.expression_memory import upsert_expression, upsert_jargon, normalize_chat_stream_id

    db = SessionLocal()
    try:
        cutoff = datetime.now() - timedelta(minutes=SCAN_WINDOW_MIN)
        rows = (
            db.query(ChatLog)
            .filter(
                ChatLog.role == "ambient",
                ChatLog.created_at >= cutoff,
                ChatLog.session_id.like("qq:%:group"),
            )
            .all()
        )
        if not rows:
            return {"scanned": 0, "expression_new": 0, "jargon_new": 0}

        # 按群分组
        group_msgs: dict[str, list[dict]] = {}
        for row in rows:
            gid = row.session_id
            group_msgs.setdefault(gid, []).append({
                "content": row.content or "",
                "sender_name": row.sender_name or "",
            })

        expr_new, expr_upd, jargon_new, jargon_upd = 0, 0, 0, 0

        for group_id, msgs in group_msgs.items():
            stream_id = normalize_chat_stream_id(
                group_id.removeprefix("qq:").removesuffix(":group"),
                chat_type="group", platform="qq")

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
