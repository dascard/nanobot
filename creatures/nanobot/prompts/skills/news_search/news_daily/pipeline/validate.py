"""Quality Validator——fatal 降级 fallback，warning 记 missing_info。"""

import logging
import re

logger = logging.getLogger("nanobot.news_daily.validate")


def fix_source_ids(digest: dict, valid_ids: set[int]) -> dict:
    def clean(ids):
        if not isinstance(ids, list):
            return []
        return [x for x in ids if isinstance(x, int) and x in valid_ids]

    ts = digest.get("top_story")
    if isinstance(ts, dict):
        ts["source_ids"] = clean(ts.get("source_ids"))

    for h in digest.get("highlights", []) or []:
        if isinstance(h, dict):
            h["source_ids"] = clean(h.get("source_ids"))

    for w in digest.get("watchlist", []) or []:
        if isinstance(w, dict):
            w["source_ids"] = clean(w.get("source_ids"))

    return digest


def safe_quality_digest(llm_digest: dict, fallback: dict, cards: list[dict]) -> dict:
    """合并 LLM 输出到 fallback，只覆盖允许的字段。"""
    if not isinstance(llm_digest, dict):
        return dict(fallback)

    valid_ids = {c.get("source_id") for c in cards if isinstance(c.get("source_id"), int)}
    digest = dict(fallback)

    for k in ["title", "subtitle", "verdict", "top_story", "highlights",
              "details", "watchlist", "missing_info", "closing", "_quality_source"]:
        if k in llm_digest and llm_digest[k]:
            digest[k] = llm_digest[k]

    digest["mode"] = "quality" if llm_digest.get("_quality_source") == "llm" else fallback.get("mode", "quality_fallback")
    digest = fix_source_ids(digest, valid_ids)
    return digest


def validate_quality_digest(digest: dict, cards: list[dict]) -> tuple[bool, list[str]]:
    """校验——仅 fatal 返回 False。"""
    fatal = []
    warnings = []
    valid_ids = {c.get("source_id") for c in cards if isinstance(c.get("source_id"), int)}

    # fatal
    if not digest.get("title"):
        fatal.append("缺少 title")
    ts = digest.get("top_story")
    if not ts:
        fatal.append("缺少 top_story")
    elif not ts.get("source_ids"):
        fatal.append("top_story 无 source_ids")
    elif isinstance(ts.get("source_ids"), list):
        if not any(sid in valid_ids for sid in ts["source_ids"]):
            fatal.append("top_story source_ids 全部无效")
    if not digest.get("highlights"):
        fatal.append("highlights 为空")

    # warnings
    if not digest.get("verdict"):
        warnings.append("缺少 verdict")
    highlights = digest.get("highlights", []) or []
    if len(highlights) < 3:
        warnings.append(f"highlights 仅 {len(highlights)} 条")
    vague = re.compile(r"(值得关注|行业持续|不断进步|日益增长|越来越|趋势)")
    for field in ["verdict", "closing"]:
        text = digest.get(field, "")
        if vague.search(text or ""):
            warnings.append(f"{field} 空泛")

    issues = fatal + warnings
    return len(fatal) == 0, issues
