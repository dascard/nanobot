from pathlib import Path


def test_legacy_prompt_modules_are_removed_from_live_tree():
    removed = [
        "core/prompt_runtime.py",
        "core/prompt_assembler.py",
        "core/prompt_compiler.py",
        "core/legacy_prompt_runtime.py",
        "scripts/build_nanobot_prompt.py",
        "scripts/migrate_legacy_fragments_to_managed.py",
        "creatures/nanobot/prompt.md",
    ]

    for path in removed:
        assert not Path(path).exists(), f"{path} should be removed after P1-6"


def test_legacy_prompt_asset_directories_are_removed_from_live_tree():
    removed = [
        "prompts.default",
        "prompts.legacy.default",
        "data/prompt_fragments",
        "data/runtime_prompt",
    ]

    for path in removed:
        assert not Path(path).exists(), f"{path} should be removed after P1-6"


def test_nanobot_config_no_longer_requires_legacy_prompt_file():
    config = Path("creatures/nanobot/config.yaml").read_text(encoding="utf-8")

    assert "system_prompt_file: prompt.md" not in config
