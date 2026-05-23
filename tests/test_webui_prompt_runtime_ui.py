from pathlib import Path


APP_JS = Path("webui/src/App.jsx")


def test_prompt_runtime_v2_is_primary_prompt_nav_entry():
    source = APP_JS.read_text(encoding="utf-8")

    assert "{ to: '/prompt-preview', label: 'V2 运行预览' }" in source
    assert "{ to: '/prompt-v2-templates', label: 'V2 模板' }" in source
    assert "{ to: '/prompts', label: 'V1 模板 / 对比' }" not in source
    assert "{ to: '/prompt-legacy', label: 'Legacy 回滚' }" not in source
    assert "{ to: '/prompt', label: '旧版 Prompt 构建' }" not in source


def test_prompt_preview_defaults_to_v2_and_prompt_path_redirects():
    source = APP_JS.read_text(encoding="utf-8")

    assert "engine: 'v2'" in source
    assert '<Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />' in source
    assert '<Route path="/prompt-legacy" element={<PromptPage />} />' in source
    assert '<Route path="/prompt-v2-templates" element={<PromptV2TemplatesPage />} />' in source


def test_prompt_runtime_v2_page_exposes_template_editor():
    source = APP_JS.read_text(encoding="utf-8")
    preview_source = source.split("function EffectivePromptPreviewPage()")[1]

    assert "function PromptV2TemplatesPage()" in source
    assert "function PromptFlowCanvas(" in source
    assert "Prompt V2 模板" in source
    assert "Canvas 编排" in source
    assert "data-testid=\"prompt-flow-canvas\"" in source
    assert "data-testid=\"prompt-flow-viewport\"" in source
    assert "data-testid=\"prompt-flow-edge-layer\"" in source
    assert "prompt-flow-scrollbar" in source
    assert "const [canvasViewport, setCanvasViewport]" in source
    assert "handleCanvasWheel" in source
    assert "startPanCanvas" in source
    assert "const pan = panRef.current" in source
    assert "panRef.current.originX" not in source
    assert "startConnection" in source
    assert "completeConnection" in source
    assert "selectedEdgeKey" in source
    assert "deleteEdge" in source
    assert "删除连线" in source
    assert "连接端口" in source
    assert "onMouseDown={e => startDragNode" in source
    assert "开始连线" not in source
    assert "连到这里" not in source
    assert "缩小" in source
    assert "放大" in source
    assert "重置视图" in source
    assert "全局可插入变量白名单" in source
    assert "node.label || node.id" in source
    assert "添加节点" in source
    assert "添加节点后在右侧选择模板" in source
    assert "templateToAdd" not in source
    assert "删除节点" in source
    assert "当前节点" in source
    assert "节点模板切换" in source
    assert "当前节点使用的模板" in source
    assert "工具模板" in source
    assert "按工具拆分" in source
    assert "当前工具使用的模板" in source
    assert "当前工具使用的模板</div>" in source
    assert "onChange={e => setSelectedToolTemplateKey(e.target.value)}" not in source
    assert "promptV2TemplateKind" in source
    assert "promptV2ToolName" in source
    assert "selectedToolTemplateKey" in source
    assert ">runtime_key<" not in source
    assert "运行时数据" in source
    assert "api.get('/prompt-v2/templates')" in source
    assert "api.get('/prompt-v2/flow')" in source
    assert "api.put(`/prompt-v2/templates/${encodeURIComponent(activeTemplateKey)}`" in source
    assert "api.put('/prompt-v2/flow'" in source
    assert "保存 V2 模板" in source
    assert "保存编排图" in source
    assert "运行时模板目录" in source
    assert "V2 模板编辑" not in preview_source
