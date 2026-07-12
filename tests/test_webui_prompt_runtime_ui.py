from pathlib import Path


APP_JS = Path("webui/src/App.jsx")
PROMPT_JS = Path("webui/src/features/prompt/PromptPages.jsx")
CSS = Path("webui/src/index.css")
DIST = Path("webui/dist")


def read_prompt_sources() -> str:
    return APP_JS.read_text(encoding="utf-8") + "\n" + PROMPT_JS.read_text(encoding="utf-8")


def read_dist_sources() -> str:
    parts = [DIST.joinpath("index.html").read_text(encoding="utf-8")]
    for path in sorted(DIST.joinpath("assets").glob("*.js")):
        parts.append(path.read_text(encoding="utf-8"))
    for path in sorted(DIST.joinpath("assets").glob("*.css")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_prompt_runtime_is_primary_prompt_nav_entry():
    source = APP_JS.read_text(encoding="utf-8")

    assert "{ to: '/prompt-preview', label: '运行预览' }" in source
    assert "{ to: '/prompt-templates', label: '模板' }" in source
    assert "label: 'V2 运行预览'" not in source
    assert "label: 'V2 模板'" not in source
    assert "{ to: '/prompts', label: 'V1 模板 / 对比' }" not in source
    assert "to: '/prompt-legacy'" not in source
    assert "{ to: '/prompt', label: '旧版 Prompt 构建' }" not in source


def test_prompt_preview_defaults_to_canonical_prompt_and_prompt_path_redirects():
    source = read_prompt_sources()

    assert "engine: 'prompt'" in source
    assert '<Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />' in source
    assert '<Route path="/prompt-legacy"' not in source
    assert '<Route path="/prompts"' not in source
    assert '<Route path="/prompt-templates" element={<PromptV2TemplatesPage />} />' in source
    assert '<Route path="/prompt-v2-templates" element={<Navigate to="/prompt-templates" replace />} />' in source


def test_legacy_prompt_pages_are_removed_from_webui():
    app_source = APP_JS.read_text(encoding="utf-8")
    prompt_source = PROMPT_JS.read_text(encoding="utf-8")
    prompt_import = next(
        line for line in app_source.splitlines()
        if "features/prompt/PromptPages" in line
    )
    prompt_import_names = prompt_import.split("import", 1)[1].split("from", 1)[0]

    assert "PromptPage" not in prompt_import_names
    assert "ManagedPromptsPage" not in prompt_import_names
    assert "<PromptPage" not in app_source
    assert "<ManagedPromptsPage" not in app_source
    assert "export function PromptPage()" not in prompt_source
    assert "export function ManagedPromptsPage()" not in prompt_source
    assert "/prompt/fragments" not in prompt_source
    assert "/prompts/" not in prompt_source


def test_prompt_runtime_v2_page_exposes_template_editor():
    source = PROMPT_JS.read_text(encoding="utf-8")
    preview_source = source.split("export function EffectivePromptPreviewPage()")[1]

    assert "export function PromptV2TemplatesPage()" in source
    assert "function PromptFlowCanvas(" in source
    assert "function selectedPromptFlowPath(" in source
    assert "entryNodeId" in source
    assert "entryNodeIds" in source
    assert "拓扑入口" in source
    assert "多个入口" not in source
    assert "Prompt 模板" in source
    assert "Prompt V2 模板" not in source
    assert "Prompt Runtime V2" not in source
    assert "Canvas 编排" in source
    assert "data-testid=\"prompt-flow-canvas\"" in source
    assert "data-testid=\"prompt-flow-viewport\"" in source
    assert "data-testid=\"prompt-flow-edge-layer\"" in source
    assert "prompt-flow-scrollbar" in source
    assert "data-testid=\"prompt-v2-workbench\"" in source
    assert "data-testid=\"prompt-v2-left-rail\"" in source
    assert "data-testid=\"prompt-v2-canvas-column\"" in source
    assert "data-testid=\"prompt-v2-side-panel\"" in source
    assert "xl:h-[calc(100vh-170px)]" in source
    assert "prompt-v2-canvas" in source
    assert "min-h-[720px]" in source
    assert "const [canvasViewport, setCanvasViewport]" in source
    assert "handleCanvasWheel" in source
    assert "addEventListener('wheel', handleCanvasWheel, { passive: false })" in source
    assert "event.stopPropagation()" in source
    assert "onWheel={handleCanvasWheel}" not in source
    assert "startPanCanvas" in source
    assert "const pan = panRef.current" in source
    assert "panRef.current.originX" not in source
    assert "startConnection" in source
    assert "completeConnection" in source
    assert "selectedEdgeKey" in source
    assert "deleteEdge" in source
    assert "updateEdgeScope" in source
    assert "连线作用范围" in source
    assert "filter(edge => !(edge.from === fromId" not in source
    assert "activeEdgeKeys.has(edgeKey)" in source
    assert "selectedPath.nodeIds" in source
    assert "删除连线" in source
    assert "连接端口" in source
    assert "onMouseDown={e => startDragNode" in source
    assert "开始连线" not in source
    assert "连到这里" not in source
    assert "缩小" in source
    assert "放大" in source
    assert "重置视图" in source
    assert "全局可插入变量白名单" in source
    assert "身份变量配置" in source
    assert "bot.character_name" in source
    assert "bot.alias_names" in source
    assert "bot.super_user_ids" not in source
    assert "api.put(`/settings/${encodeURIComponent(key)}`" in source
    assert "node.label || node.id" in source
    assert "添加节点" in source
    assert "添加节点后在右侧选择模板" in source
    assert "templateToAdd" not in source
    assert "删除节点" in source
    assert "当前节点" in source
    assert "节点模板切换" in source
    assert "当前节点使用的模板" in source
    assert "const [isLargeTemplateEditorOpen, setIsLargeTemplateEditorOpen]" in source
    assert "打开大窗编辑" in source
    assert "大窗编辑模板" in source
    assert "data-testid=\"prompt-large-template-editor\"" in source
    assert "关闭大窗" in source
    assert "工具模板" in source
    assert "任务模板" in source
    assert "资源树" in source
    assert "运行时覆盖" in source
    assert "新建模板" in source
    assert "重置覆盖" in source
    assert "删除运行时覆盖" in source
    assert "chat/main" in source
    assert "tools/custom_tool/usage" in source
    assert "按工具拆分" in source
    assert "工具提示词正文" in source
    assert "任务提示词正文" in source
    assert "真实工具 Schema" in source
    assert "工具 Schema JSON" in source
    assert "schemaEditText" in source
    assert "api.get(`/tools/${encodeURIComponent(toolName)}/schema`" in source
    assert "api.put(`/tools/${encodeURIComponent(schemaToolName)}/schema`" in source
    assert "api.delete(`/tools/${encodeURIComponent(schemaToolName)}/schema`" in source
    assert "news_search: { tool: 'news_search'" not in source
    assert "任务模板来源" in source
    assert "schemaJson" in source
    assert "schemaName" in source
    assert "selectedToolTemplate?.description || '从左侧工具列表选择模板后，在右侧编辑正文。'" not in source
    assert "onChange={e => setSelectedToolTemplateKey(e.target.value)}" not in source
    assert "promptV2TemplateKind" in source
    assert "promptV2ToolName" in source
    assert "promptV2Path" in source
    assert "templateTree" in source
    assert "selectedTaskTemplateKey" in source
    assert "selectedToolTemplateKey" in source
    assert ">runtime_key<" not in source
    assert "运行时数据" in source
    assert "api.get('/prompt/templates')" in source
    assert "api.get('/prompt/flow')" in source
    assert "api.put(`/prompt/templates/${promptV2Path(activeTemplateKey)}`" in source
    assert "api.post('/prompt/templates'" in source
    assert "api.delete(`/prompt/templates/${promptV2Path(activeTemplateKey)}`" in source
    assert "api.post(`/prompt/templates/${promptV2Path(activeTemplateKey)}/reset`" in source
    assert "api.put('/prompt/flow'" in source
    assert "保存模板" in source
    assert "保存 V2 模板" not in source
    assert "保存编排图" in source
    assert "运行时模板目录" in source
    assert "V2 模板编辑" not in preview_source


def test_prompt_v2_workbench_uses_contained_custom_scrollbars():
    css = CSS.read_text(encoding="utf-8")

    assert ".prompt-flow-scrollbar" in css
    assert "scrollbar-gutter: stable;" in css
    assert "scrollbar-width: thin;" in css
    assert ".prompt-v2-canvas" in css
    assert "overscroll-behavior: contain;" in css
    assert "touch-action: none;" in css


def test_prompt_runtime_dist_bundle_matches_canonical_prompt_routes():
    source = read_dist_sources()

    assert "/prompt/templates" in source
    assert "/prompt/flow" in source
    assert "/prompt/variables" in source
    assert "/prompt-v2/templates" not in source
    assert "/prompt-v2/flow" not in source
    assert "/prompt-v2/variables" not in source
    assert "V2 运行预览" not in source
    assert "V2 模板" not in source
    assert "Prompt Runtime V2" not in source
    assert "Prompt V2 模板" not in source
    assert "保存 V2 模板" not in source
