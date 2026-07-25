"""从冻结 Registry 派生群学习保留词快照。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping


def _term_variants(value: object) -> set[str]:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return set()
    variants = {normalized}
    variants.update(
        part
        for part in re.split(r"[./:_-]+", normalized)
        if part
    )
    return variants


@dataclass(frozen=True, slots=True)
class ReservedTermSnapshot:
    terms: frozenset[str]
    provenance: Mapping[str, tuple[str, ...]]
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )

    def contains(self, value: object) -> bool:
        return bool(_term_variants(value) & self.terms)


def build_reserved_term_snapshot() -> ReservedTermSnapshot:
    """只读取代码所有 Registry，不接受数据库或 Web 任意词表。"""

    from core.lifecycle import (
        COMPATIBILITY_REGISTRY,
        FEATURE_LIFECYCLE_REGISTRY,
    )
    from core.model_provider.route_registry import (
        list_model_route_descriptors,
    )
    from core.prompt_v2.contribution_registry import (
        canonical_prompt_contributions,
    )
    from core.prompt_v2.task_contracts import list_task_contract_keys
    from core.tool_registry import list_tool_descriptors

    sources: dict[str, tuple[str, ...]] = {
        "tools": tuple(
            descriptor.name for descriptor in list_tool_descriptors()
        ),
        "tasks": tuple(list_task_contract_keys()),
        "prompts": tuple(
            descriptor.contribution_id
            for descriptor in canonical_prompt_contributions()
        ),
        "model_routes": tuple(
            descriptor.route_key
            for descriptor in list_model_route_descriptors()
        ),
        "features": tuple(
            descriptor.feature_id
            for descriptor in FEATURE_LIFECYCLE_REGISTRY.descriptors()
        ),
        "compatibility": tuple(
            value
            for descriptor in COMPATIBILITY_REGISTRY.descriptors()
            for value in (
                descriptor.compatibility_id,
                descriptor.alias_value,
                descriptor.canonical_replacement,
            )
        ),
    }
    terms = frozenset(
        term
        for values in sources.values()
        for value in values
        for term in _term_variants(value)
    )
    canonical = json.dumps(
        {
            key: sorted(values)
            for key, values in sorted(sources.items())
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ReservedTermSnapshot(
        terms=terms,
        provenance={
            key: tuple(sorted(values))
            for key, values in sorted(sources.items())
        },
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "ReservedTermSnapshot",
    "build_reserved_term_snapshot",
]
