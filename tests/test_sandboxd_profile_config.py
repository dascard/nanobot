import json
from copy import deepcopy

import pytest

from core.sandbox.profile_catalog import (
    DEFAULT_PROFILE_MANIFEST_PATH,
    ProfileCatalogError,
    load_profile_catalog,
    parse_profile_catalog,
)
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import LocalDockerBackend


IMAGE_ID = "sha256:" + "a" * 64
PROXY_IMAGE_ID = (
    "sha256:05a5bbc3966ef22b15a0fa708722cf5a"
    "d730e8266473ab35ecfc932fb1b4e2ed"
)


def _raw_catalog():
    return json.loads(DEFAULT_PROFILE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_catalog(tmp_path, raw):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _configured_catalog(tmp_path):
    raw = _raw_catalog()
    for profile in raw["profiles"]:
        if profile["profile_id"] != "trusted_developer":
            profile["image_allowlist"] = [IMAGE_ID]
    return _write_catalog(tmp_path, raw)


def test_canonical_catalog_has_three_complete_profiles_and_fixed_timeouts():
    catalog = load_profile_catalog()

    assert set(catalog.by_id) == {
        "restricted",
        "developer",
        "trusted_developer",
    }
    assert catalog.profile("restricted").max_timeout_seconds == 120
    assert catalog.profile("developer").max_timeout_seconds == 1800
    assert catalog.profile("developer").runtime_quota_bytes > 512 * 1024 * 1024
    trusted = catalog.profile("trusted_developer")
    assert trusted.grantable is False
    assert trusted.image_configured is False
    assert trusted.network_policy_id == "trusted_not_ready"
    assert len(catalog.policy_sha256) == 64


def test_trusted_profile_cannot_be_enabled_by_manifest_only():
    raw = _raw_catalog()
    trusted = next(
        profile
        for profile in raw["profiles"]
        if profile["profile_id"] == "trusted_developer"
    )
    trusted["grantable"] = True
    trusted["image_allowlist"] = [IMAGE_ID]
    trusted["network_policy_id"] = "none"

    with pytest.raises(
        ProfileCatalogError,
        match="trusted_developer 必须保持不可授权占位",
    ):
        parse_profile_catalog(raw)


def test_policy_sha_uses_normalized_complete_policy_not_json_key_order():
    raw = _raw_catalog()
    reordered = {
        "profiles": list(reversed(raw["profiles"])),
        "catalog_generation": raw["catalog_generation"],
        "catalog_version": raw["catalog_version"],
    }

    assert (
        parse_profile_catalog(raw).policy_sha256
        == parse_profile_catalog(reordered).policy_sha256
    )

    drifted = deepcopy(raw)
    drifted["profiles"][1]["pids_limit"] += 1
    assert (
        parse_profile_catalog(raw).policy_sha256
        != parse_profile_catalog(drifted).policy_sha256
    )


def test_ipv4_mapped_ipv6_cidr_uses_version_independent_canonical_text():
    raw = _raw_catalog()

    catalog = parse_profile_catalog(raw)

    assert "::ffff:a00:0/104" in catalog.profile(
        "developer"
    ).network_denied_cidrs

    drifted = deepcopy(raw)
    developer = next(
        profile
        for profile in drifted["profiles"]
        if profile["profile_id"] == "developer"
    )
    developer["network_denied_cidrs"] = [
        "::ffff:10.0.0.0/104"
        if cidr == "::ffff:a00:0/104"
        else cidr
        for cidr in developer["network_denied_cidrs"]
    ]
    with pytest.raises(ProfileCatalogError, match="必须使用规范 CIDR"):
        parse_profile_catalog(drifted)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.update({"unknown": True}),
        lambda raw: raw["profiles"][0].update({"unknown": True}),
        lambda raw: raw["profiles"].append(deepcopy(raw["profiles"][0])),
        lambda raw: raw["profiles"][0].update(
            {"image_allowlist": ["nanobot-sandbox:tag"]}
        ),
        lambda raw: raw["profiles"][1].update(
            {"max_timeout_seconds": 3601}
        ),
        lambda raw: raw["profiles"][1].update(
            {"allow_detached_processes": True}
        ),
    ],
)
def test_catalog_rejects_unknown_duplicate_or_unsafe_policy(mutation):
    raw = _raw_catalog()
    mutation(raw)

    with pytest.raises(ProfileCatalogError):
        parse_profile_catalog(raw)


def test_catalog_loader_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        '{"catalog_version":1,"catalog_version":1,'
        '"catalog_generation":"x","profiles":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ProfileCatalogError, match="重复字段"):
        load_profile_catalog(path)


