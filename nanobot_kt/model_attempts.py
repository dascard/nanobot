"""模型候选合并和单次尝试结果判定。"""

from collections.abc import Iterable, Mapping
from typing import Any, Literal

AttemptOutcome = Literal["failure", "pending", "success"]


def classify_attempt_outcome(response: str | None) -> AttemptOutcome:
    """将空响应和框架系统错误判为失败，其余普通响应等待契约确认。"""
    text = str(response or "")
    if not text.strip() or "[系统内部错误]" in text:
        return "failure"
    return "pending"


def merge_model_candidates(
    preferred: Mapping[str, Any] | None,
    automatic: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """首选候选在前，并按候选身份保序去重。"""
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for candidate in ([preferred] if preferred is not None else []):
        model_id = str(candidate.get("id") or "").strip()
        candidate_key = str(candidate.get("_candidate_key") or model_id).strip()
        if model_id and candidate_key and candidate_key not in seen_keys:
            merged.append(dict(candidate))
            seen_keys.add(candidate_key)

    for candidate in automatic:
        model_id = str(candidate.get("id") or "").strip()
        candidate_key = str(candidate.get("_candidate_key") or model_id).strip()
        if model_id and candidate_key and candidate_key not in seen_keys:
            merged.append(dict(candidate))
            seen_keys.add(candidate_key)

    return merged
