"""输出 Schema 之后的确定性业务交叉字段校验。"""

from __future__ import annotations

from collections.abc import Mapping

from core.private_timing_contracts import (
    PRIVATE_DECISION_CONTRACT_VERSION,
    PRIVATE_TEMPLATE_INTENT_VALUES,
)
from core.task_runtime.contracts import TaskValidationDiagnostic


class TaskBusinessValidationError(ValueError):
    def __init__(
        self,
        summary: str,
        *,
        diagnostics: tuple[TaskValidationDiagnostic, ...],
    ) -> None:
        self.summary = str(summary or "业务后置校验失败")
        self.diagnostics = tuple(diagnostics)
        super().__init__(self.summary)


def _group_learning_validation_error(
    *,
    code: str,
    path: str,
    summary: str,
) -> TaskBusinessValidationError:
    return TaskBusinessValidationError(
        f"群记忆审核未通过确定性范围校验：{summary}",
        diagnostics=(
            TaskValidationDiagnostic(
                code=code,
                path=path,
                rule="evidence_scope",
                summary=summary,
            ),
        ),
    )


def _validate_group_memory_learning(
    value: Mapping[str, object],
    *,
    request_context: Mapping[str, object],
) -> dict[str, object]:
    allowed_candidate_ids = {
        str(item or "").strip()
        for item in request_context.get("allowed_candidate_ids", ())
        if str(item or "").strip()
    }
    raw_candidate_types = request_context.get("candidate_types", {})
    candidate_types = (
        {
            str(candidate_id or "").strip(): str(
                candidate_type or ""
            ).strip()
            for candidate_id, candidate_type
            in raw_candidate_types.items()
        }
        if isinstance(raw_candidate_types, Mapping)
        else {}
    )
    if set(candidate_types) != allowed_candidate_ids:
        raise _group_learning_validation_error(
            code="group_learning_candidate_contract_invalid",
            path="request_context.candidate_types",
            summary="候选类型合同必须精确覆盖当前批次",
        )
    allowed_evidence_ids = {
        int(item)
        for item in request_context.get(
            "allowed_evidence_log_ids",
            (),
        )
        if type(item) is int and item > 0
    }
    selected_candidate_types = {
        str(item or "").strip()
        for item in request_context.get(
            "selected_candidate_types",
            (),
        )
        if str(item or "").strip()
    }
    raw_target_ids = request_context.get(
        "allowed_target_memory_ids",
        {},
    )
    target_ids = (
        {
            str(candidate_id or "").strip(): {
                int(item)
                for item in values
                if type(item) is int and item > 0
            }
            for candidate_id, values in raw_target_ids.items()
            if isinstance(values, (tuple, list, set, frozenset))
        }
        if isinstance(raw_target_ids, Mapping)
        else {}
    )

    reviews = tuple(value.get("reviews") or ())
    review_ids = [
        str(review.get("candidate_id") or "").strip()
        for review in reviews
        if isinstance(review, Mapping)
    ]
    if (
        len(review_ids) != len(reviews)
        or len(review_ids) != len(set(review_ids))
        or set(review_ids) != allowed_candidate_ids
    ):
        raise _group_learning_validation_error(
            code="group_learning_candidate_scope_mismatch",
            path="reviews.candidate_id",
            summary="reviews 必须逐一且仅覆盖当前批次候选",
        )

    target_actions = {
        "merge_into",
        "add_alias",
        "conflict_with",
    }
    for review in reviews:
        if not isinstance(review, Mapping):
            raise _group_learning_validation_error(
                code="group_learning_review_invalid",
                path="reviews",
                summary="review 必须是对象",
            )
        candidate_id = str(
            review.get("candidate_id") or ""
        ).strip()
        candidate_type = str(
            review.get("candidate_type") or ""
        ).strip()
        if (
            candidate_type != candidate_types.get(candidate_id)
            or candidate_type not in selected_candidate_types
        ):
            raise _group_learning_validation_error(
                code="group_learning_candidate_type_mismatch",
                path="reviews.candidate_type",
                summary="候选类型必须匹配当前批次与已选方面",
            )
        evidence_ids = {
            int(item)
            for item in review.get("evidence_log_ids") or ()
            if type(item) is int and item > 0
        }
        if not evidence_ids or not evidence_ids <= allowed_evidence_ids:
            raise _group_learning_validation_error(
                code="group_learning_evidence_not_authorized",
                path="reviews.evidence_log_ids",
                summary="正式 evidence 只能引用本批可信新增消息",
            )
        action = str(review.get("action") or "").strip()
        target_memory_id = review.get("target_memory_id")
        if action in target_actions:
            if (
                type(target_memory_id) is not int
                or target_memory_id
                not in target_ids.get(candidate_id, set())
            ):
                raise _group_learning_validation_error(
                    code="group_learning_target_not_authorized",
                    path="reviews.target_memory_id",
                    summary="目标记忆必须属于当前会话、同类型召回集",
                )
        elif target_memory_id is not None:
            raise _group_learning_validation_error(
                code="group_learning_target_not_allowed",
                path="reviews.target_memory_id",
                summary="new／reject 动作不能携带目标记忆",
            )

    for discovery in value.get("discoveries") or ():
        if not isinstance(discovery, Mapping):
            raise _group_learning_validation_error(
                code="group_learning_discovery_invalid",
                path="discoveries",
                summary="模型补充候选必须是对象",
            )
        candidate_type = str(
            discovery.get("candidate_type") or ""
        ).strip()
        if candidate_type not in selected_candidate_types:
            raise _group_learning_validation_error(
                code="group_learning_discovery_type_not_selected",
                path="discoveries.candidate_type",
                summary="模型只能补充已选择方面的候选",
            )
        evidence_ids = {
            int(item)
            for item in discovery.get("evidence_log_ids") or ()
            if type(item) is int and item > 0
        }
        if not evidence_ids or not evidence_ids <= allowed_evidence_ids:
            raise _group_learning_validation_error(
                code="group_learning_discovery_evidence_not_authorized",
                path="discoveries.evidence_log_ids",
                summary="模型补充候选必须引用本批可信新增消息",
            )
    return dict(value)


