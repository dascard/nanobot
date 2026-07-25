/* eslint-disable */
/* 此文件由 scripts/generate_openapi_client.py 生成；请勿手工修改。 */

import { api } from '../client'

export type ApiErrorResponse = {
  code?: (string) | (null)
  detail: (string) | (Array<Record<string, unknown>>) | (Record<string, unknown>)
  retryable?: (boolean) | (null)
  trace_ref?: (string) | (null)
  [key: string]: unknown
}

export type AuditLogItemResponse = {
  action: string
  admin_user: string
  created_at: string
  detail_json: unknown
  id: number
  ip_address: string
  target_id: string
  target_type: string
}

export type AuditLogListResponse = {
  items: Array<AuditLogItemResponse>
  total: number
}

export type GroupLearningAspectResponse = {
  aspect_id: string
  display_name: string
  lifecycle: string
  memory_type: string
  owner_module: string
  prompt_injectable: boolean
  schedule_default: boolean
  task_key: string
  writes_long_term_memory: boolean
}

export type GroupLearningCandidateDetailResponse = {
  candidate: GroupLearningCandidateResponse
  evidence: Array<GroupLearningEvidenceResponse>
  next_evidence_after_id: (number) | (null)
}

export type GroupLearningCandidateListResponse = {
  chat_stream_id: string
  items: Array<GroupLearningCandidateResponse>
  next_after_id: (number) | (null)
}

export type GroupLearningCandidateResponse = {
  alias_target_memory_id: (number) | (null)
  approval_source: string
  candidate_id: string
  candidate_type: string
  chat_stream_id: string
  conflict_group_id: string
  content: string
  first_seen_at: string
  hit_count: number
  human_action: string
  human_reviewed_at: string
  human_reviewer_id: string
  id: number
  last_seen_at: string
  meaning: string
  merge_target_memory_id: (number) | (null)
  model_contract_version: string
  model_decision: string
  model_review_run_id: string
  promoted_group_memory_id: (number) | (null)
  rejection_reason_code: string
  reviewed_content: string
  reviewed_meaning: string
  rule_id: string
  rule_version: number
  source: string
  source_run_id: string
  status: string
  updated_at: string
  version: number
  waiting_reason_code: string
}

export type GroupLearningDescriptorsResponse = {
  aspect_registry: GroupLearningRegistryResponse
  aspects: Array<GroupLearningAspectResponse>
  candidate_sources: Array<string>
  candidate_statuses: Array<string>
  candidate_types: Array<string>
  conflict_resolutions: Array<string>
  evidence_policies: Array<GroupLearningEvidencePolicyResponse>
  evidence_policy_registry: GroupLearningRegistryResponse
  feature_enabled: boolean
  human_actions: Array<string>
  model_actions: Array<string>
  rule_registry: GroupLearningRegistryResponse
  rules: Array<GroupLearningRuleResponse>
  run_statuses: Array<string>
  schedule_policy: GroupLearningSchedulePolicyResponse
}

export type GroupLearningDryRunRequest = {
  chat_stream_id?: string
  rule_ids?: (Array<string>) | (null)
  text: string
}

export type GroupLearningDryRunResponse = {
  effective_rule_ids: Array<string>
  elapsed_ms: number
  input_chars: number
  matches: Array<GroupLearningRuleMatchResponse>
  registry_generation: number
  registry_sha256: string
}

export type GroupLearningEvidencePolicyResponse = {
  candidate_type: string
  explicit_evidence_kinds: Array<string>
  min_evidence_count: number
  min_explicit_evidence_count: number
  min_sender_count: number
  owner_module: string
  policy_id: string
  same_sender_cross_batch_min_batches: number
  same_sender_cross_batch_min_hits: number
  version: number
}

export type GroupLearningEvidenceResponse = {
  available: boolean
  batch_id: string
  candidate_id: string
  chat_log_id: number
  content_preview: string
  created_at: string
  evidence_id: string
  evidence_kind: string
  id: number
  preview_redacted: boolean
  preview_truncated: boolean
  sender_ref: string
  source_run_id: string
}

export type GroupLearningExtractRequest = {
  aspects?: (Array<string>) | (null)
  instructions?: string
  reason: string
  request_id: string
  window_hours?: number
}

