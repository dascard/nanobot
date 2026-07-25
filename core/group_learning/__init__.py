"""群学习候选治理的领域合同。"""

from core.group_learning.aspects import (
    GROUP_ANALYSIS_ASPECT_IDS,
    GROUP_ANALYSIS_ASPECT_REGISTRY,
    GROUP_ANALYSIS_REPORT_ASPECT_IDS,
    GROUP_ANALYSIS_TOOL_DEFAULT_ASPECT_IDS,
    GROUP_LEARNING_MEMORY_TYPES,
    GroupAnalysisAspectDescriptor,
    default_scheduled_aspects,
    default_tool_aspects,
    list_group_analysis_aspects,
    validate_aspect_selection,
)
from core.group_learning.evidence import (
    GROUP_LEARNING_EVIDENCE_KINDS,
    GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY,
    EvidenceFact,
    EvidencePolicyDecision,
    EvidencePolicyDescriptor,
    evaluate_evidence_policy,
    evidence_policy_for,
)
from core.group_learning.rules import (
    LEARNING_SIGNAL_RULE_REGISTRY,
    LearningRuleDryRun,
    LearningRuleMatch,
    LearningSignalRuleDescriptor,
    canonicalize_learning_text,
    dry_run_learning_rules,
)
from core.group_learning.scheduling import (
    GROUP_LEARNING_SCHEDULE_POLICY,
    GROUP_LEARNING_SCHEDULE_POLICY_REGISTRY,
    GroupLearningSchedulePolicy,
)
from core.group_learning.prompt_injection import (
    GroupMemoryPromptInjectionDecision,
    PROMPT_INJECTABLE_MEMORY_TYPES,
    evaluate_group_memory_prompt_injection,
)

__all__ = [
    "GROUP_ANALYSIS_ASPECT_IDS",
    "GROUP_ANALYSIS_ASPECT_REGISTRY",
    "GROUP_ANALYSIS_REPORT_ASPECT_IDS",
    "GROUP_ANALYSIS_TOOL_DEFAULT_ASPECT_IDS",
    "GROUP_LEARNING_EVIDENCE_KINDS",
    "GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY",
    "GROUP_LEARNING_MEMORY_TYPES",
    "GROUP_LEARNING_SCHEDULE_POLICY",
    "GROUP_LEARNING_SCHEDULE_POLICY_REGISTRY",
    "LEARNING_SIGNAL_RULE_REGISTRY",
    "EvidenceFact",
    "EvidencePolicyDecision",
    "EvidencePolicyDescriptor",
    "GroupAnalysisAspectDescriptor",
    "GroupMemoryPromptInjectionDecision",
    "GroupLearningSchedulePolicy",
    "LearningRuleDryRun",
    "LearningRuleMatch",
    "LearningSignalRuleDescriptor",
    "PROMPT_INJECTABLE_MEMORY_TYPES",
    "canonicalize_learning_text",
    "default_scheduled_aspects",
    "default_tool_aspects",
    "dry_run_learning_rules",
    "evaluate_evidence_policy",
    "evaluate_group_memory_prompt_injection",
    "evidence_policy_for",
    "list_group_analysis_aspects",
    "validate_aspect_selection",
]
