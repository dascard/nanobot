"""Session Summary 的纯 LLM 请求合同。"""

from __future__ import annotations

import hashlib
import html
import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from core.context_builder import sanitize_prompt_text

CANONICAL_SUMMARY_FIELDS = (
    "summary",
    "open_threads",
    "decisions",
    "important_user_requests",
    "resolved_items",
    "artifacts",
    "participants",
    "keywords",
)
_SUMMARY_LIST_FIELDS = CANONICAL_SUMMARY_FIELDS[1:]
_OBLIGATION_FIELDS = (
    "open_threads",
    "decisions",
    "important_user_requests",
    "artifacts",
)


@dataclass(frozen=True, slots=True)
class TurnFragment:
    """单条 turn 完整净化后的有序分片。"""

    turn_id: int
    role: str
    fragment_index: int
    fragment_count: int
    content: str
    sanitized_sha256: str
    fragment_sha256: str


@dataclass(frozen=True, slots=True)
class TurnCoverageManifest:
    """按来源顺序记录 turn 与分片覆盖。"""

    ordered_turn_ids: tuple[int, ...]
    turn_hashes: tuple[str, ...]
    fragment_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SummaryRequestBatch:
    """一次完整计费且不拆分 fragment 的 LLM 请求。"""

    batch_index: int
    messages: tuple[dict[str, str], ...]
    fragments: tuple[TurnFragment, ...]
    fragment_hashes: tuple[str, ...]
    previous_state_json: str


@dataclass(frozen=True, slots=True)
class SummaryObligation:
    """previous state 中必须恰好处置一次的条目。"""

    source_id: str
    field: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class InheritanceAudit:
    """inheritance 门禁通过后的脱敏计数与状态哈希。"""

    obligation_count: int
    carried_count: int
    updated_count: int
    resolved_count: int
    state_sha256: str


@dataclass(frozen=True, slots=True)
class SummaryBatchTrace:
    """单批完成后的 coverage 与 inheritance 审计。"""

    batch_index: int
    fragment_hashes: tuple[str, ...]
    inheritance_audit: InheritanceAudit
    model: str = "custom_summarizer"
    requested_model: str = "custom_summarizer"
    request_log_id: int | None = None


@dataclass(frozen=True, slots=True)
class SessionSummaryLLMResult:
    """单次摘要模型调用的内容与不可变追踪事实。"""

    content: object
    model: str
    requested_model: str
    request_log_id: int | None


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_sanitized_text(text: str, max_fragment_chars: int) -> tuple[str, ...]:
    if not text:
        return ("",)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_fragment_chars)
        if hard_end == len(text):
            chunks.append(text[start:])
            break
        newline_at = text.rfind("\n", start, hard_end)
        end = newline_at + 1 if newline_at >= start else hard_end
        chunks.append(text[start:end])
        start = end
    return tuple(chunks)


def fragment_summary_turn(
    turn: Any,
    *,
    max_fragment_chars: int,
) -> tuple[TurnFragment, ...]:
    """先完整净化一条 turn，再无损分片。"""

    raw_turn_id = getattr(turn, "id", None)
    if isinstance(raw_turn_id, bool) or not isinstance(raw_turn_id, int) or raw_turn_id <= 0:
        raise ValueError("summary_turn_id_invalid")
    if isinstance(max_fragment_chars, bool) or int(max_fragment_chars) <= 0:
        raise ValueError("summary_fragment_size_invalid")

    sanitized = sanitize_prompt_text(getattr(turn, "content", "") or "", max_chars=0)
    contents = _split_sanitized_text(sanitized, int(max_fragment_chars))
    sanitized_sha256 = _text_sha256(sanitized)
    fragment_count = len(contents)
    role = str(getattr(turn, "role", "") or "")
    return tuple(
        TurnFragment(
            turn_id=raw_turn_id,
            role=role,
            fragment_index=index,
            fragment_count=fragment_count,
            content=content,
            sanitized_sha256=sanitized_sha256,
            fragment_sha256=_text_sha256(content),
        )
        for index, content in enumerate(contents)
    )