def _validate_private_decision(
    value: Mapping[str, object],
    *,
    request_context: Mapping[str, object],
) -> dict[str, object]:
    action = str(value["action"])
    effort = str(value["effort"])
    intent = str(value["intent"])
    response_mode = str(value["response_mode"])
    confidence = float(value["confidence"])
    conflicting_signals = list(value["conflicting_signals"])
    if action == "reply_now" and response_mode == "none":
        raise TaskBusinessValidationError(
            "reply_now 必须选择 template 或 agent",
            diagnostics=(
                TaskValidationDiagnostic(
                    code="private_decision_response_mode_conflict",
                    path="response_mode",
                    rule="cross_field",
                    summary="reply_now 不能使用 none response_mode",
                ),
            ),
        )
    if action in {"no_reply", "wait"} and response_mode != "none":
        raise TaskBusinessValidationError(
            "no_reply／wait 只能使用 none response_mode",
            diagnostics=(
                TaskValidationDiagnostic(
                    code="private_decision_response_mode_conflict",
                    path="response_mode",
                    rule="cross_field",
                    summary="非回复动作不能触发模板或 Agent",
                ),
            ),
        )
    if response_mode == "template":
        raw_threshold = request_context.get(
            "template_confidence_threshold",
            0.85,
        )
        if (
            isinstance(raw_threshold, bool)
            or not isinstance(raw_threshold, (int, float))
        ):
            raise TaskBusinessValidationError(
                "模板置信度门槛无效",
                diagnostics=(
                    TaskValidationDiagnostic(
                        code="private_decision_threshold_invalid",
                        path="request_context.template_confidence_threshold",
                        rule="policy_input",
                        summary="模板门槛必须是 0..1 数值",
                    ),
                ),
            )
        threshold = float(raw_threshold)
        if (
            effort != "casual"
            or intent not in PRIVATE_TEMPLATE_INTENT_VALUES
            or conflicting_signals
            or confidence < threshold
        ):
            raise TaskBusinessValidationError(
                "模板 Fast Path 不满足确定性策略",
                diagnostics=(
                    TaskValidationDiagnostic(
                        code="private_decision_template_conflict",
                        path="response_mode",
                        rule="cross_field",
                        summary=(
                            "模板要求 casual、有限 intent、无冲突且达到置信度门槛"
                        ),
                    ),
                ),
            )
    elif effort == "casual":
        raise TaskBusinessValidationError(
            "casual effort 只能用于模板 Fast Path",
            diagnostics=(
                TaskValidationDiagnostic(
                    code="private_decision_effort_conflict",
                    path="effort",
                    rule="cross_field",
                    summary="Agent 路径不得使用 casual effort",
                ),
            ),
        )
    return dict(value)


