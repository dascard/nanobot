from pathlib import Path


APP_JS = Path("webui/src/App.jsx")
FEATURES = {
    "AgentRunsPage": Path("webui/src/features/agent-runs/AgentRunsPage.jsx"),
    "AgentRunDetailPage": Path("webui/src/features/agent-runs/AgentRunDetailPage.jsx"),
    "LLMApiLogsPage": Path("webui/src/features/agent-runs/LLMApiLogsPage.jsx"),
    "ToolCallsPage": Path("webui/src/features/agent-runs/ToolCallsPage.jsx"),
    "PromptV2TemplatesPage": Path("webui/src/features/prompt/PromptPages.jsx"),
    "EffectivePromptPreviewPage": Path("webui/src/features/prompt/PromptPages.jsx"),
    "ReplyEvalPage": Path("webui/src/features/reply-eval/ReplyEvalPage.jsx"),
    "ModelsPage": Path("webui/src/features/models/ModelsPage.jsx"),
    "ToolsPage": Path("webui/src/features/tools/ToolsPage.jsx"),
    "EvalsPage": Path("webui/src/features/evals/EvalsPage.jsx"),
}


def test_high_complexity_pages_are_not_defined_in_app_js():
    source = APP_JS.read_text(encoding="utf-8")

    for component in FEATURES:
        assert f"function {component}(" not in source
        assert f"export function {component}(" not in source


def test_app_js_imports_split_feature_pages():
    source = APP_JS.read_text(encoding="utf-8")

    assert "from './features/agent-runs/AgentRunsPage'" in source
    assert "from './features/agent-runs/AgentRunDetailPage'" in source
    assert "from './features/agent-runs/LLMApiLogsPage'" in source
    assert "from './features/agent-runs/ToolCallsPage'" in source
    assert "from './features/prompt/PromptPages'" in source
    assert "from './features/reply-eval/ReplyEvalPage'" in source
    assert "from './features/models/ModelsPage'" in source
    assert "from './features/tools/ToolsPage'" in source
    assert "from './features/evals/EvalsPage'" in source


def test_app_shell_has_fixed_navigation_and_isolated_scroll_regions():
    source = APP_JS.read_text(encoding="utf-8")
    css = Path("webui/src/index.css").read_text(encoding="utf-8")

    assert "app-shell" in source
    assert "app-sidebar" in source
    assert "app-main-scroll" in source
    assert "md:h-screen" in source
    assert "md:overflow-hidden" in source
    assert "md:overflow-y-auto" in source
    assert "#root" in css
    assert "overflow: hidden;" in css
    assert ".app-main-scroll" in css
    assert "overscroll-behavior: contain;" in css


def test_feature_files_export_pages():
    for component, path in FEATURES.items():
      source = path.read_text(encoding="utf-8")
      assert f"export function {component}(" in source
