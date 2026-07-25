"""硬编码决策规则审计器合同测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_audit_repository_detects_python_decision_rules(tmp_path):
    from scripts.audit_decision_rules import audit_repository

    _write(
        tmp_path,
        "core/private_timing.py",
        """
import re

_WAIT_MARKERS = {"等一下", "稍后"}
_DATE_PATTERN = re.compile(r"^20\\d{2}-\\d{2}-\\d{2}$")

def decide(text, status):
    if any(marker in text for marker in _WAIT_MARKERS):
        return "wait"
    if status in {"legacy", "retired"}:
        return "compat"
    if len(text) > 240:
        return "long"
    return "reply"
""".strip()
        + "\n",
    )

    report = audit_repository(tmp_path, scan_roots=("core",))
    rules = report.rules
    detectors = {rule.detector for rule in rules}

    assert "python.regex_call" in detectors
    assert "python.literal_collection" in detectors
    assert "python.string_control_flow" in detectors
    assert "python.numeric_control_flow" in detectors
    assert all(rule.path == "core/private_timing.py" for rule in rules)
    assert all(rule.owner == "chat.private_timing" for rule in rules)
    assert all(rule.category for rule in rules)
    assert all(rule.disposition for rule in rules)
    assert all(rule.rule_id.startswith("decision.") for rule in rules)
    assert all(rule.input_boundary for rule in rules)
    assert all(rule.plan_stage for rule in rules)
    assert all(isinstance(rule.current_tests, tuple) for rule in rules)


def test_audit_repository_detects_web_and_shell_rules_and_excludes_generated_roots(
    tmp_path,
):
    from scripts.audit_decision_rules import audit_repository

    _write(
        tmp_path,
        "webui/src/router.jsx",
        """
const ROUTES = ['/tools', '/sandbox']
const mention = /@\\S+/gu
if (pathname.startsWith('/sandbox')) openSandbox()
api.post('/db/query', { query })
""".strip()
        + "\n",
    )
    _write(
        tmp_path,
        "scripts/deploy.sh",
        """
case "$MODE" in
  production|staging) ;;
