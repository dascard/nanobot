import pytest


EXPECTED_ROUTE_KEYS = {
    "reply",
    "fast",
    "smart",
    "timing_gate",
    "timing_proactive",
    "outreach_extract",
    "outreach_judge",
    "outreach_generate",
    "outreach_quality",
    "news_daily_quality",
    "news_relevance_review",
    "group_analysis_topics",
    "group_analysis_titles",
    "group_analysis_quotes",
    "group_analysis_quality",
    "group_memory_learning",
    "private_decision",
    "classifier_legacy",
    "sticker_describe",
    "session_summary",
    "memory_digest",
}


def test_model_route_registry_is_frozen_and_complete():
    from core.model_provider.route_registry import (
        model_route_registry_snapshot,
    )

    snapshot = model_route_registry_snapshot()

    assert snapshot.namespace == "model_route"
    assert snapshot.generation == 1
    assert len(snapshot.sha256) == 64
    assert set(snapshot.items) == EXPECTED_ROUTE_KEYS
    assert snapshot.ordered_ids.index("reply") < snapshot.ordered_ids.index(
        "timing_proactive"
    )
    assert snapshot.ordered_ids.index("timing_gate") < snapshot.ordered_ids.index(
        "private_decision"
    )


def test_model_route_descriptors_bind_configuration_task_and_policy():
    from core.model_provider import ProviderCapability
    from core.model_provider.route_registry import (
        ModelRouteLifecycle,
        ModelRouteSloStatus,
        require_model_route_descriptor,
    )
    from core.prompt_v2.task_contracts import get_task_contract

    private_route = require_model_route_descriptor("private_decision")
    assert private_route.inherits_from == "timing_gate"
    assert private_route.setting_prefix == "model.route.private_decision"
    assert private_route.model_setting_key == (
        "model.route.private_decision.model"
    )
    assert private_route.task_contract_keys == ("tasks/private_decision",)
    assert private_route.output_contract_id == "private_decision_v2"
    assert private_route.required_provider_capabilities == frozenset(
        {ProviderCapability.CHAT_COMPLETION}
    )
    assert private_route.circuit_breaker_policy_id == (
        "model_failure_tracker.default"
    )
    assert private_route.slo.status is ModelRouteSloStatus.BASELINE_ONLY
    assert private_route.slo.baseline_artifact == (
        "docs/architecture/semantic-task-performance-baseline.json"
    )
    assert get_task_contract(private_route.task_contract_keys[0]) is not None

    session_summary = require_model_route_descriptor("session_summary")
    assert session_summary.model_setting_key == "model.session_summary"
    assert session_summary.model_fallback_setting_key == "model.fast"
    assert session_summary.fallback_route == "fast"
    assert session_summary.fallback_scope == "model_only"
    assert session_summary.task_contract_keys == (
        "tasks/session_summary_system",
        "tasks/session_summary_output",
    )

    retired = require_model_route_descriptor("classifier_legacy")
    assert retired.lifecycle is ModelRouteLifecycle.DEPRECATED


def test_model_route_compatibility_facade_reads_registry_snapshot():
    from core.model_provider.route_registry import model_route_registry_snapshot
    from core.route_metadata import (
        ROUTE_METADATA,
        route_capability_for,
        route_label_for,
        route_type_for,
    )

    snapshot = model_route_registry_snapshot()

    assert set(ROUTE_METADATA) == set(snapshot.items)
    assert ROUTE_METADATA["news_daily_quality"] == {
        "type": "task",
        "label": "AI 日报质量摘要",
    }
    assert route_type_for("sticker_describe") == "vision"
    assert route_label_for("reply") == "主回复模型"
    assert route_capability_for("sticker_describe") == "vision"


def test_unknown_model_route_fails_closed(monkeypatch):
    from clients.classifier_client import resolve_model_route
    from core.model_provider.route_registry import ModelRouteNotFoundError

    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda _key, default=None: default,
    )

    with pytest.raises(ModelRouteNotFoundError):
        resolve_model_route("missing_route")


def test_admin_model_status_exposes_route_registry_provenance(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get(
        "/api/v1/admin/models/status",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    route = payload["routes"]["private_decision"]
    registry = payload["route_registry"]
    assert registry["generation"] == 1
    assert len(registry["sha256"]) == 64
    assert route["owner"] == "core.private_timing"
    assert route["domain"] == "reply_timing"
    assert route["setting_prefix"] == "model.route.private_decision"
    assert route["inherited_from"] == "timing_gate"
    assert route["required_provider_capabilities"] == ["chat_completion"]
    assert route["output_contract_id"] == "private_decision_v2"
    task_contract = route["task_contracts"][0]
    assert task_contract["task_key"] == "tasks/private_decision"
    assert task_contract["owner_module"] == "core.task_runtime"
    assert task_contract["output_contract_id"] == "private_decision_v2"
    assert task_contract["output_failure_policy"] == (
        "single_attempt_normal_agent"
    )
    assert task_contract["source_precedence"] == ["runtime", "default"]
    assert task_contract["output_schema"]["additionalProperties"] is False
    assert route["slo"]["status"] == "baseline_only"


def test_model_route_architecture_gate_rejects_legacy_fact_sources(
    tmp_path,
):
    from scripts.check_architecture import (
        check_model_route_descriptor_consumers,
    )

    valid = tmp_path / "valid.py"
    valid.write_text(
        "from core.model_provider.route_registry import "
        "require_model_route_descriptor\n"
        "route = require_model_route_descriptor('reply')\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.py"
    metadata.write_text(
        "from core.route_metadata import ROUTE_METADATA\n",
        encoding="utf-8",
    )
    duplicated = tmp_path / "duplicated.py"
    duplicated.write_text(
        "_MODEL_SETTING_KEYS = {'reply': 'model.reply'}\n"
        "_REPLY_INHERITED_ROUTE_KEYS = {'news_daily_quality'}\n",
        encoding="utf-8",
    )

    assert check_model_route_descriptor_consumers((valid,)) == []
    errors = check_model_route_descriptor_consumers(
        (metadata, duplicated)
    )
    assert len(errors) == 3
    assert "ROUTE_METADATA" in errors[0]
    assert "_MODEL_SETTING_KEYS" in "\n".join(errors)
    assert "_REPLY_INHERITED_ROUTE_KEYS" in "\n".join(errors)
