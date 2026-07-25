import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  extractGroupLearningSession,
  getGroupLearningDescriptors,
  getGroupLearningOverview,
  listGroupLearningCandidates,
  listGroupLearningRuns,
  listGroupLearningSessions,
  listGroupMemoryItems,
  updateGroupLearningFeature,
} from '../../api/generated/adminClient'
import {
  ActionButton,
  Badge,
  Card,
  PageHeader,
  Spinner,
} from '../../components/ui'
import {
  CandidatesPanel,
  MemoriesPanel,
  OverviewPanel,
  RulesPanel,
  RunsPanel,
  SchedulePanel,
} from './GroupLearningPanels'
import { MutationConfirmation } from './MutationConfirmation'
import {
  formatGroupLearningError,
  statusTone,
} from './utils'

const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'memories', label: '正式记忆' },
  { key: 'candidates', label: '学习候选' },
  { key: 'conflicts', label: '冲突' },
  { key: 'rules', label: '提取规则' },
  { key: 'schedule', label: '定时白名单' },
  { key: 'runs', label: '运行记录' },
]

export function GroupLearningPage() {
  const [tab, setTab] = useState('overview')
  const [descriptors, setDescriptors] = useState(null)
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState('')
  const [overview, setOverview] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [candidateHasMore, setCandidateHasMore] = useState(false)
  const [memories, setMemories] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [sessionLoading, setSessionLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [featureDialog, setFeatureDialog] = useState(false)

  const selectedSession = useMemo(
    () => sessions.find(item => item.chat_stream_id === sessionId) || null,
    [sessionId, sessions],
  )

  const loadRoot = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [descriptorData, sessionData] = await Promise.all([
        getGroupLearningDescriptors(),
        listGroupLearningSessions({ limit: 500 }),
      ])
      setDescriptors(descriptorData)
      setSessions(sessionData.items || [])
      setSessionId(current => (
        current && (sessionData.items || []).some(
          item => item.chat_stream_id === current,
        )
          ? current
          : sessionData.items?.[0]?.chat_stream_id || ''
      ))
    } catch (loadError) {
      setError(formatGroupLearningError(loadError, '群学习控制面加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadSession = useCallback(async () => {
    if (!sessionId) {
      setOverview(null)
      setCandidates([])
      setMemories([])
      setRuns([])
      return
    }
    setSessionLoading(true)
    setError('')
    const results = await Promise.allSettled([
      getGroupLearningOverview({ chat_stream_id: sessionId }),
      listGroupLearningCandidates({
        chat_stream_id: sessionId,
        limit: 100,
      }),
      listGroupLearningRuns({
        chat_stream_id: sessionId,
        limit: 100,
      }),
      listGroupMemoryItems({ group_id: sessionId }),
    ])
    const [overviewResult, candidateResult, runResult, memoryResult] = results
    if (overviewResult.status === 'fulfilled') {
      setOverview(overviewResult.value)
    } else {
      setOverview(null)
    }
    if (candidateResult.status === 'fulfilled') {
      setCandidates(candidateResult.value.items || [])
      setCandidateHasMore(candidateResult.value.next_after_id != null)
    } else {
      setCandidates([])
      setCandidateHasMore(false)
    }
    if (runResult.status === 'fulfilled') {
      setRuns(runResult.value.items || [])
    } else {
      setRuns([])
    }
    if (memoryResult.status === 'fulfilled') {
      setMemories(memoryResult.value.memories || [])
    } else {
      setMemories([])
    }
    const failed = results.find(result => result.status === 'rejected')
    if (failed) {
      setError(formatGroupLearningError(
        failed.reason,
        '当前 session 的部分治理数据加载失败',
      ))
    }
    setSessionLoading(false)
  }, [sessionId])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { loadRoot() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [loadRoot])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { loadSession() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [loadSession])

  const refreshAll = useCallback(async () => {
    await loadRoot()
    await loadSession()
  }, [loadRoot, loadSession])

  const performMutation = useCallback(async (
    label,
    operation,
    onDone,
  ) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await operation()
      setNotice(`${label}已完成${result?.replayed ? '（幂等重放）' : ''}`)
      if (onDone) onDone()
      await Promise.all([loadRoot(), loadSession()])
      return true
    } catch (mutationError) {
      setError(formatGroupLearningError(mutationError, `${label}失败`))
      return false
    } finally {
      setBusy(false)
    }
  }, [loadRoot, loadSession])

  const performExtract = useCallback(({
    requestId,
    reason,
    aspects,
    windowHours,
    onDone,
  }) => performMutation(
    `立即提取 ${sessionId}`,
    () => extractGroupLearningSession({
      chat_stream_id: sessionId,
      body: {
        request_id: requestId,
        reason,
        aspects,
        window_hours: windowHours,
        instructions: '',
      },
    }),
    onDone,
  ), [performMutation, sessionId])

  const confirmFeature = ({ reason, requestId }) => performMutation(
    `${descriptors?.feature_enabled ? '关闭' : '启用'}群学习 kill switch`,
    () => updateGroupLearningFeature({
      body: {
        request_id: requestId,
        reason,
        enabled: !descriptors?.feature_enabled,
      },
    }),
    () => setFeatureDialog(false),
  )

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeader
        title="群学习与群体记忆"
        description="正则常态提取只生成 Candidate/Evidence；定时总结将其交给模型审核并允许模型补充，只有通过模型与 Evidence Policy 或人工审核后才写入 GroupMemory。"
        actions={(
          <>
            <ActionButton
              type="button"
              tone={descriptors?.feature_enabled ? 'red' : 'emerald'}
              onClick={() => setFeatureDialog(true)}
            >
              {descriptors?.feature_enabled ? '关闭学习' : '启用学习'}
            </ActionButton>
            <ActionButton type="button" onClick={refreshAll}>
              刷新
            </ActionButton>
          </>
        )}
        meta={(
          <>
            <span>feature: {descriptors?.feature_enabled ? 'enabled' : 'disabled'}</span>
            <span>白名单默认空</span>
            <span>Prompt 注入独立控制</span>
            <span>规则 registry: {descriptors?.rule_registry?.generation || 0}</span>
          </>
        )}
      />

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
          {notice}
        </div>
      )}
      {candidateHasMore && (
        <div role="status" className="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          当前只展示前 100 条候选；请使用状态、类型和来源筛选收窄范围。
        </div>
      )}

      <Card className="mb-4 p-3">
        <label className="block text-[11px] text-slate-500">
          canonical 群 session
          <select
            value={sessionId}
            onChange={event => setSessionId(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
          >
            <option value="">请选择</option>
            {sessions.map(item => (
              <option key={item.chat_stream_id} value={item.chat_stream_id}>
                {item.session_name || item.chat_stream_id} · {item.chat_stream_id}
              </option>
            ))}
          </select>
        </label>
        {selectedSession && (
          <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-slate-500">
            <Badge tone={selectedSession.schedule_enabled ? 'emerald' : 'slate'}>
              白名单 {selectedSession.schedule_enabled ? 'on' : 'off'}
            </Badge>
            <Badge tone={selectedSession.group_profile_mode === 'on' ? 'emerald' : 'slate'}>
              注入 {selectedSession.group_profile_mode}
            </Badge>
            <Badge tone={statusTone(selectedSession.last_run_status)}>
              最近运行 {selectedSession.last_run_status || 'none'}
            </Badge>
            <span>候选 {selectedSession.candidate_count}</span>
            <span>记忆 {selectedSession.memory_count}</span>
          </div>
        )}
      </Card>

      {sessions.length === 0 && (
        <Card className="mb-4 py-12 text-center text-sm text-slate-600">
          尚未发现可规范化且无身份冲突的群 session。定时白名单不会自动创建。
        </Card>
      )}

      <div
        className="mb-5 flex flex-wrap gap-2 border-b border-slate-800 pb-2"
        role="tablist"
        aria-label="群学习治理分类"
      >
        {TABS.map(item => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${tab === item.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {sessionLoading && sessionId ? <Spinner /> : (
        <>
          {tab === 'overview' && (
            <OverviewPanel overview={overview} session={selectedSession} />
          )}
          {tab === 'memories' && (
            <MemoriesPanel
              descriptors={descriptors}
              memories={memories}
              loading={sessionLoading}
            />
          )}
          {tab === 'candidates' && (
            <CandidatesPanel
              descriptors={descriptors}
              candidates={candidates}
              memories={memories}
              loading={sessionLoading}
              busy={busy}
              onMutate={performMutation}
            />
          )}
          {tab === 'conflicts' && (
            <CandidatesPanel
              descriptors={descriptors}
              candidates={candidates}
              memories={memories}
              loading={sessionLoading}
              conflictsOnly
              busy={busy}
              onMutate={performMutation}
            />
          )}
          {tab === 'rules' && (
            <RulesPanel
              key={sessionId}
              descriptors={descriptors}
              overview={overview}
              sessionId={sessionId}
              busy={busy}
              onMutate={performMutation}
            />
          )}
          {tab === 'schedule' && (
            <SchedulePanel
              key={`${sessionId}:${overview?.schedule?.config_generation || 0}`}
              descriptors={descriptors}
              overview={overview}
              sessionId={sessionId}
              busy={busy}
              onMutate={performMutation}
              onExtract={performExtract}
            />
          )}
          {tab === 'runs' && (
            <RunsPanel runs={runs} loading={sessionLoading} />
          )}
        </>
      )}

      <MutationConfirmation
        open={featureDialog}
        title={`${descriptors?.feature_enabled ? '关闭' : '启用'}群学习 kill switch`}
        description="该开关控制自动学习执行，不会修改 Prompt 注入模式，也不会删除 Candidate、Evidence 或正式记忆。"
        requestPrefix="feature"
        busy={busy}
        onCancel={() => setFeatureDialog(false)}
        onConfirm={confirmFeature}
      />
    </div>
  )
}
