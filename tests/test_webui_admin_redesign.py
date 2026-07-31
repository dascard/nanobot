from pathlib import Path


APP_JS = Path("webui/src/App.jsx")
UI_JS = Path("webui/src/components/ui.jsx")
CSS = Path("webui/src/index.css")
LLM_LOGS_JS = Path("webui/src/features/agent-runs/LLMApiLogsPage.jsx")
MODELS_JS = Path("webui/src/features/models/ModelsPage.jsx")
MODELS_DIR = Path("webui/src/features/models")
PROMPT_JS = Path("webui/src/features/prompt/PromptPages.jsx")
SESSION_CONFIG_JS = Path(
    "webui/src/features/session-config/SessionConfigsPage.jsx"
)
REPLY_EVAL_JS = Path("webui/src/features/reply-eval/ReplyEvalPage.jsx")
TOOLS_JS = Path("webui/src/features/tools/ToolsPage.jsx")
EVALS_JS = Path("webui/src/features/evals/EvalsPage.jsx")
GROUP_LEARNING_JS = Path(
    "webui/src/features/group-learning/GroupLearningPage.jsx"
)
GROUP_LEARNING_PANELS_JS = Path(
    "webui/src/features/group-learning/GroupLearningPanels.jsx"
)


def read_app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def read_ui_sources() -> str:
    paths = [
        APP_JS,
        LLM_LOGS_JS,
        PROMPT_JS,
        REPLY_EVAL_JS,
        SESSION_CONFIG_JS,
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists()) + "\n" + read_models_sources()


def read_models_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODELS_DIR.glob("*.jsx"))
    )


def test_admin_layout_uses_grouped_responsive_navigation():
    source = read_app()

    assert "const BASE_NAV_SECTIONS = [" in source
    assert "composeNavigationSections(BASE_NAV_SECTIONS)" in source
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


def test_db_browser_uses_structured_view_contract_without_sql_editor():
    source = read_app()
    db_source = source.split("function DbPage()")[1].split(
        "// ── Logs ──"
    )[0]

    assert "api.get('/db/views')" in db_source
    assert (
        "api.post(`/db/views/${encodeURIComponent(viewId)}/rows`"
        in db_source
    )
    assert "next_cursor" in db_source
    assert "selectedMeta.filters" in db_source
    assert "/db/query" not in db_source
    assert "SQL 查询" not in db_source
    assert "SELECT ..." not in db_source


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
    assert "htmlFor=\"model-catalog-provider\"" in source
    assert "Field id=\"session-config-platform-filter\"" in source
    assert "Field id=\"session-config-chat-type-filter\"" in source
    assert "Field id=\"session-config-guidance-filter\"" in source


def test_model_routes_page_links_task_and_output_contracts():
    source = read_models_sources()

    assert "routeStatus?.task_contracts" in source
    assert "Task / Output Contract" in source
    assert "contract.task_key" in source
    assert "contract.output_failure_policy" in source


def test_model_provider_page_exposes_control_plane_lifecycle_and_key_actions():
    source = read_models_sources()

    assert "Provider Connections" in source
    assert "新增 Provider" in source
    assert "内置连接" in source
    assert "KT Provider Driver" in source
    assert "OpenAI-compatible" in source
    assert "Anthropic Messages" in source
    assert "Codex OAuth" in source
    assert "credential_action" in source
    assert '<option value="keep">保持不变</option>' in source
    assert '<option value="replace">替换</option>' in source
    assert '<option value="clear">清除</option>' in source
    assert "api.post('/models/providers', { id: draft.id, ...payload })" in source
    assert "api.put(`/models/providers/${encodeURIComponent(draft.id)}`" in source
    assert "api.delete(`/models/providers/${encodeURIComponent(draft.id)}`" in source
    assert "/catalog/refresh" in source
    assert "/test`" in source
    assert "api_key_configured" in source
    assert "route_completion_supported" in source
    assert "draft.catalog?.stale" in source


def test_model_console_exposes_presets_bindings_and_operable_kt_workspace():
    source = read_models_sources()

    assert "Model Presets" in source
    assert "Route Binding" in source
    assert "max_context" in source
    assert "max_output" in source
    assert "reasoning_effort" in source
    assert "service_tier" in source
    assert "retry_policy" in source
    assert "Extra Body JSON" in source
    assert "Variation Groups" in source
    assert "Resolved Request" in source
    assert "request_preview" in source
    assert "真实 KT Driver 测试" in source
    assert "/models/codex/device-login" in source
    assert "/models/codex/usage" in source
    assert "Device Code" in source
    assert "Provider Native Tools" in source
    assert "Provider Connection" in source
    assert "Ordered Fallback" in source


