"""group_analysis 局部 RAG：只在本次分析内构造临时 bundle。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.semantic.reranker import SemanticCandidate
from core.semantic.retriever import lexical_overlap_score
from core.semantic.scoring import weighted_score


@dataclass
class MessageBundle:
    bundle_id: str
    index: int
    start: int
    end: int
    messages: list[dict[str, Any]]
    text: str
    lexical: float = 0.0
    semantic: float | None = None
    reranker: float | None = None

    @property
    def score(self) -> float:
        return weighted_score(
            {"reranker": self.reranker, "semantic": self.semantic, "lexical": self.lexical},
            {"reranker": 0.70, "semantic": 0.20, "lexical": 0.10},
        )


def _message_text(message: dict[str, Any]) -> str:
    return f"[{message.get('time', '??:??')}] [{message.get('user_id', '?')}]: {message.get('content', '')}"


def build_temporary_bundles(
    messages: list[dict[str, Any]],
    *,
    bundle_size: int = 8,
) -> list[MessageBundle]:
    size = max(1, int(bundle_size or 8))
    bundles: list[MessageBundle] = []
    for index, start in enumerate(range(0, len(messages), size)):
        group = messages[start:start + size]
        text = "\n".join(_message_text(message) for message in group)
        bundles.append(MessageBundle(
            bundle_id=f"bundle:{index}",
            index=index,
            start=start,
            end=start + len(group) - 1,
            messages=group,
            text=text,
        ))
    return bundles


def _apply_embedding_scores(query: str, bundles: list[MessageBundle], embedding_provider: Any) -> int:
    if embedding_provider is None or not bundles:
        return 0
    try:
        vectors = embedding_provider.embed([bundle.text for bundle in bundles])
    except Exception:
        return 0
    for bundle, vector in zip(bundles, vectors):
        # group_analysis 这里只做临时 scoring；provider 结果可用即记为弱相关信号。
        bundle.semantic = 1.0 if vector is not None else None
    return len(bundles)


def _apply_reranker(query: str, bundles: list[MessageBundle], reranker_provider: Any, top_k: int) -> int:
    if reranker_provider is None or not bundles:
        return 0
    candidates = [
        SemanticCandidate(
            candidate_id=bundle.bundle_id,
            source_type="group_analysis",
            title=bundle.bundle_id,
            text=bundle.text,
            metadata={"start": bundle.start, "end": bundle.end},
        )
        for bundle in bundles
    ]
    results = reranker_provider.rerank(query, candidates, top_k=None)
    results = results[: max(1, int(top_k or 40))]
    scores = {item.candidate_id: item.score for item in results}
    for bundle in bundles:
        if bundle.bundle_id in scores:
            bundle.reranker = scores[bundle.bundle_id]
    return len(results)


def _bundle_debug(bundle: MessageBundle) -> dict[str, Any]:
    return {
        "bundle_id": bundle.bundle_id,
        "start": bundle.start,
        "end": bundle.end,
        "lexical": bundle.lexical,
        "semantic": bundle.semantic,
        "reranker": bundle.reranker,
        "score": bundle.score,
        "text": bundle.text[:500],
    }


def _select_with_budget(
    bundles: list[MessageBundle],
    *,
    budget_chars: int,
) -> set[int]:
    if budget_chars <= 0:
        return {bundle.index for bundle in bundles}
    selected: set[int] = set()
    used = 0
    for bundle in sorted(bundles, key=lambda item: item.score, reverse=True):
        cost = sum(len(str(message.get("content") or "")) + 16 for message in bundle.messages)
        if selected and used + cost > budget_chars:
            continue
        selected.add(bundle.index)
        used += cost
    return selected


def select_group_analysis_context(
    messages: list[dict[str, Any]],
    *,
    query: str,
    bundle_size: int = 8,
    lexical_top_k: int = 300,
    reranker_top_k: int = 40,
    neighbor_radius: int = 1,
    budget_chars: int = 0,
    embedding_provider: Any = None,
    reranker_provider: Any = None,
) -> dict[str, Any]:
    bundles = build_temporary_bundles(messages, bundle_size=bundle_size)
    query_text = str(query or "").strip()
    for bundle in bundles:
        bundle.lexical = 1.0 if not query_text else lexical_overlap_score(query_text, bundle.text)

    lexical_candidates = sorted(
        [bundle for bundle in bundles if bundle.lexical > 0],
        key=lambda item: item.lexical,
        reverse=True,
    )[: max(1, int(lexical_top_k or 300))]
    embedding_scored = _apply_embedding_scores(query_text, lexical_candidates, embedding_provider)
    lexical_candidates.sort(key=lambda item: item.score, reverse=True)
    reranker_candidates = _apply_reranker(query_text, lexical_candidates, reranker_provider, reranker_top_k)

    if reranker_provider is not None:
        ranked_hits = sorted(
            lexical_candidates,
            key=lambda item: item.reranker if item.reranker is not None else 0.0,
            reverse=True,
        )[: max(1, int(reranker_top_k or 40))]
    else:
        ranked_hits = sorted(lexical_candidates, key=lambda item: item.score, reverse=True)[
            : max(1, int(reranker_top_k or 40))
        ]

    selected_indexes: set[int] = set()
    radius = max(0, int(neighbor_radius or 0))
    for bundle in ranked_hits:
        for index in range(max(0, bundle.index - radius), min(len(bundles), bundle.index + radius + 1)):
            selected_indexes.add(index)
    selected_bundles = [bundle for bundle in bundles if bundle.index in selected_indexes]
    budget_indexes = _select_with_budget(selected_bundles, budget_chars=int(budget_chars or 0))
    final_bundles = [bundle for bundle in selected_bundles if bundle.index in budget_indexes]

    selected_messages: dict[int, dict[str, Any]] = {}
    for bundle in final_bundles:
        for offset, message in enumerate(bundle.messages, start=bundle.start):
            selected_messages[offset] = message
    ordered_messages = [selected_messages[index] for index in sorted(selected_messages)]

    return {
        "messages": ordered_messages,
        "stats_logs": {
            "total_messages": len(messages),
            "bundle_count": len(bundles),
            "lexical_candidates": len(lexical_candidates),
            "temporary_embedding_scored": embedding_scored,
            "reranker_candidates": reranker_candidates,
            "selected_bundles": len(final_bundles),
            "selected_messages": len(ordered_messages),
        },
        "prompt_logs": {
            "hit_bundles": [_bundle_debug(bundle) for bundle in ranked_hits],
            "selected_bundles": [_bundle_debug(bundle) for bundle in final_bundles],
            "selected_message_ids": [message.get("log_id") for message in ordered_messages],
        },
    }