export type GroupLearningFeatureResponse = {
  enabled: boolean
  ok: boolean
  replayed?: boolean
}

export type GroupLearningFeatureUpdateRequest = {
  enabled: boolean
  reason: string
  request_id: string
}

export type GroupLearningGovernanceResponse = {
  ok: boolean
  replayed?: boolean
  result: Record<string, unknown>
}

export type GroupLearningOverviewResponse = {
  chat_stream_id: string
  counts: Record<string, number>
  disabled_rule_ids: Array<string>
  enabled_rule_ids: Array<string>
  feature_enabled: boolean
  group_profile_mode: string
  recent_run: (Record<string, unknown>) | (null)
  registry: Record<string, GroupLearningRegistryResponse>
  schedule: (Record<string, unknown>) | (null)
  selected_aspects: Array<string>
  stream_state: (Record<string, unknown>) | (null)
}

export type GroupLearningRegistryResponse = {
  generation: number
  sha256: string
}

export type GroupLearningReviewRequest = {
  action: "accept" | "edit_accept" | "reject" | "merge" | "resolve_conflict"
  conflict_resolution?: "" | "keep_target" | "replace_target"
  reason: string
  request_id: string
  reviewed_content: string
  reviewed_meaning?: string
  target_memory_id?: (number) | (null)
}

export type GroupLearningRuleActivationRequest = {
  chat_stream_id?: string
  enabled: boolean
  reason: string
  request_id: string
}

export type GroupLearningRuleActivationResponse = {
  chat_stream_id: string
  enabled: boolean
  global_disabled: Array<string>
  ok: boolean
  replayed?: boolean
  rule_id: string
  session_disabled: Record<string, Array<string>>
}

export type GroupLearningRuleMatchResponse = {
  candidate_type: string
  canonical_content: string
  end: number
  meaning: string
  rule_id: string
  rule_version: number
  start: number
}

export type GroupLearningRuleResponse = {
  candidate_type: string
  canonicalizer_id: string
  extractor_id: string
  globally_enabled: boolean
  lifecycle: string
  max_candidates_per_batch: number
  max_input_chars: number
  max_matches_per_message: number
  negative_fixtures: Array<string>
  owner_module: string
  pattern: string
  performance_budget_ms: number
  positive_fixtures: Array<string>
  rule_id: string
  scope: string
  version: number
}

export type GroupLearningRunListResponse = {
  chat_stream_id: string
  items: Array<GroupLearningRunResponse>
}

export type GroupLearningRunResponse = {
  accepted_count: number
  attempt_count: number
  candidate_count: number
  candidate_watermark: number
  chat_stream_id: string
  cleaned_message_count: number
  completed_at: string
  conflict_count: number
  context_end_chat_log_id: number
  context_start_chat_log_id: number
  cost_microusd: (number) | (null)
  created_at: string
  cursor_end_chat_log_id: number
  cursor_start_chat_log_id: number
  eligible_message_count: number
  error_code: string
  input_chars: number
  input_tokens: number
  job_id: string
  latency_ms: number
  mode: string
  model: string
  model_route: string
  output_tokens: number
  provider: string
  raw_message_count: number
  raw_output_bytes: number
  raw_output_sha256: string
  rejected_count: number
  rules_generation: number
  run_id: string
  selected_aspects: Array<string>
  started_at: string
  status: string
  task_contract_version: string
  task_run_id: string
  total_tokens: number
  trace_id: string
  trigger: string
  updated_at: string
  waiting_count: number
}

export type GroupLearningScheduleMutationResponse = {
  ok: boolean
  replayed?: boolean
  schedule: Record<string, unknown>
}

export type GroupLearningSchedulePauseRequest = {
  reason: string
  request_id: string
}

export type GroupLearningSchedulePolicyResponse = {
  context_message_limit: number
  default_interval_minutes: number
  default_window_hours: number
  max_interval_minutes: number
  max_new_messages: number
  max_window_hours: number
  min_interval_minutes: number
  min_new_messages: number
  min_window_hours: number
  policy_id: string
  version: string
}

export type GroupLearningSchedulePutRequest = {
  aspects?: (Array<string>) | (null)
  enabled?: boolean
  interval_minutes?: (number) | (null)
  reason: string
  request_id: string
  window_hours?: (number) | (null)
}

