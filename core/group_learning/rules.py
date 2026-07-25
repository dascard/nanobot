"""只生成候选信号的群学习规则 Registry。"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
import unicodedata
from typing import Mapping

from core.group_learning.aspects import GROUP_LEARNING_MEMORY_TYPES
from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


_RULE_LIFECYCLES = frozenset({"active", "deprecated", "retired"})
_DEFINITION_TERM = (
    r"(?P<term>[\u4e00-\u9fffA-Za-z]"
    r"[\u4e00-\u9fffA-Za-z0-9_.+\-]{1,11})"
)
_DEFINITION_MEANING = r"(?P<meaning>[^。！？\n]{2,48})"


def canonicalize_learning_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.strip("，,。.!！?？；;：:")


@dataclass(frozen=True, slots=True)
class LearningSignalRuleDescriptor:
    rule_id: str
    version: int
    candidate_type: str
    owner_module: str
    canonicalizer_id: str
    max_input_chars: int
    max_matches_per_message: int
    max_candidates_per_batch: int
    scope: str
    positive_fixtures: tuple[str, ...]
    negative_fixtures: tuple[str, ...]
    performance_budget_ms: float
    lifecycle: str
    metrics_labels: tuple[str, ...]
    pattern: str = ""
    extractor_id: str = ""

    def __post_init__(self) -> None:
        validate_identifier(
            self.rule_id,
            field_name="learning_rule.rule_id",
        )
        validate_identifier(
            self.owner_module,
            field_name="learning_rule.owner_module",
        )
        validate_identifier(
            self.canonicalizer_id,
            field_name="learning_rule.canonicalizer_id",
        )
        validate_identifier(
            self.scope,
            field_name="learning_rule.scope",
        )
        if self.candidate_type not in GROUP_LEARNING_MEMORY_TYPES:
            raise ValueError("Learning Rule candidate_type 无效")
        if self.lifecycle not in _RULE_LIFECYCLES:
            raise ValueError("Learning Rule lifecycle 无效")
        if bool(self.pattern) == bool(self.extractor_id):
            raise ValueError("Learning Rule 必须且只能声明 pattern/extractor")
        if self.version <= 0:
            raise ValueError("Learning Rule version 必须为正")
        if min(
            self.max_input_chars,
            self.max_matches_per_message,
            self.max_candidates_per_batch,
        ) <= 0:
            raise ValueError("Learning Rule 容量预算必须为正")
        if self.max_input_chars > 10_000:
            raise ValueError("Learning Rule 单条输入上限过大")
        if not 0 < float(self.performance_budget_ms) <= 100:
            raise ValueError("Learning Rule 性能预算无效")
        if not self.positive_fixtures or not self.negative_fixtures:
            raise ValueError("Learning Rule 必须声明正反例")
        if len(self.metrics_labels) != len(set(self.metrics_labels)):
            raise ValueError("Learning Rule metrics label 不能重复")
        for label in self.metrics_labels:
            validate_identifier(
                label,
                field_name="learning_rule.metrics_label",
            )
        compiled = None
        if self.pattern:
            try:
                compiled = re.compile(self.pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"Learning Rule {self.rule_id} 正则无效"
                ) from exc
            required_groups = {"term", "meaning"}
            if self.candidate_type == "slang" and not (
                required_groups <= set(compiled.groupindex)
            ):
                raise ValueError("slang 规则必须声明 term/meaning 命名组")
        else:
            validate_identifier(
                self.extractor_id,
                field_name="learning_rule.extractor_id",
            )
        for fixture in self.positive_fixtures:
            if not _extract_raw(self, fixture):
                raise ValueError(
                    f"Learning Rule {self.rule_id} 正例未命中"
                )
        for fixture in self.negative_fixtures:
            if _extract_raw(self, fixture):
                raise ValueError(
                    f"Learning Rule {self.rule_id} 负例误命中"
                )

    @property
    def registry_namespace(self) -> str:
        return "group_learning_signal_rule"

    @property
    def registry_id(self) -> str:
        return self.rule_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "version": self.version,
            "candidate_type": self.candidate_type,
            "owner_module": self.owner_module,
            "pattern": self.pattern,
            "extractor_id": self.extractor_id,
            "canonicalizer_id": self.canonicalizer_id,
            "max_input_chars": self.max_input_chars,
            "max_matches_per_message": self.max_matches_per_message,
            "max_candidates_per_batch": self.max_candidates_per_batch,
            "scope": self.scope,
            "positive_fixtures": self.positive_fixtures,
            "negative_fixtures": self.negative_fixtures,
            "performance_budget_ms": self.performance_budget_ms,
            "lifecycle": self.lifecycle,
            "metrics_labels": self.metrics_labels,
        }


@dataclass(frozen=True, slots=True)
class LearningRuleMatch:
    rule_id: str
    rule_version: int
    candidate_type: str
    canonical_content: str
    meaning: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class LearningRuleDryRun:
    input_chars: int
    elapsed_ms: float
    registry_generation: int
    registry_sha256: str
    matches: tuple[LearningRuleMatch, ...]


def _extract_short_phrase(text: str) -> list[tuple[str, str, int, int]]:
    matches: list[tuple[str, str, int, int]] = []
    for match in re.finditer(
        r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{2,8}"
        r"(?![\u4e00-\u9fff])",
        text,
    ):
        matches.append((match.group(0), "", match.start(), match.end()))
    return matches


def _extract_raw(
    descriptor: LearningSignalRuleDescriptor,
    text: str,
) -> list[tuple[str, str, int, int]]:
    bounded = str(text or "")[: descriptor.max_input_chars]
    if descriptor.pattern:
        compiled = re.compile(descriptor.pattern, re.IGNORECASE)
        return [
            (
                match.group("term"),
                match.group("meaning"),
                match.start(),
                match.end(),
            )
            for match in compiled.finditer(bounded)
        ][: descriptor.max_matches_per_message]
    if descriptor.extractor_id == "short_cjk_phrase":
        return _extract_short_phrase(bounded)[
            : descriptor.max_matches_per_message
        ]
    raise ValueError(
        f"未实现的 Learning Rule extractor：{descriptor.extractor_id}"
    )


def _build_rule_registry(
) -> RegistrySnapshot[LearningSignalRuleDescriptor]:
    descriptors = (
        LearningSignalRuleDescriptor(
            rule_id="expression.short_phrase.v1",
            version=1,
            candidate_type="expression",
            owner_module="core.group_learning",
            extractor_id="short_cjk_phrase",
            canonicalizer_id="nfkc_phrase",
            max_input_chars=2000,
            max_matches_per_message=8,
            max_candidates_per_batch=100,
            scope="group_message",
            positive_fixtures=("芜湖",),
            negative_fixtures=("https://example.com",),
            performance_budget_ms=5.0,
            lifecycle="active",
            metrics_labels=("rule_id", "candidate_type", "outcome"),
        ),
        LearningSignalRuleDescriptor(
            rule_id="slang.explicit_definition.v1",
            version=1,
            candidate_type="slang",
            owner_module="core.group_learning",
            pattern=(
                _DEFINITION_TERM
                + r"\s*(?:的意思是|指的是|是指)\s*"
                + _DEFINITION_MEANING
            ),
            canonicalizer_id="nfkc_term_meaning",
            max_input_chars=2000,
            max_matches_per_message=4,
            max_candidates_per_batch=40,
            scope="group_message",
            positive_fixtures=("摸鱼的意思是上班时偷懒",),
            negative_fixtures=("我不知道这句话什么意思",),
            performance_budget_ms=5.0,
            lifecycle="active",
            metrics_labels=("rule_id", "candidate_type", "outcome"),
        ),
        LearningSignalRuleDescriptor(
            rule_id="slang.question_definition.v1",
            version=1,
            candidate_type="slang",
            owner_module="core.group_learning",
            pattern=(
                r"什么叫\s*"
                + _DEFINITION_TERM
                + r"\s*[，,：:]?\s*(?:就是|指的是)\s*"
                + _DEFINITION_MEANING
            ),
            canonicalizer_id="nfkc_term_meaning",
            max_input_chars=2000,
            max_matches_per_message=4,
            max_candidates_per_batch=40,
            scope="group_message",
            positive_fixtures=("什么叫赛博监工，就是提醒大家交作业的人",),
            negative_fixtures=("什么叫快乐星球",),
            performance_budget_ms=5.0,
            lifecycle="active",
            metrics_labels=("rule_id", "candidate_type", "outcome"),
        ),
    )
    builder = RegistryBuilder[LearningSignalRuleDescriptor](
        "group_learning_signal_rule"
    )
    for descriptor in descriptors:
        builder.register(descriptor)
    return builder.freeze()


LEARNING_SIGNAL_RULE_REGISTRY = _build_rule_registry()


def dry_run_learning_rules(
    text: str,
    *,
    rule_ids: tuple[str, ...] | list[str] | None = None,
) -> LearningRuleDryRun:
    """执行有界只读 dry-run；结果永远不写 Candidate 或 GroupMemory。"""

    from core.group_learning.reserved_terms import (
        build_reserved_term_snapshot,
    )

    raw_text = str(text or "")
    if len(raw_text) > 10_000:
        raise ValueError("dry-run 输入超过 10000 字符")
    selected_ids = (
        tuple(LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids)
        if rule_ids is None
        else tuple(str(item or "").strip() for item in rule_ids)
    )
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("dry-run rule_ids 不能重复")
    unknown = set(selected_ids) - set(
        LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
    )
    if unknown:
        raise ValueError("dry-run 包含未知规则")

    reserved = build_reserved_term_snapshot()
    started = time.perf_counter()
    matches: list[LearningRuleMatch] = []
    for rule_id in selected_ids:
        descriptor = LEARNING_SIGNAL_RULE_REGISTRY.require(rule_id)
        if descriptor.lifecycle != "active":
            continue
        for content, meaning, start, end in _extract_raw(
            descriptor,
            raw_text,
        ):
            canonical_content = canonicalize_learning_text(content)
            canonical_meaning = canonicalize_learning_text(meaning)
            if not canonical_content or reserved.contains(canonical_content):
                continue
            matches.append(LearningRuleMatch(
                rule_id=descriptor.rule_id,
                rule_version=descriptor.version,
                candidate_type=descriptor.candidate_type,
                canonical_content=canonical_content,
                meaning=canonical_meaning,
                start=start,
                end=end,
            ))
            if len(matches) >= descriptor.max_candidates_per_batch:
                break
    elapsed_ms = (time.perf_counter() - started) * 1000
    return LearningRuleDryRun(
        input_chars=len(raw_text),
        elapsed_ms=elapsed_ms,
        registry_generation=LEARNING_SIGNAL_RULE_REGISTRY.generation,
        registry_sha256=LEARNING_SIGNAL_RULE_REGISTRY.sha256,
        matches=tuple(matches),
    )


__all__ = [
    "LEARNING_SIGNAL_RULE_REGISTRY",
    "LearningRuleDryRun",
    "LearningRuleMatch",
    "LearningSignalRuleDescriptor",
    "canonicalize_learning_text",
    "dry_run_learning_rules",
]