def validate_task_business_output(
    output_contract_id: str,
    value: Mapping[str, object],
    *,
    request_context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    context = request_context or {}
    if output_contract_id == PRIVATE_DECISION_CONTRACT_VERSION:
        return _validate_private_decision(
            value,
            request_context=context,
        )
    if output_contract_id == "news_quality_summary_v1":
        allowed = {
            int(item)
            for item in context.get("allowed_source_ids", ())
            if type(item) is int and item > 0
        }
        referenced: set[int] = set()
        top_story = value.get("top_story")
        if isinstance(top_story, Mapping):
            referenced.update(top_story.get("source_ids") or ())
        for field_name in ("highlights", "watchlist"):
            for item in value.get(field_name) or ():
                if isinstance(item, Mapping):
                    referenced.update(item.get("source_ids") or ())
        if not referenced <= allowed:
            raise TaskBusinessValidationError(
                "日报输出引用了未授权的来源卡片",
                diagnostics=(
                    TaskValidationDiagnostic(
                        code="news_source_id_not_authorized",
                        path="source_ids",
                        rule="evidence_scope",
                        summary="输出 source_ids 必须来自当前候选卡片",
                    ),
                ),
            )
        return dict(value)
    if output_contract_id == "news_relevance_review_v1":
        allowed = {
            str(item).strip()
            for item in context.get("allowed_candidate_ids", ())
            if str(item).strip()
        }
        candidate_ids = [
            str(item.get("candidate_id") or "").strip()
            for item in value.get("reviews") or ()
            if isinstance(item, Mapping)
        ]
        if (
            len(candidate_ids) != len(set(candidate_ids))
            or set(candidate_ids) != allowed
        ):
            raise TaskBusinessValidationError(
                "新闻审核必须逐一覆盖且只能引用当前批次候选",
                diagnostics=(
                    TaskValidationDiagnostic(
                        code="news_candidate_scope_mismatch",
                        path="reviews.candidate_id",
                        rule="evidence_scope",
                        summary=(
                            "candidate_id 集合必须与当前审核批次精确一致且不重复"
                        ),
                    ),
                ),
            )
        return dict(value)
    if output_contract_id == "group_analysis_topics_v1":
        allowed = {
            int(item)
            for item in context.get("allowed_evidence_log_ids", ())
            if type(item) is int and item > 0
        }
        referenced = {
            int(log_id)
            for topic in value.get("topics") or ()
            if isinstance(topic, Mapping)
            for log_id in topic.get("evidence_log_ids") or ()
        }
        if not referenced <= allowed:
            raise TaskBusinessValidationError(
                "群话题引用了当前窗口外的证据",
                diagnostics=(
                    TaskValidationDiagnostic(
                        code="group_evidence_not_authorized",
                        path="topics.evidence_log_ids",
                        rule="evidence_scope",
                        summary="证据 log_id 必须来自当前可信窗口",
                    ),
                ),
            )
        return dict(value)
    if output_contract_id == "group_memory_learning_v1":
        return _validate_group_memory_learning(
            value,
            request_context=context,
        )
    return dict(value)
