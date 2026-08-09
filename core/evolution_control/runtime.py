"""受控进化灰度的数据面解析；异常时保留已验证 baseline。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from pathlib import Path
from typing import TypeVar

from core.runtime_paths import RUNTIME_PATHS

from .contracts import EvolutionContractError
from .store import EvolutionControlStore


logger = logging.getLogger("nanobot.evolution.runtime")

EVOLUTION_CONTROL_ROOT = RUNTIME_PATHS.evolution_control_dir

_CandidateT = TypeVar("_CandidateT")


def reorder_routing_candidates(
    candidates: Sequence[_CandidateT],
    *,
    route_key: str,
    subject_id: str,
    candidate_id: Callable[[_CandidateT], str],
    root: str | Path | None = None,
) -> tuple[list[_CandidateT], dict[str, object]]:
    """按已批准灰度重排现有候选；不能引入未配置模型或删除 fallback。"""

    baseline = list(candidates)
    normalized_route = str(route_key or "").strip()
    normalized_subject = str(subject_id or "").strip()
    if not baseline or not normalized_route or not normalized_subject:
        return baseline, {
            "applied": False,
            "reason": "missing_runtime_context",
            "release_id": "",
        }

    identities = [str(candidate_id(item) or "").strip() for item in baseline]
    if any(not item for item in identities) or len(identities) != len(set(identities)):
        return baseline, {
            "applied": False,
            "reason": "ambiguous_baseline_candidates",
            "release_id": "",
        }
    try:
        from evals.harness_registry import EVAL_HARNESS_REGISTRY

        resolution = EvolutionControlStore(
            root or EVOLUTION_CONTROL_ROOT
        ).resolve_canary(
            target_kind="routing",
            resource_id=normalized_route,
            subject_id=normalized_subject,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    except (EvolutionContractError, OSError) as exc:
        logger.error("受控进化路由解析失败，保留 baseline：%s", exc)
        return baseline, {
            "applied": False,
            "reason": "control_plane_unavailable",
            "release_id": "",
        }
    if resolution.get("selected") is not True:
        return baseline, {
            "applied": False,
            "reason": str(resolution.get("reason") or "baseline"),
            "release_id": str(resolution.get("release_id") or ""),
        }

    changes = resolution.get("changes")
    if not isinstance(changes, dict) or changes.get("route_key") != normalized_route:
        logger.error("受控进化路由候选与请求 route_key 不一致，保留 baseline")
        return baseline, {
            "applied": False,
            "reason": "route_key_mismatch",
            "release_id": str(resolution.get("release_id") or ""),
        }
    requested = changes.get("ordered_model_ids")
    if not isinstance(requested, list) or any(
        not isinstance(item, str) or not item for item in requested
    ):
        return baseline, {
            "applied": False,
            "reason": "candidate_order_invalid",
            "release_id": str(resolution.get("release_id") or ""),
        }
    unknown = sorted(set(requested) - set(identities))
    if unknown:
        logger.error(
            "受控进化路由引用未配置候选，保留 baseline：%s",
            ", ".join(unknown),
        )
        return baseline, {
            "applied": False,
            "reason": "candidate_not_available",
            "release_id": str(resolution.get("release_id") or ""),
        }

    rank = {item: index for index, item in enumerate(requested)}
    baseline_rank = {
        identity: index for index, identity in enumerate(identities)
    }
    ordered_entries = sorted(
        zip(baseline, identities, strict=True),
        key=lambda item: (
            0 if item[1] in rank else 1,
            rank.get(item[1], baseline_rank[item[1]]),
        ),
    )
    ordered = [item for item, _identity in ordered_entries]
    return ordered, {
        "applied": True,
        "reason": str(resolution.get("reason") or "canary"),
        "release_id": str(resolution.get("release_id") or ""),
        "release_sha256": str(resolution.get("release_sha256") or ""),
        "candidate_sha256": str(resolution.get("candidate_sha256") or ""),
        "approval_id": str(resolution.get("approval_id") or ""),
    }


__all__ = [
    "EVOLUTION_CONTROL_ROOT",
    "reorder_routing_candidates",
]