def build_coverage_manifest(
    fragments: Iterable[TurnFragment],
) -> TurnCoverageManifest:
    """验证分片完整性并按输入顺序构建 coverage manifest。"""

    ordered = tuple(fragments)
    turn_ids: list[int] = []
    turn_hashes: list[str] = []
    fragment_hashes: list[str] = []
    seen_turn_ids: set[int] = set()
    offset = 0
    while offset < len(ordered):
        first = ordered[offset]
        turn_id = first.turn_id
        if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id <= 0:
            raise ValueError("summary_turn_id_invalid")
        if turn_id in seen_turn_ids:
            raise ValueError("summary_turn_id_duplicate")

        end = offset + 1
        while end < len(ordered) and ordered[end].turn_id == turn_id:
            end += 1
        group = ordered[offset:end]
        if first.fragment_count <= 0 or len(group) != first.fragment_count:
            raise ValueError("summary_fragment_count_invalid")
        if tuple(fragment.fragment_index for fragment in group) != tuple(range(len(group))):
            raise ValueError("summary_fragment_index_invalid")
        if any(
            fragment.fragment_count != first.fragment_count
            or fragment.role != first.role
            or fragment.sanitized_sha256 != first.sanitized_sha256
            or _text_sha256(fragment.content) != fragment.fragment_sha256
            for fragment in group
        ):
            raise ValueError("summary_fragment_manifest_invalid")
        if _text_sha256("".join(fragment.content for fragment in group)) != first.sanitized_sha256:
            raise ValueError("summary_turn_hash_invalid")

        seen_turn_ids.add(turn_id)
        turn_ids.append(turn_id)
        turn_hashes.append(first.sanitized_sha256)
        fragment_hashes.extend(fragment.fragment_sha256 for fragment in group)
        offset = end

    return TurnCoverageManifest(
        ordered_turn_ids=tuple(turn_ids),
        turn_hashes=tuple(turn_hashes),
        fragment_hashes=tuple(fragment_hashes),
    )


