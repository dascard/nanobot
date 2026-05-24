from pathlib import Path


APP_JS = Path("webui/src/App.jsx")
UI_JS = Path("webui/src/components/ui.jsx")
CSS = Path("webui/src/index.css")
LLM_LOGS_JS = Path("webui/src/features/agent-runs/LLMApiLogsPage.jsx")
MODELS_JS = Path("webui/src/features/models/ModelsPage.jsx")
PROMPT_JS = Path("webui/src/features/prompt/PromptPages.jsx")
REPLY_EVAL_JS = Path("webui/src/features/reply-eval/ReplyEvalPage.jsx")


def read_app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def read_ui_sources() -> str:
    paths = [APP_JS, LLM_LOGS_JS, MODELS_JS, PROMPT_JS, REPLY_EVAL_JS]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def test_admin_layout_uses_grouped_responsive_navigation():
    source = read_app()

    assert "const NAV_SECTIONS = [" in source
    assert "const NAV = NAV_SECTIONS.flatMap" in source
    assert "aria-label=\"打开导航\"" in source
    assert "md:hidden" in source
    assert "hidden md:flex" in source
    assert "fixed inset-0 z-40 md:hidden" in source
    assert "h-screen bg-slate-950 text-slate-200 flex overflow-hidden" not in source


def test_login_token_input_has_visible_label():
    source = read_app()

    login_source = source.split("function Login(")[1].split("// ── Layout ──")[0]
    assert "htmlFor=\"admin-token\"" in login_source
    assert "id=\"admin-token\"" in login_source
    assert "placeholder=\"API 令牌\"" not in login_source
    assert "bg-gradient-to-br" not in login_source


def test_prompt_flow_has_mobile_structured_fallback():
    source = read_ui_sources()

    assert "function PromptFlowMobileList(" in source
    assert "data-testid=\"prompt-flow-mobile-list\"" in source
    assert "hidden lg:block" in source
    assert "lg:hidden" in source


def test_key_filters_have_labels_or_aria_labels():
    source = read_ui_sources()

    assert "Field id=\"llm-log-run-filter\"" in source
    assert "id=\"llm-log-run-filter\"" in source
    assert "Field id=\"llm-log-status-filter\"" in source
    assert "id=\"model-catalog-query\"" in source
    assert "Field id=\"model-catalog-provider\"" in source


def test_reply_eval_case_editor_controls_are_labelled():
    source = read_ui_sources()

    assert "Field id=\"reply-new-case-id\"" in source
    assert "id=\"reply-new-case-input\"" in source
    assert "Field id=\"reply-edit-title\"" in source
    assert "id=\"reply-edit-context\"" in source
    assert "aria-label=\"选择测试用例\"" in source


def test_sticker_duplicate_actions_do_not_use_emoji_buttons():
    source = read_app()

    assert "RefreshCw" in source
    assert "Tags" in source
    assert "label=\"重试预览\"" in source
    assert "label=\"重试打标\"" in source
    assert ">🔄<" not in source
    assert ">🏷<" not in source


def test_shared_ui_components_support_dense_admin_shell():
    ui_source = UI_JS.read_text(encoding="utf-8")
    css_source = CSS.read_text(encoding="utf-8")

    assert "export function PageHeader(" in ui_source
    assert "export function Field(" in ui_source
    assert "export function IconButton(" in ui_source
    assert "rounded-lg" in ui_source
    assert "@media (prefers-reduced-motion: reduce)" in css_source


def test_webui_scrollbars_use_global_dark_admin_theme():
    css_source = CSS.read_text(encoding="utf-8")

    assert "--scrollbar-track: #0f172a;" in css_source
    assert "--scrollbar-thumb: #475569;" in css_source
    assert "scrollbar-gutter: stable;" in css_source
    assert "*::-webkit-scrollbar" in css_source
    assert "*::-webkit-scrollbar-track" in css_source
    assert "*::-webkit-scrollbar-thumb" in css_source
    assert "*::-webkit-scrollbar-thumb:hover" in css_source
    assert "var(--scrollbar-thumb-hover)" in css_source


def test_memory_page_exposes_group_overview_and_manual_extract():
    source = read_app()
    memory_source = source.split("function MemoryPage()")[1].split("// ── Audit ──")[0]

    assert "api.get('/group-memories/overview'" in memory_source
    assert "api.get(`/group-memories/${encodeURIComponent(target)}/items`" in memory_source
    assert "api.post(`/group-memories/${encodeURIComponent(groupId)}/extract`" in memory_source
    assert "提取记忆" in memory_source
    assert "记忆列表" in memory_source
    assert "windowHours" in memory_source
    assert "injectable_count" in memory_source


def test_memory_page_auto_loads_exact_group_input():
    source = read_app()
    memory_source = source.split("function MemoryPage()")[1].split("// ── Audit ──")[0]

    assert "memoryLoadKeyRef" in memory_source
    assert "exactOverviewGroup" in memory_source
    assert "load(exactOverviewGroup.group_id)" in memory_source
    assert "item.raw_group_id === q" in memory_source


def test_memory_page_exposes_injection_controls_and_preview():
    source = read_app()
    memory_source = source.split("function MemoryPage()")[1].split("// ── Audit ──")[0]

    assert "enableInjection" in memory_source
    assert "previewInjection" in memory_source
    assert "group-memories/${encodeURIComponent(groupId)}/injection-config" in memory_source
    assert "group-memories/${encodeURIComponent(groupId)}/injection-preview" in memory_source
    assert "updateMemory" in memory_source
    assert "api.patch(`/group-memories/items/${memoryId}`" in memory_source
    assert "一键开启注入" in memory_source
    assert "模拟注入" in memory_source
    assert "preview 模式只展示预览结果，不会真实注入 prompt。" in memory_source
    assert "禁用" in memory_source


def test_persona_page_exposes_governance_and_injection_preview():
    source = read_app()

    assert "{ to: '/persona', label: '用户画像'" in source
    assert '<Route path="/persona" element={<PersonaPage />}' in source
    persona_source = source.split("function PersonaPage()")[1].split("// ── Audit ──")[0]

    assert "api.get('/persona/users'" in persona_source
    assert "api.get(`/persona/users/${encodeURIComponent(target)}/facts`" in persona_source
    assert "api.patch(`/persona/facts/${factId}`" in persona_source
    assert "api.post(`/persona/users/${encodeURIComponent(userId)}/injection-preview`" in persona_source
    assert "画像列表" in persona_source
    assert "模拟注入" in persona_source
    assert "inject_policy" in persona_source
    assert "禁用" in persona_source