export type GroupLearningSessionItemResponse = {
  candidate_count: number
  chat_stream_id: string
  conflict_count: number
  group_profile_mode: string
  identity_status: string
  last_run_at: string
  last_run_status: string
  memory_count: number
  runtime_session_id: string
  schedule_enabled: boolean
  schedule_exists: boolean
  selected_aspects: Array<string>
  session_id: string
  session_name: string
  waiting_count: number
}

export type GroupLearningSessionListResponse = {
  items: Array<GroupLearningSessionItemResponse>
  total: number
}

export type GroupMemoryExtractRequest = {
  aspects?: (Array<string>) | (null)
  instructions?: string
  window_hours?: number
}

export type GroupMemoryExtractResponse = {
  active_count: number
  chat_stream_id: string
  deduped_count: number
  eligible_count: number
  group_id: string
  group_name: string
  injectable_count: number
  memories: Array<GroupMemoryItemResponse>
  memory_count: number
  message_count: number
  ok: boolean
  raw_count: number
  source_log_count: number
  stats: Record<string, number>
  total: number
  window_hours: (number) | (null)
}

export type GroupMemoryInjectionConfigRequest = {
  group_profile_mode?: "off" | "preview" | "on"
}

export type GroupMemoryInjectionConfigResponse = {
  chat_stream_id: string
  group_id: string
  group_profile_mode: string
  ok: boolean
}

export type GroupMemoryInjectionPreviewRequest = {
  max_chars?: number
  max_items?: number
  user_input?: string
}

export type GroupMemoryInjectionPreviewResponse = {
  debug: Record<string, unknown>
  group_id: string
  group_memory_context: string
  group_memory_context_chars: number
  group_memory_ids: Array<number>
  group_memory_skipped: Array<Record<string, unknown>>
  group_profile_mode: string
  score_components: Record<string, unknown>
}

export type GroupMemoryItemResponse = {
  chat_stream_id: string
  cluster_key: string
  confidence: number
  content: string
  content_hash: string
  decay_score: number
  disabled_reason: string
  evidence_count: number
  evidence_log_ids_json: string
  first_seen: string
  group_id: string
  id: number
  inject_policy: string
  injected_count: number
  last_injected_at: string
  last_seen: string
  memory_type: string
  merged_into_id: (number) | (null)
  meta_json: string
  rejected_reason: string
  source: string
  status: string
  updated_at: string
}

export type GroupMemoryListResponse = {
  chat_stream_id: string
  group_id: string
  memories: Array<GroupMemoryItemResponse>
  total: number
}

export type GroupMemoryOverviewItemResponse = {
  active_count: number
  group_id: string
  group_profile_mode: string
  injectable_count: number
  last_injected_at: string
  latest_log_at: string
  latest_memory_at: string
  log_count: number
  memory_count: number
  raw_group_id: string
  recent_injected_ids: Array<number>
  session_name: string
  stream_id: string
}

export type GroupMemoryOverviewResponse = {
  items: Array<GroupMemoryOverviewItemResponse>
  total: number
}

export type GroupMemoryUpdateRequest = {
  content?: (string) | (null)
  disabled_reason?: (string) | (null)
  inject_policy?: ("auto" | "manual_only" | "never") | (null)
  rejected_reason?: (string) | (null)
  status?: ("review" | "active" | "disabled" | "archived" | "rejected") | (null)
}

export type GroupMemoryUpdateResponse = {
  memory: GroupMemoryItemResponse
  ok: boolean
}

export type RuntimeModuleContributionResponse = {
  contribution_id: string
  kind: string
}

export type RuntimeModuleDiagnosticsResponse = {
  available: boolean
  composition_generation: number
  composition_sha256: string
  composition_state: string
  contribution_registry: RuntimeRegistrySnapshotResponse
  error_code: string
  module_registry: RuntimeRegistrySnapshotResponse
  modules: Array<RuntimeModuleManifestResponse>
  ready: boolean
  verification_registry: RuntimeRegistrySnapshotResponse
  verification_suites: Array<RuntimeVerificationSuiteResponse>
}

export type RuntimeModuleHealthCheckResponse = {
  detail_code: string
  healthy: boolean
  name: string
}

export type RuntimeModuleHealthResponse = {
  checks: Array<RuntimeModuleHealthCheckResponse>
  ready: boolean
  status: string
}

