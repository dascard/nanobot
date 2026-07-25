import { useMemo, useState } from 'react'

import {
  dryRunGroupLearningRules,
  getGroupLearningCandidate,
  pauseGroupLearningSchedule,
  putGroupLearningSchedule,
  reviewGroupLearningCandidate,
  setGroupLearningRuleActivation,
} from '../../api/generated/adminClient'
import {
  ActionButton,
  Badge,
  Card,
  JsonBlock,
  MiniStat,
  Spinner,
} from '../../components/ui'
import { MutationConfirmation } from './MutationConfirmation'
import {
  formatGroupLearningError,
  shortHash,
  statusTone,
} from './utils'

const ACTION_LABELS = {
  accept: '接受',
  edit_accept: '编辑后接受',
  reject: '拒绝',
  merge: '合并到正式记忆',
  resolve_conflict: '解决冲突',
}

function RegistryCard({ label, value }) {
  return (
    <Card className="p-3">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-1 text-xs text-slate-300">
        generation {value?.generation || 0}
      </div>
      <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
        {value?.sha256 || '-'}
      </div>
    </Card>
  )
}

export function OverviewPanel({ overview, session }) {
  if (!overview) {
    return (
      <Card className="py-16 text-center text-sm text-slate-600">
        请选择 canonical 群 session
      </Card>
    )
  }
  const schedule = overview.schedule || {}
  const state = overview.stream_state || {}
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <MiniStat label="正式记忆" value={overview.counts?.memories || 0} />
        <MiniStat label="学习候选" value={overview.counts?.candidates || 0} tone="blue" />
        <MiniStat label="冲突" value={overview.counts?.conflicts || 0} tone="amber" />
        <MiniStat label="等待证据" value={overview.counts?.waiting || 0} tone="amber" />
        <MiniStat label="已拒绝" value={overview.counts?.rejected || 0} />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-slate-200">学习控制面</h2>
            <Badge tone={overview.feature_enabled ? 'emerald' : 'red'}>
              kill switch {overview.feature_enabled ? 'enabled' : 'disabled'}
            </Badge>
          </div>
          <div className="space-y-2 text-xs text-slate-400">
            <div>session: <span className="font-mono text-slate-300">{overview.chat_stream_id}</span></div>
            <div>白名单: <Badge tone={schedule.enabled ? 'emerald' : 'slate'}>{schedule.enabled ? 'enabled' : 'disabled'}</Badge></div>
            <div>aspects: {(overview.selected_aspects || []).join(', ') || '-'}</div>
            <div>interval: {schedule.interval_minutes ?? '-'} min</div>
            <div>window: {schedule.window_hours ?? '-'} h</div>
            <div>next run: {schedule.next_run_at || '-'}</div>
          </div>
        </Card>
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-slate-200">Prompt 注入</h2>
            <Badge tone={overview.group_profile_mode === 'on' ? 'emerald' : overview.group_profile_mode === 'preview' ? 'blue' : 'slate'}>
              {overview.group_profile_mode || 'off'}
            </Badge>
          </div>
          <p className="text-xs leading-5 text-slate-400">
            Prompt 注入与 <code>group_learning.enabled</code> 是两条独立控制链。当前工作台只读展示注入状态，不通过旧写接口绕过 request_id 和审计原因合同。
          </p>
          <div className="mt-3 space-y-1 text-[11px] text-slate-500">
            <div>success cursor: {state.success_cursor_chat_log_id ?? '-'}</div>
            <div>candidate cursor: {state.candidate_cursor_chat_log_id ?? '-'}</div>
            <div>last success: {state.last_success_at || '-'}</div>
            <div>last error: {state.last_error_code || '-'}</div>
          </div>
        </Card>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <RegistryCard label="Aspect Registry" value={overview.registry?.aspects} />
        <RegistryCard label="Rule Registry" value={overview.registry?.rules} />
        <RegistryCard label="Evidence Registry" value={overview.registry?.evidence} />
      </div>

      {overview.recent_run && (
        <Card className="p-4">
          <h2 className="mb-3 text-sm font-medium text-slate-200">最近运行</h2>
          <div className="grid gap-2 text-xs text-slate-400 md:grid-cols-4">
            <span>status: <Badge tone={statusTone(overview.recent_run.status)}>{overview.recent_run.status}</Badge></span>
            <span>trigger: {overview.recent_run.trigger || '-'}</span>
            <span>candidates: {overview.recent_run.candidate_count || 0}</span>
            <span>latency: {overview.recent_run.latency_ms || 0} ms</span>
          </div>
        </Card>
      )}

      {session && (
        <div className="text-[11px] text-slate-500">
          发现来源：{session.runtime_session_id || session.session_id} · identity {session.identity_status}
        </div>
      )}
    </div>
  )
}

