"""SettingSpec 来源、生命周期、归属与安全覆盖契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config_registry import SETTING_DEFS, SettingDef, SettingSpec
from core.settings_service import SettingsService
from core.settings_specs import SettingCatalogError, validate_setting_catalog


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    rows = []

    def query(self, _model):
        return _FakeQuery(self.rows)

    def close(self):
        return None


def test_setting_def_is_compatible_alias_and_scope_is_inferred():
    assert SettingDef is SettingSpec
    provider = SETTING_DEFS["model.providers.newapi.base_url"]
    route = SETTING_DEFS["model.route.reply.provider"]

    assert provider.scope == "provider"
    assert route.scope == "route"
    assert provider.owner_module == "core.model_provider"
    assert provider.reloadability == "hot"
    with pytest.raises(TypeError):
        SETTING_DEFS["injected"] = provider


def test_boot_only_runtime_paths_are_registered_for_read_only_deployment():
    data_dir = SETTING_DEFS["runtime.data_dir"]
    temp_dir = SETTING_DEFS["runtime.temp_dir"]

    assert (data_dir.env_name, data_dir.default) == ("NANOBOT_DATA_DIR", "./data")
    assert (temp_dir.env_name, temp_dir.default) == ("NANOBOT_TEMP_DIR", "./tmp")
    assert data_dir.reloadability == temp_dir.reloadability == "boot_only"
    assert data_dir.database_override_allowed is False
    assert temp_dir.database_override_allowed is False


def test_reply_routing_policy_is_registered_in_setting_catalog():
    expected = {
        "model.reply": ("LLM_MODEL_REPLY", "str"),
        "model.reply_intel_floor": ("REPLY_MODEL_INTEL_FLOOR", "int"),
        "model.reply_intel_boost": ("REPLY_MODEL_INTEL_BOOST", "int"),
        "model.reply_max_cost": ("REPLY_MODEL_MAX_COST", "float"),
    }

    for key, (env_name, value_type) in expected.items():
        spec = SETTING_DEFS[key]
        assert spec.env_name == env_name
        assert spec.value_type == value_type
        assert spec.owner_module == "core.model_provider"


def test_production_model_selection_does_not_bypass_setting_specs():
    from scripts.check_architecture import check_model_setting_consumers

    assert check_model_setting_consumers() == []


def test_runtime_paths_resolve_defaults_from_setting_specs(monkeypatch, tmp_path):
    from core.runtime_paths import RuntimePaths

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NANOBOT_DATA_DIR", raising=False)
    monkeypatch.delenv("NANOBOT_TEMP_DIR", raising=False)

    paths = RuntimePaths.from_environment()

    assert paths.data_dir == (tmp_path / SETTING_DEFS["runtime.data_dir"].default)
    assert paths.temp_dir == (tmp_path / SETTING_DEFS["runtime.temp_dir"].default)


def test_database_url_invariant_ignores_database_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-environment.db")
    _FakeSession.rows = [
        SimpleNamespace(key="database.url", value="sqlite:///from-database.db"),
    ]
    service = SettingsService(session_factory=_FakeSession)

    resolved = service.get_resolved("database.url")

    assert resolved.value == "sqlite:///from-environment.db"
    assert resolved.source == "environment"
    assert resolved.origin == "env:DATABASE_URL"
    assert service.explain("database.url")["database_override_allowed"] is False


def test_environment_provenance_contains_origin_but_not_value(monkeypatch):
    monkeypatch.setenv("NANOBOT_TEMP_DIR", "/runtime/secret-looking-path")
    _FakeSession.rows = []
    service = SettingsService(session_factory=_FakeSession)

    provenance = service.environment_provenance()["runtime.temp_dir"]

    assert provenance["origin"] == "env:NANOBOT_TEMP_DIR"
    assert "value" not in provenance
    assert "/runtime/secret-looking-path" not in repr(provenance)


def test_catalog_validation_rejects_mismatched_key_and_invariant_db_source():
    spec = SettingSpec(
        key="security.mode",
        env_name="SECURITY_MODE",
        default="strict",
        value_type="str",
        category="system",
    )
    with pytest.raises(SettingCatalogError, match="不一致"):
        validate_setting_catalog({"security.other": spec})

    with pytest.raises(SettingCatalogError, match="安全不变量"):
        SettingSpec(
            key="security.invariant",
            env_name="SECURITY_INVARIANT",
            default=True,
            value_type="bool",
            category="system",
            safety_class="invariant",
        )


def test_cross_field_validator_falls_back_from_invalid_database_snapshot():
    def validate_range(values):
        if int(values["range.minimum"]) > int(values["range.maximum"]):
            raise ValueError("minimum 不能大于 maximum")

    definitions = {
        "range.minimum": SettingSpec(
            key="range.minimum",
            env_name="",
            default=1,
            value_type="int",
            category="test",
        ),
        "range.maximum": SettingSpec(
            key="range.maximum",
            env_name="",
            default=10,
            value_type="int",
            category="test",
            cross_field_validator=validate_range,
        ),
    }
    _FakeSession.rows = [
        SimpleNamespace(key="range.minimum", value="20"),
        SimpleNamespace(key="range.maximum", value="10"),
    ]
    service = SettingsService(
        session_factory=_FakeSession,
        definitions=definitions,
    )

    assert service.get("range.minimum") == 1
    assert service.get("range.maximum") == 10
    assert service.get_resolved("range.minimum").source == "default"