esac
if [[ "$IMAGE" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  exit 0
fi
""".strip()
        + "\n",
    )
    _write(tmp_path, "tests/test_noise.py", "if value == 'noise': pass\n")
    _write(tmp_path, "vendor/pkg/noise.py", "if value == 'noise': pass\n")
    _write(tmp_path, "webui/dist/app.js", "const ignored = /noise/;\n")

    report = audit_repository(
        tmp_path,
        scan_roots=("webui/src", "scripts", "tests", "vendor", "webui/dist"),
    )

    paths = {rule.path for rule in report.rules}
    detectors = {rule.detector for rule in report.rules}
    assert paths == {"scripts/deploy.sh", "webui/src/router.jsx"}
    assert "web.regex_literal" in detectors
    assert "web.route_literal" in detectors
    assert "shell.regex_condition" in detectors
    assert "shell.case_pattern" in detectors


def test_rule_ids_and_serialized_reports_are_deterministic_when_lines_move(tmp_path):
    from scripts.audit_decision_rules import (
        audit_repository,
        render_json,
        render_markdown,
    )

    source = """
import re
RULE = re.compile(r"^asset://")
def accepts(value):
    return value.startswith("asset://")
""".strip()
    _write(tmp_path, "core/assets.py", source + "\n")
    first = audit_repository(
        tmp_path,
        scan_roots=("core",),
        source_revision="abc123",
    )

    _write(tmp_path, "core/assets.py", "\n\n" + source + "\n")
    second = audit_repository(
        tmp_path,
        scan_roots=("core",),
        source_revision="abc123",
    )

    assert [rule.rule_id for rule in first.rules] == [
        rule.rule_id for rule in second.rules
    ]
    assert render_json(first).endswith("\n")
    assert render_markdown(first).endswith("\n")
    assert render_json(first) == render_json(first)
    assert render_markdown(first) == render_markdown(first)

    payload = json.loads(render_json(first))
    assert payload["schema_version"] == 1
    assert payload["source_revision"] == "abc123"
    assert payload["summary"]["rules_total"] == len(first.rules)
    assert list(payload["summary"]["by_category"]) == sorted(
        payload["summary"]["by_category"],
    )


def test_explicit_classification_overrides_are_applied_and_validated(tmp_path):
    from scripts.audit_decision_rules import (
        AuditConfigurationError,
        audit_repository,
    )

    _write(
        tmp_path,
        "core/sample.py",
        'def decide(text):\n    return text == "随便聊聊"\n',
    )
    baseline = audit_repository(tmp_path, scan_roots=("core",))
    target = baseline.rules[0]
    overrides = {
        target.rule_id: {
            "category": "natural_language_semantic",
            "disposition": "model_signal_only",
            "review_status": "reviewed",
            "reason": "人工确认：自然语言意图不能由字面量直接决定",
        }
    }

    reviewed = audit_repository(
        tmp_path,
        scan_roots=("core",),
        overrides=overrides,
    )
    rule = reviewed.rules[0]
    assert rule.category == "natural_language_semantic"
    assert rule.disposition == "model_signal_only"
    assert rule.review_status == "reviewed"
    assert rule.reason.startswith("人工确认")

    invalid = {
        target.rule_id: {
            "category": "不存在",
            "disposition": "preserve",
            "review_status": "reviewed",
            "reason": "invalid",
        }
    }
    try:
        audit_repository(
            tmp_path,
            scan_roots=("core",),
            overrides=invalid,
        )
    except AuditConfigurationError as exc:
        assert "category" in str(exc)
    else:
        raise AssertionError("非法人工分类必须被拒绝")


def test_unknown_override_rule_id_is_rejected(tmp_path):
    from scripts.audit_decision_rules import (
        AuditConfigurationError,
        audit_repository,
    )

    _write(tmp_path, "core/sample.py", "VALUE = 1\n")

    try:
        audit_repository(
            tmp_path,
            scan_roots=("core",),
            overrides={
                "decision.unknown": {
                    "category": "protocol_syntax",
                    "disposition": "preserve",
                    "review_status": "reviewed",
                    "reason": "不存在的规则",
                }
            },
        )
    except AuditConfigurationError as exc:
        assert "decision.unknown" in str(exc)
    else:
        raise AssertionError("悬空人工覆盖必须被拒绝")


def test_group_override_requires_exact_rule_set_fingerprint(tmp_path):
    from scripts.audit_decision_rules import (
        AuditConfigurationError,
        audit_repository,
    )

    _write(
        tmp_path,
        "core/private_timing.py",
        """
MARKERS = {"等一下", "稍后"}
def decide(text):
    return text in MARKERS
""".strip()
        + "\n",
    )
    baseline = audit_repository(tmp_path, scan_roots=("core",))
    rule_ids = sorted(rule.rule_id for rule in baseline.rules)
    rule_id_sha256 = hashlib.sha256(
        "\n".join(rule_ids).encode("utf-8")
    ).hexdigest()
    group_override = {
        "group_id": "review.private_timing",
        "match": {
            "path": "core/private_timing.py",
            "category": "natural_language_semantic",
        },
        "expected_count": len(rule_ids),
        "expected_rule_ids_sha256": rule_id_sha256,
        "override": {
            "category": "natural_language_semantic",
            "disposition": "model_signal_only",
            "review_status": "reviewed",
            "reason": "人工确认：只允许作为模型分类输入信号",
        },
    }

    reviewed = audit_repository(
        tmp_path,
        scan_roots=("core",),
        group_overrides=(group_override,),
    )
    assert all(rule.review_status == "reviewed" for rule in reviewed.rules)

    invalid_group = {
        **group_override,
        "expected_rule_ids_sha256": "0" * 64,
    }
    with pytest.raises(AuditConfigurationError, match="集合哈希"):
        audit_repository(
            tmp_path,
            scan_roots=("core",),
            group_overrides=(invalid_group,),
        )


def test_python_extended_syntax_and_shell_without_suffix_are_detected(tmp_path):
    from scripts.audit_decision_rules import audit_repository

    _write(
        tmp_path,
        "api/routes.py",
        """
import regex

MAPPING = {"a": "alpha", "b": "beta"}
VALUES = ["x", "y"]
PAIR = ("left", "right")
PATTERN = regex.compile(r"^v\\d+$")

@router.get("/items/{item_id}")
def get_item(item_id):
    if item_id in MAPPING or item_id in VALUES:
        return 1
    if item_id.startswith(PAIR):
        return 1
    match item_id:
        case "legacy":
            return 2
    return 0

@router.post("/items")
async def create_item():
    return None
""".strip()
        + "\n",
    )
    _write(
        tmp_path,
        "scripts/deploy",
        """
#!/usr/bin/env bash
if [[ "$COUNT" -gt 3 ]]; then
  exit 1
fi
""".strip()
        + "\n",
    )

    report = audit_repository(
        tmp_path,
        scan_roots=("api", "scripts"),
    )
    detectors = {rule.detector for rule in report.rules}

    assert "python.literal_mapping" in detectors
    assert "python.literal_collection" in detectors
    assert "python.regex_call" in detectors
    assert "python.route_literal" in detectors
    assert "python.string_control_flow" in detectors
    assert "python.match_literal" in detectors
    assert "shell.literal_condition" in detectors
    assert sum(
        rule.detector == "python.route_literal" for rule in report.rules
    ) == 2


def test_inert_payload_collections_are_not_misreported_as_decisions(tmp_path):
    from scripts.audit_decision_rules import audit_repository

    _write(
        tmp_path,
        "core/payloads.py",
        """
def build_payload():
    return {
        "status": "success",
        "data": {"count": 1, "enabled": True},
        "items": ["one", "two", "three"],
    }

def decide(value):
    allowed_modes = {"preview", "active"}
    if value in allowed_modes:
        return True
    return value == "legacy"
""".strip()
        + "\n",
    )

    report = audit_repository(tmp_path, scan_roots=("core",))
    excerpts = [rule.excerpt for rule in report.rules]

    assert not any('"status": "success"' in excerpt for excerpt in excerpts)
    assert not any('"one", "two", "three"' in excerpt for excerpt in excerpts)
    assert any("allowed_modes" in excerpt for excerpt in excerpts)


def test_auditor_excludes_itself_to_avoid_recursive_inventory_drift(tmp_path):
    from scripts.audit_decision_rules import audit_repository

    _write(
        tmp_path,
        "scripts/audit_decision_rules.py",
        'if category == "compatibility":\n    pass\n',
    )
    _write(
        tmp_path,
        "scripts/runtime_check.py",
        'if mode == "production":\n    pass\n',
    )

    report = audit_repository(tmp_path, scan_roots=("scripts",))

    assert {rule.path for rule in report.rules} == {
        "scripts/runtime_check.py"
    }


def test_scan_errors_are_reported_without_exposing_absolute_paths(tmp_path):
    from scripts.audit_decision_rules import audit_repository, render_markdown

    _write(tmp_path, "core/broken.py", "def broken(:\n")
    binary = tmp_path / "core" / "invalid.py"
    binary.write_bytes(b"\xff\xfe")

    report = audit_repository(tmp_path, scan_roots=("core",))
    markdown = render_markdown(report)

    assert report.rules == ()
    assert {error.error_type for error in report.errors} == {
        "SyntaxError",
        "UnicodeDecodeError",
    }
    assert all(str(tmp_path) not in error.summary for error in report.errors)
    assert "## 扫描错误" in markdown
    assert "`core/broken.py`" in markdown


@pytest.mark.parametrize(
    ("override", "expected_fragment"),
    [
        (
            {
                "category": "protocol_syntax",
                "disposition": "preserve",
                "review_status": "reviewed",
                "reason": "ok",
                "unexpected": "value",
            },
            "不可覆盖字段",
        ),
        (
            {
                "category": "protocol_syntax",
                "disposition": "preserve",
                "review_status": "reviewed",
            },
            "缺少人工分类字段",
        ),
        (
            {
                "category": "protocol_syntax",
                "disposition": "invalid",
                "review_status": "reviewed",
                "reason": "ok",
            },
            "disposition",
        ),
        (
            {
                "category": "protocol_syntax",
                "disposition": "preserve",
                "review_status": "invalid",
                "reason": "ok",
            },
            "review_status",
        ),
        (
            {
                "category": "protocol_syntax",
                "disposition": "preserve",
                "review_status": "reviewed",
                "reason": " ",
            },
            "reason",
        ),
    ],
)
def test_override_contract_rejects_incomplete_or_invalid_values(
    tmp_path,
    override,
    expected_fragment,
):
    from scripts.audit_decision_rules import (
        AuditConfigurationError,
        audit_repository,
    )

    _write(
        tmp_path,
        "core/sample.py",
        'def accepts(value):\n    return value == "v1"\n',
    )
    baseline = audit_repository(tmp_path, scan_roots=("core",))

    with pytest.raises(AuditConfigurationError, match=expected_fragment):
        audit_repository(
            tmp_path,
            scan_roots=("core",),
            overrides={baseline.rules[0].rule_id: override},
        )


def test_cli_writes_checks_and_reports_inventory_drift(tmp_path, capsys):
    from scripts.audit_decision_rules import main

    _write(
        tmp_path,
        "core/sample.py",
        'def accepts(value):\n    return value == "v1"\n',
    )
    common_arguments = [
        "--root",
        str(tmp_path),
        "--scan-root",
        "core",
        "--source-revision",
        "revision-1",
    ]

    assert main([*common_arguments, "--write"]) == 0
    json_output = (
        tmp_path / "docs/architecture/decision-rule-inventory.json"
    )
    markdown_output = (
        tmp_path / "docs/architecture/decision-rule-inventory.md"
    )
    assert json_output.is_file()
    assert markdown_output.is_file()
    assert main([*common_arguments, "--check"]) == 0

    markdown_output.write_text("stale\n", encoding="utf-8")
    assert main([*common_arguments, "--check"]) == 1
    assert "已漂移" in capsys.readouterr().err


def test_cli_accepts_enveloped_overrides_and_prints_markdown(tmp_path, capsys):
    from scripts.audit_decision_rules import audit_repository, main

    _write(
        tmp_path,
        "core/sample.py",
        'def accepts(value):\n    return value == "v1"\n',
    )
    baseline = audit_repository(tmp_path, scan_roots=("core",))
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(
        json.dumps(
            {
                "overrides": {
                    baseline.rules[0].rule_id: {
                        "category": "protocol_syntax",
                        "disposition": "preserve",
                        "review_status": "approved",
                        "reason": "人工确认协议版本",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "--scan-root",
                "core",
                "--source-revision",
                "revision-2",
                "--overrides",
                str(overrides_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "# 决策规则审计清单" in output
    assert "`approved`" in output
    assert r"\|" not in output


def test_cli_rejects_malformed_override_file(tmp_path):
    from scripts.audit_decision_rules import main

    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--root",
                str(tmp_path),
                "--overrides",
                str(overrides_path),
            ]
        )

    assert exc_info.value.code == 2
