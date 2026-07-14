import re
from pathlib import Path


APP = Path("webui/src/App.jsx")
PAGE = Path("webui/src/features/session-config/SessionConfigsPage.jsx")


def _page_source() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_admin_navigation_uses_session_strategy_page():
    app = APP.read_text(encoding="utf-8")

    assert (
        "import { SessionConfigsPage } from "
        "'./features/session-config/SessionConfigsPage'"
    ) in app
    assert "{ to: '/configs', label: '会话策略', icon: Gauge }" in app
    assert '<Route path="/configs" element={<SessionConfigsPage />} />' in app
    assert "function ConfigsPage()" not in app
    assert "function ConfigEditModal(" not in app


def test_session_config_page_exposes_identity_filters_and_guidance_actions():
    source = _page_source()

    for marker in (
        "platform",
        "chat_type",
        "configured",
        "session_guidance",
        "4,000",
        "预览草稿",
        "清空专属指导",
        "删除整条覆写",
        "/prompt/effective-preview",
    ):
        assert marker in source

    assert "api.get('/configs', { params })" in source
    assert "effective: viewMode === 'effective' ? 1 : 0" in source
    assert "legacy_aliases" in source
    assert "identity_conflict" in source
    assert "无法规范化" in source


def test_session_config_list_uses_only_guidance_summary():
    source = _page_source()
    list_source = source.split("function SessionConfigResults(", 1)[1].split(
        "function SessionConfigEditor(",
        1,
    )[0]

    assert "session_guidance_configured" in list_source
    assert "session_guidance_chars" in list_source
    assert "session_guidance_sha256" not in list_source
    assert re.search(r"\b(?:item|config)\.session_guidance\b", list_source) is None
    assert (
        "const hasOverride = config.has_override ?? "
        "(viewMode === 'override')"
    ) in list_source


def test_session_guidance_editor_counts_unicode_and_blocks_oversized_drafts():
    source = _page_source()

    assert 'Field id="session-guidance-editor" label="专属指导"' in source
    assert 'id="session-guidance-editor"' in source
    assert "Array.from(guidance).length" in source
    assert "guidanceChars > 4000" in source
    assert "maxLength={4000}" not in source
    assert "guidance.length" not in source
    assert "正文会发送给模型并进入高权限 LLM 请求日志" in source
    assert "禁止保存 Token、密码和隐私" in source


def test_session_guidance_editor_uses_detail_upsert_preview_and_safe_errors():
    source = _page_source()

    assert "api.get(`/configs/${encodeURIComponent(config.chat_stream_id)}`)" in source
    assert "api.put('/configs'," in source
    assert "api.put(`/configs/${encodeURIComponent(config.chat_stream_id)}`, {" in source
    assert "session_guidance: ''" in source
    assert "api.delete(`/configs/${encodeURIComponent(config.chat_stream_id)}`)" in source
    assert "session_guidance_override: guidance" in source
    assert "flow_sections" in source
    assert "section_hashes" in source
    assert "messages" in source
    assert "formatApiError" in source
    assert "alert(" not in source
    assert "saving" in source
    assert "previewing" in source
    assert "detailLoaded" in source
    assert "detailLoadFailed" in source
    assert "重试加载详情" in source
    assert "if (!detailLoaded" in source
    assert "preview.preview_exact === false" in source
    assert "群记忆 reranker 上下文未纳入本次预览" in source


def test_session_guidance_preview_restores_runtime_session_identity():
    source = _page_source()

    assert "const externalSessionId = normalizeExternalSessionId(" in source
    assert "const runtimeSessionId = externalSessionId" in source
    assert "`${form.chat_type}_${externalSessionId}`" in source
    assert "config.runtime_session_id" in source
    assert "form.platform === 'qq'" in source
    assert "session_id: runtimeSessionId" in source
    assert "group_id: form.chat_type === 'group' ? externalSessionId : ''" in source
    assert "user_id: form.chat_type === 'private' ? externalSessionId : ''" in source


def test_clearing_default_guidance_does_not_create_empty_override():
    source = _page_source()

    assert "onClick={() => onEdit({ ...config, has_override: hasOverride })}" in source
    assert "if (creating || !config.has_override)" in source


def test_session_guidance_editor_requires_explicit_identity_for_new_config():
    source = _page_source()

    assert 'Field id="session-config-platform"' in source
    assert 'Field id="session-config-chat-type"' in source
    assert 'Field id="session-config-session-id"' in source
    assert "identityReady" in source
    assert "isEditableIdentity" in source
    assert "创建会话覆写" in source
