import json
from pathlib import Path


def test_prompt_manifest_declares_only_canonical_prompt_engine():
    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["version"] == 2
    assert manifest["active_engine"] == "prompt"
    assert manifest["engines"]["prompt"]["status"] == "active"
    assert manifest["engines"]["prompt"]["default_dir"] == "prompts.v2.default"
    assert manifest["engines"]["prompt"]["runtime_dir"] == "data/prompts_v2"
    assert manifest["compat_aliases"]["v2"] == "prompt"
    assert "v1" not in manifest["engines"]
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "data/prompt_fragments" not in serialized
    assert "data/runtime_prompt" not in serialized


def test_prompt_runtime_config_default_matches_manifest_active_engine():
    from core.config_registry import SETTING_DEFS

    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["active_engine"] == "prompt"
    assert SETTING_DEFS["prompt_runtime.engine"].default == manifest["active_engine"]


def test_prompt_v2_audit_fallback_v1_policy_is_deprecated():
    from core.config_registry import SETTING_DEFS

    setting = SETTING_DEFS["prompt_runtime.v2_audit_failure_policy"]

    assert setting.default == "fail_fast"
    assert "已废弃" in setting.description
    assert "固定 fail_fast" in setting.description
