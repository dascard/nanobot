from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "manage-sandbox-production.sh"


def test_retired_owner_commands_fail_closed_without_host_actions():
    for command in (
        "provision-owner",
        "enable-workspace",
        "enable-assets",
        "enable-exec",
        "disable-owner",
    ):
        result = subprocess.run(
            [str(SCRIPT), command],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0
        assert "已永久停止执行" in combined
        assert "Sandbox 管理" in combined


def test_production_script_no_longer_contains_owner_or_tsv_write_logic():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "SANDBOX_OWNER_ID" not in source
    assert "SANDBOX_PLATFORM" not in source
    assert "PROJECT_MAP" not in source
    assert "record_project_mapping" not in source
    assert "apply_tool_override" not in source
    assert "--owner-id)" not in source
    assert "--project-id)" not in source
