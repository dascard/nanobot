"""模型候选合并和单次尝试结果判定。"""

import inspect
from collections.abc import Iterable, Mapping
from typing import Any, Literal

AttemptOutcome = Literal["failure", "pending", "success"]


def classify_attempt_outcome(response: str | None) -> AttemptOutcome:
    """将空响应和框架系统错误判为失败，其余普通响应等待契约确认。"""
    text = str(response or "")
    if not text.strip() or "[系统内部错误]" in text:
        return "failure"
    return "pending"


async def record_candidate_health(
    tracker: Any,
    candidate: Mapping[str, Any] | None,
    model_id: str,
    outcome: Literal["success", "failure"],
    session_id: str,
) -> str:
    """按模型与账号隔离健康状态，并在成功后更新 Codex 会话粘性。"""
    health_key = str((candidate or {}).get("_health_key") or model_id)
    method = getattr(tracker, f"record_{outcome}", None) if tracker else None
    if method:
        result = method(health_key)
        if inspect.isawaitable(result):
            await result
    account_id = str((candidate or {}).get("_codex_account_id") or "")
    if outcome == "success" and account_id:
        from nanobot_kt.codex_accounts import codex_account_pool

        codex_account_pool.mark_success(session_id, account_id)
    return health_key


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