def request_char_count(messages: Sequence[Mapping[str, str]]) -> int:
    """按最终 messages 的 canonical JSON 计算完整字符数。"""

    normalized: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("summary_request_message_invalid")
        normalized.append({"role": role, "content": content})
    return len(json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_summary_state(
    payload: Mapping[str, Any],
    *,
    max_state_chars: int = 4000,
) -> dict[str, Any]:
    """只保留 8 个业务字段并校验完整 canonical state 预算。"""

    state: dict[str, Any] = {
        "summary": str(payload.get("summary") or ""),
    }
    for field in _SUMMARY_LIST_FIELDS:
        value = payload.get(field)
        state[field] = list(value) if isinstance(value, list) else []
    if len(_compact_json(state)) > int(max_state_chars):
        raise ValueError("summary_state_budget_exceeded")
    return state


def _load_previous_mapping(previous: Any | None) -> tuple[Mapping[str, Any], bool]:
    if previous is None:
        return {}, False
    raw = getattr(previous, "summary_json", None)
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
    if isinstance(parsed, Mapping) and any(field in parsed for field in CANONICAL_SUMMARY_FIELDS):
        return parsed, False
    return {"summary": str(getattr(previous, "summary_text", "") or "")}, True


def canonical_previous_state(
    previous: Any | None,
    *,
    max_state_chars: int = 4000,
) -> dict[str, Any]:
    """优先读取结构化 previous，旧记录才回退到完整 legacy 文本。"""

    payload, _legacy = _load_previous_mapping(previous)
    return canonical_summary_state(payload, max_state_chars=max_state_chars)


def _normalize_obligation_text(value: Any) -> str:
    raw = value if isinstance(value, str) else _compact_json(value)
    normalized = unicodedata.normalize("NFKC", raw)
    return " ".join(normalized.split())


def build_summary_obligations(
    state: Mapping[str, Any],
    *,
    legacy_summary: bool = False,
) -> tuple[SummaryObligation, ...]:
    """按字段、规范化文本和重复序号生成稳定 obligation。"""

    canonical = canonical_summary_state(state)
    obligations: list[SummaryObligation] = []
    duplicate_counts: dict[tuple[str, str], int] = {}

    def append(field: str, value: Any) -> None:
        normalized_text = _normalize_obligation_text(value)
        duplicate_key = (field, normalized_text)
        duplicate_ordinal = duplicate_counts.get(duplicate_key, 0)
        duplicate_counts[duplicate_key] = duplicate_ordinal + 1
        source_id = hashlib.sha256(
            f"{field}{normalized_text}{duplicate_ordinal}".encode("utf-8")
        ).hexdigest()[:16]
        obligations.append(SummaryObligation(
            source_id=source_id,
            field=field,
            normalized_text=normalized_text,
        ))

    for field in _OBLIGATION_FIELDS:
        for item in canonical[field]:
            append(field, item)
    if legacy_summary and canonical["summary"]:
        append("legacy_summary", canonical["summary"])
    return tuple(obligations)


def build_previous_summary_obligations(
    previous: Any | None,
    *,
    max_state_chars: int = 4000,
) -> tuple[SummaryObligation, ...]:
    """为 previous 记录生成结构化或 legacy obligation。"""

    payload, legacy = _load_previous_mapping(previous)
    state = canonical_summary_state(payload, max_state_chars=max_state_chars)
    return build_summary_obligations(state, legacy_summary=legacy)


def _target_value(
    state: Mapping[str, Any],
    *,
    target_field: str,
    target_index: int,
) -> Any:
    if target_field not in CANONICAL_SUMMARY_FIELDS:
        raise ValueError("summary_inheritance_invalid")
    if target_field == "summary":
        if target_index != 0:
            raise ValueError("summary_inheritance_invalid")
        return state["summary"]
    values = state[target_field]
    if not isinstance(values, list) or target_index < 0 or target_index >= len(values):
        raise ValueError("summary_inheritance_invalid")
    return values[target_index]


def validate_inheritance(
    payload: Mapping[str, Any],
    obligations: Sequence[SummaryObligation],
    *,
    max_state_chars: int = 4000,
) -> InheritanceAudit:
    """验证每个 previous obligation 恰好映射到一个非空目标。"""

    state = canonical_summary_state(payload, max_state_chars=max_state_chars)
    inheritance = payload.get("inheritance", [])
    if not isinstance(inheritance, list):
        raise ValueError("summary_inheritance_invalid")
    expected = {obligation.source_id: obligation for obligation in obligations}
    if len(expected) != len(obligations):
        raise ValueError("summary_inheritance_invalid")
    seen: set[str] = set()
    counts = {"carried": 0, "updated": 0, "resolved": 0}
    for item in inheritance:
        if not isinstance(item, Mapping):
            raise ValueError("summary_inheritance_invalid")
        source_id = item.get("source_id")
        disposition = item.get("disposition")
        target_field = item.get("target_field")
        target_index = item.get("target_index")
        if (
            not isinstance(source_id, str)
            or source_id not in expected
            or source_id in seen
            or not isinstance(disposition, str)
            or disposition not in counts
            or not isinstance(target_field, str)
            or isinstance(target_index, bool)
            or not isinstance(target_index, int)
        ):
            raise ValueError("summary_inheritance_invalid")
        obligation = expected[source_id]
        if disposition == "resolved" and target_field != "resolved_items":
            raise ValueError("summary_inheritance_invalid")
        if obligation.field == "legacy_summary" and target_field != "summary":
            raise ValueError("summary_inheritance_invalid")
        source_field = "summary" if obligation.field == "legacy_summary" else obligation.field
        if disposition in {"carried", "updated"} and target_field != source_field:
            raise ValueError("summary_inheritance_invalid")
        target = _target_value(
            state,
            target_field=target_field,
            target_index=target_index,
        )
        normalized_target = _normalize_obligation_text(target)
        if (
            target is None
            or isinstance(target, (list, dict)) and not target
            or not normalized_target
            or disposition == "carried" and normalized_target != obligation.normalized_text
        ):
            raise ValueError("summary_inheritance_invalid")
        seen.add(source_id)
        counts[str(disposition)] += 1
    if seen != set(expected):
        raise ValueError("summary_inheritance_invalid")
    return InheritanceAudit(
        obligation_count=len(obligations),
        carried_count=counts["carried"],
        updated_count=counts["updated"],
        resolved_count=counts["resolved"],
        state_sha256=_text_sha256(_compact_json(state)),
    )


def strip_summary_inheritance(payload: Mapping[str, Any]) -> dict[str, Any]:
    """返回不含模型审计字段 inheritance 的业务 payload 副本。"""

    result = dict(payload)
    result.pop("inheritance", None)
    return result


def _obligation_payload(obligation: Any) -> Mapping[str, Any]:
    if isinstance(obligation, SummaryObligation):
        return {
            "source_id": obligation.source_id,
            "field": obligation.field,
            "normalized_text": obligation.normalized_text,
        }
    if isinstance(obligation, Mapping):
        return obligation
    raise ValueError("summary_obligation_invalid")


def _render_fragment(fragment: TurnFragment) -> str:
    role = html.escape(fragment.role, quote=True)
    content = html.escape(fragment.content, quote=False)
    return (
        f'<turn_fragment turn_id="{fragment.turn_id}" role="{role}" '
        f'fragment_index="{fragment.fragment_index}" '
        f'fragment_count="{fragment.fragment_count}" '
        f'sanitized_sha256="{fragment.sanitized_sha256}" '
        f'fragment_sha256="{fragment.fragment_sha256}">\n'
        f"{content}\n"
        "</turn_fragment>"
    )


def _build_request_messages(
    *,
    system_prompt: str,
    previous_state_json: str,
    available_obligations_json: str,
    fragments: Sequence[TurnFragment],
    output_instruction: str,
    batch_index: int,
) -> tuple[dict[str, str], ...]:
    pending_text = "\n".join(_render_fragment(fragment) for fragment in fragments)
    previous_state_text = html.escape(previous_state_json, quote=False)
    available_obligations_text = html.escape(available_obligations_json, quote=False)
    user_prompt = (
        f"<summary_batch index=\"{batch_index}\">\n"
        "<previous_state>\n"
        f"{previous_state_text}\n"
        "</previous_state>\n\n"
        "<available_obligations>\n"
        f"{available_obligations_text}\n"
        "</available_obligations>\n\n"
        "<pending_fragments>\n"
        f"{pending_text}\n"
        "</pending_fragments>\n\n"
        f"{output_instruction}\n"
        "</summary_batch>"
    )
    return (
        {"role": "system", "content": str(system_prompt)},
        {"role": "user", "content": user_prompt},
    )


def build_summary_request_batches(
    *,
    system_prompt: str,
    previous_state: Mapping[str, Any],
    available_obligations: Sequence[SummaryObligation | Mapping[str, Any]],
    fragments: Sequence[TurnFragment],
    output_instruction: str,
    max_request_chars: int,
    safety_chars: int,
    start_batch_index: int = 0,
) -> tuple[SummaryRequestBatch, ...]:
    """按完整 messages 预算贪心切批，任何 fragment 都保持完整。"""

    request_limit = int(max_request_chars) - int(safety_chars)
    if request_limit <= 0:
        raise ValueError("summary_request_budget_exceeded")
    previous_state_json = _compact_json(dict(previous_state))
    available_obligations_json = _compact_json([
        _obligation_payload(obligation) for obligation in available_obligations
    ])
    fixed_messages = _build_request_messages(
        system_prompt=system_prompt,
        previous_state_json=previous_state_json,
        available_obligations_json=available_obligations_json,
        fragments=(),
        output_instruction=output_instruction,
        batch_index=0,
    )
    if request_char_count(fixed_messages) > request_limit:
        raise ValueError("summary_request_budget_exceeded")

    batches: list[SummaryRequestBatch] = []
    current: list[TurnFragment] = []
    batch_index = int(start_batch_index)
    offset = 0
    ordered_fragments = tuple(fragments)
    while offset < len(ordered_fragments):
        fragment = ordered_fragments[offset]
        if _text_sha256(fragment.content) != fragment.fragment_sha256:
            raise ValueError("summary_fragment_manifest_invalid")
        tentative = (*current, fragment)
        messages = _build_request_messages(
            system_prompt=system_prompt,
            previous_state_json=previous_state_json,
            available_obligations_json=available_obligations_json,
            fragments=tentative,
            output_instruction=output_instruction,
            batch_index=batch_index,
        )
        if request_char_count(messages) <= request_limit:
            current.append(fragment)
            offset += 1
            continue
        if not current:
            raise ValueError("summary_request_budget_exceeded")

        completed_messages = _build_request_messages(
            system_prompt=system_prompt,
            previous_state_json=previous_state_json,
            available_obligations_json=available_obligations_json,
            fragments=current,
            output_instruction=output_instruction,
            batch_index=batch_index,
        )
        batches.append(SummaryRequestBatch(
            batch_index=batch_index,
            messages=completed_messages,
            fragments=tuple(current),
            fragment_hashes=tuple(item.fragment_sha256 for item in current),
            previous_state_json=previous_state_json,
        ))
        current = []
        batch_index += 1

    if current:
        completed_messages = _build_request_messages(
            system_prompt=system_prompt,
            previous_state_json=previous_state_json,
            available_obligations_json=available_obligations_json,
            fragments=current,
            output_instruction=output_instruction,
            batch_index=batch_index,
        )
        if request_char_count(completed_messages) > request_limit:
            raise ValueError("summary_request_budget_exceeded")
        batches.append(SummaryRequestBatch(
            batch_index=batch_index,
            messages=completed_messages,
            fragments=tuple(current),
            fragment_hashes=tuple(item.fragment_sha256 for item in current),
            previous_state_json=previous_state_json,
        ))
    return tuple(batches)
