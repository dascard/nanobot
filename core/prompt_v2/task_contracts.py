from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Literal

from jsonschema import Draft202012Validator

from core.private_timing_contracts import (
    PRIVATE_ACTION_VALUES,
    PRIVATE_CONFLICT_SIGNAL_VALUES,
    PRIVATE_DECISION_CONTRACT_VERSION,
    PRIVATE_EFFORT_VALUES,
    PRIVATE_INTENT_VALUES,
    PRIVATE_MATERIAL_STATE_VALUES,
    PRIVATE_MODEL_REASON_CODE_VALUES,
    PRIVATE_RESPONSE_MODE_VALUES,
)
from core.registry import RegistryBuilder, RegistrySnapshot
from core.prompt_v2.template_registry import resolve_template_key
from core.prompt_v2.variables import (
    is_empty_task_call_value,
    referenced_variable_names,
    validate_scoped_template,
)


TaskRenderMode = Literal[
    "user_prompt",
    "system_with_user_ref",
    "paired_messages",
    "code_fallback_only",
]
TaskRenderApi = Literal[
    "prompt",
    "messages",
    "paired_messages",
    "code_fallback_only",
]
TaskTemplateSource = Literal["runtime", "default", "code_fallback"]


class TaskContractError(ValueError):
    """任务模板或调用值不满足代码侧契约。"""


