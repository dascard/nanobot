# 决策规则审计清单

- Schema 版本：1
- 源提交：`da9b59dfd49d893c5c8c309fbc1884e62d0d85c6`
- 规则总数：8340
- 扫描错误：0
- 人工复核队列：721
- 完整逐项记录：`decision-rule-inventory.json`

## 分类汇总

| 分类 | 数量 |
|---|---:|
| `compatibility` | 138 |
| `configurable_policy` | 1615 |
| `data_consistency` | 478 |
| `natural_language_semantic` | 31 |
| `presentation` | 5 |
| `protocol_syntax` | 4405 |
| `security_invariant` | 1668 |

## 文件汇总

| 文件 | 命中数 |
|---|---:|
| `core/schema_migrations.py` | 243 |
| `scripts/manage-sandbox-production.sh` | 179 |
| `core/scheduled_workflow.py` | 133 |
| `webui/src/App.jsx` | 128 |
| `core/proactive_simulation.py` | 87 |
| `core/proactive_research.py` | 82 |
| `core/scheduled_task_contract.py` | 79 |
| `sandboxd/filesystem.py` | 77 |
| `api/admin/sandbox_routes.py` | 75 |
| `api/admin/model_routes.py` | 73 |
| `sandboxd/app.py` | 72 |
| `core/skills/contracts.py` | 69 |
| `core/semantic/backfill.py` | 68 |
| `core/outbound/delivery_claims.py` | 65 |
| `core/prompt_v2/audit.py` | 65 |
| `core/outbound_delivery_schema.py` | 64 |
| `core/llm_request_linter.py` | 63 |
| `core/sandbox/lease_reconciler.py` | 63 |
| `core/outbound/settlement.py` | 59 |
| `core/tracing.py` | 56 |
| `core/prompt_v2/template_migration.py` | 55 |
| `clients/new_api_client.py` | 53 |
| `core/chat_delivery_outbox_schema.py` | 53 |
| `app/memory_digest/llm_builder.py` | 51 |
| `core/proactive_outreach_schema.py` | 51 |
| `core/run_recovery/service.py` | 50 |
| `api/admin/model_preset_routes.py` | 49 |
| `clients/model_registry.py` | 49 |
| `api/admin/reply_routes.py` | 48 |
| `core/daily_digest.py` | 47 |
| `core/sandbox/process_service.py` | 47 |
| `core/scheduled_task_outbound.py` | 47 |
| `clients/classifier_client.py` | 46 |
| `scripts/sandbox-coordinated-backup.sh` | 46 |
| `api/admin/sticker_routes.py` | 45 |
| `core/prompt_v2/flow_migrations.py` | 45 |
| `api/admin/tool_routes.py` | 44 |
| `app/memory_digest/builder.py` | 42 |
| `core/persona_preprocess.py` | 41 |
| `sandboxd/network_policy.py` | 41 |
| `core/agent_orchestration/contracts.py` | 40 |
| `core/context_engine.py` | 40 |
| `api/admin/chat_config_routes.py` | 39 |
| `app/session_memory/llm_summarizer.py` | 39 |
| `core/memory_cleanup.py` | 39 |
| `nanobot_kt/bridge.py` | 39 |
| `creatures/nanobot/prompts/skills/news_search/evidence.py` | 38 |
| `scripts/assign-sandbox-project-quota.sh` | 38 |
| `app/session_memory/admin_browser.py` | 37 |
| `app/group_analysis/preprocess.py` | 36 |
| `core/release/deployment.py` | 36 |
| `core/sticker_preview.py` | 36 |
| `core/db/group_learning_governance_adapter.py` | 35 |
| `core/release/production_preflight.py` | 35 |
| `sandboxd/unified_patch.py` | 35 |
| `scripts/check_architecture.py` | 35 |
| `core/group_runtime/runtime.py` | 34 |
| `core/group_runtime/scoring.py` | 34 |
| `core/sandbox/admin_operations.py` | 34 |
| `core/context_builder.py` | 33 |
| `core/run_ledger/contracts.py` | 33 |
| `webui/src/features/sandbox/SandboxPage.test.jsx` | 33 |
| `app/session_memory/llm_contract.py` | 32 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py` | 32 |
| `core/outbound/generation.py` | 31 |
| `core/sticker_memory.py` | 31 |
| `sandboxd/docker_backend.py` | 31 |
| `api/admin/group_learning_routes.py` | 30 |
| `core/context_compaction.py` | 30 |
| `core/legacy_adapter.py` | 30 |
| `core/interoperability/a2a.py` | 29 |
| `core/mcp/contracts.py` | 29 |
| `api/admin/rag_benchmark_routes.py` | 28 |
| `app/memory_digest/jobs.py` | 28 |
| `core/group_memory.py` | 28 |
| `core/runtime_tool_service.py` | 28 |
| `core/sandbox/profile_catalog.py` | 28 |
| `core/proactive/generation.py` | 27 |
| `core/sandbox/admin_service.py` | 27 |
| `nanobot_kt/image_pipeline.py` | 27 |
| `api/admin/rag_routes.py` | 26 |
| `app/group_ingress/helpers.py` | 26 |
| `app/session_memory/jobs.py` | 26 |
| `core/prompt_v2/template_registry.py` | 26 |
| `sandboxd/environment_manager.py` | 26 |
| `webui/src/api/generated/adminClient.ts` | 26 |
| `api/admin/runtime_routes.py` | 25 |
| `core/agent_orchestration/runtime_executor.py` | 25 |
| `core/prompts/manager.py` | 25 |
| `core/run_ledger/projection.py` | 25 |
| `core/timing_score.py` | 25 |
| `sandboxd/process_manager.py` | 25 |
| `api/admin/eval_routes.py` | 24 |
| `core/outbound_transport.py` | 24 |
| `core/agent_link/runtime.py` | 23 |
| `core/model_provider/preset_config.py` | 23 |
| `core/outbound/run_claims.py` | 23 |
| `core/outbound_delivery_service.py` | 23 |
| `core/prompt_v2/compiler.py` | 23 |
| `core/release/artifacts.py` | 23 |
| `sandboxd/lease_store.py` | 23 |
| `core/agent_runtime/extension_ports.py` | 22 |
| `core/sandbox/access_policy.py` | 22 |
| `core/sandbox/tool_service.py` | 22 |
| `api/admin/proactive_outreach_routes.py` | 21 |
| `app/persona/retrieval_service.py` | 21 |
| `core/agent_runtime/contracts.py` | 21 |
| `core/db/group_learning_command_adapter.py` | 21 |
| `core/prompt_v2/task_contracts.py` | 21 |
| `core/semantic/indexer.py` | 21 |
| `api/endpoint_contracts.py` | 20 |
| `app/session_memory/rolling_summary.py` | 20 |
| `app/tool_services/sandbox.py` | 20 |
| `core/admin/table_views.py` | 20 |
| `core/interoperability/acp.py` | 20 |
| `core/model_provider/provider_config.py` | 20 |
| `core/proactive/orchestrator.py` | 20 |
| `core/semantic/jobs.py` | 20 |
| `core/web_search/relevance.py` | 20 |
| `nanobot_kt/reply_contract.py` | 20 |
| `nanobot_kt/tools/memory_query.py` | 20 |
| `nanobot_kt/tools/sandbox.py` | 20 |
| `scripts/build_context_manifest.py` | 20 |
| `webui/src/features/evals/EvalsPage.jsx` | 20 |
| `app/session_memory/windowing.py` | 19 |
| `app/tool_services/schedule_task.py` | 19 |
| `core/inbound_idempotency.py` | 19 |
| `core/memory_governance.py` | 19 |
| `core/sandbox/diagnostics.py` | 19 |
| `webui/src/features/prompt/PromptPages.jsx` | 19 |
| `api/admin/prompt_v2_routes.py` | 18 |
| `core/agent_orchestration/plan_governance.py` | 18 |
| `core/outbound/control_transitions.py` | 18 |
| `core/sandbox/lease_service.py` | 18 |
| `core/sql_readonly.py` | 18 |
| `core/task_runtime/validators.py` | 18 |
| `core/tool_contracts/ai_daily.py` | 18 |
| `core/web_search/url_policy.py` | 18 |
| `creatures/nanobot/prompts/skills/news_search/legacy_report.py` | 18 |
| `api/admin_routes.py` | 17 |
| `api/history_log_routes.py` | 17 |
| `app/group_analysis/analyzer.py` | 17 |
| `core/agent_manifest/validation.py` | 17 |
| `core/asset_tokens.py` | 17 |
| `core/semantic/adapters.py` | 17 |
| `core/settings_specs.py` | 17 |
| `core/skills/service.py` | 17 |
| `scripts/migrate-sandbox-project-map.py` | 17 |
| `workers/semantic_index_worker.py` | 17 |
| `api/admin/trace_routes.py` | 16 |
| `api/chat_recovery.py` | 16 |
| `app/group_learning/pipeline_service.py` | 16 |
| `core/proactive/model_policy.py` | 16 |
| `core/run_recovery/contracts.py` | 16 |
| `core/sandbox/client.py` | 16 |
| `core/sandbox/environment_service.py` | 16 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/evidence_light.py` | 16 |
| `nanobot_kt/output.py` | 16 |
| `nanobot_kt/tools/image_generation.py` | 16 |
| `scripts/build_behavior_baseline.py` | 16 |
| `scripts/generate_openapi_client.py` | 16 |
| `webui/src/features/sandbox/SandboxPage.jsx` | 16 |
| `api/admin/outbound_delivery_routes.py` | 15 |
| `api/admin/session_memory_routes.py` | 15 |
| `app/group_ingress/recovery.py` | 15 |
| `clients/reply_route_chat_completion_adapter.py` | 15 |
| `core/config_registry.py` | 15 |
| `core/db/group_learning_legacy_adapter.py` | 15 |
| `core/durable_tasks/service.py` | 15 |
| `core/memory_rag.py` | 15 |
| `core/outbound/policy.py` | 15 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py` | 15 |
| `foundation/llm/request_sanitizer.py` | 15 |
| `foundation/llm/safe_diagnostics.py` | 15 |
| `scripts/benchmark_classifier.py` | 15 |
| `scripts/cleanup-sandbox-runtime.sh` | 15 |
| `webui/src/features/sandbox/SandboxFilesPage.test.jsx` | 15 |
| `api/admin/log_routes.py` | 14 |
| `api/chat_response_contract.py` | 14 |
| `app/session_memory/summarizer.py` | 14 |
| `config.py` | 14 |
| `core/ai_daily_ingest.py` | 14 |
| `core/eval_sampling/store.py` | 14 |
| `core/group_learning/rules.py` | 14 |
| `core/outbound/control.py` | 14 |
| `core/run_ledger/persistence.py` | 14 |
| `core/sandbox/asset_store.py` | 14 |
| `core/sandbox/paths.py` | 14 |
| `core/sandbox/run_ledger.py` | 14 |
| `core/semantic/retriever.py` | 14 |
| `core/skills/provider.py` | 14 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/enrich.py` | 14 |
| `sandboxd/config.py` | 14 |
| `scripts/check_github_prs.py` | 14 |
| `webui/src/features/triggers/TriggersPage.test.jsx` | 14 |
| `app/group_learning/migration_audit.py` | 13 |
| `core/db/group_learning_schedule_adapter.py` | 13 |
| `core/group_runtime/state.py` | 13 |
| `core/prompt_v2/template_baseline.py` | 13 |
| `core/prompt_v2/tool_templates.py` | 13 |
| `core/task_runtime/runtime.py` | 13 |
| `core/telemetry/contracts.py` | 13 |
| `core/text_style.py` | 13 |
| `core/web_search/provider_settings.py` | 13 |
| `scripts/write_runtime_build_evidence.py` | 13 |
| `webui/src/features/reply-eval/ReplyEvalPage.jsx` | 13 |
| `app/group_analysis/repository.py` | 12 |
| `app/group_memory/retrieval_service.py` | 12 |
| `app/memory_digest/retrieval_service.py` | 12 |
| `core/agent_runtime/service_ports.py` | 12 |
| `core/db/group_learning_adapter.py` | 12 |
| `core/group_learning/prompt_injection.py` | 12 |
| `core/news/source_registry.py` | 12 |
| `core/proactive/grounding.py` | 12 |
| `core/prompt_v2/flow.py` | 12 |
| `core/run_ledger/adapters.py` | 12 |
| `core/semantic/scoring.py` | 12 |
| `core/session_goal.py` | 12 |
| `core/sticker_rag.py` | 12 |
| `core/task_runtime/resilience.py` | 12 |
| `core/tool_registration.py` | 12 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/sources/curated.py` | 12 |
| `sandboxd/container_security.py` | 12 |
| `api/admin/persona_routes.py` | 11 |
| `api/agent_link_routes.py` | 11 |
| `core/agent_runtime/native.py` | 11 |
| `core/asset_transport.py` | 11 |
| `core/generated_images.py` | 11 |
| `core/proactive/serialization.py` | 11 |
| `core/prompt_v2/prefix_cache.py` | 11 |
| `core/prompt_v2/task_templates.py` | 11 |
| `core/reply_postprocess.py` | 11 |
| `core/schedule_spec.py` | 11 |
| `core/settings_service.py` | 11 |
| `core/skills/governance.py` | 11 |
| `nanobot_kt/tools/ai_daily.py` | 11 |
| `sandboxd/quota.py` | 11 |
| `webui/src/features/rag/RagBenchmarkPage.jsx` | 11 |
| `api/admin/scheduled_task_routes.py` | 10 |
| `api/chat_persistence.py` | 10 |
| `app/group_learning/review_service.py` | 10 |
| `app/session_config/discovery_service.py` | 10 |
| `core/agent_orchestration/scheduler.py` | 10 |
| `core/chat_delivery_outbox.py` | 10 |
| `core/content_rules/contracts.py` | 10 |
| `core/eval_sampling/timing_signal_audit.py` | 10 |
| `core/model_provider/route_registry.py` | 10 |
| `core/outbound/projection.py` | 10 |
| `core/persisted_content.py` | 10 |
| `core/prompt_v2/template_resolution.py` | 10 |
| `core/runtime/events.py` | 10 |
| `core/sandbox/asset_service.py` | 10 |
| `scripts/build_semantic_task_baseline.py` | 10 |
| `scripts/docker-build.sh` | 10 |
| `scripts/sandbox-smoke-summary.py` | 10 |
| `scripts/sandbox-smoke-test.sh` | 10 |
| `api/asset_routes.py` | 9 |
| `api/routes.py` | 9 |
| `app/group_learning/candidate_service.py` | 9 |
| `core/db/session.py` | 9 |
| `core/group_learning/evidence.py` | 9 |
| `core/jobs/contracts.py` | 9 |
| `core/knowledge_rag.py` | 9 |
| `core/mcp/config_service.py` | 9 |
| `core/outbound/replay.py` | 9 |
| `core/prompt_v2/contribution_registry.py` | 9 |
| `core/runtime/plugin_lifecycle.py` | 9 |
| `core/semantic/reranker.py` | 9 |
| `core/skills/discovery.py` | 9 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/normalize_v2.py` | 9 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py` | 9 |
| `foundation/identity/normalization.py` | 9 |
| `foundation/llm/cache_shape.py` | 9 |
| `nanobot_kt/model_runtime.py` | 9 |
| `sandboxd/maintenance_probe.py` | 9 |
| `scripts/build-sandbox-image.sh` | 9 |
| `scripts/deploy_release.py` | 9 |
| `webui/src/features/manifest.jsx` | 9 |
| `webui/src/features/triggers/TriggersPage.jsx` | 9 |
| `api/admin/group_memory_routes.py` | 8 |
| `api/admin/mcp_routes.py` | 8 |
| `api/admin/skill_routes.py` | 8 |
| `api/chat_content_helpers.py` | 8 |
| `api/session_goal_routes.py` | 8 |
| `app/group_ingress/service.py` | 8 |
| `core/agent_runtime/governance.py` | 8 |
| `core/agent_runtime/governance_contracts.py` | 8 |
| `core/jobs/policies.py` | 8 |
| `core/prompt_v2/template_loader.py` | 8 |
| `core/sandbox/repositories.py` | 8 |
| `core/semantic/fts.py` | 8 |
| `core/session_guidance.py` | 8 |
| `core/task_runtime/slo.py` | 8 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/freshness.py` | 8 |
| `nanobot_kt/codex_oauth_adapter.py` | 8 |
| `nanobot_kt/codex_provider.py` | 8 |
| `nanobot_kt/scheduled_workflow_adapter.py` | 8 |
| `sandboxd/lease_backend.py` | 8 |
| `sandboxd/lease_reconciler.py` | 8 |
| `scripts/build_task_slo_manifest.py` | 8 |
| `scripts/check-sandbox-data-disk.sh` | 8 |
| `scripts/manage-prompt-runtime-production.sh` | 8 |
| `scripts/render-sandbox-profile-manifest.py` | 8 |
| `api/admin/web_search_routes.py` | 7 |
| `api/memory_routes.py` | 7 |
| `core/agent_link/protocol.py` | 7 |
| `core/artifact_port.py` | 7 |
| `core/client_meta.py` | 7 |
| `core/evolution.py` | 7 |
| `core/inbound_claim_lifecycle.py` | 7 |
| `core/knowledge_library.py` | 7 |
| `core/memory_provider/contracts.py` | 7 |
| `core/model_route_health.py` | 7 |
| `core/modules/contracts.py` | 7 |
| `core/news/signals.py` | 7 |
| `core/permissions/service.py` | 7 |
| `core/proactive/delivery.py` | 7 |
| `core/proactive/repository.py` | 7 |
| `core/prompt_v2/template_store.py` | 7 |
| `core/prompt_v2/variables.py` | 7 |
| `core/release/impact.py` | 7 |
| `core/release/verification.py` | 7 |
| `core/route_metadata.py` | 7 |
| `core/run_ledger/governance.py` | 7 |
| `core/run_ledger/read_model.py` | 7 |
| `core/run_recovery/proofs.py` | 7 |
| `core/semantic/provider_factory.py` | 7 |
| `core/tool_plan.py` | 7 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/sources/htmllist.py` | 7 |
| `creatures/nanobot/prompts/skills/news_search/runtime_cache.py` | 7 |
| `creatures/nanobot/prompts/skills/news_search/search_backend.py` | 7 |
| `foundation/message_contract/contracts.py` | 7 |
| `nanobot_kt/tools/image_summary.py` | 7 |
| `sandboxd/process_output.py` | 7 |
| `scripts/manage_prompt_templates.py` | 7 |
| `scripts/verify_prompt_runtime_release.py` | 7 |
| `workers/session_summary_worker.py` | 7 |
| `api/admin/run_evidence_routes.py` | 6 |
| `api/chat_request_contract.py` | 6 |
| `api/sticker_media_routes.py` | 6 |
| `api/task_routes.py` | 6 |
| `api/telemetry_middleware.py` | 6 |
| `app/group_analysis/local_rag.py` | 6 |
| `app/session_memory/blocks.py` | 6 |
| `app/session_memory/group_rollup.py` | 6 |
| `app/tool_services/skill.py` | 6 |
| `core/admin/idempotency.py` | 6 |
| `core/agent_orchestration/persistence.py` | 6 |
| `core/chat_delivery_service.py` | 6 |
| `core/content_rules/adapters.py` | 6 |
| `core/eval_sampling/db_sampler.py` | 6 |
| `core/fencing.py` | 6 |
| `core/group_learning/legacy_migration.py` | 6 |
| `core/message_envelope.py` | 6 |
| `core/news/policy.py` | 6 |
| `core/proactive/runtime_identity.py` | 6 |
| `core/proactive/schedule_repository.py` | 6 |
| `core/proactive_candidate.py` | 6 |
| `core/proactive_diagnostics.py` | 6 |
| `core/qq_outbound_renderer.py` | 6 |
| `core/web_search/search_runtime.py` | 6 |
| `foundation/message_contract/parsing.py` | 6 |
| `nanobot_kt/runtime_adapter.py` | 6 |
| `nanobot_kt/tool_runtime.py` | 6 |
| `scripts/build_release_manifest.py` | 6 |
| `webui/src/features/models/ModelsPage.jsx` | 6 |
| `webui/src/features/proactive-outreach/ProactiveOutreachPage.jsx` | 6 |
| `webui/src/features/session-config/SessionConfigsPage.jsx` | 6 |
| `api/group_utility_routes.py` | 5 |
| `app/group_ingress/message_adapter.py` | 5 |
| `app/group_memory/injection_service.py` | 5 |
| `app/session_memory/block_episodes.py` | 5 |
| `app/tool_services/image_summary.py` | 5 |
| `clients/provider_adapter.py` | 5 |
| `clients/task_runtime_adapter.py` | 5 |
| `core/agent_orchestration/serialization.py` | 5 |
| `core/agent_runtime/selection.py` | 5 |
| `core/compaction.py` | 5 |
| `core/durable_tasks/contracts.py` | 5 |
| `core/expression_memory.py` | 5 |
| `core/group_learning/aspects.py` | 5 |
| `core/model_provider/contracts.py` | 5 |
| `core/run_ledger/governance_service.py` | 5 |
| `core/run_recovery/coordinator.py` | 5 |
| `core/sandbox/workspace_acl.py` | 5 |
| `core/task_runtime/contracts.py` | 5 |
| `core/telemetry/job_observer.py` | 5 |
| `core/telemetry/persistence.py` | 5 |
| `core/tool_registry.py` | 5 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/digest.py` | 5 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/diversify.py` | 5 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/validate.py` | 5 |
| `nanobot_kt/codex_accounts.py` | 5 |
| `nanobot_kt/prompt_runtime.py` | 5 |
| `scripts/backfill_session_blocks.py` | 5 |
| `scripts/manage_prompt_flow.py` | 5 |
| `webui/src/features/generated-images/GeneratedImagesPage.jsx` | 5 |
| `webui/src/features/models/KtDriversPanel.jsx` | 5 |
| `webui/src/features/models/ModelCatalogPanel.jsx` | 5 |
| `webui/src/features/models/ProviderConnectionsPanel.jsx` | 5 |
| `workers/chat_delivery_worker.py` | 5 |
| `api/chat_guardrail_facade.py` | 4 |
| `api/chat_pre_bridge_decision.py` | 4 |
| `api/chat_pre_bridge_route_result.py` | 4 |
| `api/chat_private_buffer.py` | 4 |
| `app/group_analysis/render.py` | 4 |
| `app/group_analysis/service.py` | 4 |
| `app/group_learning/schedule_service.py` | 4 |
| `app/group_memory/query_service.py` | 4 |
| `app/prompt_runtime/preview_service.py` | 4 |
| `clients/mcp.py` | 4 |
| `core/agent_manifest/values.py` | 4 |
| `core/agent_runtime/recovery.py` | 4 |
| `core/json_utils.py` | 4 |
| `core/model_provider/response_normalization.py` | 4 |
| `core/news/review.py` | 4 |
| `core/proactive/model_service.py` | 4 |
| `core/proactive/prompt_policy.py` | 4 |
| `core/proactive/scheduling_service.py` | 4 |
| `core/prompt_v2/context_adapters.py` | 4 |
| `core/prompt_v2/schema.py` | 4 |
| `core/prompt_v2/template_validation.py` | 4 |
| `core/sandbox/identity.py` | 4 |
| `core/sandbox/workspace_service.py` | 4 |
| `core/tool_execution_policy.py` | 4 |
| `core/tool_schema_preview.py` | 4 |
| `core/tool_tracing.py` | 4 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/cache.py` | 4 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/normalize.py` | 4 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/rank.py` | 4 |
| `foundation/identity/contracts.py` | 4 |
| `foundation/llm/cache_usage.py` | 4 |
| `nanobot_kt/model_provider_adapter.py` | 4 |
| `nanobot_kt/runtime_context_adapter.py` | 4 |
| `sandboxd/auth.py` | 4 |
| `sandboxd/concurrency.py` | 4 |
| `scripts/build_verification_plan.py` | 4 |
| `scripts/check-loopback-image-allocation.sh` | 4 |
| `scripts/manage_models.py` | 4 |
| `scripts/prepare-runtime-directories.sh` | 4 |
| `webui/src/features/rag/RagDebugPage.jsx` | 4 |
| `webui/src/features/sandbox/SandboxFilesPage.jsx` | 4 |
| `webui/src/features/web-search/api.js` | 4 |
| `workers/outbound_delivery_worker.py` | 4 |
| `api/admin/system_routes.py` | 3 |
| `api/agent_step_routes.py` | 3 |
| `api/chat_streaming_result.py` | 3 |
| `api/memory_digest_contract.py` | 3 |
| `api/model_routes.py` | 3 |
| `app/group_learning/scheduler.py` | 3 |
| `app/group_memory/extraction_service.py` | 3 |
| `app/persona/update_service.py` | 3 |
| `app/session_memory/retrieval_service.py` | 3 |
| `app/tool_services/knowledge_query.py` | 3 |
| `bootstrap/prompt_runtime.py` | 3 |
| `core/agent_manifest/compiler.py` | 3 |
| `core/agent_orchestration/checkpoint_store.py` | 3 |
| `core/build_info.py` | 3 |
| `core/db/group_memory_adapter.py` | 3 |
| `core/durable_tasks/owner.py` | 3 |
| `core/durable_tasks/reconciler.py` | 3 |
| `core/lifecycle/compatibility_registry.py` | 3 |
| `core/lifecycle/feature_registry.py` | 3 |
| `core/llm_sdk_tracing.py` | 3 |
| `core/mcp/secrets.py` | 3 |
| `core/private_timing_policy.py` | 3 |
| `core/proactive/delivery_runtime.py` | 3 |
| `core/proactive/lease.py` | 3 |
| `core/proactive/runtime_support.py` | 3 |
| `core/proactive/scheduler.py` | 3 |
| `core/prompt_v2/section_descriptors.py` | 3 |
| `core/runtime/extensions.py` | 3 |
| `core/runtime_health.py` | 3 |
| `core/telemetry/runtime.py` | 3 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/dedup.py` | 3 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/sources/rss.py` | 3 |
| `foundation/llm/model_options.py` | 3 |
| `nanobot_kt/bridge_runtime_support.py` | 3 |
| `nanobot_kt/direct_tool_execution.py` | 3 |
| `nanobot_kt/optional_tool_api.py` | 3 |
| `scripts/manage_memory_cleanup.py` | 3 |
| `scripts/migrate_group_learning_legacy.py` | 3 |
| `webui/src/features/agent-runs/AgentRunDetailPage.jsx` | 3 |
| `webui/src/features/agent-runs/AgentRunsPage.jsx` | 3 |
| `webui/src/features/agent-runs/LLMApiLogsPage.jsx` | 3 |
| `api/admin/db_browser_routes.py` | 2 |
| `api/chat_streaming_helpers.py` | 2 |
| `app/group_ingress/response_contract.py` | 2 |
| `app/tool_services/ai_daily.py` | 2 |
| `app/tool_services/runtime_execution.py` | 2 |
| `bootstrap/model_runtime.py` | 2 |
| `bootstrap/network_check.py` | 2 |
| `bootstrap/provider_migration.py` | 2 |
| `core/agent_manifest/preflight.py` | 2 |
| `core/chat_stream_identity.py` | 2 |
| `core/content_rules/engine.py` | 2 |
| `core/context_legacy.py` | 2 |
| `core/db/adapter.py` | 2 |
| `core/eval_sampling/log_sampler.py` | 2 |
| `core/group_learning/rule_activation.py` | 2 |
| `core/group_learning/states.py` | 2 |
| `core/mcp/diagnostics.py` | 2 |
| `core/mcp/runtime.py` | 2 |
| `core/memory_provider/registry.py` | 2 |
| `core/message_transport_adapters.py` | 2 |
| `core/moderation.py` | 2 |
| `core/private_timing_contracts.py` | 2 |
| `core/prompt_v2/policy_profiles.py` | 2 |
| `core/registry/validation.py` | 2 |
| `core/run_ledger/sinks.py` | 2 |
| `core/sandbox/execution_profiles.py` | 2 |
| `core/sandbox/quota_service.py` | 2 |
| `core/sqlite_maintenance.py` | 2 |
| `core/state_manager.py` | 2 |
| `core/telemetry/__init__.py` | 2 |
| `core/timing_model_policy.py` | 2 |
| `core/token_utils.py` | 2 |
| `core/user_block_rules.py` | 2 |
| `core/web_search/provider_tests.py` | 2 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/cluster.py` | 2 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/config.py` | 2 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/summarize_quality.py` | 2 |
| `creatures/nanobot/prompts/skills/news_search/render.py` | 2 |
| `nanobot_kt/mcp_runtime.py` | 2 |
| `nanobot_kt/memory_runtime.py` | 2 |
| `nanobot_kt/message_adapter.py` | 2 |
| `nanobot_kt/model_attempts.py` | 2 |
| `nanobot_kt/tool_registration_adapter.py` | 2 |
| `scripts/build_release_impact.py` | 2 |
| `scripts/deploy-production.sh` | 2 |
| `scripts/rag_write_test_report.py` | 2 |
| `server.py` | 2 |
| `webui/src/features/manifestValidation.js` | 2 |
| `webui/src/features/models/RouteBindingsPanel.jsx` | 2 |
| `webui/src/features/tools/ToolsPage.jsx` | 2 |
| `api/admin/runtime_module_routes.py` | 1 |
| `api/chat_non_streaming_result.py` | 1 |
| `api/chat_persona_context.py` | 1 |
| `api/chat_persona_lookup.py` | 1 |
| `api/chat_push_envelope.py` | 1 |
| `api/chat_runtime_facade.py` | 1 |
| `api/evolution_routes.py` | 1 |
| `api/group_message_routes.py` | 1 |
| `api/migrate_db.py` | 1 |
| `app/group_analysis/application_service.py` | 1 |
| `app/group_analysis/cache.py` | 1 |
| `app/group_learning/query_service.py` | 1 |
| `app/group_memory/command_service.py` | 1 |
| `app/group_memory/renderer.py` | 1 |
| `app/memory_digest/quality.py` | 1 |
| `app/memory_digest/renderer.py` | 1 |
| `app/persona/injection_service.py` | 1 |
| `app/tool_services/session_plan.py` | 1 |
| `app/tool_services/sql_analysis.py` | 1 |
| `app/tool_services/web_search.py` | 1 |
| `bootstrap/job_adapters.py` | 1 |
| `bootstrap/lifespan.py` | 1 |
| `bootstrap/schedulers.py` | 1 |
| `clients/provider_catalog.py` | 1 |
| `core/agent_manifest/canonical.py` | 1 |
| `core/agent_manifest/contracts.py` | 1 |
| `core/agent_orchestration/scope.py` | 1 |
| `core/agent_step.py` | 1 |
| `core/data_clean.py` | 1 |
| `core/group_learning/reserved_terms.py` | 1 |
| `core/interoperability/headless.py` | 1 |
| `core/llm_trace_context.py` | 1 |
| `core/model_provider/catalog_runtime.py` | 1 |
| `core/model_provider/chat_runtime.py` | 1 |
| `core/model_provider/variation_resolver.py` | 1 |
| `core/persona_candidate_prompt.py` | 1 |
| `core/private_timing.py` | 1 |
| `core/proactive/topic_policy.py` | 1 |
| `core/prompt_v2/flow_storage.py` | 1 |
| `core/registry/builder.py` | 1 |
| `core/release/runtime_verify.py` | 1 |
| `core/repositories/chat_logs.py` | 1 |
| `core/retrieval/contracts.py` | 1 |
| `core/run_recovery/verification.py` | 1 |
| `core/runtime/event_bus.py` | 1 |
| `core/runtime_paths.py` | 1 |
| `core/settings_admin_service.py` | 1 |
| `core/sqlite_retry.py` | 1 |
| `core/sticker_preview_jobs.py` | 1 |
| `core/tool_contracts/result.py` | 1 |
| `core/tool_contracts/rich_output.py` | 1 |
| `core/tool_result_artifacts.py` | 1 |
| `core/web_search/usage_stats.py` | 1 |
| `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/select_.py` | 1 |
| `foundation/llm/cost_usage.py` | 1 |
| `foundation/llm/tool_policy.py` | 1 |
| `nanobot_kt/agent_link_adapter.py` | 1 |
| `nanobot_kt/agent_link_tools.py` | 1 |
| `nanobot_kt/codex_admin_adapter.py` | 1 |
| `nanobot_kt/optional_message_api.py` | 1 |
| `nanobot_kt/optional_output_api.py` | 1 |
| `nanobot_kt/request_scope.py` | 1 |
| `nanobot_kt/session_goal_runtime.py` | 1 |
| `nanobot_kt/skill_runtime.py` | 1 |
| `nanobot_kt/tool_execution_adapter.py` | 1 |
| `scripts/run_proactive_outreach_simulation.py` | 1 |
| `webui/src/api/client.js` | 1 |
| `webui/src/components/TraceView.jsx` | 1 |
| `webui/src/components/ui.jsx` | 1 |
| `webui/src/features/agent-runs/ToolCallsPage.jsx` | 1 |
| `webui/src/features/logs/ModelRepliesTab.jsx` | 1 |
| `webui/src/features/models/LocalComponentsPanel.jsx` | 1 |
| `webui/src/features/models/modelConsoleUi.jsx` | 1 |