def test_model_provider_forms_are_labelled_and_report_async_states():
    source = read_models_sources()

    for control_id in (
        "model-provider-id",
        "model-provider-display-name",
        "model-provider-driver",
        "model-provider-base-url",
        "model-provider-registry",
        "model-provider-enabled",
        "model-provider-discovery",
        "model-provider-api-key",
    ):
        assert f'id="{control_id}"' in source
        assert f'htmlFor="{control_id}"' in source or (
            f'Field id="{control_id}"' in source
        ) or (
            f'Toggle id="{control_id}"' in source
        )
    assert "保存中..." in source
    assert "同步中..." in source
    assert "测试中..." in source
    assert 'role="alert"' in source
    assert 'aria-live="polite"' in source
    assert "useDeferredValue" in source
    assert "sm:grid-cols-3" in source
    assert "focus-visible:ring-2" in source


def test_model_console_uses_icons_instead_of_status_emoji():
    source = read_models_sources()

    assert "CheckCircle2" in source
    assert "CircleAlert" in source
    assert "RefreshCw" in source
    assert "✅" not in source
    assert "❌" not in source


def test_reply_eval_case_editor_controls_are_labelled():
    source = read_ui_sources()

    assert "Field id=\"reply-new-case-id\"" in source
    assert "id=\"reply-new-case-input\"" in source
    assert "Field id=\"reply-edit-title\"" in source
    assert "id=\"reply-edit-context\"" in source
    assert "aria-label=\"选择测试用例\"" in source


def test_eval_candidate_label_uses_expected_contract_and_scoreable_fields():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "expected_json: expectedJson" not in source
    assert "api.get('/evals/expected-contract')" in source
    assert "expectedContract" in source
    assert "labelError" in source
    assert "note: labelNote" in source
    assert "timing_action" in source
    assert "should_create_jargon" in source
    assert "should_create_expression" in source
    assert "forbidden_terms" in source

    assert "expected_action" not in source
    assert "should_learn" not in source
    assert "quality" not in source
    assert "category" not in source
    assert "meaning" not in source
    assert "delay_seconds" not in source


def test_eval_candidate_promote_uses_dry_run_modal():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "promotePlan" in source
    assert "promoteError" in source
    assert "target_dataset" in source
    assert "dry_run: true" in source
    assert "dry_run: false" in source
    assert "confirmPromote" in source
    assert "已提升到 regression" not in source
    assert "api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`)" not in source


def test_evals_candidates_page_shows_summary_readiness_and_preflight():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "summary" in source
    assert "readiness" in source
    assert "blocking_reasons" in source
    assert "预检当前页" in source
    assert "/evals/candidates/preflight" in source
    assert "candidate.readiness?.ready" in source
    assert "disabled" in source
    assert "提升" in source


def test_evals_candidates_page_exposes_triage_actions():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert '<option value="deferred">deferred</option>' in source
    assert '<option value="rejected">rejected</option>' in source
    assert "/reject" in source
    assert "/defer" in source
    assert "/reopen" in source
    assert "reason_code" in source
    assert "defer_until" in source
    assert "暂缓" in source
    assert "拒绝" in source
    assert "复开" in source


def test_evals_candidates_page_exposes_read_only_batch_audit():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "批次审计" in source
    assert "batchAudit" in source
    assert "/evals/candidates/preflight" in source
    assert "top_blocking_reasons" in source
    assert "blocking_reasons" in source
    assert "/evals/candidates/batch-triage" not in source
    assert "批量拒绝" not in source
    assert "批量暂缓" not in source
    assert "批量应用" not in source


def test_evals_page_exposes_candidate_trend_report():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "趋势报表" in source
    assert "/evals/candidates/trend" in source
    assert "candidateTrend" in source
    assert "top_blocking_reasons" in source
    assert "by_status" in source
    assert "批量拒绝" not in source
    assert "批量暂缓" not in source
    assert "批量应用" not in source


def test_evals_page_exposes_timing_tuning_proposal_report():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "调参提案" in source
    assert "/evals/timing-tuning/proposal" in source
    assert "timingProposal" in source
    assert "blocked_actions" in source
    assert "candidate_sets" in source
    assert "simulation" in source
    assert "/evals/timing-tuning/proposal/review" in source
    assert "/evals/timing-tuning/proposal/reviews" in source
    assert "approved_for_manual_experiment" in source
    assert "进入人工实验" in source
    assert "应用参数" not in source
    assert "更新 baseline" not in source
    assert "写入配置" not in source


