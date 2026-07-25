"""安全内容 Rule Engine 的稳定公开接口。"""

from core.content_rules.adapters import (
    content_rule_descriptor_from_mapping,
    content_rule_descriptors_from_mappings,
    user_block_rule_descriptor,
    validate_web_content_rule,
)
from core.content_rules.contracts import (
    ContentRuleAction,
    ContentRuleAuditPolicy,
    ContentRuleDescriptor,
    ContentRuleEvaluation,
    ContentRuleFailure,
    ContentRuleFailurePolicy,
    ContentRuleInput,
    ContentRuleMatch,
    ContentRuleMatchKind,
    ContentRuleScope,
    ContentRuleSetDescriptor,
)
from core.content_rules.engine import (
    ContentRuleEngine,
    ContentRuleRegistry,
)


__all__ = [
    "ContentRuleAction",
    "ContentRuleAuditPolicy",
    "ContentRuleDescriptor",
    "ContentRuleEngine",
    "ContentRuleEvaluation",
    "ContentRuleFailure",
    "ContentRuleFailurePolicy",
    "ContentRuleInput",
    "ContentRuleMatch",
    "ContentRuleMatchKind",
    "ContentRuleRegistry",
    "ContentRuleScope",
    "ContentRuleSetDescriptor",
    "content_rule_descriptor_from_mapping",
    "content_rule_descriptors_from_mappings",
    "user_block_rule_descriptor",
    "validate_web_content_rule",
]
