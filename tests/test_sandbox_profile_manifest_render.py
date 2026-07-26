from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.sandbox.profile_catalog import load_profile_catalog


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-sandbox-profile-manifest.py"
SOURCE = ROOT / "config" / "sandbox-execution-profiles.v1.json"
PROXY_ID = (
    "sha256:05a5bbc3966ef22b15a0fa708722cf5a"
    "d730e8266473ab35ecfc932fb1b4e2ed"
)
RESTRICTED_ID = f"sha256:{'1' * 64}"
DEVELOPER_ID = f"sha256:{'2' * 64}"


def _run(tmp_path: Path, *, proxy_id: str = PROXY_ID):
    output = tmp_path / "profile-manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(SOURCE),
            "--output",
            str(output),
            "--generation",
            "20260725.2.abcdef123456",
            "--restricted-reference",
            "nanobot-sandbox-python:release-1",
            "--restricted-image-id",
            RESTRICTED_ID,
            "--developer-reference",
            "nanobot-sandbox-developer:release-1",
            "--developer-image-id",
            DEVELOPER_ID,
            "--proxy-reference",
            "nanobot-sandbox-egress-proxy:2026.07.25",
            "--proxy-image-id",
            proxy_id,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def test_rendered_manifest_binds_two_profiles_and_preserves_disabled_placeholder(
    tmp_path,
):
    result, output = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "policy_sha256=" in result.stdout
    assert "manifest_sha256=" in result.stdout
    assert output.stat().st_mode & 0o777 == 0o600

    catalog = load_profile_catalog(output)
    restricted = catalog.profile("restricted")
    developer = catalog.profile("developer")
    trusted = catalog.profile("trusted_developer")

    assert catalog.catalog_generation == "20260725.2.abcdef123456"
    assert restricted.image_reference == "nanobot-sandbox-python:release-1"
    assert restricted.image_allowlist == (RESTRICTED_ID,)
    assert developer.image_reference == "nanobot-sandbox-developer:release-1"
    assert developer.image_allowlist == (DEVELOPER_ID,)
    assert developer.network_proxy_image_allowlist == (PROXY_ID,)
    assert trusted.image_reference == developer.image_reference
    assert trusted.image_allowlist == ()
    assert trusted.grantable is False


def test_rendered_manifest_rejects_unreviewed_proxy_digest(tmp_path):
    result, output = _run(tmp_path, proxy_id=f"sha256:{'3' * 64}")

    assert result.returncode != 0
    assert "canonical 网络策略不一致" in result.stderr
    assert not output.exists()


def test_rendered_manifest_rejects_symbolic_link_source(tmp_path):
    linked_source = tmp_path / "manifest.json"
    linked_source.symlink_to(SOURCE)
    output = tmp_path / "output.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--source",
        str(linked_source),
        "--output",
        str(output),
        "--generation",
        "20260725.2.abcdef123456",
        "--restricted-reference",
        "nanobot-sandbox-python:release-1",
        "--restricted-image-id",
        RESTRICTED_ID,
        "--developer-reference",
        "nanobot-sandbox-developer:release-1",
        "--developer-image-id",
        DEVELOPER_ID,
        "--proxy-reference",
        "nanobot-sandbox-egress-proxy:2026.07.25",
        "--proxy-image-id",
        PROXY_ID,
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert not output.exists()
