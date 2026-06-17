import json
from pathlib import Path


def test_prompt_manifest_declares_v2_active_and_v1_rollback_only():
    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["active_engine"] == "v2"
    assert manifest["engines"]["v2"]["status"] == "active"
    assert manifest["engines"]["v2"]["default_dir"] == "prompts.v2.default"
    assert manifest["engines"]["v2"]["runtime_dir"] == "data/prompts_v2"
    assert manifest["engines"]["v1"]["status"] == "rollback_only"


def test_prompt_runtime_config_default_matches_manifest_active_engine():
    from core.config_registry import SETTING_DEFS

    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["active_engine"] == "v2"
    assert SETTING_DEFS["prompt_runtime.engine"].default == manifest["active_engine"]


def test_legacy_prompt_runtime_is_marked_rollback_only():
    import core.legacy_prompt_runtime as runtime

    assert runtime.IS_ROLLBACK_ONLY is True
    assert "rollback" in runtime.ROLLBACK_ONLY_REASON.lower()