export type RuntimeModuleManifestResponse = {
  compatibility_aliases: Array<string>
  contributions: Array<RuntimeModuleContributionResponse>
  domain: string
  feature_flag: string
  health: (RuntimeModuleHealthResponse) | (null)
  health_checks: Array<string>
  lifecycle: string
  module_id: string
  optional_modules: Array<string>
  owner: string
  provided_capabilities: Array<string>
  readiness_checks: Array<string>
  release_impacts: Array<string>
  required_modules: Array<string>
  shutdown_phase: number
  startup_phase: number
  version: string
}

export type RuntimeRegistrySnapshotResponse = {
  generation: number
  namespace: string
  sha256: string
}

export type RuntimeVerificationSuiteResponse = {
  allow_skip: boolean
  always_required: boolean
  applicable_release_impacts: Array<string>
  artifact_profiles: Array<string>
  cleanup: Array<string>
  command: Array<string>
  dependencies: Array<string>
  feature_enablement_gates: Array<string>
  feature_lifecycle_states: Array<string>
  output_artifacts: Array<string>
  owner: string
  preconditions: Array<string>
  required_credentials: Array<string>
  security_level: string
  success_criteria: Array<string>
  suite_id: string
  timeout_seconds: number
  working_directory: string
}

export type ToolListItemResponse = {
  category: string
  configured_disabled_reason: string
  configured_enabled: boolean
  description: string
  disabled_reason: string
  effective: boolean
  execution_port_id: string
  force_disabled: boolean
  force_disabled_group: boolean
  force_enabled: boolean
  group_default: boolean
  is_subagent: boolean
  label: string
  lightweight_default: boolean
  name: string
  override_enabled: (boolean) | (null)
  override_present: boolean
  private_default: boolean
  private_superuser_default: boolean
  registered: (boolean) | (null)
  registration_lifecycle: string
  risk_level: string
  runtime_disabled_reason: string
  runtime_effective: boolean
  sandbox_managed: boolean
  schema_provider_id: string
}

export type ToolListResponse = {
  bridge_count: number
  platform: string
  registry_available: boolean
  registry_empty: boolean
  registry_info: Record<string, unknown>
  runtime_preset: string
  tool_registration: ToolRegistrationSummaryResponse
  tools: Array<ToolListItemResponse>
}

export type ToolMutationResponse = {
  ok: boolean
  tool?: (string) | (null)
}

export type ToolOverrideBody = {
  enabled: boolean
  reason?: string
  scope_id: string
  scope_type: "group" | "user" | "chat_type" | "platform"
}

export type ToolRegistrationSummaryResponse = {
  generation: number
  sha256: string
}

export type ToolTargetItemResponse = {
  id: string
  label: string
  name: string
  recent_at: string
  scope_type: string
  source: string
}

export type ToolTargetsResponse = {
  items: Array<ToolTargetItemResponse>
  scope_type: string
}

export type ToolUpdateBody = {
  group_default?: (boolean) | (null)
  lightweight_default?: (boolean) | (null)
  private_default?: (boolean) | (null)
  private_superuser_default?: (boolean) | (null)
}