def test_sandboxd_config_uses_one_manifest_file_without_profile_env_overrides(
    tmp_path,
    monkeypatch,
):
    manifest = _configured_catalog(tmp_path)
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        str(manifest),
    )
    monkeypatch.delenv("NANOBOT_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("NANOBOT_SANDBOX_IMAGE_ALLOWLIST", raising=False)

    config = SandboxdConfig.from_env()

    assert config.profile_catalog is not None
    assert config.profile("developer").max_timeout_seconds == 1800
    assert config.profile_manifest_path == manifest


class _Image:
    id = IMAGE_ID
    attrs = {"Config": {"User": "10001:10001"}}


class _ProxyImage:
    id = PROXY_IMAGE_ID
    attrs = {
        "Config": {
            "User": "13:13",
            "Entrypoint": ["/usr/sbin/squid"],
            "ExposedPorts": {"3128/tcp": {}},
            "Labels": {
                "com.nanobot.egress-policy-id": "developer_allowlist_v1",
            },
        },
    }


class _Images:
    def get(self, reference):
        if reference == "nanobot-sandbox-egress-proxy:2026.07.25":
            return _ProxyImage()
        return _Image()


class _DockerClient:
    images = _Images()

    def ping(self):
        return True

    def info(self):
        return {"SecurityOptions": ["name=apparmor"]}


class _ReadyQuotaManager:
    def capability(self):
        return {
            "project_quota": True,
            "workspace_scope": True,
            "runtime_scope": True,
        }


def test_ready_returns_per_profile_state_without_unready_profile_blocking(
    tmp_path,
    monkeypatch,
):
    from sandboxd import docker_backend as docker_backend_module

    profiles = tmp_path / "apparmor-profiles"
    profiles.write_text(
        "nanobot-sandbox-restricted (enforce)\n"
        "nanobot-sandbox-developer (enforce)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        docker_backend_module,
        "APPARMOR_PROFILES_PATH",
        profiles,
    )
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "run" / "client.token",
        profile_manifest_path=_configured_catalog(tmp_path),
        developer_network_allowed=True,
        disk_min_free_bytes=0,
    ).validated()
    backend = LocalDockerBackend(
        config,
        docker_client=_DockerClient(),
        quota_manager=_ReadyQuotaManager(),
    )
    backend.workspace_files.layout.ensure_roots()

    ready = backend.ready()

    assert ready["catalog_generation"] == "20260725.2"
    assert ready["policy_sha256"] == config.profile_catalog.policy_sha256
    assert ready["profiles"]["restricted"]["ready"] is True
    assert ready["profiles"]["developer"]["ready"] is True
    assert (
        ready["profiles"]["developer"]["network_proxy_image_id"]
        == PROXY_IMAGE_ID
    )
    assert ready["profiles"]["trusted_developer"]["ready"] is False
    assert (
        ready["profiles"]["trusted_developer"]["error_code"]
        == "profile_disabled"
    )


def test_oneshot_image_prefers_manifest_profile_and_falls_back_to_legacy(
    tmp_path,
):
    def _spy_allowed_image(backend, calls):
        original = backend._allowed_image

        def wrapper(profile_id=None):
            calls.append(profile_id)
            return original(profile_id)

        backend._allowed_image = wrapper

    manifest_only = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "run" / "client.token",
        profile_manifest_path=_configured_catalog(tmp_path),
        disk_min_free_bytes=0,
    ).validated()
    assert manifest_only.image_reference == ""
    manifest_backend = LocalDockerBackend(
        manifest_only,
        docker_client=_DockerClient(),
    )
    manifest_calls: list = []
    _spy_allowed_image(manifest_backend, manifest_calls)
    assert manifest_backend._oneshot_image().id == IMAGE_ID
    assert manifest_calls == ["restricted"]

    legacy_only = SandboxdConfig(
        data_root=tmp_path / "data-legacy",
        socket_path=tmp_path / "run" / "sandboxd-legacy.sock",
        token_file=tmp_path / "sandboxd-legacy.token",
        client_token_path=tmp_path / "run" / "client-legacy.token",
        image_reference="nanobot-sandbox-python:test",
        image_allowlist=(IMAGE_ID,),
        disk_min_free_bytes=0,
    ).validated()
    assert legacy_only.profile("restricted").image_configured is False
    legacy_backend = LocalDockerBackend(
        legacy_only,
        docker_client=_DockerClient(),
    )
    legacy_calls: list = []
    _spy_allowed_image(legacy_backend, legacy_calls)
    assert legacy_backend._oneshot_image().id == IMAGE_ID
    assert legacy_calls == [None]


def test_missing_profile_digest_is_reported_not_ready_without_startup_failure(
    tmp_path,
):
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "run" / "client.token",
        disk_min_free_bytes=0,
    ).validated()
    backend = LocalDockerBackend(config, docker_client=_DockerClient())
    backend.workspace_files.layout.ensure_roots()

    assert config.profile("developer").image_configured is False
    ready = backend.ready()
    assert ready["profiles"]["developer"] == {
        "profile_id": "developer",
        "execution_mode": "lease",
        "grantable": True,
        "ready": False,
            "image_id": "",
            "apparmor_profile": "nanobot-sandbox-developer",
            "network_policy_id": "developer_allowlist_v1",
            "network_proxy_image_id": "",
            "error_code": "image_digest_missing",
        }