class TaskOutputContractError(ValueError):
    """模型输出无法通过任务输出契约。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "schema_invalid",
        diagnostics: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.code = str(code or "schema_invalid")
        self.diagnostics = tuple(dict(item) for item in diagnostics)
        super().__init__(message)


class TaskCallValueError(TaskContractError):
    """任务调用方没有提供满足合同的动态值。"""


@dataclass(frozen=True)
class TaskContract:
    task_key: str
    owner_module: str
    domain: str
    required_variables: frozenset[str]
    required_call_values: frozenset[str]
    non_empty_call_values: frozenset[str]
    payload_variables: frozenset[str]
    render_mode: TaskRenderMode
    output_contract_id: str
    output_schema: dict[str, Any]
    source_precedence: tuple[TaskTemplateSource, ...]
    editable: bool
    template_failure_policy: str
    output_failure_policy: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["required_variables"] = sorted(self.required_variables)
        data["required_call_values"] = sorted(self.required_call_values)
        data["non_empty_call_values"] = sorted(self.non_empty_call_values)
        data["payload_variables"] = sorted(self.payload_variables)
        data["source_precedence"] = list(self.source_precedence)
        return data


@dataclass(frozen=True)
class TaskInvocationSpec:
    invocation_id: str
    template_keys: tuple[str, ...]
    render_api: TaskRenderApi
    output_parser_owner: str


@dataclass(frozen=True, slots=True)
class TaskContractRegistryEntry:
    task_key: str
    payload_json: str

    @property
    def registry_namespace(self) -> str:
        return "task_contract"

    @property
    def registry_id(self) -> str:
        return self.task_key

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


class TaskContractRegistry:
    """构造后即冻结的后台任务合同注册表。"""

    def __init__(self, contracts: tuple[TaskContract, ...]) -> None:
        registered: dict[str, TaskContract] = {}
        for contract in contracts:
            if contract.task_key in registered:
                raise TaskContractError(
                    f"task contract 重复登记: {contract.task_key}"
                )
            registered[contract.task_key] = contract
        self._contracts = MappingProxyType(registered)
        builder = RegistryBuilder[TaskContractRegistryEntry](
            "task_contract"
        )
        for task_key in sorted(registered):
            builder.register(
                TaskContractRegistryEntry(
                    task_key=task_key,
                    payload_json=json.dumps(
                        registered[task_key].to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        self._registry_snapshot = builder.freeze()

    @property
    def frozen(self) -> bool:
        return True

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[TaskContractRegistryEntry]:
        return self._registry_snapshot

    def get(self, task_key: str) -> TaskContract | None:
        contract = self._contracts.get(resolve_template_key(task_key))
        return copy.deepcopy(contract) if contract is not None else None

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._contracts[key].to_dict()
            for key in sorted(self._contracts)
        )


def _contract(
    task_key: str,
    *,
    owner_module: str,
    domain: str,
    required: tuple[str, ...] = (),
    required_call: tuple[str, ...] | None = None,
    non_empty: tuple[str, ...] = (),
    payload: tuple[str, ...] = (),
    render_mode: TaskRenderMode,
    output_contract_id: str,
    output_schema: dict[str, Any] | None = None,
    template_failure_policy: str = "runtime_default_code_fallback",
    output_failure_policy: str = "fail_closed",
) -> TaskContract:
    required_call_values = frozenset(
        required if required_call is None else required_call
    )
    non_empty_call_values = frozenset(non_empty)
    if not non_empty_call_values <= required_call_values:
        missing = ", ".join(sorted(non_empty_call_values - required_call_values))
        raise ValueError(f"non_empty_call_values 必须同时是 required_call_values: {missing}")
    owner = str(owner_module or "").strip()
    task_domain = str(domain or "").strip()
    if not owner or not task_domain:
        raise ValueError(f"task {task_key} 必须声明 owner_module 和 domain")
    editable = render_mode != "code_fallback_only"
    source_precedence: tuple[TaskTemplateSource, ...]
    if render_mode == "code_fallback_only":
        source_precedence = ("code_fallback",)
    elif template_failure_policy == "runtime_default_fail_closed":
        source_precedence = ("runtime", "default")
    else:
        source_precedence = ("runtime", "default", "code_fallback")
    return TaskContract(
        task_key=resolve_template_key(task_key),
        owner_module=owner,
        domain=task_domain,
        required_variables=frozenset(required),
        required_call_values=required_call_values,
        non_empty_call_values=non_empty_call_values,
        payload_variables=frozenset(payload),
        render_mode=render_mode,
        output_contract_id=output_contract_id,
        output_schema=copy.deepcopy(output_schema or {}),
        source_precedence=source_precedence,
        editable=editable,
        template_failure_policy=template_failure_policy,
        output_failure_policy=output_failure_policy,
    )


def _string_array_schema(
    *,
    max_items: int,
    max_length: int,
) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max_items,
        "items": {"type": "string", "maxLength": max_length},
    }


_SOURCE_IDS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": 16,
    "uniqueItems": True,
    "items": {"type": "integer", "minimum": 1},
}


_NEWS_QUALITY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 20},
        "subtitle": {"type": "string", "maxLength": 30},
        "verdict": {"type": "string", "maxLength": 90},
        "top_story": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "what_happened": {"type": "string", "maxLength": 160},
                "why_it_matters": {"type": "string", "maxLength": 100},
                "source_ids": _SOURCE_IDS_SCHEMA,
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium"],
                },
            },
            "required": [
                "title",
                "what_happened",
                "why_it_matters",
                "source_ids",
                "confidence",
            ],
            "additionalProperties": False,
        },
        "highlights": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "maxLength": 32},
                    "text": {"type": "string", "maxLength": 240},
                    "source_ids": _SOURCE_IDS_SCHEMA,
                    "importance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": [
                    "label",
                    "text",
                    "source_ids",
                    "importance",
                ],
                "additionalProperties": False,
            },
        },
        "details": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 100},
                    "known": _string_array_schema(
                        max_items=6,
                        max_length=180,
                    ),
                    "unknown": _string_array_schema(
                        max_items=4,
                        max_length=180,
                    ),
                    "impact": {"type": "string", "maxLength": 180},
                    "source_labels": _string_array_schema(
                        max_items=8,
                        max_length=80,
                    ),
                },
                "required": [
                    "title",
                    "known",
                    "unknown",
                    "impact",
                    "source_labels",
                ],
                "additionalProperties": False,
            },
        },
        "watchlist": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 180},
                    "reason": {"type": "string", "maxLength": 180},
                    "source_ids": _SOURCE_IDS_SCHEMA,
                },
                "required": ["text", "reason", "source_ids"],
                "additionalProperties": False,
            },
        },
        "missing_info": _string_array_schema(
            max_items=12,
            max_length=180,
        ),
        "closing": {"type": "string", "maxLength": 40},
    },
    "required": [
        "title",
        "subtitle",
        "verdict",
        "top_story",
        "highlights",
        "details",
        "watchlist",
        "missing_info",
        "closing",
    ],
    "additionalProperties": False,
}


_NEWS_RELEVANCE_REVIEW_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                    "relevant": {"type": "boolean"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "model_release",
                            "product",
                            "research",
                            "policy",
                            "funding",
                            "incident",
                            "infrastructure",
                            "other",
                        ],
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "entities": _string_array_schema(
                        max_items=12,
                        max_length=80,
                    ),
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": [
                            "clear_ai_relevance",
                            "clear_non_ai",
                            "cross_domain_ai",
                            "unknown_entity",
                            "insufficient_evidence",
                            "conflicting_signals",
                        ],
                    },
                },
                "required": [
                    "candidate_id",
                    "relevant",
                    "category",
                    "importance",
                    "entities",
                    "confidence",
                    "reason_code",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reviews"],
    "additionalProperties": False,
}


_GROUP_TOPICS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "maxLength": 15},
                    "contributors": _string_array_schema(
                        max_items=8,
                        max_length=64,
                    ),
                    "detail": {"type": "string", "maxLength": 240},
                    "evidence_log_ids": {
                        **_SOURCE_IDS_SCHEMA,
                        "maxItems": 8,
                    },
                },
                "required": [
                    "topic",
                    "contributors",
                    "detail",
                    "evidence_log_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topics"],
    "additionalProperties": False,
}


_GROUP_TITLES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "users": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "maxLength": 64},
                    "title": {"type": "string", "maxLength": 8},
                    "mbti": {
                        "type": "string",
                        "pattern": "^(?:[EI][NS][TF][JP])?$",
                    },
                    "reason": {"type": "string", "maxLength": 180},
                },
                "required": ["user_id", "title", "mbti", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["users"],
    "additionalProperties": False,
}


_GROUP_QUOTES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "maxLength": 64},
                    "content": {"type": "string", "maxLength": 80},
                },
                "required": ["user_id", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["quotes"],
    "additionalProperties": False,
}


_GROUP_QUALITY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 12},
        "subtitle": {"type": "string", "maxLength": 20},
        "dimensions": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 32},
                    "percentage": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "comment": {"type": "string", "maxLength": 180},
                },
                "required": ["name", "percentage", "comment"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "maxLength": 360},
    },
    "required": ["title", "subtitle", "dimensions", "summary"],
    "additionalProperties": False,
}


_GROUP_MEMORY_LEARNING_ITEM_PROPERTIES = {
    "candidate_type": {
        "type": "string",
        "enum": ["expression", "slang", "style"],
    },
    "content": {"type": "string", "minLength": 1, "maxLength": 240},
    "meaning": {"type": "string", "maxLength": 240},
    "evidence_log_ids": {
        **_SOURCE_IDS_SCHEMA,
        "maxItems": 10,
    },
    "reason": {"type": "string", "maxLength": 240},
}


_GROUP_MEMORY_LEARNING_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "new",
                            "merge_into",
                            "add_alias",
                            "conflict_with",
                            "reject",
                        ],
                    },
                    **_GROUP_MEMORY_LEARNING_ITEM_PROPERTIES,
                    "target_memory_id": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "null"},
                        ],
                    },
                },
                "required": [
                    "candidate_id",
                    "action",
                    "candidate_type",
                    "content",
                    "meaning",
                    "evidence_log_ids",
                    "target_memory_id",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
        "discoveries": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": _GROUP_MEMORY_LEARNING_ITEM_PROPERTIES,
                "required": [
                    "candidate_type",
                    "content",
                    "meaning",
                    "evidence_log_ids",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reviews", "discoveries"],
    "additionalProperties": False,
}


_MEMORY_DIGEST_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "preview": {
            "type": "object",
            "properties": {
                "brief": {"type": "string", "maxLength": 200},
                "keywords": _string_array_schema(
                    max_items=8,
                    max_length=32,
                ),
                "participants": _string_array_schema(
                    max_items=8,
                    max_length=32,
                ),
            },
            "required": ["brief", "keywords", "participants"],
            "additionalProperties": False,
        },
        "long_summary": {
            "type": "object",
            "properties": {
                "topic_flow": {"type": "string", "maxLength": 600},
                "important_details": _string_array_schema(
                    max_items=8,
                    max_length=140,
                ),
                "conclusions": _string_array_schema(
                    max_items=6,
                    max_length=120,
                ),
                "open_loops": _string_array_schema(
                    max_items=6,
                    max_length=120,
                ),
            },
            "required": [
                "topic_flow",
                "important_details",
                "conclusions",
                "open_loops",
            ],
            "additionalProperties": False,
        },
        "recall_cards": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "maxLength": 64},
                    "type": {
                        "type": "string",
                        "enum": [
                            "decision",
                            "fact",
                            "todo",
                            "preference",
                            "module",
                            "design_rule",
                        ],
                    },
                    "text": {"type": "string", "maxLength": 120},
                    "keywords": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 6,
                        "items": {
                            "type": "string",
                            "maxLength": 32,
                        },
                    },
                    "importance": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "evidence_log_ids": {
                        **_SOURCE_IDS_SCHEMA,
                        "maxItems": 8,
                    },
                },
                "required": [
                    "card_id",
                    "type",
                    "text",
                    "keywords",
                    "importance",
                    "evidence_log_ids",
                ],
                "additionalProperties": False,
            },
        },
        "quality": {
            "type": "object",
            "properties": {
                "score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "reason": {"type": "string", "maxLength": 180},
            },
            "required": ["score", "reason"],
            "additionalProperties": False,
        },
    },
    "required": [
        "preview",
        "long_summary",
        "recall_cards",
        "quality",
    ],
    "additionalProperties": False,
}


_SESSION_SUMMARY_STRING_LIST = _string_array_schema(
    max_items=16,
    max_length=400,
)
_SESSION_SUMMARY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "open_threads": _SESSION_SUMMARY_STRING_LIST,
        "decisions": _SESSION_SUMMARY_STRING_LIST,
        "important_user_requests": _SESSION_SUMMARY_STRING_LIST,
        "resolved_items": _SESSION_SUMMARY_STRING_LIST,
        "artifacts": _SESSION_SUMMARY_STRING_LIST,
        "participants": _SESSION_SUMMARY_STRING_LIST,
        "keywords": _SESSION_SUMMARY_STRING_LIST,
        "quality": {
            "type": "object",
            "properties": {
                "score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "issues": _string_array_schema(
                    max_items=16,
                    max_length=240,
                ),
            },
            "required": ["score", "issues"],
            "additionalProperties": False,
        },
        "inheritance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": ["carried", "updated", "resolved"],
                    },
                    "target_field": {"type": "string"},
                    "target_index": {"type": "integer", "minimum": 0},
                },
                "required": [
                    "source_id",
                    "disposition",
                    "target_field",
                    "target_index",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "summary",
        "open_threads",
        "decisions",
        "important_user_requests",
        "resolved_items",
        "artifacts",
        "participants",
        "keywords",
        "quality",
        "inheritance",
    ],
    "additionalProperties": False,
}


_TASK_CONTRACT_REGISTRY = TaskContractRegistry((
        _contract(
            "tasks/classifier_legacy",
            owner_module="clients.classifier_client",
            domain="model_routing",
            required=("system_prompt", "message"),
            non_empty=("system_prompt", "message"),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="legacy_reply_v1",
        ),
        _contract(
            "tasks/private_decision",
            owner_module="core.task_runtime",
            domain="chat_routing",
            required=("message", "has_files"),
            non_empty=("message", "has_files"),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id=PRIVATE_DECISION_CONTRACT_VERSION,
            output_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(PRIVATE_ACTION_VALUES),
                    },
                    "effort": {
                        "type": "string",
                        "enum": list(PRIVATE_EFFORT_VALUES),
                    },
                    "intent": {
                        "type": "string",
                        "enum": list(PRIVATE_INTENT_VALUES),
                    },
                    "response_mode": {
                        "type": "string",
                        "enum": list(PRIVATE_RESPONSE_MODE_VALUES),
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "conflicting_signals": {
                        "type": "array",
                        "maxItems": 4,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": list(
                                PRIVATE_CONFLICT_SIGNAL_VALUES
                            ),
                        },
                    },
                    "material_state": {
                        "type": "string",
                        "enum": list(PRIVATE_MATERIAL_STATE_VALUES),
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": list(PRIVATE_MODEL_REASON_CODE_VALUES),
                    },
                },
                "required": [
                    "action",
                    "effort",
                    "intent",
                    "response_mode",
                    "confidence",
                    "conflicting_signals",
                    "material_state",
                    "reason_code",
                ],
                "additionalProperties": False,
            },
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="single_attempt_normal_agent",
        ),
        _contract(
            "tasks/news_daily_quality",
            owner_module="creatures.nanobot.news_daily",
            domain="news_daily",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="news_quality_summary_v1",
            output_schema=_NEWS_QUALITY_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_route_deterministic_fallback",
        ),
        _contract(
            "tasks/news_relevance_review",
            owner_module="core.news",
            domain="news_relevance",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="news_relevance_review_v1",
            output_schema=_NEWS_RELEVANCE_REVIEW_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="single_attempt_conservative_downrank",
        ),
        _contract(
            "tasks/group_analysis_topics",
            owner_module="app.group_analysis",
            domain="group_analysis",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="group_analysis_topics_v1",
            output_schema=_GROUP_TOPICS_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_twice_branch_failed",
        ),
        _contract(
            "tasks/group_analysis_titles",
            owner_module="app.group_analysis",
            domain="group_analysis",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="group_analysis_titles_v1",
            output_schema=_GROUP_TITLES_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_twice_branch_failed",
        ),
        _contract(
            "tasks/group_analysis_quotes",
            owner_module="app.group_analysis",
            domain="group_analysis",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="group_analysis_quotes_v1",
            output_schema=_GROUP_QUOTES_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_twice_branch_failed",
        ),
        _contract(
            "tasks/group_analysis_quality",
            owner_module="app.group_analysis",
            domain="group_analysis",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="group_analysis_quality_v1",
            output_schema=_GROUP_QUALITY_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_twice_branch_failed",
        ),
        _contract(
            "tasks/group_memory_learning",
            owner_module="app.group_learning",
            domain="group_memory_learning",
            required=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="group_memory_learning_v1",
            output_schema=_GROUP_MEMORY_LEARNING_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="single_attempt_preserve_pending",
        ),
        _contract(
            "tasks/timing_gate",
            owner_module="core.private_timing",
            domain="reply_timing",
            required=("pending_text",),
            non_empty=("pending_text",),
            payload=("pending_text",),
            render_mode="system_with_user_ref",
            output_contract_id="timing_gate_v1",
            output_failure_policy="retry_once_then_no_reply",
        ),
        _contract(
            "tasks/timing_proactive",
            owner_module="core.private_timing",
            domain="reply_timing",
            required=("pending_text",),
            non_empty=("pending_text",),
            payload=("pending_text",),
            render_mode="system_with_user_ref",
            output_contract_id="timing_proactive_v1",
            template_failure_policy="runtime_default_fail_closed",
            output_schema={
                "type": "object",
                "properties": {
                    "should_speak": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["should_speak", "reason"],
                "additionalProperties": False,
            },
        ),
        _contract(
            "tasks/memory_extract",
            owner_module="core.persona_preprocess",
            domain="persona",
            required=("conversation", "existing_memory"),
            non_empty=("conversation",),
            payload=("conversation", "existing_memory"),
            render_mode="user_prompt",
            output_contract_id="memory_candidates_v1",
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_once_keep_unprocessed",
        ),
        _contract(
            "tasks/persona_candidate_system",
            owner_module="core.persona_preprocess",
            domain="persona",
            render_mode="user_prompt",
            output_contract_id="memory_candidates_v1",
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_once_keep_unprocessed",
        ),
        _contract(
            "tasks/model_scout_system",
            owner_module="core.legacy_adapter.ModelScoutAgent",
            domain="model_provider",
            render_mode="user_prompt",
            output_contract_id="model_catalog_candidates_v1",
            output_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id"],
                },
            },
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="drop_invalid_keep_catalog",
        ),
        _contract(
            "tasks/reply_contract_retry",
            owner_module="nanobot_kt.reply_contract",
            domain="reply_contract",
            render_mode="code_fallback_only",
            output_contract_id="verified_final_action_v1",
        ),
        _contract(
            "tasks/outreach_extract",
            owner_module="core.proactive",
            domain="proactive_outreach",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_threads_v1",
            template_failure_policy="runtime_default_fail_closed",
        ),
        _contract(
            "tasks/outreach_judge",
            owner_module="core.proactive",
            domain="proactive_outreach",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_judge_v1",
            template_failure_policy="runtime_default_fail_closed",
            output_schema={
                "type": "object",
                "properties": {
                    "should_reach_out": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "next_check_in_hours": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                    },
                    "next_check_at": {"type": "string"},
                    "next_intent": {"type": "string"},
                    "outreach_kind": {
                        "type": "string",
                        "enum": ["message", "research"],
                    },
                    "research_query": {"type": "string"},
                },
                "required": [
                    "should_reach_out",
                    "reason",
                    "next_intent",
                    "outreach_kind",
                    "research_query",
                ],
                "oneOf": [
                    {"required": ["next_check_in_hours"]},
                    {"required": ["next_check_at"]},
                ],
                "additionalProperties": False,
            },
        ),
        _contract(
            "tasks/outreach_generate",
            owner_module="core.proactive",
            domain="proactive_outreach",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_message_v1",
            template_failure_policy="runtime_default_fail_closed",
        ),
        _contract(
            "tasks/proactive_research",
            owner_module="core.proactive_research",
            domain="proactive_outreach",
            required=("pending_text",),
            non_empty=("pending_text",),
            payload=("pending_text",),
            render_mode="user_prompt",
            output_contract_id="verified_research_final_action_v1",
            output_failure_policy="block",
        ),
        _contract(
            "tasks/memory_digest_system",
            owner_module="app.memory_digest",
            domain="memory_digest",
            render_mode="paired_messages",
            output_contract_id="memory_digest_v2",
            output_schema=_MEMORY_DIGEST_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="single_attempt_deterministic_fallback",
        ),
        _contract(
            "tasks/memory_digest_user",
            owner_module="app.memory_digest",
            domain="memory_digest",
            required=(
                "date",
                "session_id",
                "source_id",
                "source_type",
                "source_range",
                "message_count",
                "digest_source",
            ),
            non_empty=(
                "session_id",
                "source_id",
                "source_type",
                "source_range",
                "digest_source",
            ),
            render_mode="paired_messages",
            output_contract_id="memory_digest_v2",
            output_schema=_MEMORY_DIGEST_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="single_attempt_deterministic_fallback",
        ),
        _contract(
            "tasks/session_summary_system",
            owner_module="app.session_memory",
            domain="session_memory",
            render_mode="user_prompt",
            output_contract_id="session_summary_v1",
            output_schema=_SESSION_SUMMARY_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_then_preserve_pending",
        ),
        _contract(
            "tasks/session_summary_output",
            owner_module="app.session_memory",
            domain="session_memory",
            render_mode="user_prompt",
            output_contract_id="session_summary_v1",
            output_schema=_SESSION_SUMMARY_OUTPUT_SCHEMA,
            template_failure_policy="runtime_default_fail_closed",
            output_failure_policy="retry_then_preserve_pending",
        ),
))


_TASK_INVOCATION_SPECS: tuple[TaskInvocationSpec, ...] = (
    TaskInvocationSpec(
        "classifier_legacy",
        ("tasks/classifier_legacy",),
        "messages",
        "clients.classifier_client.Guardrail._validate_output",
    ),
    TaskInvocationSpec(
        "private_decision",
        ("tasks/private_decision",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "news_daily_quality",
        ("tasks/news_daily_quality",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "news_relevance_review",
        ("tasks/news_relevance_review",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "group_analysis_topics",
        ("tasks/group_analysis_topics",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "group_analysis_titles",
        ("tasks/group_analysis_titles",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "group_analysis_quotes",
        ("tasks/group_analysis_quotes",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "group_analysis_quality",
        ("tasks/group_analysis_quality",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "group_memory_learning",
        ("tasks/group_memory_learning",),
        "messages",
        "core.task_runtime.TaskRuntime",
    ),
    TaskInvocationSpec(
        "timing_gate",
        ("tasks/timing_gate",),
        "messages",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "timing_proactive",
        ("tasks/timing_proactive",),
        "messages",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "memory_extract",
        ("tasks/memory_extract",),
        "prompt",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "persona_candidate_system",
        ("tasks/persona_candidate_system",),
        "prompt",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "model_scout",
        ("tasks/model_scout_system",),
        "prompt",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "reply_contract_retry",
        ("tasks/reply_contract_retry",),
        "code_fallback_only",
        "nanobot_kt.reply_contract.parse_structured_final_action",
    ),
    TaskInvocationSpec(
        "outreach_extract",
        ("tasks/outreach_extract",),
        "messages",
        "core.proactive_outreach.extract_recent_threads",
    ),
    TaskInvocationSpec(
        "outreach_judge",
        ("tasks/outreach_judge",),
        "messages",
        "core.proactive.model_policy.parse_outreach_judge_contract",
    ),
    TaskInvocationSpec(
        "outreach_generate",
        ("tasks/outreach_generate",),
        "messages",
        "core.proactive_outreach.generate_outreach_message",
    ),
    TaskInvocationSpec(
        "proactive_research",
        ("tasks/proactive_research",),
        "prompt",
        "nanobot_kt.reply_contract.parse_structured_final_action",
    ),
    TaskInvocationSpec(
        "memory_digest",
        ("tasks/memory_digest_system", "tasks/memory_digest_user"),
        "paired_messages",
        "app.memory_digest.llm_builder.parse_llm_digest_response",
    ),
    TaskInvocationSpec(
        "session_summary",
        ("tasks/session_summary_system", "tasks/session_summary_output"),
        "prompt",
        "app.session_memory.llm_summarizer._accept_summary_batch_payload",
    ),
)


def get_task_contract(task_key: str) -> TaskContract | None:
    return _TASK_CONTRACT_REGISTRY.get(task_key)


def list_task_contract_keys() -> list[str]:
    return list(_TASK_CONTRACT_REGISTRY.keys())


def task_contract_registry_snapshot() -> tuple[dict[str, Any], ...]:
    return _TASK_CONTRACT_REGISTRY.snapshot()


def task_contract_registry_kernel_snapshot(
) -> RegistrySnapshot[TaskContractRegistryEntry]:
    return _TASK_CONTRACT_REGISTRY.registry_snapshot


def list_task_invocation_specs() -> list[TaskInvocationSpec]:
    return copy.deepcopy(list(_TASK_INVOCATION_SPECS))


def get_task_invocation_spec(invocation_id: str) -> TaskInvocationSpec | None:
    target = str(invocation_id or "").strip()
    for spec in _TASK_INVOCATION_SPECS:
        if spec.invocation_id == target:
            return copy.deepcopy(spec)
    return None


def get_task_invocation_for_template(task_key: str) -> TaskInvocationSpec | None:
    key = resolve_template_key(task_key)
    matches = [spec for spec in _TASK_INVOCATION_SPECS if key in spec.template_keys]
    if len(matches) > 1:
        raise TaskContractError(f"task {key} registered by multiple invocations")
    return copy.deepcopy(matches[0]) if matches else None


def validate_task_invocation_specs() -> None:
    expected_api: dict[TaskRenderMode, TaskRenderApi] = {
        "user_prompt": "prompt",
        "system_with_user_ref": "messages",
        "paired_messages": "paired_messages",
        "code_fallback_only": "code_fallback_only",
    }
    seen: set[str] = set()
    for spec in _TASK_INVOCATION_SPECS:
        if not spec.invocation_id or not spec.output_parser_owner.strip():
            raise TaskContractError("task invocation 缺少 ID 或 output parser owner")
        if not spec.template_keys:
            raise TaskContractError(f"task invocation {spec.invocation_id} 没有模板")
        for key in spec.template_keys:
            canonical = resolve_template_key(key)
            if canonical in seen:
                raise TaskContractError(f"task {canonical} invocation 重复登记")
            seen.add(canonical)
            contract = get_task_contract(canonical)
            if contract is None:
                raise TaskContractError(f"task {canonical} invocation 没有合同")
            if expected_api[contract.render_mode] != spec.render_api:
                raise TaskContractError(
                    f"task {canonical} render mode 与 invocation 不一致"
                )
    missing = sorted(set(_TASK_CONTRACT_REGISTRY.keys()) - seen)
    if missing:
        raise TaskContractError(
            "task contracts missing invocation: " + ", ".join(missing)
        )


def validate_task_template(task_key: str, body: str) -> TaskContract | None:
    key = resolve_template_key(task_key)
    contract = get_task_contract(key)
    validate_scoped_template(key, body)
    if contract is None or contract.render_mode == "code_fallback_only":
        return contract
    referenced = referenced_variable_names(body)
    missing = sorted(contract.required_variables - referenced)
    if missing:
        raise TaskContractError(
            f"task {key} missing required variables: {', '.join(missing)}"
        )
    return contract


def validate_task_call_values(task_key: str, values: dict) -> TaskContract | None:
    key = resolve_template_key(task_key)
    contract = get_task_contract(key)
    if contract is None:
        return None
    missing = sorted(
        name
        for name in contract.required_call_values
        if name not in values or values.get(name) is None
    )
    if missing:
        raise TaskCallValueError(
            f"task {key} missing required call values: {', '.join(missing)}"
        )
    empty = sorted(
        name
        for name in contract.non_empty_call_values
        if is_empty_task_call_value(values.get(name))
    )
    if empty:
        raise TaskCallValueError(
            f"task {key} empty required call values: {', '.join(empty)}"
        )
    return contract


def _parse_json_object(raw: str, *, contract_id: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        raise TaskOutputContractError(
            f"{contract_id}: empty_output",
            code="empty_output",
        )
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TaskOutputContractError(
            f"{contract_id}: invalid_json",
            code="invalid_json",
        ) from exc
    if not isinstance(value, dict):
        raise TaskOutputContractError(
            f"{contract_id}: root_must_be_object",
            code="schema_invalid",
            diagnostics=({
                "code": "root_must_be_object",
                "path": "$",
                "rule": "type",
                "summary": "输出根节点必须是对象",
            },),
        )
    return value


def _validate_output_schema(
    contract: TaskContract,
    value: dict[str, Any],
) -> dict[str, Any]:
    if not contract.output_schema:
        return value
    errors = sorted(
        Draft202012Validator(contract.output_schema).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            str(error.validator),
        ),
    )
    if not errors:
        return value
    error = errors[0]
    validator = str(error.validator or "schema")
    code = (
        "field_out_of_range"
        if validator in {
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
        }
        else "schema_invalid"
    )
    path = ".".join(str(part) for part in error.absolute_path) or "$"
    raise TaskOutputContractError(
        f"{contract.output_contract_id}: {code}",
        code=code,
        diagnostics=({
            "code": code,
            "path": path,
            "rule": validator,
            "summary": f"输出不满足 {validator} 约束",
        },),
    )


def _parse_private_decision(contract: TaskContract, raw: str) -> dict:
    value = _parse_json_object(
        raw,
        contract_id=contract.output_contract_id,
    )
    return _validate_output_schema(contract, value)


def _parse_memory_candidates(raw: str) -> dict:
    value = _parse_json_object(raw, contract_id="memory_candidates_v1")
    if "candidates" not in value:
        raise TaskOutputContractError("memory_candidates_v1: missing_candidates")
    candidates = value["candidates"]
    if not isinstance(candidates, list):
        raise TaskOutputContractError("memory_candidates_v1: candidates_must_be_list")
    if any(not isinstance(item, dict) for item in candidates):
        raise TaskOutputContractError("memory_candidates_v1: candidate_must_be_object")
    return {"candidates": candidates}


def _parse_model_catalog_candidates(raw: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        raise TaskOutputContractError("model_catalog_candidates_v1: empty_output")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TaskOutputContractError(
            "model_catalog_candidates_v1: invalid_json"
        ) from exc
    models = [value] if isinstance(value, dict) else value
    if not isinstance(models, list):
        raise TaskOutputContractError(
            "model_catalog_candidates_v1: root_must_be_array"
        )
    normalized: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            raise TaskOutputContractError(
                "model_catalog_candidates_v1: item_must_be_object"
            )
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            raise TaskOutputContractError(
                "model_catalog_candidates_v1: id_required"
            )
        normalized.append(dict(item, id=model_id))
    return {"models": normalized}


def _parse_timing_gate(raw: str) -> dict:
    value = _parse_json_object(raw, contract_id="timing_gate_v1")
    allowed = {"action", "delay_seconds", "reason"}
    if set(value) - allowed:
        raise TaskOutputContractError("timing_gate_v1: unsupported_fields")
    action = value.get("action")
    if action not in {"continue", "wait", "no_reply"}:
        raise TaskOutputContractError("timing_gate_v1: invalid_action")
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        raise TaskOutputContractError("timing_gate_v1: reason_must_be_string")
    delay = value.get("delay_seconds")
    if action == "wait":
        if isinstance(delay, bool) or not isinstance(delay, int) or not 3 <= delay <= 15:
            raise TaskOutputContractError("timing_gate_v1: invalid_wait_delay")
    elif delay is not None:
        raise TaskOutputContractError("timing_gate_v1: delay_only_allowed_for_wait")
    return {
        "action": action,
        "delay_seconds": delay if action == "wait" else None,
        "reason": reason[:200],
    }


def _parse_timing_proactive(raw: str) -> dict:
    value = _parse_json_object(raw, contract_id="timing_proactive_v1")
    if set(value) != {"should_speak", "reason"}:
        raise TaskOutputContractError("timing_proactive_v1: invalid_fields")
    should_speak = value.get("should_speak")
    reason = value.get("reason")
    if type(should_speak) is not bool:
        raise TaskOutputContractError(
            "timing_proactive_v1: should_speak_must_be_boolean"
        )
    if not isinstance(reason, str):
        raise TaskOutputContractError("timing_proactive_v1: reason_must_be_string")
    return {
        "should_speak": should_speak,
        "reason": reason[:200],
    }


def parse_task_output(task_key: str, raw: str) -> dict:
    contract = get_task_contract(task_key)
    if contract is None:
        raise TaskOutputContractError("unregistered_task_contract")
    if contract.output_contract_id == PRIVATE_DECISION_CONTRACT_VERSION:
        return _parse_private_decision(contract, raw)
    if contract.output_contract_id == "memory_candidates_v1":
        return _parse_memory_candidates(raw)
    if contract.output_contract_id == "model_catalog_candidates_v1":
        return _parse_model_catalog_candidates(raw)
    if contract.output_contract_id == "timing_gate_v1":
        return _parse_timing_gate(raw)
    if contract.output_contract_id == "timing_proactive_v1":
        return _parse_timing_proactive(raw)
    if contract.output_schema:
        value = _parse_json_object(
            raw,
            contract_id=contract.output_contract_id,
        )
        return _validate_output_schema(contract, value)
    raise TaskOutputContractError(
        f"{contract.output_contract_id}: parser_not_implemented"
    )