export function MemoriesPanel({ descriptors, memories, loading }) {
  const [memoryType, setMemoryType] = useState('')
  const types = useMemo(() => (
    [...new Set(
      (descriptors?.aspects || [])
        .map(item => item.memory_type)
        .filter(Boolean),
    )]
  ), [descriptors])
  const filtered = memoryType
    ? memories.filter(item => item.memory_type === memoryType)
    : memories

  if (loading) return <Spinner />
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-slate-200">正式长期记忆</h2>
          <p className="mt-1 text-[11px] text-slate-500">
            正式事实源为 GroupMemory。首版只读展示，避免旧写合同绕过 request_id 与 reason。
          </p>
        </div>
        <label className="text-[11px] text-slate-500">
          类型
          <select
            value={memoryType}
            onChange={event => setMemoryType(event.target.value)}
            className="ml-2 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
          >
            <option value="">全部</option>
            {types.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>

      {filtered.map(memory => (
        <Card key={memory.id} className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{memory.memory_type}</Badge>
                <Badge tone={statusTone(memory.status)}>{memory.status}</Badge>
                <Badge tone={memory.inject_policy === 'auto' ? 'emerald' : 'slate'}>
                  {memory.inject_policy}
                </Badge>
              </div>
              <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-200">
                {memory.content}
              </div>
            </div>
            <div className="text-right text-[11px] text-slate-500">
              <div>id {memory.id}</div>
              <div>{memory.updated_at || '-'}</div>
            </div>
          </div>
          <div className="mt-3 grid gap-2 text-[11px] text-slate-500 md:grid-cols-4">
            <span>confidence {Number(memory.confidence || 0).toFixed(2)}</span>
            <span>evidence {memory.evidence_count || 0}</span>
            <span>source {memory.source || '-'}</span>
            <span>injected {memory.injected_count || 0}</span>
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-[11px] text-slate-500">
              证据引用
            </summary>
            <JsonBlock
              value={memory.evidence_log_ids_json || '[]'}
              className="mt-2 max-h-36"
            />
          </details>
        </Card>
      ))}
      {filtered.length === 0 && (
        <Card className="py-16 text-center text-sm text-slate-600">
          当前 session 没有匹配的正式记忆
        </Card>
      )}
    </div>
  )
}

