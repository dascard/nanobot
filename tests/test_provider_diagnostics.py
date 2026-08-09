from __future__ import annotations

from types import SimpleNamespace

import pytest


def _provider(
    *,
    base_url: str = "https://provider.test/v1",
    enabled: bool = True,
    credential_configured: bool = True,
):
    from core.model_provider.provider_config import provider_descriptor_for_driver

    provider = SimpleNamespace(
        id="provider_a",
        display_name="Provider A",
        driver_type="openai",
        enabled=enabled,
        runtime_available=True,
        runtime_unavailable_reason="",
        credential_configured=credential_configured,
        base_url=base_url,
        api_key="provider-secret" if credential_configured else "",
        descriptor=provider_descriptor_for_driver(
            "openai",
            provider_id="provider_a",
            display_name="Provider A",
        ),
    )
    provider.internal_view = lambda: {
        "driver_type": "openai",
        "base_url": provider.base_url,
        "api_key": provider.api_key,
    }
    return provider


@pytest.mark.parametrize(
    ("error", "status", "expected"),
    [
        (RuntimeError("HTTP 401"), 0, "authentication"),
        (RuntimeError("rate limit"), 0, "rate_limit"),
        (RuntimeError("invalid JSON"), 0, "response_protocol"),
        (RuntimeError("ignored"), 503, "upstream"),
        (TimeoutError(), 0, "timeout"),
    ],
)
def test_provider_error_classification_is_stable(error, status, expected):
    from core.model_provider.diagnostics import classify_provider_error

    assert classify_provider_error(error, http_status=status).value == expected


def test_provider_doctor_stops_after_configuration_failure(monkeypatch):
    from clients.provider_doctor import run_provider_doctor

    monkeypatch.setattr(
        "clients.provider_doctor._probe_dns",
        lambda *_args: pytest.fail("配置失败后不应访问网络"),
    )

    report = run_provider_doctor(_provider(enabled=False))
    payload = report.to_dict()

    assert payload["ok"] is False
    assert payload["blocking_layer"] == "configuration"
    assert payload["checks"][0]["category"] == "configuration"
    assert all(
        item["status"] == "skipped"
        for item in payload["checks"][1:]
    )


def test_provider_doctor_runs_layered_and_explicit_capability_probes(
    monkeypatch,
):
    from clients.provider_doctor import (
        ProviderDoctorOptions,
        run_provider_doctor,
    )

    monkeypatch.setattr("clients.provider_doctor._probe_dns", lambda *_args: 2)
    monkeypatch.setattr("clients.provider_doctor._probe_tcp", lambda *_args: 3)
    monkeypatch.setattr("clients.provider_doctor._probe_tls", lambda *_args: 4)
    monkeypatch.setattr(
        "clients.provider_doctor.discover_provider_models",
        lambda *_args, **_kwargs: ["model-a"],
    )
    probed = []

    def fake_probe(_provider, *, model, kind, timeout):
        probed.append((model, kind.value, timeout))
        return 5, {"usage": {"prompt_tokens": 1}}

    monkeypatch.setattr(
        "clients.provider_doctor._probe_chat_request",
        fake_probe,
    )

    report = run_provider_doctor(
        _provider(),
        ProviderDoctorOptions(
            model="model-a",
            live_completion=True,
            probe_stream=True,
            probe_tools=True,
            probe_image=True,
            timeout_seconds=7,
            model_capabilities=frozenset({
                "supports_stream",
                "supports_tools",
                "supports_image",
            }),
        ),
    )
    checks = {item.layer.value: item for item in report.checks}

    assert report.ok is True
    assert checks["configuration"].status.value == "passed"
    assert checks["dns"].latency_ms == 2
    assert checks["transport"].latency_ms == 3
    assert checks["tls"].latency_ms == 4
    assert checks["catalog"].metadata["model_count"] == 1
    assert checks["completion"].status.value == "passed"
    assert [item[1] for item in probed] == [
        "completion",
        "stream",
        "tool",
        "image",
    ]
    serialized = str(report.to_dict())
    assert "provider-secret" not in serialized
    assert "provider.test" not in serialized


def test_provider_doctor_does_not_probe_undeclared_model_capability(monkeypatch):
    from clients.provider_doctor import (
        ProviderDoctorOptions,
        run_provider_doctor,
    )

    monkeypatch.setattr("clients.provider_doctor._probe_dns", lambda *_args: 1)
    monkeypatch.setattr("clients.provider_doctor._probe_tcp", lambda *_args: 1)
    monkeypatch.setattr("clients.provider_doctor._probe_tls", lambda *_args: 1)
    monkeypatch.setattr(
        "clients.provider_doctor.discover_provider_models",
        lambda *_args, **_kwargs: ["model-a"],
    )
    probed = []

    def fake_probe(_provider, *, model, kind, timeout):
        probed.append(kind.value)
        return 1, {}

    monkeypatch.setattr(
        "clients.provider_doctor._probe_chat_request",
        fake_probe,
    )

    report = run_provider_doctor(
        _provider(),
        ProviderDoctorOptions(
            model="model-a",
            probe_image=True,
            model_capabilities=frozenset({"supports_stream"}),
        ),
    )
    image_check = next(
        item for item in report.checks if item.layer.value == "image"
    )

    assert probed == ["completion"]
    assert image_check.status.value == "unsupported"
    assert image_check.category.value == "capability"


def test_provider_doctor_classifies_catalog_auth_failure(monkeypatch):
    from clients.provider_doctor import (
        ProviderDoctorOptions,
        run_provider_doctor,
    )

    monkeypatch.setattr("clients.provider_doctor._probe_dns", lambda *_args: 1)
    monkeypatch.setattr("clients.provider_doctor._probe_tcp", lambda *_args: 1)
    monkeypatch.setattr("clients.provider_doctor._probe_tls", lambda *_args: 1)
    monkeypatch.setattr(
        "clients.provider_doctor.discover_provider_models",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Provider 返回 HTTP 401")
        ),
    )

    report = run_provider_doctor(
        _provider(),
        ProviderDoctorOptions(live_completion=False),
    )
    checks = {item.layer.value: item for item in report.checks}

    assert report.ok is False
    assert checks["authentication"].category.value == "authentication"
    assert checks["catalog"].status.value == "skipped"
