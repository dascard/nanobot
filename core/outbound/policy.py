"""主动出站领域的纯策略与不可变事实计算。

这里不创建事务，也不查询数据库。ORM 实体只需结构化满足下方 Protocol，领域策略
因此可以脱离 SQLAlchemy 做单元测试，并可被 worker、管理端和未来存储适配器复用。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from core.fencing import positive_seconds
from core.outbound.contracts import (
    PROACTIVE_GENERATION_KINDS,
    PROACTIVE_GENERATION_METADATA_KEY,
    OutboundConflictError,
    OutboundSafetyError,
)


class ProactiveOutreachSnapshotPort(Protocol):
    """构造主动外呼不可变快照所需的最小读取接口。"""

    id: int | None
    user_id: str
    idempotency_key: str
    grounding_json: str
    judge_should: bool | None
    judge_reason: str | None
    next_check_at: datetime | None
    next_intent: str | None
    message: str | None
    forced: bool
    created_at: datetime | None


class DeliveryContractSnapshotPort(Protocol):
    """读取冻结投递合同时所需的最小接口。"""

    delivery_contract_json: str
    delivery_contract_sha256: str


def utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise TypeError("时间参数必须是 datetime 或 null")
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def local_naive_to_utc_naive(value: datetime) -> datetime:
    """把 legacy 本地 naive 时间显式换算为出站账本使用的 UTC naive。"""

    if not isinstance(value, datetime):
        raise TypeError("本地时间必须是 datetime")
    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        value = value.replace(tzinfo=local_tz)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def require_positive_seconds(value: int | float, *, name: str) -> float:
    try:
        return positive_seconds(value, field_name=name)
    except TypeError as exc:
        raise TypeError(f"{name} 必须是有限正数") from exc
    except ValueError as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc


def require_text(value: Any, *, name: str, max_length: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} 必须是字符串")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{name} 必须为 1-{max_length} 字符")
    return normalized


def safe_summary(value: Any) -> str:
    return str(value or "")[:1000]


def canonical_json(value: Mapping[str, Any], *, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} 必须是 JSON object")
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    decoded = json.loads(encoded)
    if type(decoded) is not dict:
        raise TypeError(f"{name} 必须是 JSON object")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proactive_outreach_occurrence_key(
    *,
    log_id: int,
    idempotency_key: str,
    source_revision: str = "",
) -> str:
    """把业务幂等事实映射为不暴露原始 key 的 run occurrence。"""

    if type(log_id) is not int or log_id <= 0:
        raise ValueError("log_id 必须是正整数")
    raw_key = require_text(
        idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    revision = str(source_revision or "").strip()
    digest = hashlib.sha256(
        f"{raw_key}\0{revision}".encode("utf-8")
    ).hexdigest()
    return f"proactive-outreach:{log_id}:{digest}"


def proactive_outreach_delivery_key(
    *,
    log_id: int,
    idempotency_key: str,
    source_revision: str = "",
) -> str:
    occurrence = proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=idempotency_key,
        source_revision=source_revision,
    )
    digest = hashlib.sha256(occurrence.encode("utf-8")).hexdigest()
    return f"proactive-delivery:{log_id}:{digest}"


def proactive_outreach_destination_fingerprint(user_id: str) -> str:
    target = require_text(user_id, name="user_id", max_length=255)
    return hashlib.sha256(
        f"qq_push\0private\0{target}".encode("utf-8")
    ).hexdigest()


def _proactive_grounding(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise OutboundConflictError("主动外呼 grounding 不是有效 JSON object") from exc
    if type(parsed) is not dict:
        raise OutboundConflictError("主动外呼 grounding 必须是 JSON object")
    return parsed


def _normalized_proactive_generation_metadata(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少生成元数据")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise OutboundConflictError("主动外呼 generated 生成元数据版本无效")
    kind = value.get("kind")
    if type(kind) is not str or kind not in PROACTIVE_GENERATION_KINDS:
        raise OutboundConflictError("主动外呼 generated 生成类型无效")
    judge = value.get("judge")
    if type(judge) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少冻结 Judge")
    required_judge_fields = {
        "should_reach_out",
        "reason",
        "next_check_at",
        "next_intent",
        "outreach_kind",
        "research_query",
        "error_type",
    }
    if not required_judge_fields.issubset(judge):
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 字段不完整")
    if judge.get("should_reach_out") is not True:
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 必须允许生成")
    outreach_kind = judge.get("outreach_kind")
    if outreach_kind not in {"message", "research"}:
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 类型无效")
    if kind == "research" and outreach_kind != "research":
        raise OutboundConflictError("主动外呼 research 类型与冻结 Judge 不一致")
    if kind in {"message", "forced"} and outreach_kind != "message":
        raise OutboundConflictError("主动外呼消息类型与冻结 Judge 不一致")
    input_grounding = value.get("input_grounding")
    if type(input_grounding) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少冻结输入")
    normalized_input, input_sha256 = canonical_json(
        input_grounding,
        name="proactive_outreach_generation_input",
    )
    if value.get("input_sha256") != input_sha256:
        raise OutboundConflictError("主动外呼 generated 输入摘要不一致")
    normalized_judge, _judge_sha256 = canonical_json(
        judge,
        name="proactive_outreach_generation_judge",
    )
    normalized = {
        "schema_version": 1,
        "kind": kind,
        "judge": json.loads(normalized_judge),
        "input_grounding": json.loads(normalized_input),
        "input_sha256": input_sha256,
    }
    encoded, _digest = canonical_json(
        normalized,
        name="proactive_outreach_generation_metadata",
    )
    return json.loads(encoded)


def prepare_proactive_generation_grounding(
    *,
    grounding: Mapping[str, Any],
    kind: str,
    judge: Mapping[str, Any],
) -> dict[str, Any]:
    """构造 generated 来源的权威冻结输入，不包含任何生成后输出。"""

    normalized_input, input_sha256 = canonical_json(
        grounding,
        name="proactive_outreach_generation_input",
    )
    normalized_judge, _judge_sha256 = canonical_json(
        judge,
        name="proactive_outreach_generation_judge",
    )
    metadata = _normalized_proactive_generation_metadata({
        "schema_version": 1,
        "kind": kind,
        "judge": json.loads(normalized_judge),
        "input_grounding": json.loads(normalized_input),
        "input_sha256": input_sha256,
    })
    result = json.loads(normalized_input)
    result[PROACTIVE_GENERATION_METADATA_KEY] = metadata
    return result


def proactive_generation_metadata(
    grounding: Mapping[str, Any],
) -> dict[str, Any]:
    """读取并严格校验 generated 来源中的权威生成元数据。"""

    if not isinstance(grounding, Mapping):
        raise OutboundConflictError("主动外呼 grounding 必须是 JSON object")
    return _normalized_proactive_generation_metadata(
        dict(grounding).get(PROACTIVE_GENERATION_METADATA_KEY)
    )


def _snapshot_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return utc_naive(value).isoformat(timespec="microseconds")


def proactive_outreach_source_snapshot(
    row: ProactiveOutreachSnapshotPort,
) -> dict[str, Any]:
    """生成不包含可变投影状态的主动外呼候选快照。"""

    if row.id is None or int(row.id) <= 0:
        raise OutboundConflictError("主动外呼候选尚未持久化")
    user_id = require_text(row.user_id, name="user_id", max_length=255)
    idempotency_key = require_text(
        row.idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    if row.created_at is None:
        raise OutboundConflictError("主动外呼候选缺少 created_at")
    return {
        "schema_version": 1,
        "log_id": int(row.id),
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "grounding": _proactive_grounding(row.grounding_json),
        "judge_should": (
            None if row.judge_should is None else bool(row.judge_should)
        ),
        "judge_reason": str(row.judge_reason or ""),
        "next_check_at": _snapshot_datetime(row.next_check_at),
        "next_intent": str(row.next_intent or ""),
        "message": str(row.message or ""),
        "forced": bool(row.forced),
        "created_at": _snapshot_datetime(row.created_at),
    }


def proactive_outreach_generated_source_snapshot(
    row: ProactiveOutreachSnapshotPort,
) -> dict[str, Any]:
    """冻结正文生成前输入，允许同事务追加受控的生成输出字段。"""

    if row.id is None or int(row.id) <= 0:
        raise OutboundConflictError("主动外呼候选尚未持久化")
    user_id = require_text(row.user_id, name="user_id", max_length=255)
    idempotency_key = require_text(
        row.idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    if row.created_at is None:
        raise OutboundConflictError("主动外呼候选缺少 created_at")
    grounding = _proactive_grounding(row.grounding_json)
    metadata = proactive_generation_metadata(grounding)
    input_grounding = metadata["input_grounding"]
    normalized_input, input_sha256 = canonical_json(
        input_grounding,
        name="proactive_outreach_generation_input",
    )
    if str(metadata.get("input_sha256") or "") != input_sha256:
        raise OutboundConflictError("主动外呼 generated 输入摘要不一致")
    current_input = dict(grounding)
    current_input.pop(PROACTIVE_GENERATION_METADATA_KEY, None)
    for output_key in ("research", "forced_fallback", "generation_error"):
        current_input.pop(output_key, None)
    normalized_current, _current_sha256 = canonical_json(
        current_input,
        name="proactive_outreach_generation_current_input",
    )
    if normalized_current != normalized_input:
        raise OutboundConflictError("主动外呼 generated 冻结输入已变化")
    return {
        "schema_version": 2,
        "log_id": int(row.id),
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "generation": metadata,
        "created_at": _snapshot_datetime(row.created_at),
    }


def proactive_outreach_source_revision(
    row: ProactiveOutreachSnapshotPort,
) -> str:
    _encoded, digest = canonical_json(
        proactive_outreach_source_snapshot(row),
        name="proactive_outreach_source_snapshot",
    )
    return digest


def proactive_outreach_generated_source_revision(
    row: ProactiveOutreachSnapshotPort,
) -> str:
    _encoded, digest = canonical_json(
        proactive_outreach_generated_source_snapshot(row),
        name="proactive_outreach_generated_source_snapshot",
    )
    return digest


def audit_request_sha256(namespace: str, facts: Mapping[str, Any]) -> str:
    encoded, _digest = canonical_json(facts, name=f"{namespace}_request")
    return fingerprint(namespace, encoded)


def audit_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds")


def delivery_contract(
    *,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> tuple[str, str]:
    destination_json, _ = canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    return canonical_json(
        {
            "destination_snapshot": json.loads(destination_json),
            "destination_fingerprint": require_text(
                destination_fingerprint,
                name="destination_fingerprint",
                max_length=64,
            ),
            "target_type": require_text(
                target_type,
                name="target_type",
                max_length=16,
            ),
            "endpoint_key": require_text(
                endpoint_key,
                name="endpoint_key",
                max_length=64,
            ),
            "payload_contract_fingerprint": require_text(
                payload_contract_fingerprint,
                name="payload_contract_fingerprint",
                max_length=64,
            ),
        },
        name="delivery_contract",
    )


def load_delivery_contract(
    run: DeliveryContractSnapshotPort,
) -> dict[str, Any]:
    try:
        value = json.loads(str(run.delivery_contract_json))
    except (TypeError, ValueError) as exc:
        raise OutboundSafetyError("run 的冻结投递合同无法解析") from exc
    if type(value) is not dict:
        raise OutboundSafetyError("run 的冻结投递合同必须是 JSON object")
    encoded, digest = canonical_json(value, name="delivery_contract")
    if (
        encoded != str(run.delivery_contract_json)
        or digest != str(run.delivery_contract_sha256)
    ):
        raise OutboundSafetyError("run 的冻结投递合同完整性校验失败")
    return value


def assert_delivery_contract(
    run: DeliveryContractSnapshotPort,
    *,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> dict[str, Any]:
    frozen_contract = load_delivery_contract(run)
    normalized_frozen_contract = dict(frozen_contract)
    normalized_frozen_contract.pop("endpoint_config_revision", None)
    encoded, _digest = delivery_contract(
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        target_type=target_type,
        endpoint_key=endpoint_key,
        payload_contract_fingerprint=payload_contract_fingerprint,
    )
    if encoded != canonical_json(
        normalized_frozen_contract,
        name="delivery_contract",
    )[0]:
        raise OutboundConflictError("提交事实与 occurrence 冻结投递合同不一致")
    return frozen_contract


def fingerprint(namespace: str, *parts: str) -> str:
    material = "\0".join((namespace, *parts))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def endpoint_circuit_fingerprint(endpoint_key: str) -> str:
    endpoint = require_text(endpoint_key, name="endpoint_key", max_length=64)
    return fingerprint("endpoint", endpoint)


def destination_circuit_fingerprint(
    endpoint_key: str,
    destination_fingerprint: str,
) -> str:
    endpoint = require_text(endpoint_key, name="endpoint_key", max_length=64)
    destination = require_text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    return fingerprint("destination", endpoint, destination)


def payload_contract_circuit_fingerprint(
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> str:
    endpoint = require_text(endpoint_key, name="endpoint_key", max_length=64)
    contract = require_text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )
    return fingerprint("payload_contract", endpoint, contract)


def circuit_facts(
    *,
    endpoint_key: str,
    destination_fingerprint: str,
    payload_contract_fingerprint: str,
    config_revision: str,
) -> tuple[tuple[str, str, str], ...]:
    revision = require_text(
        config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    return (
        ("endpoint", endpoint_circuit_fingerprint(endpoint_key), revision),
        (
            "destination",
            destination_circuit_fingerprint(endpoint_key, destination_fingerprint),
            revision,
        ),
        (
            "payload_contract",
            payload_contract_circuit_fingerprint(
                endpoint_key,
                payload_contract_fingerprint,
            ),
            revision,
        ),
    )


__all__ = [
    "DeliveryContractSnapshotPort",
    "ProactiveOutreachSnapshotPort",
    "assert_delivery_contract",
    "audit_datetime",
    "audit_request_sha256",
    "canonical_json",
    "circuit_facts",
    "delivery_contract",
    "destination_circuit_fingerprint",
    "endpoint_circuit_fingerprint",
    "fingerprint",
    "load_delivery_contract",
    "local_naive_to_utc_naive",
    "payload_contract_circuit_fingerprint",
    "prepare_proactive_generation_grounding",
    "proactive_generation_metadata",
    "proactive_outreach_delivery_key",
    "proactive_outreach_destination_fingerprint",
    "proactive_outreach_generated_source_revision",
    "proactive_outreach_generated_source_snapshot",
    "proactive_outreach_occurrence_key",
    "proactive_outreach_source_revision",
    "proactive_outreach_source_snapshot",
    "require_positive_seconds",
    "require_text",
    "safe_summary",
    "utc_naive",
]