function CandidateReviewDialog({
  candidate,
  descriptors,
  memories,
  busy,
  onClose,
  onMutate,
}) {
  const defaultAction = candidate?.status === 'conflict'
    ? 'resolve_conflict'
    : 'accept'
  const [action, setAction] = useState(defaultAction)
  const [content, setContent] = useState(candidate?.content || '')
  const [meaning, setMeaning] = useState(candidate?.meaning || '')
  const [targetMemoryId, setTargetMemoryId] = useState(
    candidate?.merge_target_memory_id || '',
  )
  const [resolution, setResolution] = useState('keep_target')

  if (!candidate) return null
  const actions = descriptors?.human_actions || []
  const compatibleTargets = memories.filter(
    item => item.memory_type === candidate.candidate_type && item.status === 'active',
  )

  const submit = ({ reason, requestId }) => onMutate(
    `审核候选 ${candidate.candidate_id}`,
    () => reviewGroupLearningCandidate({
      candidate_id: candidate.candidate_id,
      body: {
        request_id: requestId,
        reason,
        action,
        reviewed_content: content || candidate.content,
        reviewed_meaning: meaning,
        target_memory_id: (
          ['merge', 'resolve_conflict'].includes(action)
            ? Number(targetMemoryId)
            : null
        ),
        conflict_resolution: action === 'resolve_conflict' ? resolution : '',
      },
    }),
    onClose,
  )

  return (
    <MutationConfirmation
      open
      title="人工审核学习候选"
      description="人工审核或修改后的结果不会再次交给模型复审。目标正式记忆必须属于同一 canonical session 且类型一致。"
      requestPrefix="candidate-review"
      busy={busy}
      onCancel={onClose}
      onConfirm={submit}
    >
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          动作
          <select
            value={action}
            onChange={event => setAction(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
          >
            {actions.map(value => (
              <option key={value} value={value}>{ACTION_LABELS[value] || value}</option>
            ))}
          </select>
        </label>
        {['merge', 'resolve_conflict'].includes(action) && (
          <label className="text-xs text-slate-400">
            目标正式记忆
            <select
              value={targetMemoryId}
              onChange={event => setTargetMemoryId(event.target.value)}
              required
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
            >
              <option value="">请选择</option>
              {compatibleTargets.map(item => (
                <option key={item.id} value={item.id}>
                  #{item.id} {item.content.slice(0, 60)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {action === 'resolve_conflict' && (
        <label className="block text-xs text-slate-400">
          冲突处理
          <select
            value={resolution}
            onChange={event => setResolution(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
          >
            {(descriptors?.conflict_resolutions || []).map(value => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
      )}
      <label className="block text-xs text-slate-400">
        正文
        <textarea
          value={content}
          onChange={event => setContent(event.target.value)}
          rows={3}
          maxLength={4000}
          required
          className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
        />
      </label>
      <label className="block text-xs text-slate-400">
        释义
        <textarea
          value={meaning}
          onChange={event => setMeaning(event.target.value)}
          rows={2}
          maxLength={4000}
          className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
        />
      </label>
    </MutationConfirmation>
  )
}

export function CandidatesPanel({
  descriptors,
  candidates,
  memories,
  loading,
  conflictsOnly = false,
  busy,
  onMutate,
}) {
  const [status, setStatus] = useState('')
  const [candidateType, setCandidateType] = useState('')
  const [source, setSource] = useState('')
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [reviewing, setReviewing] = useState(null)

  const effectiveStatus = conflictsOnly ? 'conflict' : status

  const filtered = candidates.filter(item => (
    (!conflictsOnly || item.status === 'conflict')
    && (!effectiveStatus || item.status === effectiveStatus)
    && (!candidateType || item.candidate_type === candidateType)
    && (!source || item.source === source)
  ))

  const openDetail = async candidate => {
    setSelected(candidate)
    setDetail(null)
    setDetailError('')
    setDetailLoading(true)
    try {
      setDetail(await getGroupLearningCandidate({
        candidate_id: candidate.candidate_id,
      }))
    } catch (loadError) {
      setDetailError(formatGroupLearningError(
        loadError,
        '候选详情加载失败',
      ))
    } finally {
      setDetailLoading(false)
    }
  }

  if (loading) return <Spinner />
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="候选状态"
          value={effectiveStatus}
          onChange={event => setStatus(event.target.value)}
          disabled={conflictsOnly}
          className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
        >
          <option value="">全部状态</option>
          {(descriptors?.candidate_statuses || []).map(value => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
        <select
          aria-label="候选类型"
          value={candidateType}
          onChange={event => setCandidateType(event.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
        >
          <option value="">全部类型</option>
          {(descriptors?.candidate_types || []).map(value => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
        <select
          aria-label="候选来源"
          value={source}
          onChange={event => setSource(event.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
        >
          <option value="">全部来源</option>
          {(descriptors?.candidate_sources || []).map(value => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.8fr)]">
        <div className="space-y-2">
          {filtered.map(candidate => (
            <button
              key={candidate.candidate_id}
              type="button"
              onClick={() => openDetail(candidate)}
              className={`w-full rounded-lg border p-3 text-left transition-colors ${selected?.candidate_id === candidate.candidate_id ? 'border-emerald-500/50 bg-emerald-500/5' : 'border-slate-800 bg-slate-900 hover:bg-slate-800/70'}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{candidate.candidate_type}</Badge>
                  <Badge tone={statusTone(candidate.status)}>{candidate.status}</Badge>
                  <Badge>{candidate.source}</Badge>
                </div>
                <span className="text-[11px] text-slate-500">hits {candidate.hit_count}</span>
              </div>
              <div className="mt-2 text-sm text-slate-200">{candidate.content}</div>
              {candidate.meaning && (
                <div className="mt-1 text-xs text-slate-400">{candidate.meaning}</div>
              )}
              <div className="mt-2 text-[11px] text-slate-500">
                rule {candidate.rule_id || '-'} v{candidate.rule_version || 0} · model {candidate.model_decision || '-'}
              </div>
            </button>
          ))}
          {filtered.length === 0 && (
            <Card className="py-16 text-center text-sm text-slate-600">
              没有匹配的学习候选
            </Card>
          )}
        </div>

        <Card className="min-h-64 p-4">
          {!selected ? (
            <div className="py-16 text-center text-sm text-slate-600">选择候选查看受限证据</div>
          ) : detailLoading ? <Spinner /> : detail ? (
            <div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-slate-300">{selected.candidate_id}</span>
                <ActionButton
                  type="button"
                  tone="emerald"
                  onClick={() => setReviewing(detail.candidate)}
                >
                  人工审核
                </ActionButton>
              </div>
              <div className="mt-3 grid gap-2 text-[11px] text-slate-500 md:grid-cols-2">
                <span>approval {detail.candidate.approval_source || '-'}</span>
                <span>waiting {detail.candidate.waiting_reason_code || '-'}</span>
                <span>conflict {detail.candidate.conflict_group_id || '-'}</span>
                <span>run {detail.candidate.source_run_id || '-'}</span>
              </div>
              <h3 className="mb-2 mt-4 text-xs font-medium text-slate-300">Evidence 受限预览</h3>
              <div className="space-y-2">
                {(detail.evidence || []).map(item => (
                  <div key={item.evidence_id} className="rounded-lg border border-slate-800 bg-slate-950 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
                      <span>{item.evidence_kind} · {item.sender_ref || 'sender:redacted'}</span>
                      <span>log {item.chat_log_id}</span>
                    </div>
                    <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">
                      {item.available ? item.content_preview || '-' : '[不可用]'}
                    </div>
                    {(item.preview_redacted || item.preview_truncated) && (
                      <div className="mt-1 text-[11px] text-amber-300">
                        {item.preview_redacted ? '已脱敏 ' : ''}
                        {item.preview_truncated ? '已截断' : ''}
                      </div>
                    )}
                  </div>
                ))}
                {(detail.evidence || []).length === 0 && (
                  <div className="text-xs text-slate-600">没有可展示的 Evidence</div>
                )}
              </div>
            </div>
          ) : (
            <div className="py-16 text-center text-sm text-red-300">
              {detailError || '候选详情加载失败'}
            </div>
          )}
        </Card>
      </div>

      {reviewing && (
        <CandidateReviewDialog
          key={reviewing.candidate_id}
          candidate={reviewing}
          descriptors={descriptors}
          memories={memories}
          busy={busy}
          onClose={() => setReviewing(null)}
          onMutate={onMutate}
        />
      )}
    </div>
  )
}

export function RulesPanel({
  descriptors,
  overview,
  sessionId,
  busy,
  onMutate,
}) {
  const [dryRunText, setDryRunText] = useState('')
  const [selectedRuleIds, setSelectedRuleIds] = useState(null)
  const [dryRunResult, setDryRunResult] = useState(null)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [dryRunError, setDryRunError] = useState('')
  const [activation, setActivation] = useState(null)

  const effectiveSelectedRuleIds = selectedRuleIds
    ?? overview?.enabled_rule_ids
    ?? []

  const runDryRun = async () => {
    if (!dryRunText.trim()) return
    setDryRunLoading(true)
    setDryRunError('')
    try {
      setDryRunResult(await dryRunGroupLearningRules({
        body: {
          text: dryRunText,
          chat_stream_id: sessionId,
          rule_ids: (
            effectiveSelectedRuleIds.length > 0
              ? effectiveSelectedRuleIds
              : null
          ),
        },
      }))
    } catch (runError) {
      setDryRunResult(null)
      setDryRunError(formatGroupLearningError(
        runError,
        '规则 Dry-run 失败',
      ))
    } finally {
      setDryRunLoading(false)
    }
  }

  const confirmActivation = ({ reason, requestId }) => onMutate(
    `${activation.scope === 'global' ? '全局' : 'session'} ${activation.enabled ? '启用' : '停用'}规则 ${activation.rule.rule_id}`,
    () => setGroupLearningRuleActivation({
      rule_id: activation.rule.rule_id,
      body: {
        request_id: requestId,
        reason,
        enabled: activation.enabled,
        chat_stream_id: activation.scope === 'session' ? sessionId : '',
      },
    }),
    () => setActivation(null),
  )

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-sm font-medium text-slate-200">规则 Dry-run</h2>
        <p className="mt-1 text-[11px] text-slate-500">
          Dry-run 只执行当前有效规则，不能绕过全局或 session 级停用。
        </p>
        <label className="mt-3 block text-[11px] text-slate-500">
          待测试文本
          <textarea
            value={dryRunText}
            onChange={event => setDryRunText(event.target.value)}
            rows={4}
            maxLength={10000}
            placeholder="输入待测试文本；内容仅用于本次规则匹配"
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
          />
        </label>
        <div className="mt-2 flex flex-wrap gap-2">
          {(descriptors?.rules || []).map(rule => (
            <label key={rule.rule_id} className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1 text-[11px] text-slate-400">
              <input
                type="checkbox"
                checked={effectiveSelectedRuleIds.includes(rule.rule_id)}
                disabled={!overview?.enabled_rule_ids?.includes(rule.rule_id)}
                onChange={event => setSelectedRuleIds(current => {
                  const baseline = current ?? overview?.enabled_rule_ids ?? []
                  return event.target.checked
                    ? [...baseline, rule.rule_id]
                    : baseline.filter(value => value !== rule.rule_id)
                })}
              />
              {rule.rule_id}
            </label>
          ))}
        </div>
        <ActionButton
          type="button"
          tone="blue"
          className="mt-3"
          onClick={runDryRun}
          disabled={!dryRunText.trim() || dryRunLoading}
        >
          {dryRunLoading ? '执行中…' : '执行 Dry-run'}
        </ActionButton>
        {dryRunError && (
          <div role="alert" className="mt-3 text-xs text-red-300">
            {dryRunError}
          </div>
        )}
        {dryRunResult && (
          <div className="mt-3">
            <div className="mb-2 text-[11px] text-slate-500">
              {dryRunResult.matches.length} 命中 · {dryRunResult.elapsed_ms} ms · registry {shortHash(dryRunResult.registry_sha256)}
            </div>
            <JsonBlock value={dryRunResult.matches} className="max-h-64" />
          </div>
        )}
      </Card>

      {(descriptors?.rules || []).map(rule => {
        const globallyEnabled = rule.globally_enabled
        const sessionEnabled = overview?.enabled_rule_ids?.includes(rule.rule_id)
        return (
          <Card key={rule.rule_id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-slate-200">{rule.rule_id}</span>
                  <Badge>{rule.candidate_type}</Badge>
                  <Badge tone={globallyEnabled ? 'emerald' : 'red'}>
                    global {globallyEnabled ? 'on' : 'off'}
                  </Badge>
                  <Badge tone={sessionEnabled ? 'emerald' : 'red'}>
                    session {sessionEnabled ? 'on' : 'off'}
                  </Badge>
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                  owner {rule.owner_module} · v{rule.version} · lifecycle {rule.lifecycle}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <ActionButton
                  type="button"
                  tone={globallyEnabled ? 'red' : 'emerald'}
                  onClick={() => setActivation({
                    rule,
                    scope: 'global',
                    enabled: !globallyEnabled,
                  })}
                >
                  全局{globallyEnabled ? '停用' : '启用'}
                </ActionButton>
                <ActionButton
                  type="button"
                  tone={sessionEnabled ? 'red' : 'emerald'}
                  disabled={!globallyEnabled && !sessionEnabled}
                  onClick={() => setActivation({
                    rule,
                    scope: 'session',
                    enabled: !sessionEnabled,
                  })}
                >
                  当前 session {sessionEnabled ? '停用' : '启用'}
                </ActionButton>
              </div>
            </div>
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-slate-400">
                只读规则、预算和样例
              </summary>
              <div className="mt-3 space-y-3 text-xs">
                <pre className="overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] text-slate-300">
                  {rule.pattern}
                </pre>
                <div className="grid gap-3 md:grid-cols-2">
                  <JsonBlock value={rule.positive_fixtures} className="max-h-40" />
                  <JsonBlock value={rule.negative_fixtures} className="max-h-40" />
                </div>
                <div className="text-[11px] text-slate-500">
                  input ≤ {rule.max_input_chars} chars · matches/message ≤ {rule.max_matches_per_message} · candidates/batch ≤ {rule.max_candidates_per_batch} · budget {rule.performance_budget_ms} ms
                </div>
              </div>
            </details>
          </Card>
        )
      })}

      <MutationConfirmation
        open={Boolean(activation)}
        title="修改学习规则启用状态"
        description={activation ? `${activation.scope === 'global' ? '全局' : sessionId}：${activation.rule.rule_id} → ${activation.enabled ? 'enabled' : 'disabled'}` : ''}
        requestPrefix="rule-activation"
        busy={busy}
        onCancel={() => setActivation(null)}
        onConfirm={confirmActivation}
      />
    </div>
  )
}

export function SchedulePanel({
  descriptors,
  overview,
  sessionId,
  busy,
  onMutate,
  onExtract,
}) {
  const defaults = useMemo(
    () => (descriptors?.aspects || [])
      .filter(item => item.schedule_default)
      .map(item => item.aspect_id),
    [descriptors],
  )
  const policy = descriptors?.schedule_policy
  const schedule = overview?.schedule
  const [aspects, setAspects] = useState(
    () => schedule?.aspects?.length > 0 ? schedule.aspects : defaults,
  )
  const [intervalMinutes, setIntervalMinutes] = useState(
    () => schedule?.interval_minutes
      ?? policy?.default_interval_minutes
      ?? 60,
  )
  const [windowHours, setWindowHours] = useState(
    () => schedule?.window_hours
      ?? policy?.default_window_hours
      ?? 24,
  )
  const [operation, setOperation] = useState('')

  const toggleAspect = (aspectId, checked) => {
    setAspects(current => (
      checked
        ? [...current, aspectId]
        : current.filter(value => value !== aspectId)
    ))
  }

  const confirm = ({ reason, requestId }) => {
    if (operation === 'pause') {
      return onMutate(
        `暂停群学习白名单 ${sessionId}`,
        () => pauseGroupLearningSchedule({
          chat_stream_id: sessionId,
          body: { request_id: requestId, reason },
        }),
        () => setOperation(''),
      )
    }
    if (operation === 'extract') {
      return onExtract({
        requestId,
        reason,
        aspects,
        windowHours: Number(windowHours),
        onDone: () => setOperation(''),
      })
    }
    return onMutate(
      `${overview?.schedule ? '更新' : '创建'}群学习白名单 ${sessionId}`,
      () => putGroupLearningSchedule({
        chat_stream_id: sessionId,
        body: {
          request_id: requestId,
          reason,
          enabled: true,
          aspects,
          interval_minutes: Number(intervalMinutes),
          window_hours: Number(windowHours),
        },
      }),
      () => setOperation(''),
    )
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-slate-200">定时总结白名单</h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              白名单默认空，只对显式 canonical session 调度。未选择的 aspect 不调用对应模型分支。
            </p>
          </div>
          <Badge tone={overview?.schedule?.enabled ? 'emerald' : 'slate'}>
            {overview?.schedule?.enabled ? '已启用' : '未启用'}
          </Badge>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {(descriptors?.aspects || []).map(aspect => (
            <label key={aspect.aspect_id} className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950 p-3">
              <input
                type="checkbox"
                checked={aspects.includes(aspect.aspect_id)}
                onChange={event => toggleAspect(aspect.aspect_id, event.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="block text-xs text-slate-200">{aspect.display_name}</span>
                <span className="mt-1 block text-[11px] text-slate-500">
                  {aspect.aspect_id} · task {aspect.task_key} · memory {aspect.memory_type || 'none'}
                </span>
              </span>
            </label>
          ))}
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">
            间隔（分钟）
            <input
              type="number"
              value={intervalMinutes}
              min={policy?.min_interval_minutes}
              max={policy?.max_interval_minutes}
              onChange={event => setIntervalMinutes(event.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
            />
          </label>
          <label className="text-xs text-slate-400">
            首次窗口（小时）
            <input
              type="number"
              value={windowHours}
              min={policy?.min_window_hours}
              max={policy?.max_window_hours}
              onChange={event => setWindowHours(event.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <ActionButton
            type="button"
            tone="emerald"
            disabled={aspects.length === 0}
            onClick={() => setOperation('save')}
          >
            {overview?.schedule?.enabled ? '更新并恢复' : '加入白名单'}
          </ActionButton>
          <ActionButton
            type="button"
            tone="amber"
            disabled={!overview?.schedule}
            onClick={() => setOperation('pause')}
          >
            暂停
          </ActionButton>
          <ActionButton
            type="button"
            tone="blue"
            disabled={aspects.length === 0}
            onClick={() => setOperation('extract')}
          >
            立即提取
          </ActionButton>
        </div>
      </Card>

      <Card className="p-4">
        <h2 className="text-sm font-medium text-slate-200">控制面分离</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">
            <div className="mb-1 text-[11px] text-slate-500">自动学习 kill switch</div>
            <Badge tone={overview?.feature_enabled ? 'emerald' : 'red'}>
              {overview?.feature_enabled ? 'enabled' : 'disabled'}
            </Badge>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">
            <div className="mb-1 text-[11px] text-slate-500">Prompt 注入模式</div>
            <Badge tone={overview?.group_profile_mode === 'on' ? 'emerald' : 'slate'}>
              {overview?.group_profile_mode || 'off'}
            </Badge>
          </div>
        </div>
      </Card>

      <MutationConfirmation
        open={Boolean(operation)}
        title={
          operation === 'pause'
            ? '暂停定时总结'
            : operation === 'extract'
              ? '立即提取群学习'
              : '保存定时总结白名单'
        }
        description={`目标 canonical session：${sessionId}`}
        requestPrefix={`schedule-${operation || 'save'}`}
        busy={busy}
        onCancel={() => setOperation('')}
        onConfirm={confirm}
      />
    </div>
  )
}

export function RunsPanel({ runs, loading }) {
  if (loading) return <Spinner />
  return (
    <div className="space-y-3">
      {runs.map(run => (
        <Card key={run.run_id} className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-300">{run.run_id}</span>
                <Badge tone={statusTone(run.status)}>{run.status}</Badge>
                <Badge>{run.trigger}</Badge>
                <Badge>{run.mode}</Badge>
              </div>
              <div className="mt-2 text-[11px] text-slate-500">
                aspects {(run.selected_aspects || []).join(', ') || '-'}
              </div>
            </div>
            <div className="text-right text-[11px] text-slate-500">
              <div>{run.completed_at || run.started_at || run.created_at || '-'}</div>
              <div>{run.latency_ms || 0} ms · attempt {run.attempt_count || 0}</div>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-400 md:grid-cols-5">
            <span>messages {run.eligible_message_count || 0}/{run.raw_message_count || 0}</span>
            <span>candidates {run.candidate_count || 0}</span>
            <span>accepted {run.accepted_count || 0}</span>
            <span>conflict {run.conflict_count || 0}</span>
            <span>waiting {run.waiting_count || 0}</span>
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-[11px] text-slate-500">
              运行合同与资源摘要
            </summary>
            <div className="mt-2 grid gap-2 text-[11px] text-slate-400 md:grid-cols-3">
              <span>route {run.model_route || '-'}</span>
              <span>provider/model {run.provider || '-'}/{run.model || '-'}</span>
              <span>task contract {run.task_contract_version || '-'}</span>
              <span>tokens {run.input_tokens || 0}/{run.output_tokens || 0}</span>
              <span>cost {run.cost_microusd ?? '-'} µUSD</span>
              <span>error {run.error_code || '-'}</span>
              <span>cursor {run.cursor_start_chat_log_id || 0}→{run.cursor_end_chat_log_id || 0}</span>
              <span>rules generation {run.rules_generation || 0}</span>
              <span>raw output {run.raw_output_bytes || 0} B / {shortHash(run.raw_output_sha256)}</span>
            </div>
          </details>
        </Card>
      ))}
      {runs.length === 0 && (
        <Card className="py-16 text-center text-sm text-slate-600">
          当前 session 没有学习运行记录
        </Card>
      )}
    </div>
  )
}