/** Endpoint Contract: admin.audit_logs.list */
export async function listAuditLogs(
  args: {
    page?: number
    limit?: number
    action?: string
    target_type?: string
    since?: string
  } = {},
): Promise<AuditLogListResponse> {
  const response = await api.request<AuditLogListResponse>({
    method: 'GET',
    url: `/audit-logs`,
    params: {
      page: args.page,
      limit: args.limit,
      action: args.action,
      target_type: args.target_type,
      since: args.since,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.candidate_detail */
export async function getGroupLearningCandidate(
  args: {
    candidate_id: string
    evidence_after_id?: number
    evidence_limit?: number
  },
): Promise<GroupLearningCandidateDetailResponse> {
  const response = await api.request<GroupLearningCandidateDetailResponse>({
    method: 'GET',
    url: `/group-learning/candidates/${encodeURIComponent(String(args.candidate_id))}`,
    params: {
      evidence_after_id: args.evidence_after_id,
      evidence_limit: args.evidence_limit,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.candidate_review */
export async function reviewGroupLearningCandidate(
  args: {
    candidate_id: string
    body: GroupLearningReviewRequest
  },
): Promise<GroupLearningGovernanceResponse> {
  const response = await api.request<GroupLearningGovernanceResponse>({
    method: 'POST',
    url: `/group-learning/candidates/${encodeURIComponent(String(args.candidate_id))}/review`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.candidates */
export async function listGroupLearningCandidates(
  args: {
    chat_stream_id: string
    candidate_type?: string
    status?: string
    after_id?: number
    limit?: number
  },
): Promise<GroupLearningCandidateListResponse> {
  const response = await api.request<GroupLearningCandidateListResponse>({
    method: 'GET',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/candidates`,
    params: {
      candidate_type: args.candidate_type,
      status: args.status,
      after_id: args.after_id,
      limit: args.limit,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.descriptors */
export async function getGroupLearningDescriptors(
): Promise<GroupLearningDescriptorsResponse> {
  const response = await api.request<GroupLearningDescriptorsResponse>({
    method: 'GET',
    url: `/group-learning/descriptors`,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.extract */
export async function extractGroupLearningSession(
  args: {
    chat_stream_id: string
    body: GroupLearningExtractRequest
  },
): Promise<GroupMemoryExtractResponse> {
  const response = await api.request<GroupMemoryExtractResponse>({
    method: 'POST',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/extract`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.feature_update */
export async function updateGroupLearningFeature(
  args: {
    body: GroupLearningFeatureUpdateRequest
  },
): Promise<GroupLearningFeatureResponse> {
  const response = await api.request<GroupLearningFeatureResponse>({
    method: 'PUT',
    url: `/group-learning/features`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.overview */
export async function getGroupLearningOverview(
  args: {
    chat_stream_id: string
  },
): Promise<GroupLearningOverviewResponse> {
  const response = await api.request<GroupLearningOverviewResponse>({
    method: 'GET',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/overview`,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.rule_activation */
export async function setGroupLearningRuleActivation(
  args: {
    rule_id: string
    body: GroupLearningRuleActivationRequest
  },
): Promise<GroupLearningRuleActivationResponse> {
  const response = await api.request<GroupLearningRuleActivationResponse>({
    method: 'PUT',
    url: `/group-learning/rules/${encodeURIComponent(String(args.rule_id))}/activation`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.rules_dry_run */
export async function dryRunGroupLearningRules(
  args: {
    body: GroupLearningDryRunRequest
  },
): Promise<GroupLearningDryRunResponse> {
  const response = await api.request<GroupLearningDryRunResponse>({
    method: 'POST',
    url: `/group-learning/rules/dry-run`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.runs */
export async function listGroupLearningRuns(
  args: {
    chat_stream_id: string
    limit?: number
  },
): Promise<GroupLearningRunListResponse> {
  const response = await api.request<GroupLearningRunListResponse>({
    method: 'GET',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/runs`,
    params: {
      limit: args.limit,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.schedule_pause */
export async function pauseGroupLearningSchedule(
  args: {
    chat_stream_id: string
    body: GroupLearningSchedulePauseRequest
  },
): Promise<GroupLearningScheduleMutationResponse> {
  const response = await api.request<GroupLearningScheduleMutationResponse>({
    method: 'POST',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/schedule/pause`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.schedule_put */
export async function putGroupLearningSchedule(
  args: {
    chat_stream_id: string
    body: GroupLearningSchedulePutRequest
  },
): Promise<GroupLearningScheduleMutationResponse> {
  const response = await api.request<GroupLearningScheduleMutationResponse>({
    method: 'PUT',
    url: `/group-learning/sessions/${encodeURIComponent(String(args.chat_stream_id))}/schedule`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_learning.sessions */
export async function listGroupLearningSessions(
  args: {
    limit?: number
  } = {},
): Promise<GroupLearningSessionListResponse> {
  const response = await api.request<GroupLearningSessionListResponse>({
    method: 'GET',
    url: `/group-learning/sessions`,
    params: {
      limit: args.limit,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.extract */
export async function extractGroupMemories(
  args: {
    group_id: string
    body: GroupMemoryExtractRequest
  },
): Promise<GroupMemoryExtractResponse> {
  const response = await api.request<GroupMemoryExtractResponse>({
    method: 'POST',
    url: `/group-memories/${encodeURIComponent(String(args.group_id))}/extract`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.injection_config */
export async function setGroupMemoryInjectionConfig(
  args: {
    group_id: string
    body: GroupMemoryInjectionConfigRequest
  },
): Promise<GroupMemoryInjectionConfigResponse> {
  const response = await api.request<GroupMemoryInjectionConfigResponse>({
    method: 'PUT',
    url: `/group-memories/${encodeURIComponent(String(args.group_id))}/injection-config`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.injection_preview */
export async function previewGroupMemoryInjection(
  args: {
    group_id: string
    body: GroupMemoryInjectionPreviewRequest
  },
): Promise<GroupMemoryInjectionPreviewResponse> {
  const response = await api.request<GroupMemoryInjectionPreviewResponse>({
    method: 'POST',
    url: `/group-memories/${encodeURIComponent(String(args.group_id))}/injection-preview`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.items */
export async function listGroupMemoryItems(
  args: {
    group_id: string
    memory_type?: string
  },
): Promise<GroupMemoryListResponse> {
  const response = await api.request<GroupMemoryListResponse>({
    method: 'GET',
    url: `/group-memories/${encodeURIComponent(String(args.group_id))}/items`,
    params: {
      memory_type: args.memory_type,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.overview */
export async function listGroupMemoryOverview(
  args: {
    limit?: number
  } = {},
): Promise<GroupMemoryOverviewResponse> {
  const response = await api.request<GroupMemoryOverviewResponse>({
    method: 'GET',
    url: `/group-memories/overview`,
    params: {
      limit: args.limit,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.group_memory.update_item */
export async function updateGroupMemoryItem(
  args: {
    memory_id: number
    body: GroupMemoryUpdateRequest
  },
): Promise<GroupMemoryUpdateResponse> {
  const response = await api.request<GroupMemoryUpdateResponse>({
    method: 'PATCH',
    url: `/group-memories/items/${encodeURIComponent(String(args.memory_id))}`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.runtime.modules */
export async function getRuntimeModuleDiagnostics(
): Promise<RuntimeModuleDiagnosticsResponse> {
  const response = await api.request<RuntimeModuleDiagnosticsResponse>({
    method: 'GET',
    url: `/runtime/modules`,
  })
  return response.data
}

/** Endpoint Contract: admin.tools.defaults_update */
export async function updateToolDefaults(
  args: {
    tool_name: string
    body: ToolUpdateBody
  },
): Promise<ToolMutationResponse> {
  const response = await api.request<ToolMutationResponse>({
    method: 'PUT',
    url: `/tools/${encodeURIComponent(String(args.tool_name))}`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.tools.list */
export async function listTools(
  args: {
    chat_type?: string
    group_id?: string
    user_id?: string
    platform?: string
    runtime_preset?: string
  } = {},
): Promise<ToolListResponse> {
  const response = await api.request<ToolListResponse>({
    method: 'GET',
    url: `/tools`,
    params: {
      chat_type: args.chat_type,
      group_id: args.group_id,
      user_id: args.user_id,
      platform: args.platform,
      runtime_preset: args.runtime_preset,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.tools.override_delete */
export async function deleteToolOverride(
  args: {
    tool_name: string
    scope_type?: string
    scope_id?: string
  },
): Promise<ToolMutationResponse> {
  const response = await api.request<ToolMutationResponse>({
    method: 'DELETE',
    url: `/tools/${encodeURIComponent(String(args.tool_name))}/override`,
    params: {
      scope_type: args.scope_type,
      scope_id: args.scope_id,
    },
  })
  return response.data
}

/** Endpoint Contract: admin.tools.override_set */
export async function setToolOverride(
  args: {
    tool_name: string
    body: ToolOverrideBody
  },
): Promise<ToolMutationResponse> {
  const response = await api.request<ToolMutationResponse>({
    method: 'PUT',
    url: `/tools/${encodeURIComponent(String(args.tool_name))}/override`,
    data: args.body,
  })
  return response.data
}

/** Endpoint Contract: admin.tools.targets */
export async function listToolTargets(
  args: {
    scope_type?: string
    search?: string
    limit?: number
  } = {},
): Promise<ToolTargetsResponse> {
  const response = await api.request<ToolTargetsResponse>({
    method: 'GET',
    url: `/tools/targets`,
    params: {
      scope_type: args.scope_type,
      search: args.search,
      limit: args.limit,
    },
  })
  return response.data
}
