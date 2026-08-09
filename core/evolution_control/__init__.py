"""离线候选、独立门禁、人工批准和灰度回滚控制面。"""

from .contracts import (
    DATASET_SPLIT_ROLES,
    EVOLUTION_SCHEMA_VERSION,
    EvolutionCandidateBundle,
    EvolutionContractError,
    EvolutionGenerationProof,
    EvolutionTarget,
    EvolutionTargetKind,
    FrozenDatasetManifest,
    FrozenDatasetSplit,
    evolution_catalog_payload,
)
from .gates import (
    EvolutionGateEvidence,
    EvolutionSplitResult,
    evaluate_evolution_candidate,
)
from .store import EvolutionControlStore
from .runtime import reorder_routing_candidates


__all__ = [
    "DATASET_SPLIT_ROLES",
    "EVOLUTION_SCHEMA_VERSION",
    "EvolutionCandidateBundle",
    "EvolutionContractError",
    "EvolutionControlStore",
    "EvolutionGateEvidence",
    "EvolutionGenerationProof",
    "EvolutionSplitResult",
    "EvolutionTarget",
    "EvolutionTargetKind",
    "FrozenDatasetManifest",
    "FrozenDatasetSplit",
    "evaluate_evolution_candidate",
    "evolution_catalog_payload",
    "reorder_routing_candidates",
]
