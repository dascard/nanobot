"""主动外呼模型输出的纯合同策略。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from core.model_provider import ModelProviderResponse
from core.model_provider.response_normalization import strip_think_blocks
from core.proactive_diagnostics import OutreachModelContractError


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def clamp_next_check_at(
    candidate: datetime | None,
    *,
    now: datetime,
    min_interval_min: int,
    max_check_interval_min: int,
) -> datetime:
    lower = now + timedelta(minutes=max(1, int(min_interval_min)))
    upper = now + timedelta(minutes=max(1, int(max_check_interval_min)))
    if candidate is None:
        return lower
    if candidate < lower:
        return lower
    if candidate > upper:
        return upper
    return candidate


def coerce_model_response(value: Any) -> ModelProviderResponse:
    if isinstance(value, ModelProviderResponse):
        return value
    if all(
        hasattr(value, field)
        for field in (
            "content",
            "reasoning_content",
            "finish_reason",
            "usage",
            "raw_response",
        )
    ):
        return ModelProviderResponse(
            content=str(value.content or ""),
            reasoning_content=str(value.reasoning_content or ""),
            finish_reason=value.finish_reason,
            usage=dict(value.usage or {}),
            raw_response=dict(value.raw_response or {}),
        )
    # 仅兼容显式注入的旧字符串 caller；生产 Adapter 始终返回结构化合同。
    return ModelProviderResponse(
        content=strip_think_blocks(str(value or "")),
        reasoning_content="",
        finish_reason="stop",
        usage={},
        raw_response={},
    )


@dataclass(frozen=True, slots=True)
class OutreachJudgeContractPolicy:
    min_interval_min: int = 30
    max_check_interval_min: int = 1440

    def _error(
        self,
        response: ModelProviderResponse,
        *,
        now: datetime,
        error_type: str,
        reason: str,
    ) -> dict[str, Any]:
        next_check_at = clamp_next_check_at(
            None,
            now=now,
            min_interval_min=self.min_interval_min,
            max_check_interval_min=self.max_check_interval_min,
        )
        return {
            "should_reach_out": None,
            "reason": reason[:500],
            "next_check_at": next_check_at.isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "topic_type": "none",
            "topic": "",
            "evidence_ids": [],
            "raw": response.content[:1000],
            "reasoning_content": response.reasoning_content[:1000],
            "finish_reason": response.finish_reason,
            "usage": dict(response.usage),
            "error_type": error_type,
        }

    def parse(
        self,
        response: ModelProviderResponse,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        finish_reason = (response.finish_reason or "").strip().lower()
        if finish_reason != "stop":
            error_type = (
                "model_truncated"
                if finish_reason == "length"
                else "model_finish_error"
            )
            finish_label = finish_reason or "<missing>"
            return self._error(
                response,
                now=now,
                error_type=error_type,
                reason=f"主动外呼 Judge 非正常结束: {finish_label}",
            )

        cleaned = strip_think_blocks(response.content or "").strip()
        if not cleaned:
            return self._error(
                response,
                now=now,
                error_type="empty_response",
                reason="主动外呼 Judge 返回空正文",
            )
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 返回的 JSON 不完整或无效",
            )
        if not isinstance(data, dict):
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 根节点必须是 JSON 对象",
            )

        required_fields = {
            "should_reach_out",
            "reason",
            "next_intent",
            "outreach_kind",
            "research_query",
            "topic_type",
            "topic",
            "evidence_ids",
        }
        if not required_fields.issubset(data):
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 缺少必需字段",
            )

        should_reach_out = data.get("should_reach_out")
        reason = data.get("reason")
        next_intent = data.get("next_intent")
        topic_type_value = data.get("topic_type")
        topic = data.get("topic")
        evidence_ids_value = data.get("evidence_ids")
        if (
            not isinstance(should_reach_out, bool)
            or not isinstance(reason, str)
            or not isinstance(next_intent, str)
            or not isinstance(topic_type_value, str)
            or not isinstance(topic, str)
            or not isinstance(evidence_ids_value, list)
            or any(not isinstance(item, str) for item in evidence_ids_value)
        ):
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 字段类型不符合契约",
            )
        topic_type = topic_type_value.strip().lower()
        evidence_ids = list(dict.fromkeys(
            item.strip()[:200]
            for item in evidence_ids_value
            if item.strip()
        ))
        if topic_type not in {
            "follow_up",
            "discovery",
            "status_check",
            "none",
        }:
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 的 topic_type 不符合契约",
            )
        if should_reach_out:
            if topic_type == "none" or not topic.strip() or not evidence_ids:
                return self._error(
                    response,
                    now=now,
                    error_type="contract_error",
                    reason="主动外呼 Judge 缺少选题或事实依据",
                )
        elif topic_type != "none" or topic.strip() or evidence_ids:
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 不发送时不得保留待生成选题",
            )

        candidate: datetime | None = None
        if "next_check_in_hours" in data:
            hours_value = data.get("next_check_in_hours")
            if type(hours_value) in (int, float):
                hours = float(hours_value)
                if math.isfinite(hours) and hours > 0:
                    candidate = now + timedelta(hours=hours)
        elif "next_check_at" in data:
            next_check_at_value = data.get("next_check_at")
            if isinstance(next_check_at_value, str):
                candidate = parse_iso_datetime(next_check_at_value)
        if candidate is None:
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 缺少有效的下次检查时间",
            )

        outreach_kind_value = data.get("outreach_kind")
        research_query = data.get("research_query")
        outreach_kind = (
            outreach_kind_value.strip().lower()
            if isinstance(outreach_kind_value, str)
            else ""
        )
        if (
            outreach_kind not in {"message", "research"}
            or not isinstance(research_query, str)
        ):
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="主动外呼 Judge 的 outreach_kind/research_query 不符合契约",
            )
        if (
            should_reach_out
            and outreach_kind == "research"
            and not research_query.strip()
        ):
            return self._error(
                response,
                now=now,
                error_type="contract_error",
                reason="研究型主动外呼缺少 research_query",
            )

        next_check_at = clamp_next_check_at(
            candidate,
            now=now,
            min_interval_min=self.min_interval_min,
            max_check_interval_min=self.max_check_interval_min,
        )
        return {
            "should_reach_out": should_reach_out,
            "reason": reason[:500],
            "next_check_at": next_check_at.isoformat(),
            "next_intent": next_intent[:500],
            "outreach_kind": outreach_kind,
            "research_query": research_query.strip()[:1000],
            "topic_type": topic_type,
            "topic": topic.strip()[:500],
            "evidence_ids": evidence_ids[:12],
            "raw": response.content[:1000],
            "reasoning_content": response.reasoning_content[:1000],
            "finish_reason": response.finish_reason,
            "usage": dict(response.usage),
            "error_type": None,
        }


def parse_outreach_judge_contract(
    response: ModelProviderResponse,
    *,
    now: datetime,
    min_interval_min: int,
    max_check_interval_min: int,
) -> dict[str, Any]:
    return OutreachJudgeContractPolicy(
        min_interval_min=min_interval_min,
        max_check_interval_min=max_check_interval_min,
    ).parse(response, now=now)


def parse_generator_message(response: ModelProviderResponse) -> str:
    finish_reason = (response.finish_reason or "").strip().lower()
    if finish_reason != "stop":
        error_type = (
            "model_truncated" if finish_reason == "length" else "model_finish_error"
        )
        finish_label = finish_reason or "<missing>"
        raise OutreachModelContractError(
            f"主动外呼 Generator 非正常结束: {finish_label}",
            error_type=error_type,
        )
    message = strip_think_blocks(response.content or "").strip()
    if not message:
        raise OutreachModelContractError(
            "主动外呼 Generator 返回空正文",
            error_type="empty_response",
        )
    return message


def parse_outreach_quality_contract(
    response: ModelProviderResponse,
) -> dict[str, Any]:
    finish_reason = (response.finish_reason or "").strip().lower()
    if finish_reason != "stop":
        error_type = (
            "model_truncated" if finish_reason == "length" else "model_finish_error"
        )
        raise OutreachModelContractError(
            "主动外呼质量复核未正常结束",
            error_type=error_type,
        )
    cleaned = strip_think_blocks(response.content or "").strip()
    if not cleaned:
        raise OutreachModelContractError(
            "主动外呼质量复核返回空正文",
            error_type="empty_response",
        )
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise OutreachModelContractError(
            "主动外呼质量复核返回无效 JSON",
            error_type="contract_error",
        ) from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("approved"), bool)
        or not isinstance(data.get("reason"), str)
    ):
        raise OutreachModelContractError(
            "主动外呼质量复核字段不符合契约",
            error_type="contract_error",
        )
    return {
        "approved": bool(data["approved"]),
        "reason": str(data["reason"])[:500],
    }


__all__ = [
    "OutreachJudgeContractPolicy",
    "clamp_next_check_at",
    "coerce_model_response",
    "parse_generator_message",
    "parse_iso_datetime",
    "parse_outreach_judge_contract",
    "parse_outreach_quality_contract",
]
