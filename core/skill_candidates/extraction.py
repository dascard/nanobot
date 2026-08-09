"""只从脱敏离线 Run Viewer 提取流程、失败模式和确定性 Skill 草案。"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any

from core.evolution_control.contracts import canonical_json, sha256_json
from core.skills import (
    SkillContractError,
    SkillScopeTarget,
    normalize_semver,
    normalize_skill_name,
    parse_skill_bundle,
)

from .contracts import (
    MAX_PATTERNS,
    MAX_SOURCE_RUNS,
    SKILL_CANDIDATE_SCHEMA_VERSION,
    ExperienceFailurePattern,
    ExperienceProcessStep,
    SkillCandidateContractError,
    SkillExperienceCandidate,
    SourceRunEvidence,
)


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>'\"]+")
_BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|authorization)"
    r"\s*[:=]\s*[^\s,;]{4,}"
)
_LONG_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])")
_TAG_RE = re.compile(r"<[^>\r\n]{1,160}>")
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_CAPABILITY_RE = re.compile(
    r"^(?:[a-z0-9][a-z0-9._-]{0,63}|"
    r"[\u3400-\u9fff][\u3400-\u9fffA-Za-z0-9._-]{0,31})$"
)
_ALLOWED_APPLICABILITY = frozenset({
    "all",
    "chat",
    "private",
    "group",
    "scheduled",
    "task",
})
_TERMINAL_FAILURES = frozenset({
    "ambiguous",
    "cancelled",
    "failed",
    "timed_out",
})
_REDACTION_FIELDS = (
    "hidden_reasoning",
    "prompt_and_messages",
    "tool_arguments_and_results",
    "sandbox_command_and_output",
    "secrets_and_credentials",
)


def _safe_sha256(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise SkillCandidateContractError(f"{name} 必须是 SHA-256")
    return normalized


def sanitize_experience_text(
    value: object,
    *,
    maximum: int = 160,
) -> tuple[str, int]:
    """去除凭据、标识信息、控制字符和 Prompt 标签，仅保留有界分类文本。"""

    raw = unicodedata.normalize("NFC", str(value or ""))
    filtered = "".join(
        " " if char in {"\r", "\n", "\t"} else char
        for char in raw
        if unicodedata.category(char) not in {"Cc", "Cf"}
    )
    redactions = 0
    for pattern, replacement in (
        (_BEARER_RE, "[凭据已脱敏]"),
        (_SECRET_ASSIGNMENT_RE, "[凭据已脱敏]"),
        (_EMAIL_RE, "[邮箱已脱敏]"),
        (_PHONE_RE, "[手机号已脱敏]"),
        (_IP_RE, "[地址已脱敏]"),
        (_URL_RE, "[链接已脱敏]"),
        (_LONG_SECRET_RE, "[长标识已脱敏]"),
        (_TAG_RE, "[标签已脱敏]"),
    ):
        filtered, count = pattern.subn(replacement, filtered)
        redactions += count
    normalized = " ".join(filtered.split()).strip()
    if len(normalized) > maximum:
        normalized = normalized[:maximum].rstrip()
        redactions += 1
    return normalized, redactions


def _strict_json_hash(value: object, name: str) -> str:
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillCandidateContractError(f"{name} 必须是 JSON") from exc
    if len(encoded) > 8 * 1024 * 1024:
        raise SkillCandidateContractError(f"{name} 超过 8 MiB")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillCandidateContractError(f"{name} 必须是 JSON 对象")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SkillCandidateContractError(f"{name} 必须是 JSON 数组")
    return value


def _normalized_kind(value: object) -> str:
    normalized, _count = sanitize_experience_text(value, maximum=64)
    candidate = normalized.lower().replace(" ", "_")
    if re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", candidate) is None:
        return "operation"
    return candidate


@dataclass(frozen=True, slots=True)
class SkillDraftSpec:
    name: str
    version: str
    description: str
    target_scope: str
    target_scope_key: str
    baseline_bundle_sha256: str
    source_revision: str
    created_at: str
    capability_tags: tuple[str, ...]
    applies_to: tuple[str, ...]
    allowed_tools: tuple[str, ...] = ()
    generator_id: str = "trajectory-skill-extractor"
    generator_version: str = "1.0.0"
    generation_cost_microunits: int = 0

    def __post_init__(self) -> None:
        try:
            name = normalize_skill_name(self.name)
            version = normalize_semver(self.version)
            target = SkillScopeTarget(self.target_scope, self.target_scope_key)
        except (SkillContractError, ValueError) as exc:
            raise SkillCandidateContractError("Skill 草案规格无效") from exc
        if target.scope.value == "builtin":
            raise SkillCandidateContractError("经验候选不能发布到 builtin scope")
        description, _count = sanitize_experience_text(
            self.description,
            maximum=1024,
        )
        if not description:
            raise SkillCandidateContractError("draft.description 不能为空")
        baseline = _safe_sha256(
            self.baseline_bundle_sha256,
            "draft.baseline_bundle_sha256",
        )
        revision = str(self.source_revision or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision) is None:
            raise SkillCandidateContractError("draft.source_revision 无效")
        tags = tuple(sorted(str(item or "").strip() for item in self.capability_tags))
        if (
            not tags
            or len(tags) > 32
            or len(tags) != len(set(tags))
            or any(not _CAPABILITY_RE.fullmatch(item) for item in tags)
        ):
            raise SkillCandidateContractError("draft.capability_tags 无效")
        applies_to = tuple(sorted(str(item or "").strip() for item in self.applies_to))
        if (
            not applies_to
            or len(applies_to) > 6
            or len(applies_to) != len(set(applies_to))
            or any(item not in _ALLOWED_APPLICABILITY for item in applies_to)
            or ("all" in applies_to and len(applies_to) != 1)
        ):
            raise SkillCandidateContractError("draft.applies_to 无效")
        tools = tuple(sorted(str(item or "").strip() for item in self.allowed_tools))
        if (
            len(tools) > 32
            or len(tools) != len(set(tools))
            or any(not _TOOL_RE.fullmatch(item) for item in tools)
        ):
            raise SkillCandidateContractError("draft.allowed_tools 无效")
        generator_id = str(self.generator_id or "").strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,127}", generator_id) is None:
            raise SkillCandidateContractError("draft.generator_id 无效")
        generator_version, _count = sanitize_experience_text(
            self.generator_version,
            maximum=128,
        )
        if not generator_version:
            raise SkillCandidateContractError("draft.generator_version 无效")
        if (
            type(self.generation_cost_microunits) is not int
            or not 0 <= self.generation_cost_microunits <= 10**12
        ):
            raise SkillCandidateContractError(
                "draft.generation_cost_microunits 无效"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "target_scope", target.scope.value)
        object.__setattr__(self, "target_scope_key", target.scope_key)
        object.__setattr__(self, "baseline_bundle_sha256", baseline)
        object.__setattr__(self, "source_revision", revision)
        object.__setattr__(self, "capability_tags", tags)
        object.__setattr__(self, "applies_to", applies_to)
        object.__setattr__(self, "allowed_tools", tools)
        object.__setattr__(self, "generator_id", generator_id)
        object.__setattr__(self, "generator_version", generator_version)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "target_scope": self.target_scope,
            "target_scope_key": self.target_scope_key,
            "baseline_bundle_sha256": self.baseline_bundle_sha256,
            "source_revision": self.source_revision,
            "created_at": self.created_at,
            "capability_tags": list(self.capability_tags),
            "applies_to": list(self.applies_to),
            "allowed_tools": list(self.allowed_tools),
            "generator_id": self.generator_id,
            "generator_version": self.generator_version,
            "generation_cost_microunits": self.generation_cost_microunits,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SkillDraftSpec":
        payload = _mapping(value, "draft spec")
        required = {
            "name",
            "version",
            "description",
            "target_scope",
            "target_scope_key",
            "baseline_bundle_sha256",
            "source_revision",
            "created_at",
            "capability_tags",
            "applies_to",
            "allowed_tools",
            "generator_id",
            "generator_version",
            "generation_cost_microunits",
        }
        missing = sorted(required - payload.keys())
        unknown = sorted(payload.keys() - required)
        if missing or unknown:
            raise SkillCandidateContractError(
                "draft spec 字段不完整或含未知字段"
            )
        return cls(
            name=payload["name"],
            version=payload["version"],
            description=payload["description"],
            target_scope=payload["target_scope"],
            target_scope_key=payload["target_scope_key"],
            baseline_bundle_sha256=payload["baseline_bundle_sha256"],
            source_revision=payload["source_revision"],
            created_at=payload["created_at"],
            capability_tags=tuple(
                _sequence(payload["capability_tags"], "draft.capability_tags")
            ),
            applies_to=tuple(
                _sequence(payload["applies_to"], "draft.applies_to")
            ),
            allowed_tools=tuple(
                _sequence(payload["allowed_tools"], "draft.allowed_tools")
            ),
            generator_id=payload["generator_id"],
            generator_version=payload["generator_version"],
            generation_cost_microunits=payload["generation_cost_microunits"],
        )


@dataclass(frozen=True, slots=True)
class _ProjectedRun:
    evidence: SourceRunEvidence
    successful_steps: tuple[tuple[str, str], ...]
    failures: tuple[dict[str, object], ...]
    observed_tools: frozenset[str]
    redaction_count: int


def _project_run_view(
    raw_view: Mapping[str, Any],
    *,
    extra_evidence_sha256s: Sequence[str],
) -> _ProjectedRun:
    view = _mapping(raw_view, "run viewer")
    if view.get("offline") is not True or view.get("source") != "persisted_evidence":
        raise SkillCandidateContractError(
            "经验提取只接受 persisted_evidence 离线 Run Viewer"
        )
    if str(view.get("schema_version") or "") != "1.0":
        raise SkillCandidateContractError("Run Viewer schema_version 不受支持")
    redaction = _mapping(view.get("redaction"), "run viewer.redaction")
    if any(redaction.get(field) != "omitted" for field in _REDACTION_FIELDS):
        raise SkillCandidateContractError(
            "Run Viewer 未证明正文、隐藏推理和凭据已省略"
        )
    run_id = str(view.get("run_id") or "").strip()
    summary = _mapping(view.get("summary"), "run viewer.summary")
    status = str(summary.get("status") or "").strip().lower()
    if status == "succeeded":
        outcome = "succeeded"
    elif status in _TERMINAL_FAILURES:
        outcome = "failed"
    else:
        raise SkillCandidateContractError(
            "只允许从成功或已终止失败的 Run 提取经验"
        )
    timeline = _sequence(view.get("timeline"), "run viewer.timeline")
    failures = _sequence(view.get("failures"), "run viewer.failures")
    if not 1 <= len(timeline) <= 2_000 or len(failures) > 2_000:
        raise SkillCandidateContractError("Run Viewer trajectory 数量越界")

    redaction_count = 0
    successful_steps: list[tuple[str, str]] = []
    failed_spans: list[dict[str, object]] = []
    observed_tools: set[str] = set()
    for raw_item in timeline:
        item = _mapping(raw_item, "run viewer.timeline item")
        kind = _normalized_kind(item.get("kind"))
        if kind == "run":
            continue
        name, count = sanitize_experience_text(item.get("name"), maximum=160)
        redaction_count += count
        if not name:
            name = kind
            redaction_count += 1
        item_status = str(item.get("status") or "").strip().lower()
        signature = (kind, name)
        if item_status == "succeeded":
            if not successful_steps or successful_steps[-1] != signature:
                successful_steps.append(signature)
        elif item_status in _TERMINAL_FAILURES:
            failed_spans.append({
                "kind": kind,
                "name": name,
                "code": "failed_span",
                "error_type": "",
                "retryable": False,
            })
        if kind == "tool" and _TOOL_RE.fullmatch(name):
            observed_tools.add(name)

    projected_failures: list[dict[str, object]] = []
    for raw_item in failures:
        item = _mapping(raw_item, "run viewer.failure item")
        kind = _normalized_kind(item.get("kind"))
        name, count = sanitize_experience_text(item.get("name"), maximum=160)
        redaction_count += count
        code, count = sanitize_experience_text(item.get("code"), maximum=128)
        redaction_count += count
        error_type, count = sanitize_experience_text(
            item.get("error_type"),
            maximum=128,
        )
        redaction_count += count
        projected_failures.append({
            "kind": kind,
            "name": name or kind,
            "code": code or "failed",
            "error_type": error_type,
            "retryable": item.get("retryable") is True,
        })
    if outcome == "failed" and not projected_failures:
        projected_failures = failed_spans or [{
            "kind": "run",
            "name": "run",
            "code": status,
            "error_type": "",
            "retryable": False,
        }]
    successful_steps = successful_steps[:MAX_PATTERNS]
    projected_failures = projected_failures[:MAX_PATTERNS]
    projection = {
        "run_id": run_id,
        "outcome": outcome,
        "successful_steps": [list(item) for item in successful_steps],
        "failures": projected_failures,
    }
    view_sha = _strict_json_hash(view, "run viewer")
    trajectory_sha = sha256_json(projection)
    evidence_hashes = tuple(sorted({
        view_sha,
        *(
            _safe_sha256(item, "extra evidence")
            for item in extra_evidence_sha256s
        ),
    }))
    return _ProjectedRun(
        evidence=SourceRunEvidence(
            run_id=run_id,
            outcome=outcome,
            run_view_sha256=view_sha,
            trajectory_sha256=trajectory_sha,
            span_count=len(timeline),
            failure_count=len(projected_failures),
            redaction_count=redaction_count,
            evidence_sha256s=evidence_hashes,
        ),
        successful_steps=tuple(successful_steps),
        failures=tuple(projected_failures),
        observed_tools=frozenset(observed_tools),
        redaction_count=redaction_count,
    )


def _canonical_process_steps(
    runs: Sequence[_ProjectedRun],
) -> tuple[ExperienceProcessStep, ...]:
    successful = [run for run in runs if run.evidence.outcome == "succeeded"]
    sequences = Counter(run.successful_steps for run in successful if run.successful_steps)
    if not sequences:
        raise SkillCandidateContractError("成功 trajectory 中没有可提取流程")
    representative = sorted(
        sequences,
        key=lambda item: (-sequences[item], -len(item), canonical_json(item)),
    )[0]
    steps: list[ExperienceProcessStep] = []
    for position, (kind, name) in enumerate(representative, start=1):
        supporting = tuple(
            run.evidence.run_id
            for run in successful
            if (kind, name) in run.successful_steps
        )
        steps.append(ExperienceProcessStep(
            position=position,
            kind=kind,
            name=name,
            supporting_run_ids=supporting,
        ))
    return tuple(steps)


def _failure_patterns(
    runs: Sequence[_ProjectedRun],
) -> tuple[ExperienceFailurePattern, ...]:
    occurrences: Counter[tuple[str, str, str, str, bool]] = Counter()
    supporting: defaultdict[
        tuple[str, str, str, str, bool],
        set[str],
    ] = defaultdict(set)
    for run in runs:
        for item in run.failures:
            key = (
                str(item["kind"]),
                str(item["name"]),
                str(item["code"]),
                str(item["error_type"]),
                item["retryable"] is True,
            )
            occurrences[key] += 1
            supporting[key].add(run.evidence.run_id)
    ordered = sorted(
        occurrences,
        key=lambda item: (-occurrences[item], canonical_json(item)),
    )[:MAX_PATTERNS]
    if not ordered:
        raise SkillCandidateContractError("失败 trajectory 中没有失败模式")
    return tuple(
        ExperienceFailurePattern(
            kind=key[0],
            name=key[1],
            code=key[2],
            error_type=key[3],
            retryable=key[4],
            occurrence_count=occurrences[key],
            supporting_run_ids=tuple(sorted(supporting[key])),
        )
        for key in ordered
    )


def _render_skill_md(
    spec: SkillDraftSpec,
    *,
    corpus_sha256: str,
    process_steps: Sequence[ExperienceProcessStep],
    failure_patterns: Sequence[ExperienceFailurePattern],
    source_count: int,
) -> str:
    quoted_description = json.dumps(spec.description, ensure_ascii=False)
    permissions = ",".join(f"tool:{item}" for item in spec.allowed_tools)
    capabilities = ",".join(spec.capability_tags)
    applies_to = ",".join(spec.applies_to)
    lines = [
        "---",
        f"name: {spec.name}",
        f"description: {quoted_description}",
        "compatibility: Nanobot Server；仅使用当前请求实际授权的工具和权限。",
        "metadata:",
        f'  version: "{spec.version}"',
        '  nanobot.dependencies: ""',
        f'  nanobot.permissions: "{permissions}"',
        f'  nanobot.capabilities: "{capabilities}"',
        f'  nanobot.applies-to: "{applies_to}"',
        f'  nanobot.experience-corpus-sha256: "{corpus_sha256}"',
    ]
    if spec.allowed_tools:
        lines.append(f"allowed-tools: {' '.join(spec.allowed_tools)}")
    lines.extend([
        "---",
        "",
        f"# {spec.name} 经验流程",
        "",
        "本草案由成功与失败 Run 的脱敏结构化证据离线提取。它只描述可复用流程，",
        "不能扩大当前 ToolPlan、权限、数据作用域或网络能力。",
        "",
        "## 已验证流程",
        "",
    ])
    for item in process_steps:
        lines.append(
            f"{item.position}. 执行 `{item.kind}` 阶段的 `{item.name}`；"
            f"该步骤获得 {len(item.supporting_run_ids)} 条成功 Run 支持。"
        )
    lines.extend(["", "## 失败模式与规避", ""])
    for item in failure_patterns:
        retry = "可按宿主恢复策略重试" if item.retryable else "不要盲目重试"
        details = f"，错误类型 `{item.error_type}`" if item.error_type else ""
        lines.append(
            f"- `{item.kind}/{item.name}` 出现 `{item.code}`{details}：{retry}；"
            f"先保留失败证据并遵守当前恢复与副作用幂等合同。"
        )
    lines.extend([
        "",
        "## 证据边界",
        "",
        f"- 来源：{source_count} 条已脱敏 Run Viewer；语料摘要 `{corpus_sha256}`。",
        "- 未使用消息、Prompt、工具参数/结果、Sandbox 命令/输出或隐藏推理正文。",
        "- 只有独立评测通过且人工批准后，草案才能发布为受管 Skill 版本。",
        "",
    ])
    return "\n".join(lines)


def extract_skill_candidate(
    run_views: Sequence[Mapping[str, Any]],
    *,
    spec: SkillDraftSpec,
    extra_evidence_by_run: Mapping[str, Sequence[str]] | None = None,
) -> SkillExperienceCandidate:
    """确定性提取候选；不调用模型、网络、工具、生产正文或仓库。"""

    if not isinstance(spec, SkillDraftSpec):
        raise TypeError("spec 必须是 SkillDraftSpec")
    if not 2 <= len(run_views) <= MAX_SOURCE_RUNS:
        raise SkillCandidateContractError(
            f"run_views 必须包含 2..{MAX_SOURCE_RUNS} 项"
        )
    evidence_map = dict(extra_evidence_by_run or {})
    projected: list[_ProjectedRun] = []
    seen_run_ids: set[str] = set()
    for raw_view in run_views:
        raw_run_id = str(raw_view.get("run_id") or "")
        item = _project_run_view(
            raw_view,
            extra_evidence_sha256s=evidence_map.get(raw_run_id, ()),
        )
        if item.evidence.run_id in seen_run_ids:
            raise SkillCandidateContractError("run_views 不能包含重复 Run")
        seen_run_ids.add(item.evidence.run_id)
        projected.append(item)
    unknown_evidence_runs = sorted(set(evidence_map) - seen_run_ids)
    if unknown_evidence_runs:
        raise SkillCandidateContractError(
            "extra_evidence_by_run 引用了未提供的 Run"
        )
    if {item.evidence.outcome for item in projected} != {"succeeded", "failed"}:
        raise SkillCandidateContractError("必须同时提供成功和失败 trajectory")

    observed_tools = frozenset().union(
        *(item.observed_tools for item in projected)
    )
    unobserved_tools = sorted(set(spec.allowed_tools) - observed_tools)
    if unobserved_tools:
        raise SkillCandidateContractError(
            "草案不能申请 trajectory 未使用的工具: "
            + ", ".join(unobserved_tools)
        )
    process_steps = _canonical_process_steps(projected)
    failure_patterns = _failure_patterns(projected)
    sources = tuple(item.evidence for item in projected)
    corpus_sha = sha256_json({
        "source_trajectory_sha256s": sorted(
            item.trajectory_sha256 for item in sources
        ),
        "process_pattern_sha256s": sorted(
            item.pattern_sha256 for item in process_steps
        ),
        "failure_pattern_sha256s": sorted(
            item.pattern_sha256 for item in failure_patterns
        ),
    })
    skill_md = _render_skill_md(
        spec,
        corpus_sha256=corpus_sha,
        process_steps=process_steps,
        failure_patterns=failure_patterns,
        source_count=len(sources),
    )
    try:
        bundle = parse_skill_bundle(skill_md.encode("utf-8"))
    except SkillContractError as exc:
        raise SkillCandidateContractError("提取出的 Skill 草案不满足规范") from exc
    candidate_seed = sha256_json({
        "target_scope": spec.target_scope,
        "target_scope_key": spec.target_scope_key,
        "baseline_bundle_sha256": spec.baseline_bundle_sha256,
        "bundle_sha256": bundle.bundle_sha256,
        "source_trajectory_sha256s": sorted(
            item.trajectory_sha256 for item in sources
        ),
        "pattern_sha256s": sorted(
            [item.pattern_sha256 for item in process_steps]
            + [item.pattern_sha256 for item in failure_patterns]
        ),
    })
    return SkillExperienceCandidate(
        schema_version=SKILL_CANDIDATE_SCHEMA_VERSION,
        candidate_id=f"skillcand_{candidate_seed[:32]}",
        created_at=spec.created_at,
        generator_id=spec.generator_id,
        generator_version=spec.generator_version,
        source_revision=spec.source_revision,
        target_scope=spec.target_scope,
        target_scope_key=spec.target_scope_key,
        baseline_bundle_sha256=spec.baseline_bundle_sha256,
        draft_skill_md=skill_md,
        source_runs=sources,
        process_steps=process_steps,
        failure_patterns=failure_patterns,
        raw_production_content_access=False,
        network_access=False,
        repository_operations="forbidden",
        generation_cost_microunits=spec.generation_cost_microunits,
        redaction_count=sum(item.redaction_count for item in projected),
    )


__all__ = [
    "SkillDraftSpec",
    "extract_skill_candidate",
    "sanitize_experience_text",
]
