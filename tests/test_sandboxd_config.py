from pathlib import Path

import pytest

from sandboxd.config import SandboxdConfig


IMAGE_ID = "sha256:" + "a" * 64


def _config(tmp_path, **overrides):
    values = {
        "data_root": tmp_path / "data",
        "socket_path": tmp_path / "run" / "sandboxd.sock",
        "token_file": tmp_path / "sandboxd.token",
        "client_token_path": tmp_path / "run" / "client.token",
        "image_reference": "nanobot-sandbox-python:test",
        "image_allowlist": (IMAGE_ID,),
    }
    values.update(overrides)
    return SandboxdConfig(**values)


def test_sandboxd_config_requires_digest_allowlist_and_non_latest_image(tmp_path):
    assert _config(tmp_path).validated().image_allowlist == (IMAGE_ID,)

    with pytest.raises(ValueError):
        _config(tmp_path, image_reference="nanobot-sandbox-python:latest").validated()
    with pytest.raises(ValueError):
        _config(tmp_path, image_allowlist=("nanobot-sandbox-python:test",)).validated()
    with pytest.raises(ValueError):
        _config(tmp_path, apparmor_profile="unconfined").validated()
    with pytest.raises(ValueError):
        _config(
            tmp_path,
            admin_token_file=tmp_path / "sandboxd.token",
        ).validated()
    with pytest.raises(ValueError):
        _config(
            tmp_path,
            admin_client_token_path=tmp_path / "run" / "client.token",
        ).validated()


def test_systemd_unit_limits_surface_and_does_not_start_tcp_listener():
    unit = Path("deploy/systemd/nanobot-sandboxd.service").read_text()

    assert "Requires=docker.service" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "PrivateNetwork=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/srv/nanobot /run/nanobot-sandboxd" in unit
    assert (
        "ConditionPathExists="
        "/etc/nanobot/sandbox-execution-profiles.v1.json"
    ) in unit
    assert (
        "ExecStartPre=/usr/bin/install -m 0640 -o root "
        "-g nanobot-sandboxd "
        "/etc/nanobot/sandbox-execution-profiles.v1.json "
        "/run/nanobot-sandboxd/profile-manifest.json"
    ) in unit
    assert "RuntimeDirectoryPreserve=restart" in unit
    assert "--host" not in unit
    assert "--port" not in unit


def test_compose_only_mounts_sandboxd_socket_into_server():
    compose = Path("docker-compose.yml").read_text()
    server, workers = compose.split("  session-summary-worker:", 1)

    assert "/run/nanobot-sandboxd:/run/nanobot-sandboxd:ro" in server
    assert "/var/run/docker.sock" not in compose
    assert "/srv/nanobot" not in compose
    assert "/run/nanobot-sandboxd" not in workers


def test_sandbox_apparmor_profiles_separate_restricted_and_loopback_network():
    restricted = Path(
        "deploy/apparmor/nanobot-sandbox-restricted"
    ).read_text()
    developer = Path(
        "deploy/apparmor/nanobot-sandbox-developer"
    ).read_text()

    assert "profile nanobot-sandbox-restricted" in restricted
    assert "/usr/local/lib/libpython3.11.so.1.0 mr," in restricted
    assert "/usr/local/lib/python3.11/ r," in restricted
    assert "deny network," in restricted
    assert "network inet stream," not in restricted

    assert "profile nanobot-sandbox-developer" in developer
    assert "network inet stream," in developer
    assert "network inet6 stream," in developer
    assert "deny network," not in developer
    assert "deny /var/run/docker.sock rw," in developer
