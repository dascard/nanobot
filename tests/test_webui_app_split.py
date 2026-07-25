from pathlib import Path


APP_JS = Path("webui/src/App.jsx")
MANIFEST_JS = Path("webui/src/features/manifest.jsx")
WEB_SEARCH_API_JS = Path("webui/src/features/web-search/api.js")
FEATURES = {
    "AgentRunsPage": Path("webui/src/features/agent-runs/AgentRunsPage.jsx"),
    "AgentRunDetailPage": Path("webui/src/features/agent-runs/AgentRunDetailPage.jsx"),
    "LLMApiLogsPage": Path("webui/src/features/agent-runs/LLMApiLogsPage.jsx"),
    "ToolCallsPage": Path("webui/src/features/agent-runs/ToolCallsPage.jsx"),
    "PromptV2TemplatesPage": Path("webui/src/features/prompt/PromptPages.jsx"),
    "EffectivePromptPreviewPage": Path("webui/src/features/prompt/PromptPages.jsx"),
    "ReplyEvalPage": Path("webui/src/features/reply-eval/ReplyEvalPage.jsx"),
    "ModelsPage": Path("webui/src/features/models/ModelsPage.jsx"),
    "WebSearchPage": Path("webui/src/features/web-search/WebSearchPage.jsx"),
    "ToolsPage": Path("webui/src/features/tools/ToolsPage.jsx"),
    "EvalsPage": Path("webui/src/features/evals/EvalsPage.jsx"),
    "GeneratedImagesPage": Path("webui/src/features/generated-images/GeneratedImagesPage.jsx"),
    "ProactiveOutreachPage": Path("webui/src/features/proactive-outreach/ProactiveOutreachPage.jsx"),
}


def test_high_complexity_pages_are_not_defined_in_app_js():
    source = APP_JS.read_text(encoding="utf-8")

    for component in FEATURES:
        assert f"function {component}(" not in source
        assert f"export function {component}(" not in source


def test_app_js_imports_split_feature_pages():
    app_source = APP_JS.read_text(encoding="utf-8")
    manifest_source = MANIFEST_JS.read_text(encoding="utf-8")

    assert "from './features/agent-runs/AgentRunsPage'" in app_source
    assert "from './features/agent-runs/AgentRunDetailPage'" in app_source
    assert "from './features/agent-runs/LLMApiLogsPage'" in app_source
    assert "from './features/agent-runs/ToolCallsPage'" in app_source
    assert "from './features/reply-eval/ReplyEvalPage'" in app_source
    assert "from './features/web-search/WebSearchPage'" in app_source
    assert "from './features/evals/EvalsPage'" in app_source
    assert "from './features/generated-images/GeneratedImagesPage'" in app_source
    assert "from './features/proactive-outreach/ProactiveOutreachPage'" in app_source

    assert "import('./prompt/PromptPages')" in manifest_source
    assert "module.PromptV2TemplatesPage" in manifest_source
    assert "module.EffectivePromptPreviewPage" in manifest_source
    assert "import('./models/ModelsPage')" in manifest_source
    assert "module.ModelsPage" in manifest_source
    assert "import('./tools/ToolsPage')" in manifest_source
    assert "module.ToolsPage" in manifest_source


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


def test_generated_images_page_is_wired_for_gallery():
    app_source = APP_JS.read_text(encoding="utf-8")
    page_source = FEATURES["GeneratedImagesPage"].read_text(encoding="utf-8")

    assert "to: '/generated-images'" in app_source
    assert 'path="/generated-images"' in app_source
    assert "生成图片" in app_source
    assert "api.get('/generated-images'" in page_source
    assert "api.post('/generated-images'" in page_source
    assert "AuthImage" in page_source
    assert "完整提示词" in page_source
    assert "测试生图" in page_source
    assert "generated-image-prompt" in page_source
    assert "最近结果" in page_source


def test_web_search_page_is_wired_into_admin_app():
    source = APP_JS.read_text(encoding="utf-8")
    page_source = FEATURES["WebSearchPage"].read_text(encoding="utf-8")
    api_source = WEB_SEARCH_API_JS.read_text(encoding="utf-8")

    assert "to: '/web-search'" in source
    assert 'path="/web-search"' in source
    assert "搜索 API" in source
    assert "export function WebSearchPage(" in page_source
    assert "api.get('/web-search/providers'" in api_source
    assert "api.put(`/web-search/providers/${encodeURIComponent(providerId)}`" in api_source
    assert "api.post(`/web-search/providers/${encodeURIComponent(providerId)}/test`" in api_source
    assert "api.post('/web-search/preview'" in api_source


def test_web_search_page_exposes_preview_search_and_model_message():
    page_source = FEATURES["WebSearchPage"].read_text(encoding="utf-8")

    assert "previewWebSearch" in page_source
    assert "web-search-preview-query" in page_source
    assert "发送给模型的消息" in page_source
    assert "result.results || []" in page_source
    assert "搜索质量" in page_source
    assert "Provider 尝试链" in page_source
    assert "attempted_providers" in page_source


def test_web_search_page_displays_provider_usage_counts():
    page_source = FEATURES["WebSearchPage"].read_text(encoding="utf-8")

    assert "调用次数" in page_source
    assert "success_calls" in page_source
    assert "failure_calls" in page_source
    assert "优先级" in page_source
    assert "priority" in page_source


def test_web_search_page_no_longer_shows_not_tested_copy():
    page_source = FEATURES["WebSearchPage"].read_text(encoding="utf-8")

    assert "暂不测试" not in page_source
    assert "暂不支持连接测试" not in page_source


def test_proactive_outreach_page_is_wired_into_admin_app():
    source = APP_JS.read_text(encoding="utf-8")
    page_source = FEATURES["ProactiveOutreachPage"].read_text(encoding="utf-8")

    assert "to: '/proactive-outreach'" in source
    assert 'path="/proactive-outreach"' in source
    assert "主动外呼" in source
    assert "export function ProactiveOutreachPage(" in page_source
    assert "api.get('/proactive-outreach/status'" in page_source
    assert "api.get('/proactive-outreach/logs'" in page_source
    assert "api.put(`/proactive-outreach/settings/${encodeURIComponent(key)}`" in page_source
    assert "api.post('/proactive-outreach/run-once'" in page_source
    assert "业务记录" in page_source
    assert "运行日志" in page_source
    assert "LLM 请求" in page_source
    assert "proactive_outreach.enabled" in page_source
    assert "proactive_outreach.ambiguous_hold_min" in page_source
    assert "投递不确定冻结" in page_source
    assert "proactive_outreach.repeat_topic_cooldown_min" in page_source
    assert "proactive_outreach.allow_early_surge" in page_source
    assert "bot.super_user_ids" not in page_source
    assert "super_user_ids" not in page_source
    assert "super_user_count" in page_source
    assert "target_fingerprint: logTargetFingerprint.trim()" in page_source
    assert "user_id: logUser.trim()" not in page_source
