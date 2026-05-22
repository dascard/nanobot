from pathlib import Path


APP_JS = Path("webui/src/App.jsx")


def test_prompt_runtime_v2_is_primary_prompt_nav_entry():
    source = APP_JS.read_text(encoding="utf-8")

    assert "{ to: '/prompt-preview', label: 'Prompt Runtime V2' }" in source
    assert "{ to: '/prompt-legacy', label: 'Legacy 回滚' }" in source
    assert "{ to: '/prompt', label: '旧版 Prompt 构建' }" not in source


def test_prompt_preview_defaults_to_v2_and_prompt_path_redirects():
    source = APP_JS.read_text(encoding="utf-8")

    assert "engine: 'v2'" in source
    assert '<Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />' in source
    assert '<Route path="/prompt-legacy" element={<PromptPage />} />' in source


def test_prompt_runtime_v2_page_exposes_template_editor():
    source = APP_JS.read_text(encoding="utf-8")

    assert "V2 模板编辑" in source
    assert "api.get('/prompt-v2/templates')" in source
    assert "api.put(`/prompt-v2/templates/${encodeURIComponent(v2SelectedTemplate)}`" in source
    assert "保存 V2 模板" in source
    assert "运行时模板目录" in source
