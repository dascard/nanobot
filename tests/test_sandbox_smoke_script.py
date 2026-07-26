from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sandbox-smoke-test.sh"


def test_smoke_script_has_six_structured_fail_closed_groups():
    source = SCRIPT.read_text(encoding="utf-8")
    syntax = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert syntax.returncode == 0, syntax.stderr
    assert help_result.returncode == 0, help_result.stderr
    for option in (
        "--manifest",
        "--data-root",
        "--evidence-root",
        "--preflight-only",
    ):
        assert option in help_result.stdout
    for group_id in (
        "basic-security",
        "lease",
        "process",
        "developer-toolchain",
        "network",
        "data-continuity",
    ):
        assert f"  {group_id} \\" in source
    assert "--junitxml=" in source
    assert "sandbox-smoke-summary.py" in source
    assert 'summary_file="${evidence_dir}/summary.json"' in source
    assert "tests <= 0" not in source
    assert "grep -Eq '1 passed'" not in source


def test_smoke_script_preflight_checks_real_host_security_and_three_images():
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split("preflight_checks() {", 1)[1].split(
        "\nsummary_arguments=()",
        1,
    )[0]

    assert "EUID == 0" in body
    assert "command -v install" in body
    assert "Docker 未启用 seccomp" in body
    assert "Docker 未启用 AppArmor" in body
    assert "nanobot-sandbox-restricted" in body
    assert "nanobot-sandbox-developer" in body
    assert "check-sandbox-data-disk.sh" in body
    assert "--check-capability" in body
    assert "load_profile_catalog" in body
    assert 'for profile_id in ("restricted", "developer")' in body
    assert "network_proxy_image_allowlist" in body
    assert "trusted.grantable" in body


def test_smoke_script_contains_no_global_or_unknown_resource_cleanup():
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in (
        "docker system prune",
        "docker image prune",
        "docker volume prune",
        "docker network prune",
        "docker compose down -v",
        "\nsudo -",
        "\nsudo /",
        "docker rm ",
        "docker network rm ",
    ):
        assert forbidden not in source


def test_blocked_preflight_writes_structured_summary(tmp_path):
    evidence_root = tmp_path / "evidence"
    result = subprocess.run(
        [
            str(SCRIPT),
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--data-root",
            str(tmp_path / "missing-data"),
            "--evidence-root",
            str(evidence_root),
            "--preflight-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    summaries = list(evidence_root.glob("*/summary.json"))
    assert result.returncode == 2
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["preflight"]["status"] == "blocked"
    assert summary["groups"] == []
    assert summary["result"] == "blocked"
