#!/usr/bin/env python3
"""生成架构治理前的确定性行为 Golden。

该基线不是把所有现状永久合法化。Manifest 对每个快照显式标注
``known_bad``、``preserve`` 或 ``security_invariant``，后续只有经批准的行为
变更才能更新对应 Golden。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
FIXTURE_RELATIVE_PATH = Path(
    "tests/fixtures/architecture_behavior_cases.json"
)
GOLDEN_RELATIVE_ROOT = Path("tests/golden/architecture_behavior")
MANIFEST_RELATIVE_PATH = Path("docs/architecture/behavior-baseline.json")
ADMIN_SQL_SECURITY_BASELINE_SHA256 = (
    "5f369375cd179c523d77fc5728228fa1f6d70ef5d68b40f8dc55935213a4dbed"
)
STAGE47_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "24a8ea0718109500b32a1284ca7c1b810c47ad372bbebf752d4c9e2fd09cad5d"
)
STAGE5_PRIVATE_TIMING_BASELINE_SHA256 = (
    "42c50c79818dc783d15d070339f1c35bde2f4613951cc1075ba8ec14d51137a1"
)
STAGE5_PROMPT_RUNTIME_BASELINE_SHA256 = (
    "517b6dc684e6995f27a122d4e658e72d61a7cfddedb3eed60a7c4eb8dd7b95ab"
)
STAGE5_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "b88655df8155786208304d2252574f03995a25fb07d27ab1b2087653b907871c"
)
STAGE6_NEWS_SIGNAL_BASELINE_SHA256 = (
    "cca9102e4053fdbca33c38d5a16aee9b9a83ad7c290b144cc8a21cbaeda3ea5b"
)
STAGE6_PROMPT_RUNTIME_BASELINE_SHA256 = (
    "3e5fb59f1e05aa568eb285d3e564c96e73d1e424e2fb37168446ed9f673b1824"
)
STAGE6_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "89ab0c4a6e53be676d39cc717f74948d79a1008ad37802b53827656b49c96661"
)
STAGE7A_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "59ca2e9a073385589f81b7374a91030605c583122f2c3af2b74b27b1b1e40d9c"
)
STAGE7B_PROMPT_RUNTIME_BASELINE_SHA256 = (
    "e2947c4572ab7fa53619dbebd07c7a6ab5c4a28735d5de6209342c730d1226ee"
)
STAGE7C_PROMPT_RUNTIME_BASELINE_SHA256 = (
    "d6127b3f19ff082bdbb3163a384f394ca5b748fa524ecab0b176aa26e8fb54d5"
)
STAGE7B_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "6e902760c2fd31dcb2ae7b7ae8c10f7cc046ef9fbc6db23d386b7a36cbfce336"
)
STAGE7C_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "a2e5de1a65d2804950629d03cec6062ccf3594f53a5ed152c0a886e0f8e77c68"
)
STAGE6_PROMPT_INVENTORY_BASELINE_SHA256 = (
    "5405407faf159d9d99209d7452213172c8aaca86228d2e52f6e7d70b6579c43e"
)
PROACTIVE_OUTREACH_PROMPT_BASELINE_SHA256 = (
    "79a2d6dd773aeb8c314a610188eca9b88efafb1627c17eaf000dbbfe6bc9976d"
)
PROACTIVE_OUTREACH_RUNTIME_REGISTRY_BASELINE_SHA256 = (
    "1a3a4a5eef507c9c669a45e0281168bd534a3bf76b75d4e0b931f7eaad666552"
)

SNAPSHOT_CLASSIFICATIONS = {
    "group_analysis": "known_bad",
    "news_heuristics": "preserve",
    "private_timing": "preserve",
    "prompt_runtime": "preserve",
    "runtime_registries": "preserve",
    "security_invariants": "security_invariant",
}

SNAPSHOT_NOTES = {
    "group_analysis": (
        "记录现有命令清洗和正则时间窗口解析；阶段 7 可按批准差异更新。"
    ),
    "news_heuristics": (
        "记录新闻确定性信号、边界原因和统一来源 Registry；信号不再直接删除候选。"
    ),
    "private_timing": (
        "记录私聊 Timing v2 的 disabled、observation、active、模板、"
        "低置信和失败降级策略；数值 Timing 只保留为诊断快照。"
    ),
    "prompt_runtime": (
        "记录版本化 Prompt 文件及 section 权威描述符，防止无意漂移。"
    ),
    "runtime_registries": (
        "记录 route、tool、setting 和 task 当前事实源，供后续收敛对账。"
    ),
    "security_invariants": (
        "Sandbox 路径、Admin 结构化视图、URL、CQ 和 Prompt 权威边界不得削弱。"
    ),
}


class BehaviorBaselineError(RuntimeError):
    """行为基线无法构建或校验。"""


def render_json(value: Any) -> str:
    """生成稳定 JSON 文本。"""

    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BehaviorBaselineError(f"无法读取行为 fixture：{path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise BehaviorBaselineError("行为 fixture schema_version 无效")
    required = {
        "group_clean_messages",
        "group_windows",
        "news_texts",
        "private_policy",
        "admin_table_views",
        "prompt_nodes",
        "publication_text",
        "qq_messages",
        "sandbox_paths",
        "timing_decisions",
    }
    missing = required - set(payload)
    if missing:
        raise BehaviorBaselineError(
            "行为 fixture 缺少分区：" + ", ".join(sorted(missing))
        )
    return payload


def _ensure_repository_importable(root: Path) -> None:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def _private_timing_snapshot(fixture: Mapping[str, Any]) -> dict[str, Any]:
    from core.private_timing_contracts import (
        PRIVATE_DECISION_CONTRACT_VERSION,
    )
    from core.private_timing_policy import (
        PrivateTimingPolicy,
        PrivateTimingRolloutMode,
    )
    from core.timing_score import decide_timing

    policy_results = []
    model_defaults = {
        "action": "reply_now",
        "effort": "short",
        "intent": "general_question",
        "response_mode": "agent",
        "confidence": 0.92,
        "parse_quality": "schema_valid",
        "error_type": None,
        "conflicting_signals": [],
        "material_state": "none",
        "reason_code": "clear_request",
        "contract_version": PRIVATE_DECISION_CONTRACT_VERSION,
        "task_run_id": "taskrun_behavior_baseline",
    }
    for case in fixture["private_policy"]:
        policy = PrivateTimingPolicy(
            mode=PrivateTimingRolloutMode(case["mode"]),
            decision_confidence_threshold=0.70,
            template_confidence_threshold=0.85,
            source="behavior_fixture",
        )
        if case.get("without_model"):
            decision = policy.disabled_decision(timing_scoring=None)
        else:
            model_result = dict(model_defaults)
            model_result.update(case.get("model_result") or {})
            decision = policy.decide(
                model_result,
                timing_scoring=None,
            )
        policy_results.append({
            "id": case["id"],
            "decision": asdict(decision),
        })

    timing_results = []
    allowed_arguments = {
        "is_group",
        "is_private",
        "is_at_bot",
        "is_reply_to_bot",
        "bot_name_mentioned",
        "direct_call",
        "is_directed_to_other",
        "has_other_recipient",
        "is_other_bot",
        "has_files",
        "linger_score",
        "force_direct_score",
        "min_interval_active",
        "min_interval_remaining",
    }
    for case in fixture["timing_decisions"]:
        arguments = {
            key: value
            for key, value in case.items()
            if key in allowed_arguments
        }
        result = decide_timing(case["text"], **arguments)
        timing_results.append(
            {
                "id": case["id"],
                "decision": asdict(result),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_results": policy_results,
        "timing_results": timing_results,
    }


def _news_snapshot(fixture: Mapping[str, Any]) -> dict[str, Any]:
    from core.news.signals import NewsSignalExtractor
    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize_v2 import (
        extract_entities,
        extract_topic_keys,
        normalize_title,
        token_set,
    )

    extractor = NewsSignalExtractor()
    results = []
    for case in fixture["news_texts"]:
        combined = f"{case['title']} {case['summary']}"
        assessment = extractor.assess(
            candidate_id=case["id"],
            title=case["title"],
            summary=case["summary"],
        )
        results.append(
            {
                "id": case["id"],
                "normalized_title": normalize_title(case["title"]),
                "tokens": sorted(token_set(combined)),
                "entities": sorted(extract_entities(combined)),
                "topics": sorted(extract_topic_keys(combined)),
                "positive_signals": list(assessment.positive_signals),
                "negative_signals": list(assessment.negative_signals),
                "known_entities": list(assessment.known_entities),
                "unknown_entities": list(assessment.unknown_entities),
                "relevance_score": assessment.relevance_score,
                "review_reason": assessment.review_reason.value,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "results": results}


def _group_snapshot(fixture: Mapping[str, Any]) -> dict[str, Any]:
    from app.group_analysis.preprocess import (
        clean_message,
        parse_instruction_window_hours,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "clean_messages": [
            {
                "id": case["id"],
                "result": clean_message(case["content"]),
            }
            for case in fixture["group_clean_messages"]
        ],
        "instruction_windows": [
            {
                "id": case["id"],
                "hours": parse_instruction_window_hours(
                    case["instructions"]
                ),
            }
            for case in fixture["group_windows"]
        ],
    }


def _sandbox_path_results(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    from core.sandbox.contracts import SandboxServiceError
    from core.sandbox.paths import validate_relative_path

    results = []
    for case in fixture["sandbox_paths"]:
        try:
            components = validate_relative_path(
                case["path"],
                allow_empty=bool(case["allow_empty"]),
            )
        except SandboxServiceError as exc:
            result: dict[str, Any] = {
                "status": "error",
                "code": exc.code.value,
                "summary": exc.summary,
            }
        else:
            result = {
                "status": "success",
                "components": list(components),
            }
        results.append({"id": case["id"], "result": result})
    return results


def _admin_table_view_results(
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    from api.admin.db_browser_routes import AdminTableViewQuery
    from core.admin.table_views import ADMIN_TABLE_VIEW_REGISTRY

    request_schema = AdminTableViewQuery.model_json_schema()
    views = []
    for case in fixture["admin_table_views"]:
        descriptor = ADMIN_TABLE_VIEW_REGISTRY.get(case["view_id"])
        record: dict[str, Any] = {
            "id": case["id"],
            "view_id": case["view_id"],
            "registered": descriptor is not None,
        }
        if descriptor is not None:
            allowed_columns = set(descriptor.allowed_columns)
            record.update({
                "allowed_columns": list(descriptor.allowed_columns),
                "filters": [
                    item.filter_id for item in descriptor.filters
                ],
                "default_sort": descriptor.default_sort.to_dict(),
                "redact_columns": list(descriptor.redact_columns),
                "preview_only_columns": list(
                    descriptor.preview_only_columns
                ),
                "forbidden_columns_visible": sorted(
                    allowed_columns
                    & set(case.get("must_not_expose", []))
                ),
            })
        views.append(record)
    return {
        "registry": {
            "namespace": ADMIN_TABLE_VIEW_REGISTRY.namespace,
            "generation": ADMIN_TABLE_VIEW_REGISTRY.generation,
            "sha256": ADMIN_TABLE_VIEW_REGISTRY.sha256,
        },
        "request_fields": sorted(
            (request_schema.get("properties") or {}).keys()
        ),
        "extra_fields_forbidden": (
            request_schema.get("additionalProperties") is False
        ),
        "views": views,
    }


def _publication_results(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    from core.proactive_research import validate_research_publication_text

    return [
        {
            "id": case["id"],
            "validation_error": validate_research_publication_text(
                case["text"],
                case["sources"],
            ),
        }
        for case in fixture["publication_text"]
    ]


def _qq_results(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    from core.qq_outbound_renderer import render_qq_message_items

    results = []
    for case in fixture["qq_messages"]:
        rendered = render_qq_message_items(case["messages"])
        results.append(
            {
                "id": case["id"],
                "message": rendered.message,
                "warnings": list(rendered.warnings),
            }
        )
    return results


def _prompt_node_results(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    from core.prompt_v2.section_descriptors import (
        PromptSectionDescriptorError,
        descriptor_for_node,
        validate_node_descriptor_declaration,
    )

    results = []
    for case in fixture["prompt_nodes"]:
        descriptor = descriptor_for_node(case["node"]).to_dict()
        try:
            validate_node_descriptor_declaration(case["node"])
        except PromptSectionDescriptorError as exc:
            validation = {
                "status": "error",
                "error_type": type(exc).__name__,
                "summary": str(exc),
            }
        else:
            validation = {"status": "success"}
        results.append(
            {
                "id": case["id"],
                "descriptor": descriptor,
                "validation": validation,
            }
        )
    return results


def _security_snapshot(fixture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sandbox_paths": _sandbox_path_results(fixture),
        "admin_table_views": _admin_table_view_results(fixture),
        "publication_text": _publication_results(fixture),
        "qq_messages": _qq_results(fixture),
        "prompt_nodes": _prompt_node_results(fixture),
    }


def _versioned_prompt_paths(root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            [
                "git",
                "ls-files",
                "data/prompts_v2/**",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BehaviorBaselineError(
            "无法枚举版本化 Prompt Runtime 文件"
        ) from exc
    tracked_runtime_paths = [
        root / line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    canonical_root = root / "prompts.v2.default"
    canonical_paths: list[Path] = []
    if canonical_root.exists():
        for path in sorted(canonical_root.rglob("*")):
            if path.is_symlink():
                raise BehaviorBaselineError(
                    "canonical Prompt 文件不能是符号链接："
                    + path.relative_to(root).as_posix()
                )
            if path.is_file() and path.suffix in {".md", ".json"}:
                canonical_paths.append(path)
    missing = [
        path for path in tracked_runtime_paths if not path.is_file()
    ]
    if missing:
        raise BehaviorBaselineError(
            "版本化 Prompt 文件缺失："
            + ", ".join(path.relative_to(root).as_posix() for path in missing)
        )
    return sorted(set(canonical_paths) | set(tracked_runtime_paths))


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _prompt_snapshot(root: Path) -> dict[str, Any]:
    from core.prompt_v2.section_descriptors import (
        list_canonical_section_descriptors,
    )

    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_bytes(path.read_bytes()),
            "size_bytes": path.stat().st_size,
        }
        for path in _versioned_prompt_paths(root)
    ]
    descriptors = [
        descriptor.to_dict()
        for descriptor in list_canonical_section_descriptors()
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "files": files,
        "section_descriptors": descriptors,
    }


def _setting_snapshot() -> list[dict[str, Any]]:
    from core.config_registry import SETTING_DEFS

    return [
        {
            "key": key,
            "env_name": definition.env_name,
            "default": _jsonable(definition.default),
            "value_type": definition.value_type,
            "category": definition.category,
            "restart_required": bool(definition.restart_required),
            "dangerous": bool(definition.dangerous),
            "source_precedence": list(definition.source_precedence),
            "owner_module": definition.owner_module,
            "safety_class": definition.safety_class,
        }
        for key, definition in sorted(SETTING_DEFS.items())
    ]


def _tool_snapshot() -> list[dict[str, Any]]:
    from core.tool_registry import list_tool_descriptors

    return [
        {
            "name": descriptor.name,
            "category": descriptor.definition.category,
            "risk_level": descriptor.definition.risk_level,
            "private_default": descriptor.definition.private_default,
            "group_default": descriptor.definition.group_default,
            "availability_policy": descriptor.availability_policy,
            "execution_policy": descriptor.execution_policy,
            "trace_policy": descriptor.trace_policy,
            "prompt_template_keys": list(descriptor.prompt_template_keys),
            "owner_module": descriptor.owner_module,
            "domain": descriptor.domain,
            "framework_owned": descriptor.framework_owned,
        }
        for descriptor in list_tool_descriptors()
    ]


def _registry_snapshot() -> dict[str, Any]:
    from core.news.source_registry import get_news_source_registry
    from core.model_provider.route_registry import (
        list_model_route_descriptors,
        model_route_registry_snapshot,
    )
    from core.prompt_v2.task_contracts import task_contract_registry_snapshot
    from core.route_metadata import ROUTE_METADATA

    model_route_snapshot = model_route_registry_snapshot()
    news_source_registry = get_news_source_registry()
    news_source_snapshot = news_source_registry.registry_snapshot
    return {
        "schema_version": SCHEMA_VERSION,
        "routes": {
            key: dict(value) for key, value in sorted(ROUTE_METADATA.items())
        },
        "tools": _tool_snapshot(),
        "settings": _setting_snapshot(),
        "task_contracts": list(task_contract_registry_snapshot()),
        "model_route_registry": {
            "generation": model_route_snapshot.generation,
            "sha256": model_route_snapshot.sha256,
            "routes": [
                descriptor.metadata()
                for descriptor in list_model_route_descriptors()
            ],
        },
        "news_source_registry": {
            "resource_version": news_source_registry.resource_version,
            "generation": news_source_snapshot.generation,
            "sha256": news_source_snapshot.sha256,
            "sources": [
                descriptor.registry_payload()
                for descriptor in news_source_registry.descriptors()
            ],
        },
    }


def build_behavior_snapshots(
    root: Path,
    fixture: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """在不访问网络和生产数据库的前提下生成全部快照。"""

    repository_root = root.resolve()
    _ensure_repository_importable(repository_root)
    return {
        "group_analysis": _group_snapshot(fixture),
        "news_heuristics": _news_snapshot(fixture),
        "private_timing": _private_timing_snapshot(fixture),
        "prompt_runtime": _prompt_snapshot(repository_root),
        "runtime_registries": _registry_snapshot(),
        "security_invariants": _security_snapshot(fixture),
    }


def _git_revision(root: Path, *extra_args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *extra_args, "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BehaviorBaselineError("无法读取 Git 基线版本") from exc
    revision = completed.stdout.strip()
    if len(revision) != 40:
        raise BehaviorBaselineError("Git 基线版本格式无效")
    return revision


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _manifest(
    root: Path,
    *,
    fixture_path: Path,
    snapshot_paths: Mapping[str, Path],
) -> dict[str, Any]:
    kt_root = root / "vendor/KohakuTerrarium"
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline_git_commit": _git_revision(root),
        "runtime_versions": {
            "python_constraint": ">=3.11",
            "generated_with_python": (
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
            "kt_commit": _git_revision(kt_root),
            "prompt_runtime_schema": "v2",
        },
        "fixture": {
            "path": fixture_path.relative_to(root).as_posix(),
            "sha256": _sha256_file(fixture_path),
        },
        "generation": {
            "command": (
                "python scripts/build_behavior_baseline.py "
                "--root \"$PWD\" --write"
            ),
            "environment": (
                "清除代理；不访问网络；不读取生产数据库；只读取版本化 Prompt 文件"
            ),
        },
        "approved_changes": [
            {
                "id": "stage1_admin_structured_views",
                "snapshot_id": "security_invariants",
                "stage": "阶段 1.1",
                "before_sha256": ADMIN_SQL_SECURITY_BASELINE_SHA256,
                "after_sha256": _sha256_file(
                    snapshot_paths["security_invariants"]
                ),
                "reason": (
                    "删除 Admin 任意 SQL 产品入口，改为代码所有的结构化 "
                    "Table View Registry；Sandbox、URL、CQ 和 Prompt "
                    "安全基线保持不变。"
                ),
            },
            {
                "id": "stage47_task_slo_registry",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 4.7",
                "before_sha256": (
                    STAGE47_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "after_sha256": STAGE5_RUNTIME_REGISTRY_BASELINE_SHA256,
                "reason": (
                    "为现有语义 Task Route 增加显式 Task SLO Descriptor "
                    "引用；未改变 Tool、Setting、Prompt 或安全边界。"
                ),
            },
            {
                "id": "stage5_private_timing_policy",
                "snapshot_id": "private_timing",
                "stage": "阶段 5",
                "before_sha256": STAGE5_PRIVATE_TIMING_BASELINE_SHA256,
                "after_sha256": _sha256_file(
                    snapshot_paths["private_timing"]
                ),
                "reason": (
                    "删除私聊自然语言关键词的最终语义决策，改为单次结构化"
                    "分类、按会话观察和显式发布门禁；模型或合同失败统一进入"
                    "正常 Agent。"
                ),
            },
            {
                "id": "stage5_private_timing_prompt",
                "snapshot_id": "prompt_runtime",
                "stage": "阶段 5",
                "before_sha256": STAGE5_PROMPT_RUNTIME_BASELINE_SHA256,
                "after_sha256": STAGE6_PROMPT_RUNTIME_BASELINE_SHA256,
                "reason": (
                    "同步 private_decision_v2 的 canonical/runtime Prompt、"
                    "变量和严格结构化输出约束。"
                ),
            },
            {
                "id": "stage5_private_timing_registry",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 5",
                "before_sha256": STAGE5_RUNTIME_REGISTRY_BASELINE_SHA256,
                "after_sha256": STAGE6_RUNTIME_REGISTRY_BASELINE_SHA256,
                "reason": (
                    "把 private_decision 输出合同升级为 v2，登记私聊灰度"
                    "设置和 Feature 生命周期；默认保持 disabled。"
                ),
            },
            {
                "id": "stage6_news_signals",
                "snapshot_id": "news_heuristics",
                "stage": "阶段 6",
                "before_sha256": STAGE6_NEWS_SIGNAL_BASELINE_SHA256,
                "after_sha256": _sha256_file(
                    snapshot_paths["news_heuristics"]
                ),
                "reason": (
                    "删除关键词直接过滤，把词典和正则收敛为可审计信号；"
                    "未知实体、冲突和边界候选进入批量审核。"
                ),
            },
            {
                "id": "stage6_prompt_inventory_coverage",
                "snapshot_id": "prompt_runtime",
                "stage": "阶段 6",
                "before_sha256": STAGE6_PROMPT_RUNTIME_BASELINE_SHA256,
                "after_sha256": STAGE6_PROMPT_INVENTORY_BASELINE_SHA256,
                "reason": (
                    "修复 Prompt Golden 只枚举 Git 已跟踪文件的覆盖缺口，"
                    "使 canonical 目录中新登记但尚未提交的模板也进入审计。"
                ),
            },
            {
                "id": "stage6_news_prompt",
                "snapshot_id": "prompt_runtime",
                "stage": "阶段 6",
                "before_sha256": STAGE6_PROMPT_INVENTORY_BASELINE_SHA256,
                "after_sha256": STAGE7B_PROMPT_RUNTIME_BASELINE_SHA256,
                "reason": (
                    "新增 news_relevance_review 严格批量输出合同及 "
                    "canonical/runtime Prompt。"
                ),
            },
            {
                "id": "stage7b_group_learning_prompt",
                "snapshot_id": "prompt_runtime",
                "stage": "阶段 7B",
                "before_sha256": STAGE7B_PROMPT_RUNTIME_BASELINE_SHA256,
                "after_sha256": (
                    STAGE7C_PROMPT_RUNTIME_BASELINE_SHA256
                ),
                "reason": (
                    "同步 group_memory_learning 的 candidate-only 审核边界；"
                    "模型输出只形成观察建议，不直接激活、删除或注入长期记忆。"
                ),
            },
            {
                "id": "stage7c_group_analysis_prompt",
                "snapshot_id": "prompt_runtime",
                "stage": "阶段 7C",
                "before_sha256": (
                    STAGE7C_PROMPT_RUNTIME_BASELINE_SHA256
                ),
                "after_sha256": (
                    PROACTIVE_OUTREACH_PROMPT_BASELINE_SHA256
                ),
                "reason": (
                    "为 group_analysis 增加 Registry 驱动的可选 aspects，"
                    "同步显式工具兼容默认和定时学习默认；同时把经来源审核"
                    "且无冲突的正式群体记忆作为不可信背景接入 canonical "
                    "Prompt Contribution，未选择方面不创建模型分支或报告"
                    "区块。"
                ),
            },
            {
                "id": "proactive_outreach_fact_guard_prompt",
                "snapshot_id": "prompt_runtime",
                "stage": "主动外呼事实性治理",
                "before_sha256": (
                    PROACTIVE_OUTREACH_PROMPT_BASELINE_SHA256
                ),
                "after_sha256": _sha256_file(
                    snapshot_paths["prompt_runtime"]
                ),
                "reason": (
                    "把主动外呼话题提取升级为带生命周期和证据的结构化"
                    "合同，向正文传递完整选题依据，并新增生成后事实性与"
                    "语义质量复核 Prompt。"
                ),
            },
            {
                "id": "stage6_news_registry",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 6",
                "before_sha256": STAGE6_RUNTIME_REGISTRY_BASELINE_SHA256,
                "after_sha256": STAGE7A_RUNTIME_REGISTRY_BASELINE_SHA256,
                "reason": (
                    "登记新闻来源、模型路由、Task、SLO、Feature 和受管设置；"
                    "新行为默认 disabled 且 SLO 仅允许观察。"
                ),
            },
            {
                "id": "stage7a_group_learning_registry",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 7A",
                "before_sha256": (
                    STAGE7A_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "after_sha256": STAGE7B_RUNTIME_REGISTRY_BASELINE_SHA256,
                "reason": (
                    "登记默认关闭的群学习总开关；Schema、只读查询和"
                    "迁移审计不启用候选 Writer 或 Prompt 注入。"
                ),
            },
            {
                "id": "stage7b_group_learning_registry",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 7B",
                "before_sha256": (
                    STAGE7B_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "after_sha256": (
                    STAGE7C_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "reason": (
                    "把 group_memory_learning Task owner 纠正为"
                    " candidate-only 应用模块 app.group_learning；"
                    "群学习 Feature 仍保持 experimental 且默认关闭。"
                ),
            },
            {
                "id": "stage8_group_learning_rule_controls",
                "snapshot_id": "runtime_registries",
                "stage": "阶段 8",
                "before_sha256": (
                    STAGE7C_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "after_sha256": (
                    PROACTIVE_OUTREACH_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "reason": (
                    "登记 Web 群学习工作台使用的受管规则启停配置；默认值"
                    "为空且标记为危险设置，不创建白名单、不启用学习或"
                    " Prompt 注入。"
                ),
            },
            {
                "id": "proactive_outreach_fact_guard_registry",
                "snapshot_id": "runtime_registries",
                "stage": "主动外呼事实性治理",
                "before_sha256": (
                    PROACTIVE_OUTREACH_RUNTIME_REGISTRY_BASELINE_SHA256
                ),
                "after_sha256": _sha256_file(
                    snapshot_paths["runtime_registries"]
                ),
                "reason": (
                    "升级主动外呼话题与 Judge 输出合同，登记独立质量复核"
                    "模型路由及其受管配置。"
                ),
            },
        ],
        "snapshots": [
            {
                "id": snapshot_name,
                "path": snapshot_paths[snapshot_name]
                .relative_to(root)
                .as_posix(),
                "sha256": _sha256_file(snapshot_paths[snapshot_name]),
                "classification": SNAPSHOT_CLASSIFICATIONS[snapshot_name],
                "note": SNAPSHOT_NOTES[snapshot_name],
            }
            for snapshot_name in sorted(snapshot_paths)
        ],
    }


def write_baseline(root: Path) -> None:
    fixture_path = root / FIXTURE_RELATIVE_PATH
    fixture = load_fixture(fixture_path)
    snapshots = build_behavior_snapshots(root, fixture)
    snapshot_paths: dict[str, Path] = {}
    for snapshot_name, payload in sorted(snapshots.items()):
        path = root / GOLDEN_RELATIVE_ROOT / f"{snapshot_name}.json"
        _write_atomic(path, render_json(payload))
        snapshot_paths[snapshot_name] = path
    manifest = _manifest(
        root,
        fixture_path=fixture_path,
        snapshot_paths=snapshot_paths,
    )
    _write_atomic(root / MANIFEST_RELATIVE_PATH, render_json(manifest))


def check_baseline(root: Path) -> list[str]:
    errors: list[str] = []
    fixture_path = root / FIXTURE_RELATIVE_PATH
    try:
        fixture = load_fixture(fixture_path)
        snapshots = build_behavior_snapshots(root, fixture)
    except BehaviorBaselineError as exc:
        return [str(exc)]

    for snapshot_name, payload in sorted(snapshots.items()):
        path = root / GOLDEN_RELATIVE_ROOT / f"{snapshot_name}.json"
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(f"缺少或无法读取 Golden：{path.relative_to(root)}")
            continue
        if current != render_json(payload):
            errors.append(f"行为 Golden 已漂移：{path.relative_to(root)}")

    manifest_path = root / MANIFEST_RELATIVE_PATH
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"缺少或无法读取 Manifest：{MANIFEST_RELATIVE_PATH}")
        return errors
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("行为基线 Manifest schema_version 无效")
    fixture_record = manifest.get("fixture") or {}
    if fixture_record.get("sha256") != _sha256_file(fixture_path):
        errors.append("行为 fixture SHA-256 已漂移")
    records = {
        item.get("id"): item
        for item in manifest.get("snapshots", [])
        if isinstance(item, dict)
    }
    if set(records) != set(SNAPSHOT_CLASSIFICATIONS):
        errors.append("行为基线 Manifest 快照集合不完整")
    for snapshot_name, classification in SNAPSHOT_CLASSIFICATIONS.items():
        record = records.get(snapshot_name) or {}
        path = root / GOLDEN_RELATIVE_ROOT / f"{snapshot_name}.json"
        if record.get("classification") != classification:
            errors.append(f"{snapshot_name} 分类已漂移")
        if path.is_file() and record.get("sha256") != _sha256_file(path):
            errors.append(f"{snapshot_name} SHA-256 已漂移")
    return errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成或检查架构治理前的行为 Golden",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="仓库根目录，默认当前目录",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="写入 Golden 和 Manifest")
    mode.add_argument("--check", action="store_true", help="检查行为基线漂移")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.write:
            write_baseline(root)
            return 0
        errors = check_baseline(root)
    except BehaviorBaselineError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if errors:
        print("行为基线检查失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