def test_sticker_duplicate_actions_do_not_use_emoji_buttons():
    source = read_app()

    assert "RefreshCw" in source
    assert "Tags" in source
    assert "label=\"重试预览\"" in source
    assert "label=\"重试打标\"" in source
    assert ">🔄<" not in source
    assert ">🏷<" not in source


def test_sticker_duplicate_page_formats_api_errors():
    source = read_app()
    sticker_source = source.split("function StickerDedupPage()")[1].split("// ── Stickers ──")[0]

    assert "formatApiError(" in source
    assert "setNearError(formatApiError(e))" in sticker_source
    assert "alert(formatApiError(e))" in sticker_source
    assert "e?.response?.data?.detail || e.message" not in sticker_source


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


def test_group_learning_workbench_exposes_governed_management_surfaces():
    page_source = GROUP_LEARNING_JS.read_text(encoding="utf-8")
    panel_source = GROUP_LEARNING_PANELS_JS.read_text(encoding="utf-8")

    assert "getGroupLearningDescriptors()" in page_source
    assert "listGroupLearningSessions({ limit: 500 })" in page_source
    assert "extractGroupLearningSession({" in page_source
    assert "updateGroupLearningFeature({" in page_source
    assert "正式记忆" in page_source
    assert "学习候选" in page_source
    assert "定时白名单" in page_source
    assert "运行记录" in page_source
    assert "reviewGroupLearningCandidate({" in panel_source
    assert "setGroupLearningRuleActivation({" in panel_source
    assert "putGroupLearningSchedule({" in panel_source
    assert "pauseGroupLearningSchedule({" in panel_source
    assert "Evidence 受限预览" in panel_source


def test_session_summary_browser_exposes_llm_regeneration_controls():
    source = read_app()
    summary_source = source.split("function SessionSummaryBrowser(")[1].split("// ── Persona ──")[0]

    assert "重新生成 LLM 摘要" in summary_source
    assert "生成近期摘要" in summary_source
    assert "重新生成长期摘要" in summary_source
    assert "重试失败摘要任务" in summary_source
    assert "代码兜底" in summary_source
    assert "api.post(`/session-memory/${encodeURIComponent(selectedSession)}/rolling-summary/enqueue-llm`" in summary_source
    assert "api.post(`/session-memory/${encodeURIComponent(selectedSession)}/rolling-summary/run`" in summary_source
    assert "api.post(`/session-memory/${encodeURIComponent(selectedSession)}/digests/run`" in summary_source
    assert "api.post(`/session-memory/jobs/${jobId}/retry`" in summary_source
    assert "setOperationError(formatApiError(e))" in summary_source


def test_long_memory_digest_browser_exposes_generation_metadata():
    source = read_app()
    summary_source = source.split("function SessionSummaryBrowser(")[1].split("// ── Persona ──")[0]

    assert "summary_type" in summary_source
    assert "source_id" in summary_source
    assert "source_range" in summary_source
    assert "generator" in summary_source
    assert "quality_score" in summary_source
    assert "prompt_template" in summary_source
    assert "prompt_version" in summary_source
    assert "fallback_reason" in summary_source
    assert "recall_card_count" in summary_source
    assert "message_count" in summary_source


def test_group_learning_workbench_only_selects_discovered_canonical_sessions():
    page_source = GROUP_LEARNING_JS.read_text(encoding="utf-8")

    assert "item.chat_stream_id === current" in page_source
    assert "canonical 群 session" in page_source
    assert "listGroupLearningSessions" in page_source
    assert "groupId" not in page_source


def test_group_learning_workbench_does_not_bypass_governed_writes():
    page_source = GROUP_LEARNING_JS.read_text(encoding="utf-8")
    panel_source = GROUP_LEARNING_PANELS_JS.read_text(encoding="utf-8")
    combined = f"{page_source}\n{panel_source}"

    assert "request_id: requestId" in combined
    assert "reason," in combined
    assert "Prompt 注入独立控制" in page_source
    assert "首版只读展示" in panel_source
    assert "setGroupMemoryInjectionConfig" not in combined
    assert "updateGroupMemoryItem" not in combined


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


def test_tools_page_exposes_platform_scope_controls():
    source = TOOLS_JS.read_text(encoding="utf-8")

    assert "tool-platform-select" in source
    assert "指定平台" in source
    assert "listTools({" in source
    assert "platform," in source
    assert "scope_type: 'platform'" in source or 'scope_type: \"platform\"' in source


def test_tools_page_does_not_advertise_text_based_runtime_downgrade():
    source = TOOLS_JS.read_text(encoding="utf-8")

    assert "普通聊天不会根据消息文本自动裁剪工具" in source
    assert "不会按消息内容切换" in source
    assert "运行时自动降档" not in source
    assert "lightweight_default" not in source
