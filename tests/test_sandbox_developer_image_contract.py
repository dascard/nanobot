from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVELOPER_CONTEXT = ROOT / "docker/sandbox/developer"
BASE_IMAGE = (
    "python:3.11.13-slim-bookworm@sha256:"
    "86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1"
)


def test_developer_image_context_is_minimal_pinned_and_non_root():
    dockerfile = (DEVELOPER_CONTEXT / "Dockerfile").read_text()
    context_files = {
        path.relative_to(DEVELOPER_CONTEXT).as_posix()
        for path in DEVELOPER_CONTEXT.rglob("*")
        if path.is_file()
    }

    assert context_files == {
        "Dockerfile",
        "requirements.in",
        "requirements.lock",
        "toolchain-manifest.json",
    }
    assert BASE_IMAGE in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "WORKDIR /workspace" in dockerfile
    assert "COPY requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "COPY toolchain-manifest.json" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "nanobot-runtime" not in dockerfile
    assert ":latest" not in dockerfile
    assert "PIP_NO_INDEX" not in dockerfile


def test_developer_image_installs_declared_toolchain_without_privilege_tools():
    dockerfile = (DEVELOPER_CONTEXT / "Dockerfile").read_text()
    required_packages = {
        "bash",
        "ca-certificates",
        "coreutils",
        "curl",
        "diffutils",
        "findutils",
        "g++",
        "gcc",
        "git",
        "grep",
        "gzip",
        "jq",
        "make",
        "nodejs",
        "npm",
        "openssh-client",
        "patch",
        "pkg-config",
        "ripgrep",
        "sed",
        "tar",
        "unzip",
        "wget",
    }
    install_block = dockerfile.split(
        "apt-get install -y --no-install-recommends",
        1,
    )[1].split("&& rm -rf /var/lib/apt/lists/*", 1)[0]

    for package in required_packages:
        assert package in install_block.split()
    assert "sudo" not in install_block.split()
    assert "docker" not in install_block.split()
    assert "pytest==8.3.5" in (
        DEVELOPER_CONTEXT / "requirements.in"
    ).read_text()
    lock = (DEVELOPER_CONTEXT / "requirements.lock").read_text()
    assert "pytest==8.3.5" in lock
    assert "--hash=sha256:" in lock


def test_developer_cache_and_home_environment_are_runtime_scoped():
    dockerfile = (DEVELOPER_CONTEXT / "Dockerfile").read_text()
    expected = {
        "HOME=/runtime/home",
        "XDG_CACHE_HOME=/runtime/cache",
        "PIP_CACHE_DIR=/runtime/pip-cache",
        "UV_CACHE_DIR=/runtime/uv-cache",
        "npm_config_cache=/runtime/npm-cache",
        "PYTHONPYCACHEPREFIX=/runtime/pycache",
        "TMPDIR=/tmp",
    }

    for assignment in expected:
        assert assignment in dockerfile


def test_developer_toolchain_manifest_matches_image_contract():
    manifest = json.loads(
        (DEVELOPER_CONTEXT / "toolchain-manifest.json").read_text()
    )

    assert manifest["schema_version"] == 1
    assert manifest["profile_id"] == "developer"
    assert manifest["base_image"] == BASE_IMAGE
    assert manifest["default_user"] == "10001:10001"
    assert manifest["workdir"] == "/workspace"
    assert set(manifest["forbidden_commands"]) == {"docker", "sudo"}
    assert manifest["writable_roots"] == ["/workspace", "/runtime", "/tmp"]
    assert manifest["readonly_roots"] == ["/inputs"]
    assert manifest["network_contract"] == (
        "docker-network-none-with-loopback"
    )
    assert {"python", "pip", "pytest", "node", "npm", "bash", "git"} <= set(
        manifest["required_commands"]
    )


def test_restricted_and_developer_apparmor_network_contracts_are_separate():
    restricted = (
        ROOT / "deploy/apparmor/nanobot-sandbox-restricted"
    ).read_text()
    developer = (
        ROOT / "deploy/apparmor/nanobot-sandbox-developer"
    ).read_text()

    assert "profile nanobot-sandbox-restricted" in restricted
    assert "deny network," in restricted
    assert "network inet stream," not in restricted
    assert "profile nanobot-sandbox-developer" in developer
    assert "network inet stream," in developer
    assert "network inet6 stream," in developer
    assert "deny network," not in developer
    assert "/usr/share/nodejs/** rix," in developer
    assert "/usr/lib/git-core/** ixr," in developer
    for profile in (restricted, developer):
        assert "deny capability," in profile
        assert "deny mount," in profile
        assert "deny /var/run/docker.sock rw," in profile
        assert "/workspace/** rwklix," in profile
        assert "/runtime/** rwklix," in profile
        assert "/inputs/** rix," in profile


def test_build_script_verifies_developer_isolation_and_loopback():
    script = (ROOT / "scripts/build-sandbox-image.sh").read_text()

    assert 'profile="python"' in script
    assert "nanobot-sandbox-developer" in script
    assert "--network none" in script
    assert "--read-only" in script
    assert "--cap-drop ALL" in script
    assert "--security-opt no-new-privileges" in script
    assert "! command -v docker" in script
    assert "! command -v sudo" in script
    assert "https://1.1.1.1/" in script
    assert "python -m http.server 18080" in script
    assert "http://127.0.0.1:18080/index.html" in script


def test_production_installer_loads_both_apparmor_profiles():
    script = (ROOT / "scripts/manage-sandbox-production.sh").read_text()

    install_body = script.split(
        "install_apparmor_profile() {",
        1,
    )[1].split("\n}", 1)[0]
    assert "nanobot-sandbox-restricted" in install_body
    assert "nanobot-sandbox-developer" in install_body
    assert "apparmor_parser -r" in install_body
    assert (
        "NANOBOT_SANDBOX_APPARMOR_PROFILE="
        "nanobot-sandbox-restricted"
    ) in script
