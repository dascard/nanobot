import json
from copy import deepcopy

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.execution_profiles import (
    ExecutionProfileRegistry,
    load_execution_profile_registry,
)
from core.sandbox.profile_catalog import (
    DEFAULT_PROFILE_MANIFEST_PATH,
    parse_profile_catalog,
)


IMAGE_ID = "sha256:" + "a" * 64


def _raw_catalog():
    return json.loads(DEFAULT_PROFILE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _registry_with_images():
    raw = _raw_catalog()
    for profile in raw["profiles"]:
        if profile["profile_id"] != "trusted_developer":
            profile["image_allowlist"] = [IMAGE_ID]
    return ExecutionProfileRegistry(parse_profile_catalog(raw))


def _controller_state(registry):
    restricted = registry.descriptors["restricted"]
    developer = registry.descriptors["developer"]
    return {
        "catalog_generation": registry.catalog_generation,
        "policy_sha256": registry.policy_sha256,
        "profiles": {
            "restricted": {
                "ready": True,
                "execution_mode": "oneshot",
                "image_id": IMAGE_ID,
                "apparmor_profile": "nanobot-sandbox-restricted",
                "network_policy_id": restricted.network_policy_id,
                "network_proxy_image_id": "",
            },
            "developer": {
                "ready": True,
                "execution_mode": "lease",
                "image_id": IMAGE_ID,
                "apparmor_profile": "nanobot-sandbox-developer",
                "network_policy_id": developer.network_policy_id,
                "network_proxy_image_id": (
                    developer.network_proxy_image_allowlist[0]
                ),
            },
        },
    }


def test_registry_is_a_read_only_projection_of_canonical_catalog():
    registry = load_execution_profile_registry()

    assert set(registry.descriptors) == {
        "restricted",
        "developer",
        "trusted_developer",
    }
    developer = registry.descriptor("developer")
    assert developer.max_timeout_seconds == 1800
    assert developer.memory_bytes >= 2 * 1024 * 1024 * 1024
    assert developer.allow_long_running_processes is True
    assert developer.allow_detached_processes is False
    assert developer.network_policy_id == "developer_allowlist_v1"
    assert "github.com" in developer.network_allowlist
    with pytest.raises(TypeError):
        registry.descriptors["developer"] = developer


def test_registry_generation_and_complete_policy_sha_are_stable():
    first = load_execution_profile_registry()
    second = load_execution_profile_registry()

    assert first.catalog_generation == second.catalog_generation
    assert first.policy_sha256 == second.policy_sha256

    raw = _raw_catalog()
    raw["profiles"][1]["runtime_quota_bytes"] += 1
    drifted = ExecutionProfileRegistry(parse_profile_catalog(raw))
    assert drifted.policy_sha256 != first.policy_sha256

    raw = _raw_catalog()
    raw["profiles"][1]["network_allowlist"].append("example.com")
    network_drifted = ExecutionProfileRegistry(parse_profile_catalog(raw))
    assert network_drifted.policy_sha256 != first.policy_sha256


def test_controller_profile_requires_generation_full_sha_readiness_and_image():
    registry = _registry_with_images()
    state = _controller_state(registry)

    resolved = registry.require_controller_profile(state, "developer")

    assert resolved.profile_id == "developer"
    assert resolved.image_id == IMAGE_ID

    for field in ("catalog_generation", "policy_sha256"):
        drifted = deepcopy(state)
        drifted[field] = "0" * 64
        with pytest.raises(SandboxServiceError) as error:
            registry.require_controller_profile(drifted, "developer")
        assert error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE

    not_ready = deepcopy(state)
    not_ready["profiles"]["developer"]["ready"] = False
    with pytest.raises(SandboxServiceError, match="尚未就绪"):
        registry.require_controller_profile(not_ready, "developer")

    wrong_image = deepcopy(state)
    wrong_image["profiles"]["developer"]["image_id"] = "sha256:" + "b" * 64
    with pytest.raises(SandboxServiceError, match="运行时状态"):
        registry.require_controller_profile(wrong_image, "developer")


def test_same_image_digest_cannot_hide_other_policy_drift():
    expected = _registry_with_images()
    raw = _raw_catalog()
    for profile in raw["profiles"]:
        if profile["profile_id"] != "trusted_developer":
            profile["image_allowlist"] = [IMAGE_ID]
    raw["profiles"][1]["pids_limit"] += 1
    drifted = ExecutionProfileRegistry(parse_profile_catalog(raw))
    state = _controller_state(drifted)

    assert state["profiles"]["developer"]["image_id"] == IMAGE_ID
    with pytest.raises(SandboxServiceError, match="完整 Profile 策略"):
        expected.require_controller_profile(state, "developer")


def test_trusted_profile_and_unknown_profile_are_not_grantable():
    registry = load_execution_profile_registry()

    with pytest.raises(SandboxServiceError) as trusted:
        registry.descriptor("trusted_developer")
    assert trusted.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    with pytest.raises(SandboxServiceError) as unknown:
        registry.descriptor("model_supplied")
    assert unknown.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
