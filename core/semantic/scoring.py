"""RAG 评分与配额工具。"""

from __future__ import annotations

from datetime import datetime, timezone


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def weighted_score(
    components: dict[str, float | None],
    weights: dict[str, float],
) -> float:
    available = {key: value for key, value in components.items() if value is not None}
    active_weights = {
        key: float(weights[key])
        for key in available
        if key in weights and float(weights[key]) > 0
    }
    denom = sum(active_weights.values())
    if denom <= 0:
        return 0.0
    score = sum(active_weights[key] * clamp01(float(available[key])) for key in active_weights) / denom
    return round(score, 12)


def normalize_semantic_cosine(cosine: float | None, *, floor: float) -> float | None:
    if cosine is None:
        return None
    if floor >= 1.0:
        return 1.0
    return clamp01((float(cosine) - float(floor)) / (1.0 - float(floor)))


def normalize_sqlite_bm25(
    raw: float | None,
    *,
    best: float | None,
    worst: float | None,
) -> float | None:
    if raw is None:
        return None
    if best is None or worst is None or worst == best:
        return 1.0
    return clamp01((float(worst) - float(raw)) / (float(worst) - float(best)))


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def recency_score(
    *timestamps: object,
    now: datetime | None = None,
    half_life_days: float = 90.0,
    floor: float = 0.05,
    default: float = 0.5,
) -> float:
    valid = [
        _to_utc_naive(dt)
        for dt in (_coerce_datetime(value) for value in timestamps)
        if dt is not None
    ]
    if not valid:
        return clamp01(default)

    reference = _to_utc_naive(now or datetime.now())
    latest = max(valid)
    age_seconds = max(0.0, (reference - latest).total_seconds())
    half_life_seconds = max(float(half_life_days) * 86400.0, 1.0)
    floor = clamp01(floor)
    score = floor + (1.0 - floor) * (0.5 ** (age_seconds / half_life_seconds))
    return round(clamp01(score), 12)


def passes_relevance_gate(
    components: dict[str, float | None],
    *,
    degraded: bool,
    min_reranker: float = 0.45,
) -> bool:
    reranker = components.get("reranker")
    semantic = components.get("semantic")
    lexical = components.get("lexical")

    if not degraded:
        return reranker is not None and float(reranker) >= float(min_reranker)

    return (
        (semantic is not None and float(semantic) >= 0.35)
        or (lexical is not None and float(lexical) >= 0.10)
    )


def normalize_source_weights(
    weights: dict[str, float],
    enabled_sources: set[str],
) -> dict[str, float]:
    active = {
        source: max(0.0, float(weight))
        for source, weight in weights.items()
        if source in enabled_sources and float(weight) > 0
    }
    total = sum(active.values())
    if total <= 0:
        return {}
    return {source: weight / total for source, weight in active.items()}


def allocate_source_quotas(
    total_k: int,
    source_weights: dict[str, float],
    *,
    min_per_source: int = 3,
) -> dict[str, int]:
    sources = sorted(source_weights, key=source_weights.get, reverse=True)
    total_k = int(total_k)
    if total_k <= 0 or not sources:
        return {}

    if len(sources) > total_k:
        return {source: (1 if index < total_k else 0) for index, source in enumerate(sources)}

    if len(sources) * min_per_source > total_k:
        min_per_source = max(1, total_k // len(sources))

    base = {source: min_per_source for source in sources}
    remaining = max(0, total_k - sum(base.values()))
    raw_extra = {source: remaining * float(source_weights[source]) for source in sources}
    quotas = {source: base[source] + int(raw_extra[source]) for source in sources}

    missing = total_k - sum(quotas.values())
    if missing > 0:
        ranked = sorted(
            sources,
            key=lambda source: raw_extra[source] - int(raw_extra[source]),
            reverse=True,
        )
        for source in ranked[:missing]:
            quotas[source] += 1
    elif missing < 0:
        for source in reversed(sources):
            while missing < 0 and quotas[source] > 0:
                quotas[source] -= 1
                missing += 1

    return quotas
