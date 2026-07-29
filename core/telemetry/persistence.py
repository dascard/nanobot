"""RuntimeEvent 到 SQLAlchemy 观测账本的安全 Sink。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.observability import RuntimeTelemetryEvent
from core.runtime.events import RuntimeEvent


_SENSITIVE_KEY_PARTS = (
    "authorization",
    "command",
    "content",
    "cookie",
    "password",
    "prompt",
    "secret",
    "stderr",
    "stdout",
    "token",
)

_SAFE_TOKEN_COUNT_KEYS = frozenset({
    "input_token_estimate",
    "input_tokens",
    "output_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "total_tokens",
})


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_attributes(
    attributes: Mapping[str, object],
) -> tuple[dict[str, object], int]:
    safe: dict[str, object] = {}
    dropped = 0
    for raw_key, value in attributes.items():
        key = str(raw_key or "").strip()
        if (
            not key
            or len(key) > 64
            or (
                key not in _SAFE_TOKEN_COUNT_KEYS
                and any(
                    part in key.lower()
                    for part in _SENSITIVE_KEY_PARTS
                )
            )
            or (
                key in _SAFE_TOKEN_COUNT_KEYS
                and (type(value) is not int or value < 0)
            )
            or type(value) not in {bool, int, float, str}
        ):
            dropped += 1
            continue
        if isinstance(value, str) and (
            len(value) > 256
            or any(ord(character) < 32 for character in value)
        ):
            dropped += 1
            continue
        safe[key] = value
    return safe, dropped


def _row_from_event(event: RuntimeEvent) -> RuntimeTelemetryEvent:
    attributes, additionally_dropped = _safe_attributes(event.attributes)
    failure_code = str(attributes.get("failure_code") or "")[:64]
    return RuntimeTelemetryEvent(
        event_id=event.event_id,
        name=event.name,
        domain=event.domain,
        phase=event.phase,
        occurred_at=_utc_naive(event.occurred_at),
        request_id=event.context.request_id,
        session_id=event.context.session_id,
        turn_id=event.context.turn_id,
        trace_id=event.context.trace_id,
        run_id=event.context.run_id,
        task_id=event.context.task_id,
        task_run_id=event.context.task_run_id,
        job_id=event.context.job_id,
        tool_call_id=event.context.tool_call_id,
        delivery_id=event.context.delivery_id,
        parent_job_id=event.context.parent_job_id,
        registry_generation=event.provenance.registry_generation,
        registry_sha256=event.provenance.registry_sha256,
        module_id=event.provenance.module_id,
        module_version=event.provenance.module_version,
        artifact_revision=event.provenance.artifact_revision,
        failure_code=failure_code,
        attributes_json=json.dumps(
            attributes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        dropped_attribute_count=(
            event.dropped_attribute_count + additionally_dropped
        ),
    )


class SqlAlchemyRuntimeEventSink:
    """短事务、幂等写入的生产 RuntimeEvent Sink。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        self._session_factory = session_factory

    def emit(self, event: RuntimeEvent) -> None:
        self.emit_many((event,))

    def emit_many(self, events: Sequence[RuntimeEvent]) -> None:
        candidates = tuple(events)
        if not candidates:
            return
        if any(not isinstance(event, RuntimeEvent) for event in candidates):
            raise TypeError("Telemetry Sink 只接受 RuntimeEvent")
        db = self._session_factory()
        try:
            event_ids = tuple(event.event_id for event in candidates)
            existing = {
                str(row[0])
                for row in (
                    db.query(RuntimeTelemetryEvent.event_id)
                    .filter(RuntimeTelemetryEvent.event_id.in_(event_ids))
                    .all()
                )
            }
            for event in candidates:
                if event.event_id not in existing:
                    db.add(_row_from_event(event))
            db.commit()
        except IntegrityError:
            # 多进程重复投递同一 event_id 时，事实已存在即视为幂等成功。
            db.rollback()
            for event in candidates:
                self._emit_one_after_conflict(event)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _emit_one_after_conflict(self, event: RuntimeEvent) -> None:
        db = self._session_factory()
        try:
            if db.get(RuntimeTelemetryEvent, event.event_id) is None:
                db.add(_row_from_event(event))
                db.commit()
        except IntegrityError:
            db.rollback()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


__all__ = ["SqlAlchemyRuntimeEventSink"]
