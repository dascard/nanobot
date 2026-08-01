"""阶段 8：静态 Web Feature Manifest 与工作台边界合同。"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "webui" / "src" / "features" / "manifest.jsx"
VALIDATION = (
    ROOT / "webui" / "src" / "features" / "manifestValidation.js"
)
APP = ROOT / "webui" / "src" / "App.jsx"
GROUP_PAGE = (
    ROOT
    / "webui"
    / "src"
    / "features"
    / "group-learning"
    / "GroupLearningPage.jsx"
)
GROUP_PANELS = (
    ROOT
    / "webui"
    / "src"
    / "features"
    / "group-learning"
    / "GroupLearningPanels.jsx"
)
RUNTIME_PAGE = (
    ROOT
    / "webui"
    / "src"
    / "features"
    / "runtime"
    / "RuntimeDiagnosticsPage.jsx"
)


def test_static_manifest_owns_first_feature_routes_and_metadata():
    source = MANIFEST.read_text(encoding="utf-8")

    for feature_id in (
        "prompt.runtime.preview",
        "prompt.runtime.templates",
        "model.routes",
        "tool.management",
        "sandbox.management",
        "sandbox.filesystem",
        "trigger.management",
        "group.learning",
        "runtime.module-diagnostics",
    ):
        assert f"featureId: '{feature_id}'" in source
    for field in (
        "featureId:",
        "route:",
        "navGroup:",
        "label:",
        "icon:",
        "component:",
        "requiredCapability:",
        "lifecycle:",
        "backendOperationIds:",
        "requiredRegistryGeneration:",
        "featureFlag:",
        "owner:",
        "order:",
    ):
        assert field in source
    assert "freezeWebFeatureManifest(" in source
    assert "WEB_FEATURE_ROUTES" in source
    assert "composeNavigationSections" in source


def test_manifest_only_uses_literal_local_lazy_imports():
    source = MANIFEST.read_text(encoding="utf-8")
    imports = re.findall(r"\bimport\(([^)]+)\)", source)

    assert imports
    assert all(
        value.strip().startswith(("'", '"'))
        for value in imports
    )
    assert "http://" not in source
    assert "https://" not in source
    assert "remoteEntry" not in source
    assert "registerFeature" not in source


def test_manifest_validation_fails_closed_on_all_required_conflicts():
    source = VALIDATION.read_text(encoding="utf-8")
    check_script = (
        ROOT
        / "webui"
        / "scripts"
        / "check-feature-manifest.mjs"
    ).read_text(encoding="utf-8")
    package = (
        ROOT / "webui" / "package.json"
    ).read_text(encoding="utf-8")

    assert "重复 Web Feature ID" in source
    assert "重复 Web Feature route" in source
    assert "重复 Web Feature nav order" in source
    assert "Web Navigation route 冲突" in MANIFEST.read_text(
        encoding="utf-8"
    )
    assert "Web Navigation order 冲突" in MANIFEST.read_text(
        encoding="utf-8"
    )
    assert "包含未知字段" in source
    assert "remoteEntry" in check_script
    assert "check:feature-manifest" in package
    assert "npm run check:feature-manifest && vite build" in package


def test_app_uses_composition_root_and_no_longer_embeds_group_learning():
    source = APP.read_text(encoding="utf-8")

    assert "composeNavigationSections(BASE_NAV_SECTIONS)" in source
    assert "WEB_FEATURE_ROUTES.map" in source
    assert "<React.Suspense" in source
    assert "function MemoryPage()" not in source
    assert "/group-memories" not in source
    assert '<Route path="/memory"' not in source
    assert '<Route path="/session-summaries/recent"' in source
    assert '<Route path="/session-summaries/long"' in source


def test_group_learning_web_uses_backend_descriptors_as_fact_source():
    source = (
        GROUP_PAGE.read_text(encoding="utf-8")
        + GROUP_PANELS.read_text(encoding="utf-8")
    )

    assert "getGroupLearningDescriptors" in source
    assert "descriptors?.aspects" in source
    assert "descriptors?.candidate_types" in source
    assert "descriptors?.candidate_statuses" in source
    assert "descriptors?.candidate_sources" in source
    assert "descriptors?.schedule_policy" in source
    assert "memory.meta_json" not in source
    assert "raw model output" not in source.lower()
    for hardcoded_aspect in (
        "'topics'",
        "'expressions'",
        "'slang'",
        "'style'",
        "'titles'",
        "'quotes'",
        "'quality'",
    ):
        assert hardcoded_aspect not in source


def test_group_learning_and_runtime_pages_only_use_generated_clients():
    group_source = (
        GROUP_PAGE.read_text(encoding="utf-8")
        + GROUP_PANELS.read_text(encoding="utf-8")
    )
    runtime_source = RUNTIME_PAGE.read_text(encoding="utf-8")

    assert "api/generated/adminClient" in group_source
    assert "api/generated/adminClient" in runtime_source
    assert "api.get(" not in group_source
    assert "api.post(" not in group_source
    assert "api.put(" not in group_source
    assert "api.get(" not in runtime_source