## 人工复核队列

| Rule ID | 位置 | 检测器 | 分类 | 处置 | 阶段 | 复核 |
|---|---|---|---|---|---|---|
| `decision.583cd4913f37f129e979` | `api/admin/chat_config_routes.py:45` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：_LEGACY_LEARNING_FIELDS = ( "enable_expression_learning", "enable_jargon_learning", ) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.cbabe82b15aa83c9bab9` | `api/admin/chat_config_routes.py:129` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：value not in {0, 1} |  |  |  |  |  |
|  | 原因：人工复核：Web 动作字段只接受 0/1 是持久化请求合同 |  |  |  |  |  |
| `decision.1ed15908ad21eddb34fd` | `api/admin/chat_config_routes.py:138` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：flags[field] == 1 |  |  |  |  |  |
|  | 原因：人工复核：0/1 动作字段到类型化动作的映射属于持久化合同 |  |  |  |  |  |
| `decision.262139b3d06393b5fc4f` | `api/admin/chat_config_routes.py:260` | `python.literal_mapping` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：result = { "chat_stream_id": sid, "talk_value": 0.5, "mentioned_bot_reply": True, "use_expression": False, "enable_expression_learning": False, "enable_jargon_learning": False, "group_profile_mode": "off", "planner_smooth": 3, "legacy_grou… |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6ca795304a0ab7091053` | `api/admin/chat_config_routes.py:398` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(row.match_type or "") == "regex" |  |  |  |  |  |
|  | 原因：人工复核：识别既有 regex 仅用于执行旧规则单向关闭迁移 |  |  |  |  |  |
| `decision.0abdb1fef12979b214c8` | `api/admin/chat_config_routes.py:399` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：body.enabled == 0 |  |  |  |  |  |
|  | 原因：人工复核：只允许关闭既有 Web regex 是旧规则迁移期的单向兼容门禁 |  |  |  |  |  |
| `decision.05ec86e829e3aa58963f` | `api/admin/group_memory_routes.py:31` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.get( "/groups/{group_id:path}/memories", operation_id="adminGroupMemoriesListLegacy", response_model=GroupMemoryListResponse, responses=standard_error_responses(401, 422), deprecated=True, ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.f85079b308f26939f9cb` | `api/admin/group_memory_routes.py:76` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.get( "/group-memories/overview", operation_id="adminGroupMemoryOverview", response_model=GroupMemoryOverviewResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.f480bf09d07d72e176bd` | `api/admin/group_memory_routes.py:99` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.get( "/group-memories/{group_id:path}/items", operation_id="adminGroupMemoryItems", response_model=GroupMemoryListResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.0d0d86a26d8920c8ccf4` | `api/admin/group_memory_routes.py:115` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.post( "/group-memories/{group_id:path}/extract", operation_id="adminGroupMemoryExtract", response_model=GroupMemoryExtractResponse, responses=standard_error_responses( 400, 401, 404, 409, 422, 500, 502, ), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.7d99eeee83da2ea9c798` | `api/admin/group_memory_routes.py:140` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.put( "/group-memories/{group_id:path}/injection-config", operation_id="adminGroupMemoryInjectionConfig", response_model=GroupMemoryInjectionConfigResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.349a3f8fce8f7c4344eb` | `api/admin/group_memory_routes.py:184` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.post( "/group-memories/{group_id:path}/injection-preview", operation_id="adminGroupMemoryInjectionPreview", response_model=GroupMemoryInjectionPreviewResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.5376e580c0edb6040fb9` | `api/admin/group_memory_routes.py:219` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.patch( "/group-memories/items/{memory_id}", operation_id="adminGroupMemoryUpdateItem", response_model=GroupMemoryUpdateResponse, responses=standard_error_responses( 400, 401, 404, 409, 422, ), ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.d850df81268084bf8747` | `api/admin/group_memory_routes.py:333` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要：router.post( "/groups/{group_id:path}/memories/extract", operation_id="adminGroupMemoriesExtractLegacy", response_model=GroupMemoryExtractResponse, responses=standard_error_responses( 400, 401, 404, 409, 422, 500, 502, ), deprecated=True, ) |  |  |  |  |  |
|  | 原因：人工复核：群记忆路由和 operation ID 由 Endpoint Registry 管理，是显式版本化的公开协议资源 |  |  |  |  |  |
| `decision.f23e287ed444a0261c3f` | `api/admin/log_routes.py:47` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：router.get( "/audit-logs", operation_id="adminAuditLogsList", response_model=AuditLogListResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.7f6bd6fff1fadfa92981` | `api/admin/log_routes.py:80` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：router.get("/logs") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.22bb74f3797abb8aa01d` | `api/admin/log_routes.py:88` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：fname not in [f["name"] for f in files] |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.1c708ec14f91c12c6590` | `api/admin/log_routes.py:101` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：router.post("/logs/frontend-error") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.5bf1e481390283342872` | `api/admin/log_routes.py:111` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：".log." in n |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.9849f0499dc8cf453c5b` | `api/admin/log_routes.py:111` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：n.startswith("nanobot.log.") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.f8f88e7d96df2f069681` | `api/admin/log_routes.py:111` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：n.endswith(".log") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.fb5c6c3fbc3eb22b8fbd` | `api/admin/log_routes.py:111` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：n == "nanobot.log" |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.9de5985c908105ba7f72` | `api/admin/log_routes.py:114` | `python.regex_call` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3}\s+\[(?P<level>[A-Z]+)\]") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.38336aa203f7e06745bd` | `api/admin/log_routes.py:118` | `python.regex_call` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：_LOG_START_RE.match(line or "") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.5dd29a32ccd66d128806` | `api/admin/log_routes.py:149` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：router.get("/logs/{name}") |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.0f68974a61eee2f99310` | `api/admin/log_routes.py:186` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：lines_text == "all" |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.fff58d5b81a7967d3aa8` | `api/admin/log_routes.py:195` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(level or "").upper() == "ERROR" |  |  |  |  |  |
|  | 原因：人工复核：审计与日志路由、日志文件名和行格式判断是版本化协议与资源语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.7a543215b0b7c9150e02` | `api/admin/model_preset_routes.py:1013` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：model == "未指定" |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.54225895d77b8fa254cc` | `api/admin/model_routes.py:139` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：rk == "classifier_legacy" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6ab1ad985dbf35c80593` | `api/admin/outbound_delivery_routes.py:450` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：ProactiveOutreachLog.status == "legacy_ambiguous_hold" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.977c501b8afbd22c5090` | `api/admin/reply_routes.py:426` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：category in {"被叫到", "直接问题", "情绪低落", "技术求助", "身份试探"} |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.c0a0d839ec2d68dc5b14` | `api/admin/tool_routes.py:46` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：raw.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.293b085dc11fe7b9460e` | `api/admin/tool_routes.py:48` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：raw.endswith(":group") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.2df4d19b1c758306b3c2` | `api/admin/tool_routes.py:48` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：raw.startswith("qq:") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.f596aa95b1cc6ab15dc4` | `api/admin/tool_routes.py:68` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：lowered.startswith("private_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.4ef9dc7f55417ba8b8cd` | `api/admin/tool_routes.py:72` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要："local_test" in lowered |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.8c3b2d20d3719c14bb65` | `api/admin/tool_routes.py:72` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：lowered.endswith("_test") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.c083518a9165fbb8a28e` | `api/admin/tool_routes.py:85` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.get( "", operation_id="adminToolsList", response_model=ToolListResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.c31b4c56bb7ab1d88bfe` | `api/admin/tool_routes.py:189` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：name in ("memory_read", "memory_write") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.2a8dd00e78c1ffaf3b44` | `api/admin/tool_routes.py:243` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.get( "/targets", operation_id="adminToolTargetsList", response_model=ToolTargetsResponse, responses=standard_error_responses(401, 422), ) |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.1df38eec6b2672d4d6d5` | `api/admin/tool_routes.py:252` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type == "platform" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.50678e579de9f4877575` | `api/admin/tool_routes.py:254` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type == "user" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.36ae319066d86ab445e2` | `api/admin/tool_routes.py:262` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "platform" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.52354ba6396434b538c9` | `api/admin/tool_routes.py:296` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：ToolOverride.scope_type == "platform" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.616bf7a6fa3e57ad1fda` | `api/admin/tool_routes.py:307` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "group" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.5ba06f504ba39bdedc6e` | `api/admin/tool_routes.py:310` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "group" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.5b5fc1ff80a277e1a724` | `api/admin/tool_routes.py:312` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：raw_id.startswith("qq:") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.74a5fc6abe3873f38440` | `api/admin/tool_routes.py:312` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：raw_id.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.c796f49fa13d40805489` | `api/admin/tool_routes.py:312` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "user" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.5e587a5eb8b65f354191` | `api/admin/tool_routes.py:317` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "group" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.d4a22f78ba7dad560e2e` | `api/admin/tool_routes.py:335` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "group" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.3af0f59b26d125b6cc69` | `api/admin/tool_routes.py:337` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：recent_at > old["_recent_at"] |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.4c8b81299d8fb78c9c92` | `api/admin/tool_routes.py:352` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope == "group" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.5ccbc99969a626a564eb` | `api/admin/tool_routes.py:366` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：uid.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.e5433b6a8ab0dd8384f0` | `api/admin/tool_routes.py:373` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：uid.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.baee268a62b619a5a86f` | `api/admin/tool_routes.py:375` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：sid.startswith("private_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.4c8419337c509ddbd0bf` | `api/admin/tool_routes.py:383` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：uid.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.3e7718c5538534968667` | `api/admin/tool_routes.py:385` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：sid.startswith("private_") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.6d2eb9c5e1ce5f3e6dfb` | `api/admin/tool_routes.py:401` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.get("/effective") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.694b4e4e4d82131328d9` | `api/admin/tool_routes.py:442` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.get("/decisions") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.98862f0a2bdbb05822bd` | `api/admin/tool_routes.py:470` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.get("/{tool_name}/schema") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.f7ce045ba9a791695bbf` | `api/admin/tool_routes.py:481` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.put("/{tool_name}/schema") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.1a68a71b796dac686790` | `api/admin/tool_routes.py:503` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.delete("/{tool_name}/schema") |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.164763293e4ddf056737` | `api/admin/tool_routes.py:523` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.put( "/{tool_name}", operation_id="adminToolDefaultsUpdate", response_model=ToolMutationResponse, responses=standard_error_responses( 400, 401, 404, 409, 422, ), ) |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.36216c05a3bc39efe818` | `api/admin/tool_routes.py:607` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.put( "/{tool_name}/override", operation_id="adminToolOverrideSet", response_model=ToolMutationResponse, responses=standard_error_responses( 400, 401, 404, 409, 422, ), ) |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.1e4ad6ef00c4b86c5220` | `api/admin/tool_routes.py:631` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type not in {"group", "user", "chat_type", "platform"} |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.13b0b38c67e5f8137d49` | `api/admin/tool_routes.py:633` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type == "chat_type" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.6574f85856d5392d4735` | `api/admin/tool_routes.py:633` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_id not in {"private", "private_superuser", "group"} |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.a92a5008f3cc28d2a21a` | `api/admin/tool_routes.py:635` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type == "platform" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.e93219c7be359eea07bf` | `api/admin/tool_routes.py:639` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type in {"group", "user"} |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.5375116032a0e688d760` | `api/admin/tool_routes.py:669` | `python.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：router.delete( "/{tool_name}/override", operation_id="adminToolOverrideDelete", response_model=ToolMutationResponse, responses=standard_error_responses( 401, 404, 409, 422, ), ) |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.66028dadc80edb0eda09` | `api/admin/tool_routes.py:691` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：scope_type == "platform" |  |  |  |  |  |
|  | 原因：人工复核：工具管理路由、作用域枚举和兼容身份语法属于 Endpoint Registry 管理的公开协议资源 |  |  |  |  |  |
| `decision.eef589ef7fb11488e810` | `api/endpoint_contracts.py:16` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$") |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.6dd7b1780ea67345df03` | `api/endpoint_contracts.py:17` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[a-z][a-z0-9_.-]{2,127}$") |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.c450b7ce1e8a435e354c` | `api/endpoint_contracts.py:84` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_CONTRACT_ID_RE.fullmatch(self.contract_id) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.7648d87ac8a3c52f3edd` | `api/endpoint_contracts.py:86` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_OPERATION_ID_RE.fullmatch(self.operation_id) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.30ae87c0547d70211760` | `api/endpoint_contracts.py:88` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_OPERATION_ID_RE.fullmatch(self.client_function) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.853774f8f459b8862ba5` | `api/endpoint_contracts.py:92` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.path.startswith("/api/") |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.869ef50ca2a1bd6ddcc9` | `api/endpoint_contracts.py:98` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.pagination not in { "none", "limit", "page_limit", "cursor_limit", } |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.747ababddff7a2f6d3ae` | `api/endpoint_contracts.py:108` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：status < 400 |  |  |  |  |  |
|  | 原因：人工复核：HTTP 错误状态范围下界属于协议定义，不是可配置业务阈值 |  |  |  |  |  |
| `decision.874691bdedd7bc88a772` | `api/endpoint_contracts.py:108` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：status > 599 |  |  |  |  |  |
|  | 原因：人工复核：HTTP 错误状态范围上界属于协议定义，不是可配置业务阈值 |  |  |  |  |  |
| `decision.d05f11c4665bc3902cfa` | `api/endpoint_contracts.py:146` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：method not in {"HEAD", "OPTIONS"} |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.39ae0cca244301e49c8b` | `api/endpoint_contracts.py:149` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.sub( r"[^A-Za-z0-9]+", "_", route.path_format.strip("/"), ) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.6765524a5e706d899c10` | `api/endpoint_contracts.py:154` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.sub( r"[^A-Za-z0-9]+", "_", str(route.name or "endpoint"), ) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.9abae2bae411bb991791` | `api/endpoint_contracts.py:163` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.sub( r"[^A-Za-z0-9]+", "_", str(path or "").strip("/"), ) |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.2bf548de52aedfb05088` | `api/endpoint_contracts.py:209` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(status).startswith("2") |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.21a7afe71d2ab02e4888` | `api/endpoint_contracts.py:231` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(status).startswith("2") |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.c887519ff2bae0b814a2` | `api/endpoint_contracts.py:306` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item.get("in") == "query" |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.775c5b57b0fb74bf8b51` | `api/endpoint_contracts.py:308` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：{"cursor", "limit"} <= names |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.91b449c46160d494d967` | `api/endpoint_contracts.py:310` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：{"page", "limit"} <= names |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.5281dcf93ff45f8e911b` | `api/endpoint_contracts.py:312` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要："limit" in names |  |  |  |  |  |
|  | 原因：人工复核：端点 ID、HTTP 方法、路径、分页与 OpenAPI 投影判断属于 Endpoint Contract 的确定性协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.c068f423a6617e3b74c8` | `api/endpoint_contracts.py:448` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：registry_generation > 0 |  |  |  |  |  |
|  | 原因：人工复核：Registry generation 必须为正是统一 Registry Kernel 的一致性合同 |  |  |  |  |  |
| `decision.a7844e81197b77c61b78` | `api/telemetry_middleware.py:25` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(request_id) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.5cb2a9702cfa097efb26` | `api/telemetry_middleware.py:26` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 33 |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.76d1e9d0fca8cbe05522` | `api/telemetry_middleware.py:26` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) > 126 |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.34b3e009f100260867be` | `api/telemetry_middleware.py:81` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：scope.get("type") != "http" |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.bbff5394c237ca1ea079` | `api/telemetry_middleware.py:101` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：message.get("type") == "http.response.start" |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.8b3b6d2f4353e4aa25ec` | `api/telemetry_middleware.py:140` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：status_code >= 500 |  |  |  |  |  |
|  | 原因：人工复核：Request ID 字符边界、ASGI HTTP 消息和 5xx 范围属于 HTTP Telemetry 的确定性协议合同 |  |  |  |  |  |
| `decision.e99ee3503e743c0e51b2` | `app/group_analysis/analyzer.py:127` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：route_key not in { "group_analysis_topics", "group_analysis_titles", "group_analysis_quotes", "group_analysis_quality", } |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.ab8fd5ab790104d7e8bb` | `app/group_analysis/analyzer.py:297` | `python.string_control_flow` | `presentation` | `resource` | 阶段 7A–7D | `reviewed` |
|  | 摘要："[图片" in content |  |  |  |  |  |
|  | 原因：人工复核：图片占位符属于群消息展示协议 |  |  |  |  |  |
| `decision.96141c78627e27780a46` | `app/group_analysis/analyzer.py:331` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：branch == "topics" |  |  |  |  |  |
|  | 原因：人工复核：topics 是群分析分支 ID |  |  |  |  |  |
| `decision.0e9980040d8b1baa0cc2` | `app/group_analysis/analyzer.py:447` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：branch == "topics" |  |  |  |  |  |
|  | 原因：人工复核：群分析按冻结 Aspect ID 选择固定 Task 分支，属于结构化执行协议，不判断自然语言语义 |  |  |  |  |  |
| `decision.d730d8b6fd5cf1cf2ba9` | `app/group_analysis/local_rag.py:63` | `python.literal_collection` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：generic_phrases = { "生成群日报", "群日报", "日报", "总结", "看看今天群里聊了什么", "看看群里聊了什么", "今天聊了什么", "最近聊了什么", "全部历史", } |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.0dcbb047395794541d53` | `app/group_analysis/local_rag.py:76` | `python.literal_collection` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：thematic_markers = ( "重点", "主题", "关于", "围绕", "只看", "筛选", "检索", "搜索", "专题", "分析", ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.ecdf1b01f94a800d3a77` | `app/group_analysis/preprocess.py:56` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：re.search(r"(全部\|全量\|所有\|不限\|不限制\|完整历史\|全部历史)", text) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.b9d36b2ad0307d4408c5` | `app/group_analysis/preprocess.py:58` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：re.search(r"最近\s*(\d+)\s*小时", text) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.f6b065e99940e30f1144` | `app/group_analysis/preprocess.py:61` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要：re.search(r"最近\s*(\d+)\s*天", text) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.23d013cec4216d13b53b` | `app/group_analysis/preprocess.py:92` | `python.literal_collection` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：_HTML_ARTIFACT_MARKERS = ( "<!doctype html", "<html", "<article", "<style", "group-analysis-report", "news-brief", "nanobot_reply_output", ) |  |  |  |  |  |
|  | 原因：人工复核：HTML 产物标记用于排除非聊天正文 |  |  |  |  |  |
| `decision.dd89fed3f17f69ed0506` | `app/group_analysis/preprocess.py:102` | `python.literal_collection` | `security_invariant` | `preserve` | 阶段 7A–7D | `reviewed` |
|  | 摘要：_INTERNAL_TEXT_PREFIXES = ( "[NO_SEND]", "[系统内部错误]", "[工具错误]", "Traceback", ) |  |  |  |  |  |
|  | 原因：人工复核：内部错误和控制前缀不得进入群学习输入 |  |  |  |  |  |
| `decision.32fc0076b928d58a991b` | `app/group_analysis/preprocess.py:126` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：value.strip().lower() in {"1", "true", "yes", "on", "是"} |  |  |  |  |  |
|  | 原因：人工复核：布尔文本是环境配置解析协议 |  |  |  |  |  |
| `decision.f2be9b91f78ddcf9cfbc` | `app/group_analysis/preprocess.py:338` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 7A–7D | `reviewed` |
|  | 摘要："回复" in (raw_content or "") |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.bfb268e779387baaf4c7` | `app/group_analysis/render.py:187` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要："topics" in selected_aspects |  |  |  |  |  |
|  | 原因：人工复核：报告按已校验 Aspect 集合决定固定区块可见性，属于结构化渲染协议 |  |  |  |  |  |
| `decision.7514aa89280d2bc2b2f8` | `app/group_learning/migration_audit.py:70` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：row.human_action in { "accept", "edit_accept", "create", "legacy_accept", "legacy_reject", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4feb43ca2b5f28c9c752` | `app/group_learning/migration_audit.py:81` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：row.source in {"legacy_expression", "legacy_jargon"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.5a1a1973ce90fe70c562` | `app/group_learning/migration_audit.py:265` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：row.human_action == "legacy_reject" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.d89a90c6238c4a3f9ed7` | `app/group_learning/migration_audit.py:277` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：row.source == "legacy_group_memory" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.cc8c25a3cd2137de550f` | `app/group_learning/schedule_service.py:70` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：value.strip().lower() in { "1", "true", "yes", "on", "是", } |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.27fed8eca6b6f2f3183b` | `app/group_memory/query_service.py:238` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：injected_at > str(row["last_injected_at"]) |  |  |  |  |  |
|  | 原因：人工复核：群记忆概览必须选择时间更晚的注入记录，属于稳定投影的一致性规则 |  |  |  |  |  |
| `decision.349f6a12171819409271` | `app/memory_digest/builder.py:31` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：_COMMAND_WORDS = { "签到", "打卡", "钓鱼", "千连钓鱼", "万连钓鱼", "抽卡", "十连抽卡", "千连抽卡", "万连抽卡", "宠物", "宠物系统", } |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.06a3d0ec235cec124409` | `app/memory_digest/builder.py:44` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：_STOPWORDS = { "这个", "那个", "今天", "一下", "可以", "不是", "就是", "感觉", "群里", "讨论", "效果", "图片", "http", "https", "www", "com", "video", } |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.928063c648060b530a8b` | `app/memory_digest/builder.py:86` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："日报" in c |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.9732ea37d032b99dc97a` | `app/memory_digest/builder.py:88` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："群聊分析" in c |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.7e43810be923587a73b9` | `app/memory_digest/builder.py:153` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.fullmatch(r"[\d\s.。!！?？哈啊哦嗯呃~～]+", content) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.12368737b66ebe65ba3c` | `app/memory_digest/builder.py:340` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.fullmatch(r"\[图片[:：]\d+张\]", normalized) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.16d10bd777cacb104429` | `app/memory_digest/builder.py:352` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.fullmatch(r"(十\|百\|千\|万\|\d+)?连?(钓鱼\|抽卡)", normalized) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.c8cd8446ddad9607838b` | `app/memory_digest/jobs.py:234` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(normalized_worker) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Memory Digest worker 标识长度是租约身份边界，必须确定性校验 |  |  |  |  |  |
| `decision.904300631f07db0dd374` | `app/memory_digest/jobs.py:435` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：MemoryDigestJob.locked_by == str(claim.worker_id or "") |  |  |  |  |  |
|  | 原因：人工复核：Memory Digest 结算必须匹配原 claim 的 worker 身份 |  |  |  |  |  |
| `decision.5683b56428f3e2195cd1` | `app/memory_digest/jobs.py:436` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：MemoryDigestJob.lease_token == str(claim.lease_token or "") |  |  |  |  |  |
|  | 原因：人工复核：Memory Digest 结算必须匹配不可伪造的 lease token |  |  |  |  |  |
| `decision.01b6e54bbad6dbe94ba2` | `app/memory_digest/jobs.py:437` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：MemoryDigestJob.attempt_count == int(claim.attempt_count or 0) |  |  |  |  |  |
|  | 原因：人工复核：Memory Digest 结算必须匹配 claim 的 attempt 代次 |  |  |  |  |  |
| `decision.6206e9cb497a74d55fe1` | `app/memory_digest/jobs.py:438` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：MemoryDigestJob.source_revision == str(claim.source_revision or "") |  |  |  |  |  |
|  | 原因：人工复核：Memory Digest 结算必须匹配 claim 的 source revision |  |  |  |  |  |
| `decision.17968bb9c71b94ba0a73` | `app/memory_digest/llm_builder.py:30` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile( r"(?:忽略\|无视).{0,12}(?:系统\|之前\|上述).{0,12}(?:指令\|提示)\|" r"ignore.{0,12}(?:previous\|system).{0,12}instructions?", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.b6d81767058028a90542` | `app/memory_digest/llm_builder.py:71` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^今天.*讨论") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.7c8e7ee19e569a8c42e2` | `app/memory_digest/llm_builder.py:72` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^用户希望.*更好") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.d5a9e471e79733f2d22c` | `app/memory_digest/llm_builder.py:73` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^需要优化") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.bb5ca16bcf94ad858675` | `app/memory_digest/llm_builder.py:74` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^本次(对话\|讨论\|会话).*围绕") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.1ecbbd032531a9aeae76` | `app/memory_digest/llm_builder.py:75` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^需要进一步.*(优化\|改进\|完善)") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.d096005ebe9077b1779c` | `app/memory_digest/llm_builder.py:76` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^讨论了.*相关") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.a8940af9c5119f1c8e14` | `app/memory_digest/llm_builder.py:77` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^用户.*(提出\|询问\|想知道)") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.e85607943a9a3e547acc` | `app/memory_digest/llm_builder.py:78` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"^系统.*(应该\|需要\|可以)") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.9e11150470913017b0d5` | `app/memory_digest/llm_builder.py:98` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.findall(r"[一-鿿]{2,}", text) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.076fd0b7710323cbdf09` | `app/memory_digest/llm_builder.py:100` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.findall(r"[一-鿿]{2,}", source_text) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.3ec46f98a1bce6cba4d1` | `app/memory_digest/retrieval_service.py:68` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：digest_status(meta) == "legacy" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.51c34ffa0c7759509179` | `app/persona/retrieval_service.py:203` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(getattr(row, "confidence", "") or "") == "归档" |  |  |  |  |  |
|  | 原因：人工复核：归档是画像事实状态，不是自然语言语义判断 |  |  |  |  |  |
| `decision.6bef7f69b6fb4e670dc4` | `app/persona/retrieval_service.py:207` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 3／4 | `reviewed` |
|  | 摘要：relevance < 0.15 |  |  |  |  |  |
|  | 原因：人工复核：相关度下限是检索策略阈值，应进入类型化策略配置 |  |  |  |  |  |
| `decision.2650cad6573be55a3a47` | `app/persona/retrieval_service.py:210` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：relevance <= 0 |  |  |  |  |  |
|  | 原因：人工复核：非正相关度是检索结果有效性边界 |  |  |  |  |  |
| `decision.af88d31aed6bfa0ced76` | `app/session_memory/jobs.py:46` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.job_id <= 0 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary 租约必须引用正整数 Job ID |  |  |  |  |  |
| `decision.1a75897e116913a36720` | `app/session_memory/jobs.py:48` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(worker_id) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary worker 标识长度是租约身份边界 |  |  |  |  |  |
| `decision.522439ed9bcfaa72612a` | `app/session_memory/jobs.py:50` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：len(owner_token) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary owner token 长度是租约身份边界 |  |  |  |  |  |
| `decision.098b9f4a28efbe32ae1e` | `app/session_memory/jobs.py:52` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.generation <= 0 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary 租约 generation 必须为正整数 |  |  |  |  |  |
| `decision.f968a3ff01c2d16f5998` | `app/session_memory/jobs.py:54` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.attempt_no <= 0 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary 租约 attempt 必须为正整数 |  |  |  |  |  |
| `decision.406932fd9dc169cdc889` | `app/session_memory/jobs.py:101` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(normalized) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary owner 标识长度是租约身份边界 |  |  |  |  |  |
| `decision.317d03057a1893b1e705` | `app/session_memory/jobs.py:103` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：Session Summary owner 标识拒绝控制字符以保护日志和租约字段 |  |  |  |  |  |
| `decision.791e19451dfebfc53171` | `app/session_memory/jobs.py:396` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：SessionSummaryJob.status == "running" |  |  |  |  |  |
|  | 原因：人工复核：只有 running Session Summary 才能被活动租约结算 |  |  |  |  |  |
| `decision.3992c0a53f0ba068fb65` | `app/session_memory/jobs.py:398` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：SessionSummaryJob.lease_token == "" |  |  |  |  |  |
|  | 原因：人工复核：历史 running 记录不得伪造空 lease token 为活动租约 |  |  |  |  |  |
| `decision.8a32ab1d64e852342f00` | `app/session_memory/jobs.py:615` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：SessionSummaryJob.status == "running" |  |  |  |  |  |
|  | 原因：人工复核：Session Summary 迁移只重排历史 running 状态 |  |  |  |  |  |
| `decision.874839ca1aeebc331744` | `app/session_memory/jobs.py:618` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：SessionSummaryJob.lease_token != "" |  |  |  |  |  |
|  | 原因：人工复核：只有携带 lease token 的历史 running 记录才可能表示活动租约 |  |  |  |  |  |
| `decision.639f0598afb68d1fe512` | `app/session_memory/jobs.py:624` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：SessionSummaryJob.lease_token == "" |  |  |  |  |  |
|  | 原因：人工复核：迁移后历史 running 记录不得保留空 token 的伪租约 |  |  |  |  |  |
| `decision.217155e7170bb1d1bcc8` | `app/session_memory/llm_contract.py:397` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：obligation.field == "legacy_summary" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.329904dc288d8670c4ba` | `app/session_memory/llm_contract.py:444` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：obligation.field == "legacy_summary" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.47de2bab1b938e209292` | `app/session_memory/llm_contract.py:534` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：obligation.field == "legacy_summary" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.cb3c1381ce97c1b7f6ee` | `app/session_memory/llm_contract.py:536` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：obligation.field == "legacy_summary" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.32d9b0f806a347d6cf94` | `app/session_memory/llm_summarizer.py:638` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：str(item).lower().startswith(("warning", "warn:", "警告")) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.3b51f163bd542c06679e` | `app/session_memory/rolling_summary.py:332` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："必须调用" in text |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.85d8f1c1d4e8c535af13` | `app/session_memory/rolling_summary.py:332` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："工具" in text |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.fde322c8cfe7bd7fdd50` | `app/session_memory/summarizer.py:58` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.sub(_URL_RE, "链接", str(text or ""), flags=re.IGNORECASE) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.9e2347b777e04c91cb3d` | `app/session_memory/summarizer.py:76` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.sub( r"代码兜底摘要：仅继承上次摘要正文；本轮新增内容见结构化字段，\s*" r"建议等待或手动生成 LLM 摘要提升质量。", "", value, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.d57e90d5e1ecdd996dbe` | `app/session_memory/summarizer.py:82` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.sub(r"(?m)^\s*此前已知:\s*$", "", value) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.ecb02dfba6b3deafcf16` | `app/session_memory/summarizer.py:83` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.sub( r"(?m)^\s*本轮新增\s+\d+\s+条消息" r"（用户\s+\d+\s+条、助手\s+\d+\s+条）。\s*$", "", value, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.a5a23ccaf8fafa403eff` | `app/session_memory/windowing.py:31` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile(r"\[用户名\]\s*([^\r\n\[]+)") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.f1e1ec136afe7c4efb48` | `bootstrap/job_adapters.py:119` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：claim.decision != "claimed" |  |  |  |  |  |
|  | 原因：人工复核：Job Lease Adapter 只能投影 decision=claimed 的活动 claim |  |  |  |  |  |
| `decision.1b2c68eeec60dd2c029c` | `clients/classifier_client.py:270` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^(是\|否)[,，](-?\d+)$") |  |  |  |  |  |
|  | 原因：人工复核：正则解析分类器的结构化输出兼容格式 |  |  |  |  |  |
| `decision.a91a03bfaba448933166` | `clients/classifier_client.py:788` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：result[k] not in ("", "未指定", 30) |  |  |  |  |  |
|  | 原因：人工复核：未指定值属于模型输出合同 |  |  |  |  |  |
| `decision.43bf656b8781ca81f20e` | `clients/classifier_client.py:925` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：m == "未指定" |  |  |  |  |  |
|  | 原因：人工复核：未指定标签属于模型输出合同 |  |  |  |  |  |
| `decision.baaa5fe18b85e158d74c` | `clients/classifier_client.py:1120` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：stripped in ("是", "是，") |  |  |  |  |  |
|  | 原因：人工复核：自由文本是标签属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.72d7232428298b311379` | `clients/classifier_client.py:1122` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：stripped in ("否", "否，") |  |  |  |  |  |
|  | 原因：人工复核：自由文本否标签属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.9e8edaf0af8157d0cefa` | `clients/classifier_client.py:1139` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：type_str == "否" |  |  |  |  |  |
|  | 原因：人工复核：自由文本类型标签属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.215f3c0d2b679b98779a` | `clients/classifier_client.py:1175` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：type_str == "否" |  |  |  |  |  |
|  | 原因：人工复核：自由文本类型标签属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.2910f749265a970c43e7` | `clients/new_api_client.py:839` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：hard_markers = ["设计", "证明", "推导", "架构", "审计", "优化", "debug", "reason", "analyze", "复杂", "proof"] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.189018f10cc2f9c290bd` | `clients/new_api_client.py:844` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：coding_markers = ["代码", "code", "python", "sql", "bug", "javascript", "typescript", "前端", "后端"] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.11bc8ea3473806ae0a9d` | `clients/new_api_client.py:849` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：easy_markers = ["翻译", "润色", "摘要", "改写", "hello", "hi", "你好", "解释一下"] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.90a867d5c507b97f5f32` | `clients/task_runtime_adapter.py:93` | `python.numeric_control_flow` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：exc.code in {401, 403} |  |  |  |  |  |
|  | 原因：人工复核：HTTP 401/403 到 authorization 的映射来自协议状态码，不读取响应正文 |  |  |  |  |  |
| `decision.f364f5f0bbf1d020f710` | `core/agent_runtime/extension_ports.py:180` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(compatibility) > 500 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.35c9f8bfc4189e1f95de` | `core/chat_stream_identity.py:19` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：normalized.startswith("group_") |  |  |  |  |  |
|  | 原因：人工复核：group_ 是入口 Adapter 明确支持的旧身份前缀，只用于兼容归一和用量观测 |  |  |  |  |  |
| `decision.cc15727203840059c661` | `core/chat_stream_identity.py:21` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：normalized.startswith("private_") |  |  |  |  |  |
|  | 原因：人工复核：private_ 是入口 Adapter 明确支持的旧身份前缀，只用于兼容归一和用量观测 |  |  |  |  |  |
| `decision.90c31aab6e0a1df0ea28` | `core/config_registry.py:89` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：mode not in {"disabled", "observation", "active"} |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.2bb09c3158cbc2479baa` | `core/config_registry.py:109` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：default_kind not in {"native", "kt"} |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.6482b45201662be4cc5c` | `core/config_registry.py:114` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要："*" in allowlist |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.e2cd34598b38a763b6a0` | `core/config_registry.py:114` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要："?" in allowlist |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.ec7febdaf357dde829ce` | `core/config_registry.py:116` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：default_kind == "kt" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.b285bea2e346029ab3bb` | `core/config_registry.py:1995` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_route_descriptor.inherits_from != "reply" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.b311d9dcf2b5deda403e` | `core/config_registry.py:2049` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_field == "temperature" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.2bb496715819bdcd3bc9` | `core/config_registry.py:2060` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_field == "temperature" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.dae06bbee217953780ff` | `core/config_registry.py:2061` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_field == "timeout" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.671ba1803851f9f59377` | `core/config_registry.py:2063` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_field == "temperature" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.b80da902b1dcbf99117e` | `core/config_registry.py:2064` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_field == "timeout" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.5f13cbd491b0243fead7` | `core/config_registry.py:2080` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_route_descriptor.inherits_from == "reply" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.b79aa7ab1e709629e053` | `core/config_registry.py:2111` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：descriptor.route_type != "controller" |  |  |  |  |  |
|  | 原因：人工复核：受管设置的枚举和值格式校验属于类型化配置合同 |  |  |  |  |  |
| `decision.98636769c55b8c6ac665` | `core/content_rules/adapters.py:23` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：numeric < 0 |  |  |  |  |  |
|  | 原因：人工复核：负数据库 ID 不能进入稳定规则标识 |  |  |  |  |  |
| `decision.8f7959bd73d986244532` | `core/content_rules/adapters.py:112` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：target_type == "group" |  |  |  |  |  |
|  | 原因：人工复核：group 是 UserBlockRule 的类型化目标范围 |  |  |  |  |  |
| `decision.37ea401471d40f8bd07d` | `core/content_rules/adapters.py:120` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：target_type == "private" |  |  |  |  |  |
|  | 原因：人工复核：private 是 UserBlockRule 的类型化目标范围 |  |  |  |  |  |
| `decision.9434b86100aa0af01deb` | `core/content_rules/adapters.py:124` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：target_type == "all" |  |  |  |  |  |
|  | 原因：人工复核：all 是 UserBlockRule 的类型化全局范围 |  |  |  |  |  |
| `decision.31a9a74fdd1323069170` | `core/content_rules/adapters.py:195` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(normalized_pattern) > 256 |  |  |  |  |  |
|  | 原因：人工复核：Web 规则模式的 256 字符上限是不可提升的安全子集边界 |  |  |  |  |  |
| `decision.951e860c60ac9a2cf250` | `core/content_rules/adapters.py:196` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要："\x00" in normalized_pattern |  |  |  |  |  |
|  | 原因：人工复核：Web 规则拒绝 NUL 用于保护存储和匹配边界 |  |  |  |  |  |
| `decision.4a64db4ae63b370b1b28` | `core/content_rules/contracts.py:12` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile( r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$" ) |  |  |  |  |  |
|  | 原因：人工复核：语义版本正则只验证 Descriptor 版本协议 |  |  |  |  |  |
| `decision.f87702232219263645d0` | `core/content_rules/contracts.py:85` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(char) < 32 |  |  |  |  |  |
|  | 原因：人工复核：规则元数据拒绝控制字符用于保护日志和 Registry 字段 |  |  |  |  |  |
| `decision.cd60cb94c8e3d6ed9610` | `core/content_rules/contracts.py:89` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_VERSION_PATTERN.fullmatch(self.version) |  |  |  |  |  |
|  | 原因：人工复核：Descriptor 版本必须满足语义版本协议 |  |  |  |  |  |
| `decision.9630ae61ef5b505f1aa9` | `core/content_rules/contracts.py:91` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要："\x00" in self.pattern |  |  |  |  |  |
|  | 原因：人工复核：Descriptor 模式拒绝 NUL 用于保护存储和匹配边界 |  |  |  |  |  |
| `decision.15c1c99ed6a6b24f1d80` | `core/content_rules/contracts.py:93` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(self.pattern) > 4096 |  |  |  |  |  |
|  | 原因：人工复核：规则模式的绝对长度上限用于限制资源消耗，不允许由调用方放宽 |  |  |  |  |  |
| `decision.4f2f06d72561e2d42d5c` | `core/content_rules/contracts.py:102` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 <= self.priority <= 1_000_000 |  |  |  |  |  |
|  | 原因：人工复核：priority 的整数范围属于 Descriptor 排序合同 |  |  |  |  |  |
| `decision.50bb4f62e618965de530` | `core/content_rules/contracts.py:105` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：1 <= self.input_max_length <= 1_000_000 |  |  |  |  |  |
|  | 原因：人工复核：规则输入的绝对长度范围用于阻止无界内容扫描 |  |  |  |  |  |
| `decision.b5bd4bb7517586b2f11f` | `core/content_rules/contracts.py:107` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：1 <= self.match_max_count <= 1000 |  |  |  |  |  |
|  | 原因：人工复核：单条规则命中数绝对上限用于阻止无界匹配 |  |  |  |  |  |
| `decision.368607204813f3b42d9b` | `core/content_rules/contracts.py:109` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：1 <= self.performance_budget_ms <= 1000 |  |  |  |  |  |
|  | 原因：人工复核：单条规则性能预算的绝对范围用于阻止无界执行 |  |  |  |  |  |
| `decision.fce5c5f05101248d6ef6` | `core/content_rules/contracts.py:116` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item not in {"private", "group"} |  |  |  |  |  |
|  | 原因：人工复核：private/group 是规则可声明的完整会话类型集合 |  |  |  |  |  |
| `decision.72ade8a82a0c25b2ec76` | `core/content_rules/engine.py:94` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：value.chat_type != "group" |  |  |  |  |  |
|  | 原因：人工复核：group 是 ContentRuleInput 的类型化会话范围 |  |  |  |  |  |
| `decision.9786eee8f3f8de8d4b45` | `core/content_rules/engine.py:184` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：count <= 0 |  |  |  |  |  |
|  | 原因：人工复核：非正命中数表示规则未命中，属于 Engine 结果合同 |  |  |  |  |  |
| `decision.28ab2c3f14d47d416325` | `core/context_compaction.py:56` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：re.compile( r"(?:ignore\|disregard\|forget).{0,48}(?:previous\|above\|system\|developer)" r"\|(?:忽略\|无视\|忘掉).{0,32}(?:上文\|之前\|系统\|开发者)", re.IGNORECASE \| re.DOTALL, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.6f0c654ea0001e31073c` | `core/context_compaction.py:66` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 8 | `auto_classified` |
|  | 摘要：re.compile( r"(?:call\|invoke\|run\|use).{0,24}(?:tool\|function)" r"\|(?:调用\|执行\|使用).{0,16}(?:工具\|函数)", re.IGNORECASE \| re.DOTALL, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.d97b5753cbeb4207a96d` | `core/context_legacy.py:64` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：row.role == "assistant" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7394f0a8e35745a4fecb` | `core/context_legacy.py:156` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(parts) <= 3 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4d04ce9359769d72985c` | `core/daily_digest.py:91` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：MODEL_HINTS = [ "qwen", "deepseek", "kimi", "gpt", "claude", "gemini", "llama", "mistral", "hunyuan", "glm", "通义", "豆包", "混元", "智谱", "阶跃", "minimax", ] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.76a46241e9db8ec981b3` | `core/daily_digest.py:282` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："日报" in c |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.ee061c0ba1dbf62f8028` | `core/daily_digest.py:284` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："群聊分析" in c |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.52a58c1f6f59aa737780` | `core/db/group_learning_adapter.py:62` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：source == "legacy_expression" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7d708d093b6a49ff4774` | `core/db/group_learning_adapter.py:560` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：GroupMemory.source != "legacy_group_learning_migration" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.cfff8078f42c594b1fb2` | `core/db/group_learning_governance_adapter.py:105` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：GroupLearningCandidate.candidate_id == str(candidate_id or "").strip() |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.e2e3d8919d9f55306497` | `core/db/group_learning_governance_adapter.py:184` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：GroupMemory.status == "active" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.ed825b91d39d63374e0b` | `core/db/group_learning_governance_adapter.py:198` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(row.approved_content_hash or "") != approved_content_hash |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.9ea3a01b59adb5753d67` | `core/db/group_learning_governance_adapter.py:216` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(row.chat_stream_id or "") != chat_stream_id |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.ac9425cd3c198c49e548` | `core/db/group_learning_governance_adapter.py:217` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(row.memory_type or "") != memory_type |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.be75ee9c412ff651980d` | `core/db/group_learning_governance_adapter.py:218` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(row.status or "") != "active" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.9af1e177b08876670f14` | `core/db/group_learning_governance_adapter.py:259` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：identity.chat_type != "group" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.34315223e6a3038cc2e1` | `core/db/group_learning_governance_adapter.py:276` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：int(row.chat_log_id or 0) > 0 |  |  |  |  |  |
|  | 原因：人工复核：只有正整数 ChatLog ID 才能进入正式记忆 evidence 列表，属于持久化数据合同 |  |  |  |  |  |
| `decision.d895f15da119257c6e7d` | `core/db/group_learning_governance_adapter.py:290` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.7b64a562487e3f7ebf82` | `core/db/group_learning_governance_adapter.py:318` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：approval_source == "model" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.df2b24d8970e54db9350` | `core/db/group_learning_governance_adapter.py:323` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：approval_source == "model" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.f075c420ac136ab26e3e` | `core/db/group_learning_governance_adapter.py:328` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.b9170375378b12290242` | `core/db/group_learning_governance_adapter.py:402` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action in {"merge_into", "add_alias"} |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.58061832a85c03bf6db0` | `core/db/group_learning_governance_adapter.py:405` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "merge_into" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.970201bf81c4226c0053` | `core/db/group_learning_governance_adapter.py:413` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "merge_into" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.cf08254525f0fe844ada` | `core/db/group_learning_governance_adapter.py:491` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "accepted" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.203486c501fa97b8aa40` | `core/db/group_learning_governance_adapter.py:496` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "merged" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.886b0825987f03835725` | `core/db/group_learning_governance_adapter.py:498` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "alias" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.7ba5bf97ebbbac5ce480` | `core/db/group_learning_governance_adapter.py:500` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "rejected" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.c440237cd80af6a08687` | `core/db/group_learning_governance_adapter.py:503` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "conflict" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.20743655cbd139dc2c9e` | `core/db/group_learning_governance_adapter.py:506` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.status == "waiting_for_evidence" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.6b62542808815f4e5325` | `core/db/group_learning_governance_adapter.py:520` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：identity.chat_type != "group" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.c574dacaf28c0ab3a8e5` | `core/db/group_learning_governance_adapter.py:536` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：run.status == "succeeded" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.fd5f7823762286a34d55` | `core/db/group_learning_governance_adapter.py:536` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：run.mode == "active" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.72bfa47f41f6f4fa670e` | `core/db/group_learning_governance_adapter.py:542` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：run.status != "succeeded" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.736807a96baa515a4e59` | `core/db/group_learning_governance_adapter.py:542` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：run.mode != "candidate_only" |  |  |  |  |  |
|  | 原因：人工复核：候选、目标、正式记忆、运行和游标必须满足同会话同类型及合法状态的一致性约束 |  |  |  |  |  |
| `decision.b74ec832ee3476539829` | `core/db/group_learning_governance_adapter.py:546` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：candidate.approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.734b0be6279ece24e0c2` | `core/db/group_learning_governance_adapter.py:555` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "reject" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.60e9cd236d56e37dff9d` | `core/db/group_learning_governance_adapter.py:566` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "conflict_with" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.9661594be851c01a7e3d` | `core/db/group_learning_governance_adapter.py:619` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(run.trigger or "") == "schedule" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.263e6e1a774914ffa68d` | `core/db/group_learning_governance_adapter.py:662` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "reject" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.7f03d5c17068200c67e0` | `core/db/group_learning_governance_adapter.py:673` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action in {"merge", "resolve_conflict"} |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.bc0e62128880f27d2dc4` | `core/db/group_learning_governance_adapter.py:680` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "merge" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.c87f48b24e7f079fc47f` | `core/db/group_learning_governance_adapter.py:695` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：resolution == "keep_target" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.0eef3c4ab8516ed9821f` | `core/db/group_learning_governance_adapter.py:782` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(memory.governance_mode or "") != "human_managed" |  |  |  |  |  |
|  | 原因：人工复核：候选动作、状态、来源、会话类型和治理模式分支属于冻结的群学习协议合同 |  |  |  |  |  |
| `decision.97cdb1848f1f539fccfe` | `core/db/group_learning_legacy_adapter.py:78` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row.chat_stream_id or "") != write.chat_stream_id |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.ea51b235bbcc8c710896` | `core/db/group_learning_legacy_adapter.py:79` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row.memory_type or "") != write.candidate_type |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.65f6f1b3f1881f06d419` | `core/db/group_learning_legacy_adapter.py:93` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：identity.chat_type != "group" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.76a1abdd1b3fac82a5a7` | `core/db/group_learning_legacy_adapter.py:155` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row.status or "") == "active" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1b10e06432c54e690bcd` | `core/db/group_learning_legacy_adapter.py:158` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row.inject_policy or "") != "manual_only" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.ce90dacf9aa4636476c7` | `core/db/group_learning_legacy_adapter.py:182` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：identity.chat_type != "group" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.28f810573b032e88434d` | `core/db/group_learning_legacy_adapter.py:211` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：item.planned_status == "accepted" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a149d84d159f5e673386` | `core/db/group_learning_legacy_adapter.py:244` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：item.planned_status == "accepted" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9b734f9ca9965be9c48f` | `core/db/group_learning_legacy_adapter.py:248` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：item.planned_status == "rejected" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4a748815b89d137e9e7a` | `core/db/group_learning_legacy_adapter.py:277` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 7A–7D | `reviewed` |
|  | 摘要：write.source == "legacy_group_memory" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1627c51bc8abe80e9f8b` | `core/db/group_learning_legacy_adapter.py:284` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：write.approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.b7ffd37fe0166a9c2768` | `core/db/group_learning_legacy_adapter.py:285` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：write.planned_status == "accepted" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.43a8492015211b0e4e56` | `core/db/group_learning_legacy_adapter.py:320` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：write.approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1c3635c64a82e058bbbb` | `core/db/group_learning_legacy_adapter.py:325` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：write.approval_source == "human" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.44801240e1194d1d14a4` | `core/db/group_learning_legacy_adapter.py:345` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：write.planned_status == "rejected" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.09dd7ffbc67d6074ec8d` | `core/db/group_memory_adapter.py:93` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：GroupMemory.chat_stream_id == str(chat_stream_id or "").strip() |  |  |  |  |  |
|  | 原因：人工复核：群记忆 SQL Adapter 按 canonical chat_stream_id 精确匹配授权数据 |  |  |  |  |  |
| `decision.1ce4afb6625ef2be5218` | `core/eval_sampling/timing_signal_audit.py:12` | `python.literal_collection` | `data_consistency` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：FALSE_POSITIVE_LABELS = {"false_positive", "fp", "误判", "假阳性"} |  |  |  |  |  |
|  | 原因：人工复核：评测标签是版本化数据枚举 |  |  |  |  |  |
| `decision.f486d36ca3e3d367b1db` | `core/eval_sampling/timing_signal_audit.py:13` | `python.literal_collection` | `data_consistency` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：TRUE_POSITIVE_LABELS = {"true_positive", "tp", "正确"} |  |  |  |  |  |
|  | 原因：人工复核：评测标签是版本化数据枚举 |  |  |  |  |  |
| `decision.b7f8eca4a6a55434cf4b` | `core/expression_memory.py:101` | `python.literal_collection` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：allowed = {"talk_value", "mentioned_bot_reply", "use_expression", "enable_expression_learning", "enable_jargon_learning", "planner_smooth"} |  |  |  |  |  |
|  | 原因：人工复核：允许更新字段属于配置写入合同 |  |  |  |  |  |
| `decision.5cf5ed11ac072f47bbb8` | `core/expression_memory.py:110` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：k in ("mentioned_bot_reply", "use_expression", "enable_expression_learning", "enable_jargon_learning") |  |  |  |  |  |
|  | 原因：人工复核：布尔配置字段属于配置写入合同 |  |  |  |  |  |
| `decision.f1e67e7508250a78b757` | `core/expression_memory.py:141` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：ExpressionMemory.status == "active" |  |  |  |  |  |
|  | 原因：人工复核：表达记忆激活状态属于持久化状态机 |  |  |  |  |  |
| `decision.a71df7669a44a64e3ff3` | `core/expression_memory.py:147` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：ExpressionMemory.scene == "" |  |  |  |  |  |
|  | 原因：人工复核：空场景是持久化查询条件 |  |  |  |  |  |
| `decision.fb3fade640bb79f3db9c` | `core/expression_memory.py:174` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：JargonMemory.status == "active" |  |  |  |  |  |
|  | 原因：人工复核：黑话记忆激活状态属于持久化状态机 |  |  |  |  |  |
| `decision.ec9e8c18ac35f26a244e` | `core/group_learning/aspects.py:70` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：self.lifecycle not in {"active", "deprecated", "retired"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.f018f2c696acb7cf0b65` | `core/group_learning/legacy_migration.py:97` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(target_type or "") != expected_target_type |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.c74f88edfe498e047be8` | `core/group_learning/legacy_migration.py:98` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(target_id or "") != str(int(legacy_id)) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9d31b41f304e31da716c` | `core/group_learning/legacy_migration.py:101` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：int(audit_log_id or 0) <= 0 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.87f5f18bc288fadece4f` | `core/group_learning/legacy_migration.py:111` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：detail.get("schema_version") != 1 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.aa03031093248c809fd2` | `core/group_learning/legacy_migration.py:112` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(detail.get("chat_stream_id") or "") != chat_stream_id |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.42df429aeef63a1add06` | `core/group_learning/legacy_migration.py:113` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(detail.get("content_hash") or "") != legacy_content_hash(content, meaning) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.95823a34cc5efe2effba` | `core/group_learning/rules.py:82` | `python.numeric_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 < float(self.performance_budget_ms) <= 100 |  |  |  |  |  |
|  | 原因：人工复核：群学习定义规则的正则编译只验证候选提取器，不能直接激活正式记忆 |  |  |  |  |  |
| `decision.18b4f62d24a33f1b07aa` | `core/group_learning/rules.py:176` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.finditer( r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{2,8}" r"(?![\u4e00-\u9fff])", text, ) |  |  |  |  |  |
|  | 原因：人工复核：群表达短语正则只能产出待审核候选，不能直接写入或注入 GroupMemory |  |  |  |  |  |
| `decision.03cf38a64f5aa675363e` | `core/group_learning/rules.py:191` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(descriptor.pattern, re.IGNORECASE) |  |  |  |  |  |
|  | 原因：人工复核：群学习定义模式的运行时编译只服务候选提取，最终语义由模型或人工审核 |  |  |  |  |  |
| `decision.35043f9f5092b32a71d5` | `core/group_learning/rules.py:199` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：compiled.finditer(bounded) |  |  |  |  |  |
|  | 原因：人工复核：群学习正则匹配只能生成 candidate/evidence 信号，不能自动激活正式记忆 |  |  |  |  |  |
| `decision.2e7d6ec83efc0608c19e` | `core/group_memory.py:126` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 7A–7D | `auto_classified` |
|  | 摘要：stopwords = {"的", "了", "是", "在", "和", "也", "都", "就", "不", "会", "很", "有", "这", "那", "群", "里", "经常", "喜欢", "大家", "比较", "觉得", "有点", "非常", "特别"} |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.b0c2e807ca161c2fa1bf` | `core/group_runtime/state.py:48` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：parse_quality == "legacy" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.077238be2c52247773ca` | `core/jobs/contracts.py:26` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) >= 32 |  |  |  |  |  |
|  | 原因：人工复核：Job 合同元数据清除控制字符以保护日志、Registry 和追踪字段 |  |  |  |  |  |
| `decision.6849527ffbfcdc3e8705` | `core/jobs/contracts.py:161` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.generation <= 0 |  |  |  |  |  |
|  | 原因：人工复核：通用 JobLease generation 必须为正整数 |  |  |  |  |  |
| `decision.9cff6bb6edb46dbe2570` | `core/jobs/contracts.py:167` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.attempt_no <= 0 |  |  |  |  |  |
|  | 原因：人工复核：通用 JobLease attempt 必须为正整数 |  |  |  |  |  |
| `decision.ec2452e3e1ae3d76ef0d` | `core/jobs/contracts.py:212` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.generation < 0 |  |  |  |  |  |
|  | 原因：人工复核：JobRecord generation 不得为负数 |  |  |  |  |  |
| `decision.6e37463c89de4c5090c7` | `core/jobs/contracts.py:218` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.attempt_count < 0 |  |  |  |  |  |
|  | 原因：人工复核：JobRecord attempt_count 不得为负数 |  |  |  |  |  |
| `decision.0bb7436cc8df86a8c960` | `core/legacy_adapter.py:92` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：log['role'] == 'user' |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.be6172aae6e87401db4a` | `core/legacy_adapter.py:102` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：Persona.status == "active" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1f0171e051e5003c07b5` | `core/legacy_adapter.py:142` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：ChatLog.processed == 0 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a1e5635e02422ae30bec` | `core/legacy_adapter.py:176` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：ChatLog.processed == 0 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.231bc9d0d20521a392a3` | `core/legacy_adapter.py:222` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："error" in data |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.cc9c2b6769c24acc02c9` | `core/legacy_adapter.py:222` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："code" in data |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.fc0d9b20954e3786f7e5` | `core/legacy_adapter.py:243` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(persona_obj.status or "") == "archived" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.35135e4ac41b03bc384c` | `core/legacy_adapter.py:331` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：provider_type == "dify" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.3b26cf633fdbe1b27e1c` | `core/legacy_adapter.py:385` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："choices" in resp |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.5c05276b90ce39012e8c` | `core/legacy_adapter.py:449` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(parts) > 1 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.02ca8b1a498b5d6ed32d` | `core/legacy_adapter.py:458` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：cmd == "vibe" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.01a0c226623fd1f15e4f` | `core/legacy_adapter.py:461` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：cmd == "ai_daily" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.8e5455aba55bb69f9384` | `core/legacy_adapter.py:478` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：func_name == "run_sql_analysis" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4d7e130ffe4a18c93704` | `core/legacy_adapter.py:480` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：func_name == "run_python_analysis" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.8d2418ded2191faf4d0c` | `core/legacy_adapter.py:484` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：func_name == "run_ai_daily" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.79f74a89bddb6a63eab9` | `core/legacy_adapter.py:496` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：query.startswith(f"/{t}") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.fd5bf2c06fd9864cae76` | `core/legacy_adapter.py:496` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：query.startswith("/") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.568839eadfc8dba0fdb3` | `core/legacy_adapter.py:520` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：round_idx == 0 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9a9c3c21e4d2e7fe959a` | `core/legacy_adapter.py:530` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："error" in resp |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4f60bb578206b5125bdb` | `core/legacy_adapter.py:642` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：int(stats.get("processing_errors", 0) or 0) > 0 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.091c19c1e65173f33349` | `core/legacy_adapter.py:693` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：raw_role == "user" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.aba9ffaeb380f2c49acb` | `core/legacy_adapter.py:697` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：role == "user" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.ffbd8e0238e1a48270ed` | `core/legacy_adapter.py:710` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(content) > 200 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.2cdf6631d725f8586f95` | `core/legacy_adapter.py:716` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：max_consecutive >= 5 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.2d33865cdd7690bf9bb6` | `core/legacy_adapter.py:716` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：correction_rate > 0.15 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.8ebaa2a2ecda5b15769f` | `core/legacy_adapter.py:716` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：long_exchanges >= 3 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.0acda1be70af59449d53` | `core/legacy_adapter.py:718` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：turn_count >= 3 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.228d53a55e63288e8982` | `core/legacy_adapter.py:752` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："搜集" in scout_info |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.b5ae8e82497e8d3eb5f2` | `core/legacy_adapter.py:752` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要："search" in scout_info.lower() |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a0572b4027722ed772a4` | `core/legacy_adapter.py:754` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(scout_info) > 5 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.31f2cca00767b7828fe0` | `core/lifecycle/compatibility_registry.py:55` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／7D | `reviewed` |
|  | 摘要：self.consecutive_zero_usage_days <= 0 |  |  |  |  |  |
|  | 原因：人工复核：零使用天数必须为正是 Compatibility 移除门禁自身的数据合同 |  |  |  |  |  |
| `decision.e5f7fbcd8ae13e087d4e` | `core/lifecycle/compatibility_registry.py:57` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／7D | `reviewed` |
|  | 摘要：self.minimum_full_releases <= 0 |  |  |  |  |  |
|  | 原因：人工复核：至少跨过一次完整发布是 Compatibility 移除门禁自身的数据合同 |  |  |  |  |  |
| `decision.681cb4a02a176eecb94d` | `core/lifecycle/compatibility_registry.py:125` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／7D | `reviewed` |
|  | 摘要：ord(char) < 32 |  |  |  |  |  |
|  | 原因：人工复核：拒绝控制字符用于阻止兼容元数据污染日志和协议字段 |  |  |  |  |  |
| `decision.099e1d343bb37a50a359` | `core/lifecycle/feature_registry.py:91` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(char) < 32 |  |  |  |  |  |
|  | 原因：人工复核：拒绝控制字符用于保护 Feature 生命周期元数据合同 |  |  |  |  |  |
| `decision.48d58a7342b7e4a1053f` | `core/lifecycle/feature_registry.py:223` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：tool.availability_policy == "force_disabled" |  |  |  |  |  |
|  | 原因：人工复核：force_disabled 是 Tool Descriptor 的确定性可用性状态，不是自然语言判断 |  |  |  |  |  |
| `decision.b838399c5c6154d3238a` | `core/llm_request_linter.py:78` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：content.lstrip().startswith("## 交互定位") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.09b142f89637ea94079c` | `core/llm_request_linter.py:84` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："## 私聊行为" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.95451a8a6627a560a278` | `core/llm_request_linter.py:87` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："本轮只随口接一句" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.fa2dbe5381259430fd28` | `core/llm_request_linter.py:88` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："本轮简短处理" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.b586998d9cbc411e8829` | `core/llm_request_linter.py:89` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："本轮认真处理" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.099b79c6560aa1c4e9f9` | `core/llm_request_linter.py:92` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："群聊行为" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.2cb77e819437e06402c8` | `core/llm_request_linter.py:94` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："群聊上下文使用规则" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.b89c4f2783039300b28a` | `core/llm_request_linter.py:104` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："运行时上下文" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.6056da01746c08f06589` | `core/llm_request_linter.py:108` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："你刚才没有调用 reply 或 no_reply 工具" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.1bdc21c6c24bda9c37e6` | `core/llm_request_linter.py:115` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："系统生成的上下文提示，不是用户发言" in content |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.69b5aef477f096f198ab` | `core/llm_request_linter.py:216` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 8 | `reviewed` |
|  | 摘要：src["source"] == "legacy_runtime_tool_prompt" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7c906ddab2af1566ebc6` | `core/model_provider/provider_config.py:496` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：explicit_sources = { "database", "environment", "legacy_database", "legacy_settings", "settings", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7d02bb991466f2c7da64` | `core/model_provider/provider_config.py:517` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：key_source not in { "database", "legacy_database", "environment", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.74f85ea7d0aa0c1bff47` | `core/model_route_health.py:160` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：target_model not in {"", "未指定", "*"} |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.0f3971b83caf88152f6e` | `core/modules/contracts.py:102` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／7D | `reviewed` |
|  | 摘要：self.lifecycle not in {"active", "deprecated"} |  |  |  |  |  |
|  | 原因：人工复核：模块生命周期枚举是确定性状态合同，不是旧兼容路径 |  |  |  |  |  |
| `decision.909ca7767d7315472416` | `core/news/policy.py:56` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0 <= float(value) <= 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.c845447b1232c22155d3` | `core/news/policy.py:58` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：sum(weights) > 1.000001 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.4f584aa54c0297bb3b0b` | `core/news/policy.py:61` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0 <= self.review_boundary_min <= self.review_boundary_max <= 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.4993fd4a2deafa604200` | `core/news/policy.py:67` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0 < self.failure_downrank_factor <= 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.cd332429bec9ff4ea92b` | `core/news/policy.py:69` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：min( self.latest_hours, self.daily_freshness_hours, self.top_story_hours, self.per_source_quota, self.max_final_clusters, self.max_articles_per_domain, self.max_clusters_per_domain, self.max_same_entity_clusters, ) <= 0 |  |  |  |  |  |
|  | 原因：人工复核：新闻窗口和配额必须为正是 NewsRankingPolicy 的有效状态合同 |  |  |  |  |  |
| `decision.0a1d9305be522649fbd5` | `core/news/policy.py:80` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0 <= self.cluster_similarity_threshold <= 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.02f8b18bfcb5c1c4f69d` | `core/news/review.py:48` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0 <= threshold <= 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.f1d8d5b07588681d095b` | `core/news/review.py:52` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：1 <= int(self.max_batch_size) <= 40 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.2316ddeb4c680ee1b664` | `core/news/review.py:202` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：( candidate_id := str(getattr(item, "id", "") or "") ) in assessments |  |  |  |  |  |
|  | 原因：人工复核：审核结果只能关联当前批次候选 ID，属于批量结果一致性合同 |  |  |  |  |  |
| `decision.a26b805acc9e440cc5b0` | `core/news/review.py:301` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：reason_code == "clear_non_ai" |  |  |  |  |  |
|  | 原因：人工复核：只有结构化 clear_non_ai 高置信证据才允许删除候选，是审核后置门禁 |  |  |  |  |  |
| `decision.b3ab967e4f3ef6559bb0` | `core/news/signals.py:22` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"(?:\b(?:llm\|gpt\|claude\|gemini\|qwen\|deepseek\|mistral\|" r"llama\|grok\|kimi\|transformer\|embedding)\b\|" r"大模型\|人工智能\|多模态\|智能体\|模型权重)", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.08d587f311efba3c0f2a` | `core/news/signals.py:31` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"(?:\b(?:openai\|anthropic\|huggingface\|nvidia\|api\|token\|" r"benchmark\|fine[-. ]?tun(?:e\|ing)?)\b\|" r"模型发布\|推理服务\|算力\|微调\|开源模型)", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：人工复核：AI 产品、基础设施和发布词典只生成新闻候选信号，不得直接删除或激活候选 |  |  |  |  |  |
| `decision.f6426987ba81519061e1` | `core/news/signals.py:42` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"\b(?:clinical\|patient\|hospital\|surgery\|cancer\|drug\|" r"neuroscience\|diagnosis)\b", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：人工复核：医学词典只生成冲突信号，最终相关性由结构化审核决定 |  |  |  |  |  |
| `decision.6764f255842767f57c4f` | `core/news/signals.py:50` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"\b(?:oscar\|movie\|film\|actor\|music streaming)\b", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：人工复核：娱乐词典只生成负向候选信号，不能作为新闻删除规则 |  |  |  |  |  |
| `decision.6d794813d482fafad127` | `core/news/signals.py:105` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"\b[A-Z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*\b" ) |  |  |  |  |  |
|  | 原因：人工复核：实体候选正则只提取待审核实体，不判断实体是否属于 AI 领域 |  |  |  |  |  |
| `decision.8d5974f30c0798279db5` | `core/news/signals.py:164` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：_ENTITY_CANDIDATE.findall(text) |  |  |  |  |  |
|  | 原因：人工复核：实体候选扫描只产生模型审核输入，不承担最终相关性决策 |  |  |  |  |  |
| `decision.19cc496731a28a4add1c` | `core/news/signals.py:187` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：0.25 <= score <= 0.65 |  |  |  |  |  |
|  | 原因：人工复核：新闻权重、边界、批量大小和阈值属于集中 Policy 的可配置数值合同 |  |  |  |  |  |
| `decision.ce36104017a83296f347` | `core/news/source_registry.py:84` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：来源 Descriptor 禁止控制字符，防止资源、日志和错误字段注入 |  |  |  |  |  |
| `decision.d14c600def9446d104b2` | `core/news/source_registry.py:154` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：parsed.scheme != "https" |  |  |  |  |  |
|  | 原因：人工复核：新闻来源只允许 HTTPS，是固定来源 Registry 的网络安全边界 |  |  |  |  |  |
| `decision.fd178d9435cb25bc92c2` | `core/news/source_registry.py:189` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：lifecycle == "retired" |  |  |  |  |  |
|  | 原因：人工复核：retired 来源不可启用是 Source Descriptor 生命周期合同 |  |  |  |  |  |
| `decision.5dbb986af4d5063d9ae9` | `core/news/source_registry.py:381` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.lifecycle == "retired" |  |  |  |  |  |
|  | 原因：人工复核：retired 来源不可被 operator override 重新启用 |  |  |  |  |  |
| `decision.06899f737df52e1a8033` | `core/news/source_registry.py:385` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："enabled" in override |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.efca9d768840e464ec68` | `core/news/source_registry.py:390` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："enabled" in override |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.85d8f5591acec70c40ec` | `core/news/source_registry.py:392` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："quality_weight" in override |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.27b46535c3d8626a7206` | `core/news/source_registry.py:399` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："fetch_timeout_seconds" in override |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.0dd87bd6574b61241fa3` | `core/news/source_registry.py:408` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："per_run_limit" in override |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.c0730144a414f31fecc7` | `core/news/source_registry.py:458` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.lifecycle != "retired" |  |  |  |  |  |
|  | 原因：人工复核：Runtime 来源选择必须排除 retired 生命周期状态 |  |  |  |  |  |
| `decision.ec7c66697a001e780503` | `core/news/source_registry.py:485` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：set(raw) != { "schema_version", "resource_version", "sources", } |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.445f2c7820f5772afd71` | `core/news/source_registry.py:491` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：raw["schema_version"] != 1 |  |  |  |  |  |
|  | 原因：人工复核：新闻核心模块中的模式、结构化审核字段与来源描述符校验属于确定性协议合同 |  |  |  |  |  |
| `decision.68e5e2f896cf7bd8b067` | `core/outbound/control.py:253` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：control.mode != "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.d69193512ef40052db52` | `core/outbound/control.py:263` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：control.mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1ccbbdb5ddeeaad14eec` | `core/outbound/control_transitions.py:44` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.84b9cd92acf06a4b150b` | `core/outbound/control_transitions.py:63` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9c40e7d5829f572daef5` | `core/outbound/control_transitions.py:86` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9fe2159bf3e6e7681df3` | `core/outbound/control_transitions.py:238` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：(old_mode, target_mode) in { ("legacy_direct", "outbox_hold"), ("outbox_active", "outbox_draining"), } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6ed46cc2cbf7fad6d680` | `core/outbound/control_transitions.py:252` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：old_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7205b7fc430df312b73f` | `core/outbound/control_transitions.py:281` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：(old_mode, target_mode) in { ("legacy_direct", "outbox_hold"), ("outbox_draining", "legacy_direct"), } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.205fd64539190013006f` | `core/outbound/delivery_claims.py:94` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：run.delivery_mode != "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.9d21c976b98d8e1ddefb` | `core/outbound/delivery_claims.py:104` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.4ed524d98ec36907ea62` | `core/outbound/delivery_claims.py:179` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：run.delivery_mode != "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.682aae5d2116d209c042` | `core/outbound/delivery_claims.py:225` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：run.delivery_mode != "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a195c0732b763b36d0c2` | `core/outbound/delivery_claims.py:250` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.34d90755ee42fdd39ec0` | `core/outbound/delivery_claims.py:376` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.819fa40f303eff4c3783` | `core/outbound/delivery_claims.py:526` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：mode not in {"legacy_direct", "outbox"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7af76039026283bcb255` | `core/outbound/delivery_claims.py:1175` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row.status or "") in { "cancelled", "legacy_ambiguous_hold", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.b0a9913e59d28c13e893` | `core/outbound/delivery_claims.py:1347` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：row.status != "legacy_ambiguous_hold" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.dd84d61a999879828363` | `core/outbound/delivery_claims.py:1352` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：ProactiveOutreachLog.status == "legacy_ambiguous_hold" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.77fd685a00732a247514` | `core/outbound_delivery_service.py:475` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：category == "transient" |  |  |  |  |  |
|  | 原因：人工复核：transient 到稳定 Delivery failure code 的映射属于投递结果协议 |  |  |  |  |  |
| `decision.617cd0e49725ae5e7f67` | `core/outbound_delivery_service.py:477` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：category == "ambiguous" |  |  |  |  |  |
|  | 原因：人工复核：ambiguous 到稳定 Delivery failure code 的映射属于投递结果协议 |  |  |  |  |  |
| `decision.84fdcf3e4c8920610e6d` | `core/outbound_delivery_service.py:479` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：category == "success" |  |  |  |  |  |
|  | 原因：人工复核：success 不产生失败码是投递 Telemetry 的确定性协议 |  |  |  |  |  |
| `decision.be7c037b8cc09553c003` | `core/persisted_content.py:29` | `python.regex_call` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_INLINE_DATA_RE.sub("[内联二进制已移除]", text) |  |  |  |  |  |
|  | 原因：人工复核：所有消息历史必须移除内联二进制，不能把大结果或秘密载荷当作正文持久化 |  |  |  |  |  |
| `decision.a5cd840ac1c93dcd84a1` | `core/persisted_content.py:30` | `python.regex_call` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_FILE_URI_RE.sub("[宿主文件路径已移除]", text) |  |  |  |  |  |
|  | 原因：人工复核：消息历史必须移除 file URI，避免泄露宿主文件系统边界 |  |  |  |  |  |
| `decision.fd5cea9e6c0cd7f54688` | `core/persisted_content.py:31` | `python.regex_call` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_WINDOWS_HOST_PATH_RE.sub("[宿主文件路径已移除]", text) |  |  |  |  |  |
|  | 原因：人工复核：消息历史必须移除 Windows 宿主路径，稳定引用只能使用 Artifact 合同 |  |  |  |  |  |
| `decision.0ebb0c6519ab9cdf6417` | `core/persisted_content.py:32` | `python.regex_call` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_POSIX_HOST_PATH_RE.sub("[宿主文件路径已移除]", text) |  |  |  |  |  |
|  | 原因：人工复核：消息历史必须移除 POSIX 宿主路径，模型只能看到受控虚拟路径和 Artifact 引用 |  |  |  |  |  |
| `decision.60013418eaf1d57b0775` | `core/persona_preprocess.py:181` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：should_store.strip().lower() not in {"false", "0", "no", "否"} |  |  |  |  |  |
|  | 原因：人工复核：布尔自由文本属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.cdfcded1abe02424c174` | `core/persona_preprocess.py:208` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：should_inject.strip().lower() not in {"false", "0", "no", "否"} |  |  |  |  |  |
|  | 原因：人工复核：布尔自由文本属于待迁移的旧模型输出格式 |  |  |  |  |  |
| `decision.3d9a217977577bc99ea9` | `core/persona_preprocess.py:279` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：legacy_type == "behavior" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.16bb7e064bf18cbfd0b5` | `core/persona_preprocess.py:281` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：legacy_type == "trait" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6361fa3c335324cf3796` | `core/persona_preprocess.py:371` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：PersonaFact.confidence != "归档" |  |  |  |  |  |
|  | 原因：人工复核：归档过滤属于画像事实状态机 |  |  |  |  |  |
| `decision.9098360a8ece82182a47` | `core/persona_preprocess.py:381` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.confidence == "归档" |  |  |  |  |  |
|  | 原因：人工复核：归档是画像事实状态 |  |  |  |  |  |
| `decision.bb4cf418e64eca54e487` | `core/persona_preprocess.py:384` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.confidence == "确认" |  |  |  |  |  |
|  | 原因：人工复核：确认是画像事实置信状态 |  |  |  |  |  |
| `decision.2c03a5274e6680b15679` | `core/persona_preprocess.py:385` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：row.confidence == "可能" |  |  |  |  |  |
|  | 原因：人工复核：可能是画像事实置信状态 |  |  |  |  |  |
| `decision.f2794cd3228202c78c1b` | `core/persona_preprocess.py:664` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：PersonaFact.confidence != "归档" |  |  |  |  |  |
|  | 原因：人工复核：归档过滤属于画像事实状态机 |  |  |  |  |  |
| `decision.ae6258e4d3ca1d52d8d0` | `core/persona_preprocess.py:676` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(f.confidence or "") == "待确认" |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.969c9bd9a13edfe63ab8` | `core/persona_preprocess.py:700` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：PersonaFact.confidence != "归档" |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.e248c9b51f4e762a77de` | `core/proactive/delivery.py:160` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：existing.status in { "sending", "queued", "delivering", "retry_wait", "sent", "sent_after_ambiguous_replay", "failed", "blocked", "ambiguous", "legacy_ambiguous_hold", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.afe823d61a9f364075a0` | `core/proactive/delivery.py:531` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：claim.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7fb4501b8bdd586f408a` | `core/proactive/delivery_runtime.py:133` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.7c421afc8b64e25071e3` | `core/proactive/generation.py:845` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：work.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.e161f66a15568b9830b8` | `core/proactive/model_policy.py:161` | `python.literal_collection` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：required_fields = { "should_reach_out", "reason", "next_intent", "outreach_kind", "research_query", "topic_type", "topic", "evidence_ids", } |  |  |  |  |  |
|  | 原因：人工复核：必填字段集合属于结构化模型输出合同 |  |  |  |  |  |
| `decision.56f170396e9f6c7e3dd4` | `core/proactive/model_policy.py:206` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type not in { "follow_up", "discovery", "status_check", "none", } |  |  |  |  |  |
|  | 原因：人工复核：主动外呼 topic_type 枚举属于结构化模型输出合同 |  |  |  |  |  |
| `decision.132f4f921a19ae74e294` | `core/proactive/model_policy.py:219` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type == "none" |  |  |  |  |  |
|  | 原因：人工复核：不发送决策的空选题约束属于结构化模型输出合同 |  |  |  |  |  |
| `decision.b0243f617426b59b9b91` | `core/proactive/model_policy.py:226` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type != "none" |  |  |  |  |  |
|  | 原因：人工复核：不发送决策的空选题约束属于结构化模型输出合同 |  |  |  |  |  |
| `decision.3720df5f37c7ecdc8ff1` | `core/proactive/model_service.py:127` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type == "follow_up" |  |  |  |  |  |
|  | 原因：人工复核：跟进选题只能映射开放话题证据，属于服务端事实合同 |  |  |  |  |  |
| `decision.5b6d1854614be7084530` | `core/proactive/model_service.py:134` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type == "discovery" |  |  |  |  |  |
|  | 原因：人工复核：探索选题只能映射有效画像事实，属于服务端事实合同 |  |  |  |  |  |
| `decision.6c43c766f99b505f85ec` | `core/proactive/model_service.py:141` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：topic_type == "status_check" |  |  |  |  |  |
|  | 原因：人工复核：状态检查只能映射已核验行动，属于服务端事实合同 |  |  |  |  |  |
| `decision.03885a80753f3086107d` | `core/proactive/orchestrator.py:253` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：retired != 1 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.b373e07199e739e0b6b5` | `core/proactive/orchestrator.py:344` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／7D | `reviewed` |
|  | 摘要：retired != 1 |  |  |  |  |  |
|  | 原因：人工复核：遗留强制候选的单行状态迁移必须满足 CAS，属于数据一致性合同 |  |  |  |  |  |
| `decision.87cc29573490f0f1a615` | `core/proactive/schedule_repository.py:176` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：status in { "sending", "queued", "delivering", "retry_wait", "sent", "sent_after_ambiguous_replay", "failed", "blocked", "ambiguous", "legacy_ambiguous_hold", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.436021170e2b7d8fbc18` | `core/proactive/scheduler.py:72` | `python.literal_collection` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：_CHECK_THRESHOLD_KEYS = ( "min_interval_min", "max_check_interval_min", "max_silence_min", "ambiguous_hold_min", "repeat_topic_cooldown_min", ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.72b67d4767c725caf6c6` | `core/proactive_diagnostics.py:9` | `python.literal_mapping` | `presentation` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：_GENERATION_ERROR_SUMMARIES = { "model_truncated": "主动外呼正文生成被截断", "model_finish_error": "主动外呼正文生成未正常结束", "empty_response": "主动外呼正文生成返回空正文", "contract_error": "主动外呼正文不符合生成契约", "quality_rejected": "主动外呼正文未通过质量复核", "generation_timeout": "主动外呼… |  |  |  |  |  |
|  | 原因：人工复核：错误摘要是面向运维的稳定展示资源 |  |  |  |  |  |
| `decision.91c634adb80d1ecbf975` | `core/proactive_diagnostics.py:25` | `python.literal_mapping` | `presentation` | `resource` | 阶段 3／4 | `reviewed` |
|  | 摘要：_JUDGEMENT_ERROR_SUMMARIES = { "model_error": "主动外呼 Judge 调用失败", "model_truncated": "主动外呼 Judge 返回被截断", "model_finish_error": "主动外呼 Judge 未正常结束", "empty_response": "主动外呼 Judge 返回空正文", "contract_error": "主动外呼 Judge 不符合判断契约", } |  |  |  |  |  |
|  | 原因：人工复核：错误摘要是面向运维的稳定展示资源 |  |  |  |  |  |
| `decision.0525b77c9409cd4996f3` | `core/proactive_outreach_schema.py:19` | `python.literal_collection` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_SQL_KEYWORDS = { "check", "collate", "current_date", "current_time", "current_timestamp", } |  |  |  |  |  |
|  | 原因：人工复核：SQL 关键字集合用于约束迁移表达式语法 |  |  |  |  |  |
| `decision.454fe9e3797fdbb266e4` | `core/proactive_research.py:102` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^\s*摘要:\s*(.*?)\s*$") |  |  |  |  |  |
|  | 原因：人工复核：摘要行是受控研究输出协议 |  |  |  |  |  |
| `decision.b2841372bad4bc21fc17` | `core/proactive_research.py:103` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^\s*时间:\s*(.*?)\s*$") |  |  |  |  |  |
|  | 原因：人工复核：时间行是受控研究输出协议 |  |  |  |  |  |
| `decision.a140cddf1176cf199482` | `core/proactive_research.py:586` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：stripped_lines.count(_RESULTS_MARKER) != 1 |  |  |  |  |  |
|  | 原因：人工复核：结果标记只能出现一次是输出合同约束 |  |  |  |  |  |
| `decision.dd20ab38c2a99f9af21e` | `core/prompt_v2/template_migration.py:389` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 8 | `reviewed` |
|  | 摘要：report.drift_status != "untracked_legacy" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a877f363d9b2b80010e8` | `core/prompt_v2/template_resolution.py:130` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 8 | `reviewed` |
|  | 摘要：baseline_report.drift_status != "untracked_legacy" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.96369b6f45154bb81ca1` | `core/prompt_v2/variables.py:108` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：normalized in { "tasks/classifier_legacy", "tasks/agent_subtask", "tasks/private_decision", "tasks/news_daily_quality", "tasks/news_relevance_review", "tasks/group_analysis_topics", "tasks/group_analysis_titles", "tasks/group_analysis_quot… |  |  |  |  |  |
|  | 原因：人工复核：共享 message 变量适用的 Task key 集合属于 Prompt Runtime 类型化协议 |  |  |  |  |  |
| `decision.308707ec7b6bc1b6ff38` | `core/qq_outbound_renderer.py:103` | `python.regex_call` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：_UNTRUSTED_CQ_FILE_RE.sub( "（文件消息已拒绝，请使用资产下载链接）", expanded, ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.de8470021ca17ec7bc09` | `core/route_metadata.py:55` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：_DEPRECATED_PROVIDERS: set[str] = {"local_qwen", "vision_qwen"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.296e7822ab49d31381ff` | `core/route_metadata.py:91` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：(provider_id or "").strip() in _DEPRECATED_PROVIDERS |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.59cb358a76ba12d9efe3` | `core/runtime/events.py:393` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：event_field.kind not in { "boolean", "count", "duration_ms", } |  |  |  |  |  |
|  | 原因：人工复核：只有 Descriptor 明确声明的数值型敏感名称字段可作为计数进入 Telemetry，字符串仍被拒绝 |  |  |  |  |  |
| `decision.9e3fcd85f7abb856573d` | `core/runtime/extensions.py:140` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 <= self.priority <= 1_000_000 |  |  |  |  |  |
|  | 原因：人工复核：Hook priority 范围属于 Descriptor 结构约束，不是运行时业务阈值 |  |  |  |  |  |
| `decision.23ecc2b371c622eae62c` | `core/runtime/extensions.py:156` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.output_contract != "none" |  |  |  |  |  |
|  | 原因：人工复核：Observer 无返回值属于只读 Hook 的确定性输出合同 |  |  |  |  |  |
| `decision.3b68c7f2229895f995a3` | `core/runtime/extensions.py:551` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(contracts) > 1 |  |  |  |  |  |
|  | 原因：人工复核：同一 Transform Pipeline 的输入输出合同必须唯一 |  |  |  |  |  |
| `decision.fca1c28f8a344d215324` | `core/sandbox/admin_operations.py:73` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：len(normalized_worker) > 128 |  |  |  |  |  |
|  | 原因：人工复核：Sandbox Admin worker 标识长度是租约身份边界 |  |  |  |  |  |
| `decision.9eca082101b144c19f32` | `core/sandbox/admin_operations.py:701` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：int(data.get("generation") or 0) != generation |  |  |  |  |  |
|  | 原因：人工复核：Sandbox Admin 操作结果必须匹配活动租约 generation |  |  |  |  |  |
| `decision.039e7fdb58b87b8a5019` | `core/scheduled_task_outbound.py:793` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：result.delivery_mode != "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.43a59c138f4a6b7ee85f` | `core/scheduled_task_outbound.py:924` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：OutboundRun.delivery_mode == "legacy_direct" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.0830480d746a45b603ce` | `core/schema_migrations.py:729` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：confidence == "可能" |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.53e276e59b27b959f6e3` | `core/schema_migrations.py:729` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：confidence == "确认" |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.277a1cc40c89500a8a8b` | `core/schema_migrations.py:2468` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：str(row[0] or "").strip() not in legacy_values |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.87908051fec41570f536` | `core/semantic/jobs.py:362` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：SemanticIndexJob.lease_token == str(candidate.lease_token or "") |  |  |  |  |  |
|  | 原因：人工复核：Semantic Index 回收必须匹配原 lease token |  |  |  |  |  |
| `decision.a503906d57422ab73da4` | `core/semantic/jobs.py:363` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：SemanticIndexJob.locked_by == str(candidate.locked_by or "") |  |  |  |  |  |
|  | 原因：人工复核：Semantic Index 回收必须匹配原 worker 身份 |  |  |  |  |  |
| `decision.92c29c9b8de46e8092e1` | `core/semantic/jobs.py:365` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：SemanticIndexJob.attempt_count == int(candidate.attempt_count or 0) |  |  |  |  |  |
|  | 原因：人工复核：Semantic Index 回收必须匹配原 attempt 代次 |  |  |  |  |  |
| `decision.d447d701ca26918ff978` | `core/semantic/jobs.py:369` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：SemanticIndexJob.source_revision == str(candidate.source_revision or "") |  |  |  |  |  |
|  | 原因：人工复核：Semantic Index 回收必须匹配原 source revision |  |  |  |  |  |
| `decision.e154553f30d12cb8e801` | `core/settings_admin_service.py:25` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(key) > 255 |  |  |  |  |  |
|  | 原因：人工复核：系统设置键长度上限是持久化合同，不交由模型或运行时策略决定 |  |  |  |  |  |
| `decision.4df30648d30953d08692` | `core/settings_service.py:195` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：source == "legacy_database" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.f15bf39534d8a44d8ba5` | `core/settings_service.py:211` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：source == "legacy_environment" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.57757a0f714cf244cbe7` | `core/settings_service.py:314` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：resolved.source in {"environment", "legacy_environment"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.02cc7305e078e15937f3` | `core/settings_service.py:356` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：database_sources = {"database", "legacy_database"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6653ea83f1d14b005753` | `core/settings_specs.py:259` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：source in {"database", "legacy_database"} |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.6531f0d0b5d018241d7d` | `core/skills/contracts.py:400` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：len(compatibility) > 500 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.d4cf87acef7db0a8dd7b` | `core/task_runtime/resilience.py:18` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile( r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$" ) |  |  |  |  |  |
|  | 原因：人工复核：正则只验证 ResiliencePolicy Descriptor 的语义版本 |  |  |  |  |  |
| `decision.1fbbc4f68f832c70c81a` | `core/task_runtime/resilience.py:29` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：韧性策略元数据拒绝控制字符用于保护 Registry 和日志字段 |  |  |  |  |  |
| `decision.e082cb5e85a1c83776df` | `core/task_runtime/resilience.py:75` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：_VERSION_PATTERN.fullmatch(version) |  |  |  |  |  |
|  | 原因：人工复核：ResiliencePolicy 版本必须满足语义版本协议 |  |  |  |  |  |
| `decision.11110b4d548865c3ec1f` | `core/task_runtime/resilience.py:89` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：total_timeout <= 0 |  |  |  |  |  |
|  | 原因：人工复核：总 timeout 必须为正是韧性预算结构约束 |  |  |  |  |  |
| `decision.37a8412d827b23eaa481` | `core/task_runtime/resilience.py:91` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：attempt_timeout <= 0 |  |  |  |  |  |
|  | 原因：人工复核：单次 timeout 必须为正是韧性预算结构约束 |  |  |  |  |  |
| `decision.3c30e6c017b48fa4e909` | `core/task_runtime/resilience.py:103` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：backoff_initial < 0 |  |  |  |  |  |
|  | 原因：人工复核：backoff 不能为负是退避算法结构约束 |  |  |  |  |  |
| `decision.b3bb7abadad8452a73e9` | `core/task_runtime/resilience.py:110` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：backoff_multiplier < 1 |  |  |  |  |  |
|  | 原因：人工复核：backoff multiplier 下限属于退避算法结构约束 |  |  |  |  |  |
| `decision.84728d3f3cf8c2c13464` | `core/task_runtime/resilience.py:115` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 <= jitter_ratio <= 1 |  |  |  |  |  |
|  | 原因：人工复核：jitter ratio 范围属于退避策略结构约束 |  |  |  |  |  |
| `decision.afd44b292bcde0705677` | `core/task_runtime/resilience.py:128` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.max_attempts > 1 |  |  |  |  |  |
|  | 原因：人工复核：多次 attempt 必须声明可重试分类或错误码，避免无条件重试 |  |  |  |  |  |
| `decision.14dae40398c816db6436` | `core/task_runtime/resilience.py:270` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：attempt_no <= 0 |  |  |  |  |  |
|  | 原因：人工复核：attempt 序号必须为正是退避计算输入合同 |  |  |  |  |  |
| `decision.48d0990bc488a036240b` | `core/task_runtime/resilience.py:274` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 <= sample <= 1 |  |  |  |  |  |
|  | 原因：人工复核：jitter 样本范围属于随机源输入合同 |  |  |  |  |  |
| `decision.621495fe37d8f9feff13` | `core/task_runtime/runtime.py:132` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：value >= 0 |  |  |  |  |  |
|  | 原因：人工复核：模型 usage 只接受非负整数，防止非法 Token 统计污染 SLO 账本 |  |  |  |  |  |
| `decision.5d8e8a4ea5c401b9b048` | `core/task_runtime/runtime.py:133` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：value >= 0 |  |  |  |  |  |
|  | 原因：人工复核：模型 usage 的浮点数只有非负且为整数时才可投影为 Token 计数 |  |  |  |  |  |
| `decision.04e5697d0194b27165c0` | `core/task_runtime/runtime.py:456` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：backoff_seconds > 0 |  |  |  |  |  |
|  | 原因：人工复核：仅正 backoff 才调用 sleeper 是 ResiliencePolicy 的确定性执行合同 |  |  |  |  |  |
| `decision.31bce1e4037ac9d15e34` | `core/task_runtime/runtime.py:553` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：policy.slo_descriptor_id == "task_slo.by_invocation.v1" |  |  |  |  |  |
|  | 原因：人工复核：task_slo.by_invocation.v1 是 ResiliencePolicy 与逐 Task SLO 的版本化绑定协议 |  |  |  |  |  |
| `decision.79e91379f7dd6fc33fc4` | `core/task_runtime/slo.py:15` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile( r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$" ) |  |  |  |  |  |
|  | 原因：人工复核：SLO 语义版本、Route／Task 绑定和 invocation SLO 引用属于版本化 Task 合同语法 |  |  |  |  |  |
| `decision.e4c052095697fff5941f` | `core/task_runtime/slo.py:52` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：SLO 正数、比率和控制字符校验是 Descriptor 的确定性有效状态合同 |  |  |  |  |  |
| `decision.2bde934ad4a26c03bde7` | `core/task_runtime/slo.py:62` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：value <= 0 |  |  |  |  |  |
|  | 原因：人工复核：SLO 正数、比率和控制字符校验是 Descriptor 的确定性有效状态合同 |  |  |  |  |  |
| `decision.b0bcf2487ec09f7cd67f` | `core/task_runtime/slo.py:78` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：normalized <= 0 |  |  |  |  |  |
|  | 原因：人工复核：SLO 正数、比率和控制字符校验是 Descriptor 的确定性有效状态合同 |  |  |  |  |  |
| `decision.271a9fefdd96ccc931cc` | `core/task_runtime/slo.py:93` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：0 <= normalized <= 1 |  |  |  |  |  |
|  | 原因：人工复核：SLO 正数、比率和控制字符校验是 Descriptor 的确定性有效状态合同 |  |  |  |  |  |
| `decision.50f88cb58cb5f804a580` | `core/task_runtime/slo.py:160` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_VERSION_PATTERN.fullmatch(version) |  |  |  |  |  |
|  | 原因：人工复核：SLO 语义版本、Route／Task 绑定和 invocation SLO 引用属于版本化 Task 合同语法 |  |  |  |  |  |
| `decision.84991d2ab70ad30c0811` | `core/task_runtime/slo.py:380` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：route.runtime_task_key != f"tasks/{descriptor.task_id}" |  |  |  |  |  |
|  | 原因：人工复核：SLO 语义版本、Route／Task 绑定和 invocation SLO 引用属于版本化 Task 合同语法 |  |  |  |  |  |
| `decision.4080dcf12b2c84a263fb` | `core/task_runtime/slo.py:411` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：policy.slo_descriptor_id != "task_slo.by_invocation.v1" |  |  |  |  |  |
|  | 原因：人工复核：SLO 语义版本、Route／Task 绑定和 invocation SLO 引用属于版本化 Task 合同语法 |  |  |  |  |  |
| `decision.0c6612c9d9753cab91c8` | `core/task_runtime/validators.py:79` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.18c00ae7518d3d1d15a0` | `core/task_runtime/validators.py:98` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.6e24a24a8e24ced2c6f1` | `core/task_runtime/validators.py:124` | `python.literal_collection` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：target_actions = { "merge_into", "add_alias", "conflict_with", } |  |  |  |  |  |
|  | 原因：人工复核：需要目标记忆 ID 的群学习动作集合属于版本化输出协议 |  |  |  |  |  |
| `decision.f50c391858b660df920d` | `core/task_runtime/validators.py:154` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.1015ffe8f5f38687f10a` | `core/task_runtime/validators.py:201` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.407788cb71d1763f9665` | `core/task_runtime/validators.py:223` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action == "reply_now" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.e8398e5471e85766a1aa` | `core/task_runtime/validators.py:223` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：response_mode == "none" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.0e1a38b3d3e9ee93ae4e` | `core/task_runtime/validators.py:235` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：response_mode != "none" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.27f0080e09792d3361ca` | `core/task_runtime/validators.py:235` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：action in {"no_reply", "wait"} |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.3defa50b5eaca12d708b` | `core/task_runtime/validators.py:247` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：response_mode == "template" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.548c7cd00f4e2f424da1` | `core/task_runtime/validators.py:269` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：effort != "casual" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.b3ddc9f2656a62a64cb7` | `core/task_runtime/validators.py:287` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：effort == "casual" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.a959b3a4e156cb31358b` | `core/task_runtime/validators.py:314` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：output_contract_id == "news_quality_summary_v1" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.2939c8f7a816aae91870` | `core/task_runtime/validators.py:318` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.538afd878323b467e9e8` | `core/task_runtime/validators.py:341` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：output_contract_id == "news_relevance_review_v1" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.26d72b2ae13f44724e76` | `core/task_runtime/validators.py:370` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：output_contract_id == "group_analysis_topics_v1" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.d43d237578776084d4e2` | `core/task_runtime/validators.py:374` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item > 0 |  |  |  |  |  |
|  | 原因：人工复核：Task 证据、来源和目标 ID 必须为正整数，属于结构化合同有效状态校验 |  |  |  |  |  |
| `decision.2ff9e639dff2a90af148` | `core/task_runtime/validators.py:395` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 7A–7D | `reviewed` |
|  | 摘要：output_contract_id == "group_memory_learning_v1" |  |  |  |  |  |
|  | 原因：人工复核：Task 输出合同 ID、字段集合、枚举及群学习合同分派属于结构化协议 |  |  |  |  |  |
| `decision.b4def108635828697327` | `core/telemetry/__init__.py:17` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：name == "JobTelemetryEmitter" |  |  |  |  |  |
|  | 原因：人工复核：惰性导出名称是为解除包初始化环而冻结的公开模块协议 |  |  |  |  |  |
| `decision.03226e746616fd0d3485` | `core/telemetry/__init__.py:21` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：name == "SqlAlchemyRuntimeEventSink" |  |  |  |  |  |
|  | 原因：人工复核：惰性导出名称是为解除包初始化环而冻结的公开模块协议 |  |  |  |  |  |
| `decision.8a6bfe854c18f8fc50df` | `core/telemetry/contracts.py:13` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[a-z][a-z0-9_]{2,127}$") |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.9e7ee25eb1e073b63bbc` | `core/telemetry/contracts.py:14` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[a-z][a-z0-9_]{0,63}$") |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.c3aae954c809a8d57596` | `core/telemetry/contracts.py:15` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[a-z][a-z0-9_.-]{1,127}$") |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.65b898c17a8dc3b2e770` | `core/telemetry/contracts.py:16` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[0-9a-f]{64}$") |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.0e12371bcdc3fb5355b7` | `core/telemetry/contracts.py:34` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.db87629c81be1c917c60` | `core/telemetry/contracts.py:85` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：self.registry_generation <= 0 |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.1d4e3887ea6ae4e5080b` | `core/telemetry/contracts.py:91` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 4 | `reviewed` |
|  | 摘要：_SHA256_RE.fullmatch(registry_sha256) |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.8a99bf1e0c1e180e18b6` | `core/telemetry/contracts.py:96` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_MODULE_ID_RE.fullmatch(module_id) |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.4d2b2acabf5332c78e45` | `core/telemetry/contracts.py:128` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_METRIC_NAME_RE.fullmatch(metric_name) |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.9ce5938ed33447e58cb2` | `core/telemetry/contracts.py:137` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_MODULE_ID_RE.fullmatch(owner_module) |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.33a09a9ff14b959536b0` | `core/telemetry/contracts.py:149` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_LABEL_NAME_RE.fullmatch(label) |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.ecbd4aaba28fd0055c0d` | `core/telemetry/contracts.py:155` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(labels) > 12 |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.6ed02e8bb1b178c1a086` | `core/telemetry/contracts.py:165` | `python.numeric_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：item <= 0 |  |  |  |  |  |
|  | 原因：人工复核：关联标识、Registry provenance、指标名、label 和 bucket 校验是版本化 Telemetry 合同，不承担业务语义判断 |  |  |  |  |  |
| `decision.1d703b910a6953ef674e` | `core/telemetry/job_observer.py:138` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：Job 状态、重试、租约和关联字段到生命周期事件的映射是持久化状态机的一致性投影 |  |  |  |  |  |
| `decision.f9864c88643cac2c8623` | `core/telemetry/job_observer.py:211` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：transition == "retry_scheduled" |  |  |  |  |  |
|  | 原因：人工复核：Job 状态、重试、租约和关联字段到生命周期事件的映射是持久化状态机的一致性投影 |  |  |  |  |  |
| `decision.333e3b7cfc2a468af9ed` | `core/telemetry/job_observer.py:213` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：status in {"failed", "ambiguous", "blocked"} |  |  |  |  |  |
|  | 原因：人工复核：Job 状态、重试、租约和关联字段到生命周期事件的映射是持久化状态机的一致性投影 |  |  |  |  |  |
| `decision.db32b7d8f29d462ae9b2` | `core/telemetry/job_observer.py:245` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：status in {"pending", "retry_wait"} |  |  |  |  |  |
|  | 原因：人工复核：Job 状态、重试、租约和关联字段到生命周期事件的映射是持久化状态机的一致性投影 |  |  |  |  |  |
| `decision.639d3aa6040278d8e459` | `core/telemetry/job_observer.py:309` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：adapter.job_type == "outbound_delivery" |  |  |  |  |  |
|  | 原因：人工复核：Job 状态、重试、租约和关联字段到生命周期事件的映射是持久化状态机的一致性投影 |  |  |  |  |  |
| `decision.3b34bc810d84fa25e477` | `core/telemetry/persistence.py:16` | `python.literal_collection` | `security_invariant` | `preserve` | 阶段 8 | `reviewed` |
|  | 摘要：_SENSITIVE_KEY_PARTS = ( "authorization", "command", "content", "cookie", "password", "prompt", "secret", "stderr", "stdout", "token", ) |  |  |  |  |  |
|  | 原因：人工复核：敏感键、控制字符和字段长度的二次过滤用于阻止正文、凭据和日志注入进入 Telemetry 账本 |  |  |  |  |  |
| `decision.99a0f861dcb2f235b2a6` | `core/telemetry/persistence.py:54` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(key) > 64 |  |  |  |  |  |
|  | 原因：人工复核：敏感键、控制字符和字段长度的二次过滤用于阻止正文、凭据和日志注入进入 Telemetry 账本 |  |  |  |  |  |
| `decision.9cbe9a17e5d720a823d9` | `core/telemetry/persistence.py:64` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：value < 0 |  |  |  |  |  |
|  | 原因：人工复核：敏感键、控制字符和字段长度的二次过滤用于阻止正文、凭据和日志注入进入 Telemetry 账本 |  |  |  |  |  |
| `decision.73b415801c72f167a564` | `core/telemetry/persistence.py:71` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(value) > 256 |  |  |  |  |  |
|  | 原因：人工复核：敏感键、控制字符和字段长度的二次过滤用于阻止正文、凭据和日志注入进入 Telemetry 账本 |  |  |  |  |  |
| `decision.0be8d041d47e5a0a1209` | `core/telemetry/persistence.py:72` | `python.numeric_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(character) < 32 |  |  |  |  |  |
|  | 原因：人工复核：敏感键、控制字符和字段长度的二次过滤用于阻止正文、凭据和日志注入进入 Telemetry 账本 |  |  |  |  |  |
| `decision.08e285f6b723a3dc496e` | `core/telemetry/runtime.py:43` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：capacity <= 0 |  |  |  |  |  |
|  | 原因：人工复核：队列容量关系和测试生命周期分支是 Telemetry Runtime 的确定性有效状态合同 |  |  |  |  |  |
| `decision.dd45678a91a653ebe7c4` | `core/telemetry/runtime.py:43` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：batch_size <= 0 |  |  |  |  |  |
|  | 原因：人工复核：队列容量关系和测试生命周期分支是 Telemetry Runtime 的确定性有效状态合同 |  |  |  |  |  |
| `decision.6eaeb1c3d239c6ce4e16` | `core/telemetry/runtime.py:197` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：os.environ.get("NANOBOT_TESTING") == "1" |  |  |  |  |  |
|  | 原因：人工复核：队列容量关系和测试生命周期分支是 Telemetry Runtime 的确定性有效状态合同 |  |  |  |  |  |
| `decision.f5dd293ca1efea29b368` | `core/timing_model_policy.py:52` | `python.numeric_control_flow` | `protocol_syntax` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：ord(char) < 32 |  |  |  |  |  |
|  | 原因：人工复核：控制字符拒绝属于类型化配置来源标识的语法边界 |  |  |  |  |  |
| `decision.327a6662e8529aa3c9a3` | `core/timing_score.py:23` | `python.literal_collection` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：_ACK_WORDS = { "嗯", "嗯嗯", "哦", "噢", "好", "好的", "好滴", "收到", "ok", "okay", "行", "可以", "哈哈", "哈哈哈", "hh", } |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.e12a9c06e919c5176314` | `core/timing_score.py:40` | `python.literal_collection` | `natural_language_semantic` | `model_signal_only` | 阶段 3／4 | `reviewed` |
|  | 摘要：_REQUEST_MARKERS = ( "帮我", "查下", "查一下", "看看", "看下", "继续", "总结", "怎么", "为什么", "能不能", "可以不", "发我", "给我", "解释", "分析", ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.e31ad8e27ac88243d7fe` | `core/tool_contracts/ai_daily.py:19` | `python.literal_collection` | `configurable_policy` | `configure` | 阶段 8 | `reviewed` |
|  | 摘要：AI_DAILY_FRESHNESS_VALUES = ("today", "latest", "week", "custom") |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.0d4e9e14cc9ad5b685a5` | `core/tool_contracts/ai_daily.py:23` | `python.literal_collection` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：NEWS_REQUEST_KIND_VALUES = ("search", "daily_digest") |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.25380e579a9645e817ba` | `core/tool_contracts/ai_daily.py:24` | `python.literal_collection` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：NEWS_CACHE_POLICY_VALUES = ("use", "bypass", "refresh") |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.20f2a7f0f30967ecaa29` | `core/tool_contracts/ai_daily.py:83` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：self.cache_policy in {"bypass", "refresh"} |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.29fa682228f43645cd36` | `core/tool_contracts/ai_daily.py:89` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：self.cache_policy == "bypass" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.00f03480362fccfda7f8` | `core/tool_contracts/ai_daily.py:95` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：self.cache_policy == "refresh" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.3985959496183f11d88c` | `core/tool_contracts/ai_daily.py:187` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.afdd061200bfe183c38a` | `core/tool_contracts/ai_daily.py:209` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 8 | `reviewed` |
|  | 摘要：1 <= value <= AI_DAILY_MAX_RESULTS_LIMIT |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.042674f51d0c21260605` | `core/tool_contracts/ai_daily.py:223` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：freshness == "custom" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.21798e1e180fed9c49d2` | `core/tool_contracts/ai_daily.py:234` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：freshness == "today" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.8a00ff3a5db444ece73b` | `core/tool_contracts/ai_daily.py:242` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：freshness == "latest" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.f6cb4f03244633943769` | `core/tool_contracts/ai_daily.py:248` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：freshness == "week" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.9abb5ad537a6c1dcd5ad` | `core/tool_contracts/ai_daily.py:280` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：re.sub(r"\s+", " ", query) |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.e455ee997d599e0c7bb4` | `core/tool_contracts/ai_daily.py:283` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 8 | `reviewed` |
|  | 摘要：len(normalized_query) > 1000 |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.b49dc4a7f07331be0d2d` | `core/tool_contracts/ai_daily.py:291` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：request_kind == "daily_digest" |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.850902a04724cc9f19ac` | `core/tool_contracts/ai_daily.py:292` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：freshness in {"today", "latest"} |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.7261b265bfcde791c045` | `core/tool_contracts/ai_daily.py:360` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：re.sub(r"\s+", " ", raw_query) |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的标准化、缓存模式和 request kind 分支属于类型化请求协议 |  |  |  |  |  |
| `decision.8ac752e4c2f6ae4fa572` | `core/tool_contracts/ai_daily.py:363` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 8 | `reviewed` |
|  | 摘要：len(query) > 1000 |  |  |  |  |  |
|  | 原因：人工复核：NewsRequest 的枚举、长度和数量边界属于可配置合同 |  |  |  |  |  |
| `decision.55208b247bb8b2fe9f26` | `core/tool_registration.py:171` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 8 | `reviewed` |
|  | 摘要：lifecycle != "retired" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.384d1bec4bfe2f1324dc` | `core/tool_registration.py:184` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：lifecycle == "retired" |  |  |  |  |  |
|  | 原因：人工复核：retired 是 ToolRegistration 的类型化生命周期状态，必须与 Feature tombstone 一致 |  |  |  |  |  |
| `decision.cdaec5e6db56a86a5263` | `core/tool_tracing.py:130` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 8 | `reviewed` |
|  | 摘要：status != "success" |  |  |  |  |  |
|  | 原因：人工复核：Tool 执行状态到稳定 failure code 的映射属于工具追踪协议 |  |  |  |  |  |
| `decision.ac1e7a8e9d2a4332ba9a` | `core/user_block_rules.py:46` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：target_type == "group" |  |  |  |  |  |
|  | 原因：人工复核：仅群目标需要归一 group_id，属于身份作用域合同 |  |  |  |  |  |
| `decision.c02c766de9a90e3f4de1` | `creatures/nanobot/prompts/skills/news_search/evidence.py:137` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"(\$[\d.]+)\s*(/\|per\|/1[Kk]\|/1[Mm]\|每)", re.IGNORECASE ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.ed4ac957b2595e30a3c5` | `creatures/nanobot/prompts/skills/news_search/evidence.py:148` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"^(登录\|注册\|订阅\|广告\|推广\|相关文章\|阅读更多\|" r"分享到\|Cookie\|Privacy\|Terms\|©\|All Rights)", re.IGNORECASE ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.3078bbd46a22c11a033f` | `creatures/nanobot/prompts/skills/news_search/evidence.py:175` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：RELEVANCE_KEYWORDS.search(sent) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.c9909bfa2f91072c8a56` | `creatures/nanobot/prompts/skills/news_search/evidence.py:249` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"(发布\|推出\|宣布\|开源\|支持\|上线\|开放\|降价\|免费\|" r"超越\|超过\|达到\|实现\|launch\|release\|announce\|support\|open.source)", re.IGNORECASE ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.6cb79dba25cc8e4356c2` | `creatures/nanobot/prompts/skills/news_search/evidence.py:283` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要："开源" in text |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.db96a90a109433ee24bd` | `creatures/nanobot/prompts/skills/news_search/evidence.py:283` | `python.string_control_flow` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要："免费" in text |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.f5aab14c6885d6286fbc` | `creatures/nanobot/prompts/skills/news_search/evidence.py:355` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile(r"(值得关注\|行业持续\|不断进步\|日益增长\|越来越\|趋势)") |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.917038a08f3baa941cec` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:16` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：TRUSTED_NEWS_DOMAINS = { "openai.com", "anthropic.com", "googleblog.com", "deepmind.google", "microsoft.com", "aws.amazon.com", "ai.meta.com", "huggingface.co", "arxiv.org", "nature.com", "techcrunch.com", "theverge.com", "venturebeat.com"… |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.ba420771ff493ac6d244` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:31` | `python.literal_collection` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：MODEL_NAME_HINTS = { "qwen", "deepseek", "kimi", "gpt", "claude", "gemini", "llama", "mistral", "hunyuan", "glm", "通义", "豆包", "混元", "智谱", "阶跃", "minimax", } |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.246f03e3ff2a11ec550f` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:53` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：domain.endswith(".edu") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.98f16d932dbdf53d4689` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:53` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：domain.endswith(".gov") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.a6f7717bc1cb1ddd200f` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:55` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：len(title) > 20 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.1fd3e61530e85d2ac7a9` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:57` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：len(body) > 60 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.75d5d92b454d121f44ed` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:59` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：url.startswith("https://") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.0420622ec022acc03e02` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:84` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：delta <= timedelta(days=1) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.c3b82687167c0af7e48f` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:86` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：delta <= timedelta(days=3) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.fe273cd76d3afe99dde8` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:88` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：delta <= timedelta(days=7) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.32a16b285a5bb2339e9b` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:90` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：delta <= timedelta(days=30) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.61ba97167d1ca49df8ba` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:124` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：signal < 2 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.64a4d485611b14924940` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:143` | `python.regex_call` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：re.sub(r"\s+", " ", (text or "").strip()) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.0d94cf79deab3ea0f1f7` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:300` | `python.regex_call` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：re.search(r"\d", value) |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.be6e55b176e9665df9f3` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:306` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：len(value) >= 18 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.47827097ec1d5795caa1` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:368` | `python.numeric_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：_specificity_score(summary) >= 2 |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.5950e1f9f827fa66ec4d` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:502` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：content.startswith("Failed") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.64752273e04b5f27cb7c` | `creatures/nanobot/prompts/skills/news_search/legacy_report.py:502` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 6 | `reviewed` |
|  | 摘要：content.startswith("Error extracting") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.3e7bc0fbd18dae3bce88` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/config.py:26` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.group == "core_provider" |  |  |  |  |  |
|  | 原因：人工复核：旧配置投影到 News Policy／Source Registry 的映射属于兼容资源 |  |  |  |  |  |
| `decision.8bbdde3237dda829165e` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/config.py:31` | `python.string_control_flow` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.group == "core_provider" |  |  |  |  |  |
|  | 原因：人工复核：旧配置投影到 News Policy／Source Registry 的映射属于兼容资源 |  |  |  |  |  |
| `decision.96a6ce94f138c5941291` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/evidence_light.py:23` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile( r"(发布\|推出\|宣布\|开源\|支持\|上线\|开放\|降价\|免费\|" r"超越\|超过\|达到\|实现\|launch\|release\|announce\|support\|open.source)", re.IGNORECASE, ) |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.c87ac3347587a598844f` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/normalize_v2.py:115` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：re.sub(r"[^\w\s一-鿿]", " ", t) |  |  |  |  |  |
|  | 原因：人工复核：字符归一化正则不负责新闻语义判断 |  |  |  |  |  |
| `decision.f2c76128db458ea43cf4` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/normalize_v2.py:123` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：re.search(r"[一-鿿]", part) |  |  |  |  |  |
|  | 原因：人工复核：中文字符检测用于分词归一化 |  |  |  |  |  |
| `decision.1ad6dabf0a45dd8346e6` | `creatures/nanobot/prompts/skills/news_search/news_daily/pipeline/validate.py:74` | `python.regex_call` | `natural_language_semantic` | `model_signal_only` | 阶段 6 | `reviewed` |
|  | 摘要：re.compile(r"(值得关注\|行业持续\|不断进步\|日益增长\|越来越\|趋势)") |  |  |  |  |  |
|  | 原因：人工复核：自然语言字面量只能作为候选信号，最终语义必须由结构化模型任务或人工结论产生 |  |  |  |  |  |
| `decision.67b137d5e489af505eff` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:252` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("anthropic.com/news/", "/news/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.ab01960aab19a781c9a5` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:258` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("kimi.com/blog/", "/blog/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.99b844ed51f6a0de622e` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:303` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("x.ai/news/", "/news/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.77c9af1275e146efb0aa` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:308` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("cohere.com/blog/", "/blog/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.d3fade95ef68e096d67f` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:313` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("ai.meta.com/blog/", "/blog/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.cb68f2210f2b88a24fc0` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:314` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：blocked_markers = ("?filter", "?category") |  |  |  |  |  |
|  | 原因：人工复核：来源过滤查询参数属于版本化来源 Descriptor |  |  |  |  |  |
| `decision.50096ae1ad5b83a6ddcb` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/adapters.py:319` | `python.literal_collection` | `protocol_syntax` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：path_markers = ("mistral.ai/news/", "/news/") |  |  |  |  |  |
|  | 原因：人工复核：新闻来源路径是版本化来源 Descriptor |  |  |  |  |  |
| `decision.2ba6d3c5ab48b51f11a3` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/curated.py:46` | `python.regex_call` | `presentation` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：re.sub(r'AI\s*早报\s*\d{4}-\d{2}-\d{2}\s*', '', text) |  |  |  |  |  |
|  | 原因：人工复核：早报标题清理属于来源展示适配 |  |  |  |  |  |
| `decision.2e10141a425c73e6258c` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/curated.py:47` | `python.regex_call` | `presentation` | `resource` | 阶段 6 | `reviewed` |
|  | 摘要：re.sub(r'视频版：[\w\s｜\|]+', '', text) |  |  |  |  |  |
|  | 原因：人工复核：视频版文案清理属于来源展示适配 |  |  |  |  |  |
| `decision.d3cb8e92cdaf13e7a838` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:35` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.adapter_kind == "qwen_api_json" |  |  |  |  |  |
|  | 原因：人工复核：来源 adapter_kind 到固定 Provider 的选择属于显式 Descriptor 协议 |  |  |  |  |  |
| `decision.e28a568c27f998103f77` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:38` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.adapter_kind.endswith("_html") |  |  |  |  |  |
|  | 原因：人工复核：来源 adapter_kind 到固定 Provider 的选择属于显式 Descriptor 协议 |  |  |  |  |  |
| `decision.46331473aebaef080c75` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:39` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：descriptor.adapter_kind == "html_list" |  |  |  |  |  |
|  | 原因：人工复核：来源 adapter_kind 到固定 Provider 的选择属于显式 Descriptor 协议 |  |  |  |  |  |
| `decision.c6631479378fa3f400ea` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:63` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：len(descriptor.modes) > 1 |  |  |  |  |  |
|  | 原因：人工复核：来源 mode 数量只用于构造兼容投影，属于可配置来源策略 |  |  |  |  |  |
| `decision.d3f01f8b1785559e5da8` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:63` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："search" not in descriptor.modes |  |  |  |  |  |
|  | 原因：人工复核：搜索来源投影必须与 Descriptor modes 保持一致 |  |  |  |  |  |
| `decision.34cdf825f1ad4c0a08ce` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:80` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：cfg.adapter_kind == "juya_rss" |  |  |  |  |  |
|  | 原因：人工复核：来源 adapter_kind 到固定 Provider 的选择属于显式 Descriptor 协议 |  |  |  |  |  |
| `decision.d1f8eee701279a2a4e4b` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:85` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：cfg.adapter_kind == "html_list" |  |  |  |  |  |
|  | 原因：人工复核：来源 adapter_kind 到固定 Provider 的选择属于显式 Descriptor 协议 |  |  |  |  |  |
| `decision.a9f9f0628166b353b07e` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:100` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：len(descriptor.modes) == 1 |  |  |  |  |  |
|  | 原因：人工复核：来源 mode 数量只用于构造兼容投影，属于可配置来源策略 |  |  |  |  |  |
| `decision.b8fe420f0efbd46f9728` | `creatures/nanobot/prompts/skills/news_search/news_daily/sources/official.py:100` | `python.string_control_flow` | `data_consistency` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要："search" in descriptor.modes |  |  |  |  |  |
|  | 原因：人工复核：搜索来源投影必须与 Descriptor modes 保持一致 |  |  |  |  |  |
| `decision.82270cf204b90d2d8a54` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:30` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode not in ("auto", "fast", "quality", "daily") |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.b2bac87bcb31790ca3b5` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:32` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode != "auto" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.a53c6a3fb834837de808` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:67` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：item.source_group != "community" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.6ad47fc7662ef2a95543` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:115` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：seen_entities.get(e, 0) >= MAX_SAME_ENTITY_CLUSTERS_DAILY |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.1b372cdd2c16c4fc4255` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:118` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：seen_domains.get(rep.domain, 0) >= MAX_CLUSTERS_PER_DOMAIN_FINAL |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.c6655297fabafcdfc094` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:147` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：n_src >= 2 |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.90b675d5f17b64bc8669` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:150` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：len(a.summary) > 10 |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.4b2928c5444f94c07885` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:168` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：len(a.summary) > 20 |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.a61eec6223e453bd5908` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:213` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：len(html) < 800 |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.02d234bd0cae7fcd7322` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:269` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：request.freshness != "today" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.c1d9d42ae383081e5400` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:398` | `python.numeric_control_flow` | `configurable_policy` | `configure` | 阶段 6 | `reviewed` |
|  | 摘要：candidate_count == 0 |  |  |  |  |  |
|  | 原因：人工复核：日报候选数、配额和阶段阈值属于集中可配置 Policy |  |  |  |  |  |
| `decision.b3d9ceff6d2370937e91` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:405` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode == "quality" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.b4099c0f29a53dbf8863` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:428` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode == "daily" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.69d6908ce3f4983bc6ea` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:463` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode == "fast" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.8741b037949628c8d40b` | `creatures/nanobot/prompts/skills/news_search/news_daily/tool.py:495` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：mode != "daily" |  |  |  |  |  |
|  | 原因：人工复核：日报 pipeline mode、缓存和结构化阶段分支属于显式执行协议 |  |  |  |  |  |
| `decision.40c41c355e89e6e06c9f` | `creatures/nanobot/prompts/skills/news_search/runtime_cache.py:55` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text) |  |  |  |  |  |
|  | 原因：人工复核：中文日期正则属于确定性日期协议解析 |  |  |  |  |  |
| `decision.1b90a2987a58c29841b4` | `creatures/nanobot/prompts/skills/news_search/runtime_cache.py:59` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text) |  |  |  |  |  |
|  | 原因：人工复核：月日正则属于确定性日期协议解析 |  |  |  |  |  |
| `decision.b76cf87b1c5280f4a7cc` | `creatures/nanobot/prompts/skills/news_search/runtime_cache.py:63` | `python.regex_call` | `protocol_syntax` | `preserve` | 阶段 6 | `reviewed` |
|  | 摘要：re.search(r"\b(today)\b\|今天\|今日", text, flags=re.IGNORECASE) |  |  |  |  |  |
|  | 原因：人工复核：相对日期词解析属于查询时间边界 |  |  |  |  |  |
| `decision.10836c46312cf6092425` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:48` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：os.environ.get("NEWS_SEARCH_DDG_ENABLED", "0") == "1" |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.1185b0070c0b70d2692f` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:163` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：child.tag.endswith("encoded") |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.c2e3ed133cc4b64f9d97` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:163` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：child.tag.endswith("content") |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.33f5814eb14604394c1c` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:258` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：child.tag.endswith("encoded") |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.76b1381082a9838aa45f` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:258` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：child.tag.endswith("content") |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.968a4252cd5ee20ce73e` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:344` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：request.freshness in {"today", "latest"} |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.bd7f7e2691a0a9215955` | `creatures/nanobot/prompts/skills/news_search/search_backend.py:346` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 6 | `reviewed` |
|  | 摘要：request.freshness == "week" |  |  |  |  |  |
|  | 原因：人工复核：搜索后端仅按 NewsRequest 的显式 freshness 和执行模式分支 |  |  |  |  |  |
| `decision.78f31fa69b408682a975` | `nanobot_kt/bridge.py:122` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：direct_markers = ( "群聊总结", "群总结", "群日报", "分析群", "总结群", "分析这个群", "总结这个群", ) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.280f1c6b1ad6514eccc2` | `nanobot_kt/bridge.py:1120` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：'[系统内部错误]' in response |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.beeb1c3552646a5db389` | `nanobot_kt/bridge.py:1121` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：'[工具错误]' in response |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.8475c5c4d0fc9bdcd380` | `nanobot_kt/codex_admin_adapter.py:14` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："不存在" in str(message) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.4eada97c303f015a80d7` | `nanobot_kt/image_pipeline.py:124` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：value.startswith("[图片") |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.3d496df939dbf4234236` | `nanobot_kt/image_pipeline.py:265` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："图片过大" in str(e) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.4f71f2b5c9a2e3c3ada2` | `nanobot_kt/model_attempts.py:13` | `python.string_control_flow` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要："[系统内部错误]" in text |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.9866f4e5657a2154baaf` | `nanobot_kt/reply_contract.py:381` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：fake_patterns = [ r"(调用\|使用\|已调用\|已使用\|通过\|call)\s{0,12}`?reply`?", r"`?reply`?\s*工具.{0,8}(调用\|使用\|发送)", r"reply\s*\(\s*[\"']", r"(发送\|回复\|回答).{0,4}(调用\|使用).{0,4}reply", ] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.9cfaa728a064f7bbb617` | `nanobot_kt/tools/ai_daily.py:186` | `python.literal_collection` | `configurable_policy` | `policy` | 阶段 8 | `auto_classified` |
|  | 摘要：deepen_markers = ["深入", "全面", "对比", "价格", "白嫖", "便宜", "free", "cheap", "pricing"] |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.f79a15f86b8d7619a16e` | `scripts/benchmark_classifier.py:251` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：cleaned in ("是", "是，") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.95d704747a0dd4f3c7c2` | `scripts/benchmark_classifier.py:256` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：cleaned in ("否", "否，") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.2f43e5f6dcbf71bac210` | `scripts/benchmark_classifier.py:263` | `python.regex_call` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.match(r"^(是\|否)[,，](-?\d+)$", cleaned) |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.a9d674ed469b84f766a2` | `scripts/benchmark_classifier.py:271` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：cleaned.startswith("是") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.d9eddbad37ff3c6e846d` | `scripts/benchmark_classifier.py:273` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：cleaned.startswith("否") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.09d65209ccdfccaad727` | `scripts/benchmark_classifier.py:320` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：parsed["type"] in ("是", "是≈") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.d742c571ef6ef1b0a850` | `scripts/benchmark_classifier.py:320` | `python.string_control_flow` | `protocol_syntax` | `compatibility_migration` | 阶段 3／4 | `reviewed` |
|  | 摘要：parsed["type"] in ("否", "否≈") |  |  |  |  |  |
|  | 原因：人工复核：基准脚本解析旧分类器自由文本输出 |  |  |  |  |  |
| `decision.098668cf86d4007e5ea6` | `scripts/build_behavior_baseline.py:116` | `python.literal_mapping` | `data_consistency` | `policy` | 阶段 5 | `reviewed` |
|  | 摘要：SNAPSHOT_CLASSIFICATIONS = { "agent_runtime": "preserve", "group_analysis": "known_bad", "news_heuristics": "preserve", "private_timing": "preserve", "prompt_runtime": "preserve", "runtime_registries": "preserve", "security_invariants": "s… |  |  |  |  |  |
|  | 原因：人工复核：行为快照分类和结构数量属于 Golden 数据一致性合同 |  |  |  |  |  |
| `decision.e090d7390ad4316ff731` | `scripts/build_behavior_baseline.py:198` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：payload.get("schema_version") != 1 |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.81e1e57e2a58fa938286` | `scripts/build_behavior_baseline.py:229` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：payload.get("schema_version") != 1 |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.8f2046cada7c2fdc7bbf` | `scripts/build_behavior_baseline.py:279` | `python.literal_mapping` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：model_defaults = { "action": "reply_now", "effort": "short", "intent": "general_question", "response_mode": "agent", "confidence": 0.92, "parse_quality": "schema_valid", "error_type": None, "conflicting_signals": [], "material_state": "non… |  |  |  |  |  |
|  | 原因：人工复核：行为快照分类和结构数量属于 Golden 数据一致性合同 |  |  |  |  |  |
| `decision.a9564ac3a044b9cc74eb` | `scripts/build_behavior_baseline.py:315` | `python.literal_collection` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：allowed_arguments = { "is_group", "is_private", "is_at_bot", "is_reply_to_bot", "bot_name_mentioned", "direct_call", "is_directed_to_other", "has_other_recipient", "is_other_bot", "has_files", "linger_score", "force_direct_score", "min_int… |  |  |  |  |  |
|  | 原因：人工复核：行为快照分类和结构数量属于 Golden 数据一致性合同 |  |  |  |  |  |
| `decision.e2f4bef788d7aaeaab5a` | `scripts/build_behavior_baseline.py:483` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：request_schema.get("additionalProperties") is False |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.e1f8e8e1eba712aa081c` | `scripts/build_behavior_baseline.py:593` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：path.suffix in {".md", ".json"} |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.7977e9a871a9ff25ba2b` | `scripts/build_behavior_baseline.py:1135` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(revision) != 40 |  |  |  |  |  |
|  | 原因：人工复核：行为快照分类和结构数量属于 Golden 数据一致性合同 |  |  |  |  |  |
| `decision.f12fe030561f6532f104` | `scripts/build_behavior_baseline.py:1671` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：manifest.get("schema_version") != SCHEMA_VERSION |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.005abcd2d8c0d229c9d8` | `scripts/build_behavior_baseline.py:1674` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：fixture_record.get("sha256") != _sha256_file(fixture_path) |  |  |  |  |  |
|  | 原因：人工复核：Golden 禁止符号链接和宿主路径泄露的检查属于安全边界 |  |  |  |  |  |
| `decision.17f8179df4ce3988794d` | `scripts/build_behavior_baseline.py:1677` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：runtime_fixture_record.get("path") != ( RUNTIME_FIXTURE_RELATIVE_PATH.as_posix() ) |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.3babdf923857902b7d1e` | `scripts/build_behavior_baseline.py:1681` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：runtime_fixture_record.get("sha256") != _sha256_file( runtime_fixture_path ) |  |  |  |  |  |
|  | 原因：人工复核：Golden 禁止符号链接和宿主路径泄露的检查属于安全边界 |  |  |  |  |  |
| `decision.1e4c9e6b436f09a9fced` | `scripts/build_behavior_baseline.py:1685` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：runtime_fixture_record.get("framework_dependency") != "none" |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.1874863f7711e8b2c7db` | `scripts/build_behavior_baseline.py:1697` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：record.get("classification") != classification |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.45f61540e7c872834245` | `scripts/build_behavior_baseline.py:1699` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：record.get("sha256") != _sha256_file(path) |  |  |  |  |  |
|  | 原因：人工复核：Golden 禁止符号链接和宿主路径泄露的检查属于安全边界 |  |  |  |  |  |
| `decision.1de126df46f2f30cd742` | `scripts/build_behavior_baseline.py:1739` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：__name__ == "__main__" |  |  |  |  |  |
|  | 原因：人工复核：行为 Golden 的文件类型、Schema 与批准链校验属于生成器协议 |  |  |  |  |  |
| `decision.2cdec36c001b54d55808` | `scripts/build_task_slo_manifest.py:48` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：payload.get("schema_version") != 1 |  |  |  |  |  |
|  | 原因：人工复核：Manifest Schema 版本和脚本入口是确定性的生成器协议 |  |  |  |  |  |
| `decision.4558826262c11835bcee` | `scripts/build_task_slo_manifest.py:59` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：numeric < 0 |  |  |  |  |  |
|  | 原因：人工复核：数值合法性、唯一基线和观测覆盖判断属于 Manifest 的确定性数据合同 |  |  |  |  |  |
| `decision.8e8c0912e939179825c5` | `scripts/build_task_slo_manifest.py:151` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 4 | `reviewed` |
|  | 摘要：token_coverage > 0 |  |  |  |  |  |
|  | 原因：人工复核：Token 覆盖是否存在只决定 SLO 观测完备性，不是凭据或权限安全边界 |  |  |  |  |  |
| `decision.06059235e760621a5eaa` | `scripts/build_task_slo_manifest.py:154` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：cost_coverage > 0 |  |  |  |  |  |
|  | 原因：人工复核：数值合法性、唯一基线和观测覆盖判断属于 Manifest 的确定性数据合同 |  |  |  |  |  |
| `decision.efa397ad81af7db89595` | `scripts/build_task_slo_manifest.py:172` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 4 | `reviewed` |
|  | 摘要：token_coverage <= 0 |  |  |  |  |  |
|  | 原因：人工复核：Token 覆盖是否存在只决定 SLO 观测完备性，不是凭据或权限安全边界 |  |  |  |  |  |
| `decision.7a030f7d246d79cc29ac` | `scripts/build_task_slo_manifest.py:176` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：cost_coverage <= 0 |  |  |  |  |  |
|  | 原因：人工复核：数值合法性、唯一基线和观测覆盖判断属于 Manifest 的确定性数据合同 |  |  |  |  |  |
| `decision.14e45f18873cb6bf0f4b` | `scripts/build_task_slo_manifest.py:212` | `python.numeric_control_flow` | `data_consistency` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：len(baseline_paths) != 1 |  |  |  |  |  |
|  | 原因：人工复核：数值合法性、唯一基线和观测覆盖判断属于 Manifest 的确定性数据合同 |  |  |  |  |  |
| `decision.ff2204ad1698b8327ca7` | `scripts/build_task_slo_manifest.py:288` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：__name__ == "__main__" |  |  |  |  |  |
|  | 原因：人工复核：Manifest Schema 版本和脚本入口是确定性的生成器协议 |  |  |  |  |  |
| `decision.e253efa286db6e7150f8` | `scripts/check_architecture.py:437` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 3／4 | `reviewed` |
|  | 摘要：root not in {"sqlalchemy", "core"} |  |  |  |  |  |
|  | 原因：人工复核：数据库 Port 合同禁止依赖 SQLAlchemy 和 core 实现层，是架构依赖方向门禁 |  |  |  |  |  |
| `decision.060fee0095598c4b9624` | `scripts/check_architecture.py:475` | `python.string_control_flow` | `security_invariant` | `preserve` | 阶段 4 | `reviewed` |
|  | 摘要：root not in { "api", "clients", "creatures", "fastapi", "nanobot_kt", "sandboxd", } |  |  |  |  |  |
|  | 原因：人工复核：数据库 Adapter 禁止反向依赖 API、KT 和外部交付层，是架构依赖方向门禁 |  |  |  |  |  |
| `decision.12a3c89aa526b429c8dd` | `scripts/generate_openapi_client.py:26` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$") |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.6fcc57e1e248d08ae9c9` | `scripts/generate_openapi_client.py:47` | `python.regex_call` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：_TS_IDENTIFIER_RE.fullmatch(name) |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.035b491006c677ed92d4` | `scripts/generate_openapi_client.py:60` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要："const" in schema |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.5eac180029af951a50cd` | `scripts/generate_openapi_client.py:83` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type == "string" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.12095280dda4e0b040d4` | `scripts/generate_openapi_client.py:85` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type in {"integer", "number"} |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.0beee2d3252937473ece` | `scripts/generate_openapi_client.py:87` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type == "boolean" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.248faa022683e4483838` | `scripts/generate_openapi_client.py:89` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type == "null" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.91024f59c3b9396e84fb` | `scripts/generate_openapi_client.py:91` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type == "array" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.a0dcf492d02fb82cd10b` | `scripts/generate_openapi_client.py:121` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：schema_type == "object" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.57cc69e185115d1d61ca` | `scripts/generate_openapi_client.py:199` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：str(status).startswith("2") |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.0b5f701a25d1aae139b3` | `scripts/generate_openapi_client.py:221` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：location not in {"path", "query"} |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.23689623b358d7cf555d` | `scripts/generate_openapi_client.py:229` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：location == "path" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.2987853d17fbf95fc183` | `scripts/generate_openapi_client.py:277` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：location == "path" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.f5bdf3ae305fa530d9f3` | `scripts/generate_openapi_client.py:282` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：location == "query" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.f0025231940c58c78d5f` | `scripts/generate_openapi_client.py:420` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：path.read_text(encoding="utf-8") != content |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.cb7e2d8718bf7b4e26c0` | `scripts/generate_openapi_client.py:446` | `python.string_control_flow` | `protocol_syntax` | `policy` | 阶段 3／4 | `reviewed` |
|  | 摘要：__name__ == "__main__" |  |  |  |  |  |
|  | 原因：人工复核：Schema 到 TypeScript 的类型投影、参数位置和漂移检查属于生成器协议语法，不承担业务语义判断 |  |  |  |  |  |
| `decision.e3745bb8bdbcf35a6145` | `scripts/migrate_group_learning_legacy.py:27` | `python.regex_call` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：re.compile(r"^[0-9a-f]{64}$") |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.5e5b30c3de67126b094f` | `scripts/migrate_group_learning_legacy.py:171` | `python.string_control_flow` | `compatibility` | `compatibility_migration` | 阶段 3／7D | `reviewed` |
|  | 摘要：__name__ == "__main__" |  |  |  |  |  |
|  | 原因：人工复核：属于旧身份、旧路由、旧交付模式或兼容模块，必须经有期限的迁移门禁退役 |  |  |  |  |  |
| `decision.c5830b85d3fa2a01a95b` | `webui/src/api/generated/adminClient.ts:645` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/audit-logs`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.ac7f748322a5ed2d86b3` | `webui/src/api/generated/adminClient.ts:667` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/candidates/${encodeURIComponent(String(args.candidate_id))}`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.f65a9af084772ecd85c0` | `webui/src/api/generated/adminClient.ts:685` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/candidates/${encodeURIComponent(String(args.candidate_id))}/review`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.e6ea020e87f89756e6ea` | `webui/src/api/generated/adminClient.ts:703` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/candidates`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.0993e8f3e7e756cfd7c4` | `webui/src/api/generated/adminClient.ts:719` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/descriptors`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.ebd4c08030331503b6f1` | `webui/src/api/generated/adminClient.ts:733` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/extract`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.33d236b57eb21a2d3cf4` | `webui/src/api/generated/adminClient.ts:747` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/features`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.cb5191be44db02514939` | `webui/src/api/generated/adminClient.ts:761` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/overview`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.6cfb550322f7491d44f8` | `webui/src/api/generated/adminClient.ts:775` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/rules/${encodeURIComponent(String(args.rule_id))}/activation`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.4b303819b993aa7c63a5` | `webui/src/api/generated/adminClient.ts:789` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/rules/dry-run`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.b2f9510d59a03111e239` | `webui/src/api/generated/adminClient.ts:804` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/runs`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.692b6ae61fb8bb0d6170` | `webui/src/api/generated/adminClient.ts:821` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/schedule/pause`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.99c80c03f1b548fcb9e5` | `webui/src/api/generated/adminClient.ts:836` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/schedule`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.4ec4bd7a14aa78a42dbc` | `webui/src/api/generated/adminClient.ts:850` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-learning/sessions`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.8cac9ed7b6c7f4365dac` | `webui/src/api/generated/adminClient.ts:867` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/${encodeURIComponent(String(args.group_id))}/extract`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.c6da9aa5fb55a66bac8f` | `webui/src/api/generated/adminClient.ts:882` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/${encodeURIComponent(String(args.group_id))}/injection-config`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.b5f8f95649279e64c4c3` | `webui/src/api/generated/adminClient.ts:897` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/${encodeURIComponent(String(args.group_id))}/injection-preview`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.4e3c1b3db00639cac57c` | `webui/src/api/generated/adminClient.ts:912` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/${encodeURIComponent(String(args.group_id))}/items`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.df1cf79f1ae871cc4d82` | `webui/src/api/generated/adminClient.ts:928` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/overview`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.aa4b35f7b08b11246647` | `webui/src/api/generated/adminClient.ts:945` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/group-memories/items/${encodeURIComponent(String(args.memory_id))}`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.976eb7bbfb97fd4872a7` | `webui/src/api/generated/adminClient.ts:956` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 1／3 | `reviewed` |
|  | 摘要：url: `/runtime/modules`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.26ed397c5f744a25b09a` | `webui/src/api/generated/adminClient.ts:970` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：url: `/tools/${encodeURIComponent(String(args.tool_name))}`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.4dbe545c4cba2271a0f8` | `webui/src/api/generated/adminClient.ts:988` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：url: `/tools`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.35400a58eda0bcd5a064` | `webui/src/api/generated/adminClient.ts:1010` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：url: `/tools/${encodeURIComponent(String(args.tool_name))}/override`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.00095ac7ccd93d140aa1` | `webui/src/api/generated/adminClient.ts:1028` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：url: `/tools/${encodeURIComponent(String(args.tool_name))}/override`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.0eaad8cd8456b7805cb7` | `webui/src/api/generated/adminClient.ts:1044` | `web.route_literal` | `protocol_syntax` | `resource` | 阶段 8 | `reviewed` |
|  | 摘要：url: `/tools/targets`, |  |  |  |  |  |
|  | 原因：人工复核：客户端路由字面量由 Endpoint Registry 和 OpenAPI 生成器管理，是不可手工编辑的公开协议资源 |  |  |  |  |  |
| `decision.0eb63edbe28803afb90c` | `webui/src/features/rag/RagBenchmarkPage.jsx:834` | `web.regex_literal` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：placeholder="搜索 case/query" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" /> |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.83cc62c6e20c91cd8197` | `webui/src/features/triggers/TriggersPage.test.jsx:134` | `web.regex_literal` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：expect(await screen.findByText(/执行 #33/)).toBeInTheDocument() |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
| `decision.d77dddd704e8b65f60e5` | `webui/src/features/triggers/TriggersPage.test.jsx:155` | `web.regex_literal` | `configurable_policy` | `policy` | 阶段 3／4 | `auto_classified` |
|  | 摘要：fireEvent.click(screen.getByRole('radio', { name: /固定正文/ })) |  |  |  |  |  |
|  | 原因：包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。 |  |  |  |  |  |
