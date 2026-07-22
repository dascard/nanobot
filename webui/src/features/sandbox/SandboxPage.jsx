import { useCallback, useEffect, useMemo, useState } from 'react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  Field,
  MiniStat,
  PageHeader,
  SectionHeader,
  Spinner,
} from '../../components/ui'

const MIB = 1024 * 1024
const TERMINAL_OPERATION_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])
const CAPABILITY_OPTIONS = [
  { value: 'off', label: '关闭', description: '立即撤销此会话的全部 Sandbox 能力' },
  { value: 'workspace', label: 'Workspace', description: '允许列出、读取、搜索和写入工作区' },
  { value: 'assets', label: 'Assets', description: '包含 Workspace，并允许导入和发布资产' },
  { value: 'exec', label: 'Exec', description: '包含 Assets，并允许执行受限 Python / Shell' },
]

function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    return detail.message || detail.error || JSON.stringify(detail)
  }
  return error?.message || fallback
}

function requestId(prefix) {
  const fallback = `${Date.now()}${Math.random().toString(16).slice(2)}`
  const random = globalThis.crypto?.randomUUID?.().replaceAll('-', '') || fallback
  return `${prefix}_${random}`.slice(0, 64)
}

function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let current = bytes
  let unit = -1
  do {
    current /= 1024
    unit += 1
  } while (current >= 1024 && unit < units.length - 1)
  return `${current >= 10 ? current.toFixed(1) : current.toFixed(2)} ${units[unit]}`
}

function quotaBytesToMiB(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < MIB) return ''
  return String(Math.ceil(bytes / MIB))
}

function formatDate(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-CN')
}

function statusTone(value) {
  if (['active', 'applied', 'succeeded', 'completed', 'ready'].includes(value)) return 'emerald'
  if (['pending', 'running', 'provisioning', 'applying'].includes(value)) return 'amber'
  if (['failed', 'error'].includes(value)) return 'red'
  if (value === 'cancelled' || value === 'disabled' || value === 'off') return 'slate'
  return 'blue'
}

function StatusBadge({ value, label }) {
  return <Badge tone={statusTone(value)}>{label || value || '未知'}</Badge>
}

function EmptyState({ children }) {
  return <div className="py-10 text-center text-xs text-slate-500">{children}</div>
}

export function SandboxPage() {
  const tabs = [
    { key: 'overview', label: '概览' },
    { key: 'access', label: '访问授权' },
    { key: 'workspaces', label: 'Workspace' },
    { key: 'runs', label: '运行记录' },
    { key: 'operations', label: '操作与审计' },
  ]
  const [tab, setTab] = useState('overview')
  const [status, setStatus] = useState(null)
  const [sessions, setSessions] = useState([])
  const [grants, setGrants] = useState([])
  const [workspaces, setWorkspaces] = useState([])
  const [operations, setOperations] = useState([])
  const [auditLogs, setAuditLogs] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [operationId, setOperationId] = useState('')
  const [operationState, setOperationState] = useState(null)

  const loadAll = useCallback(async ({ silent = false } = {}) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    const requests = [
      api.get('/sandbox/status'),
      api.get('/sandbox/sessions', { params: { limit: 100 } }),
      api.get('/sandbox/access-grants'),
      api.get('/sandbox/workspaces'),
      api.get('/sandbox/operations', { params: { limit: 100 } }),
      api.get('/sandbox/audit-logs', { params: { limit: 100 } }),
      api.get('/sandbox/runs', { params: { limit: 100 } }),
    ]
    const results = await Promise.allSettled(requests)
    const setters = [
      result => setStatus(result),
      result => setSessions(result.items || []),
      result => setGrants(result.items || []),
      result => setWorkspaces(result.items || []),
      result => setOperations(result.items || []),
      result => setAuditLogs(result.items || []),
      result => setRuns(result.items || []),
    ]
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') setters[index](result.value.data || {})
    })
    const failed = results.find(result => result.status === 'rejected')
    setError(failed ? formatApiError(failed.reason, '部分 Sandbox 管理数据加载失败') : '')
    setLoading(false)
    setRefreshing(false)
  }, [])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { loadAll() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [loadAll])

  useEffect(() => {
    if (!operationId) return undefined
    let stopped = false
    let timer = null
    const poll = async () => {
      try {
        const response = await api.get(`/sandbox/operations/${encodeURIComponent(operationId)}`)
        if (stopped) return
        const next = response.data.operation
        setOperationState(next)
        if (TERMINAL_OPERATION_STATUSES.has(next?.status)) {
          setOperationId('')
          setNotice(next.status === 'succeeded'
            ? 'Sandbox 管理操作已完成。'
            : `Sandbox 管理操作结束：${next.error_summary || next.status}`)
          await loadAll({ silent: true })
          return
        }
        timer = globalThis.setTimeout(poll, 1200)
      } catch (pollError) {
        if (stopped) return
        setError(formatApiError(pollError, '查询 Sandbox 管理操作失败'))
        timer = globalThis.setTimeout(poll, 2500)
      }
    }
    poll()
    return () => {
      stopped = true
      if (timer) globalThis.clearTimeout(timer)
    }
  }, [operationId, loadAll])

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeader
        title="Sandbox 管理"
        description="按 canonical chat session 管理能力、Workspace 和 project quota。user_id 仅用于识别与审计，不参与授权。"
        actions={(
          <ActionButton type="button" onClick={() => loadAll({ silent: true })} disabled={refreshing}>
            {refreshing ? '刷新中…' : '刷新'}
          </ActionButton>
        )}
        meta={(
          <>
            <span>授权事实源：sandbox_access_grants</span>
            <span>群聊 Sandbox：固定关闭</span>
            <span>Workspace：默认按 session 隔离</span>
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
      {operationState && !TERMINAL_OPERATION_STATUSES.has(operationState.status) && (
        <div role="status" className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          <span>管理操作执行中</span>
          <code>{operationState.operation_id}</code>
          <StatusBadge value={operationState.status} />
          <span>步骤：{operationState.step || '-'}</span>
          <span>尝试：{operationState.attempt_count}/{operationState.max_attempts}</span>
        </div>
      )}

      <div className="mb-5 flex flex-wrap gap-2 border-b border-slate-800 pb-2" role="tablist" aria-label="Sandbox 管理分类">
        {tabs.map(item => (
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

      {tab === 'overview' && (
        <OverviewTab
          status={status}
          grants={grants}
          workspaces={workspaces}
          operations={operations}
          runs={runs}
          onChanged={() => loadAll({ silent: true })}
          setError={setError}
          setNotice={setNotice}
        />
      )}
      {tab === 'access' && (
        <AccessTab
          sessions={sessions}
          grants={grants}
          defaultQuotaBytes={status?.limits?.workspace_default_quota_bytes}
          onAccepted={operation => {
            setOperationState(operation)
            setOperationId(operation.operation_id)
          }}
          setError={setError}
          setNotice={setNotice}
        />
      )}
      {tab === 'workspaces' && (
        <WorkspacesTab
          workspaces={workspaces}
          onAccepted={operation => {
            setOperationState(operation)
            setOperationId(operation.operation_id)
          }}
          setError={setError}
          setNotice={setNotice}
        />
      )}
      {tab === 'runs' && (
        <RunsTab
          runs={runs}
          onChanged={() => loadAll({ silent: true })}
          setError={setError}
          setNotice={setNotice}
        />
      )}
      {tab === 'operations' && <OperationsTab operations={operations} auditLogs={auditLogs} />}
    </div>
  )
}

function OverviewTab({ status, grants, workspaces, operations, runs, onChanged, setError, setNotice }) {
  const feature = status?.feature || {}
  const controller = status?.controller || {}
  const usage = status?.usage || {}
  const limits = status?.limits || {}
  const ready = controller.ready || {}
  const [enabledDraft, setEnabledDraft] = useState(null)
  const [execEnabledDraft, setExecEnabledDraft] = useState(null)
  const [saving, setSaving] = useState(false)
  const enabled = enabledDraft ?? Boolean(feature.enabled)
  const execEnabled = enabled && (
    execEnabledDraft ?? Boolean(feature.exec_enabled)
  )

  const saveFeatures = async () => {
    setSaving(true)
    setError('')
    try {
      await api.put('/sandbox/features', {
        enabled,
        exec_enabled: enabled && execEnabled,
        reason: 'Web Sandbox 管理页更新业务开关',
      })
      setNotice('Sandbox 业务开关已更新。')
      await onChanged()
      setEnabledDraft(null)
      setExecEnabledDraft(null)
    } catch (saveError) {
      setError(formatApiError(saveError, 'Sandbox 业务开关更新失败'))
    } finally {
      setSaving(false)
    }
  }

  const killSwitch = async () => {
    if (!globalThis.confirm('确认无损关闭 Sandbox 总开关和 Exec 开关？Workspace 与 Asset 不会删除。')) return
    setSaving(true)
    setError('')
    try {
      const response = await api.post('/sandbox/kill-switch', { reason: 'Web 管理员触发无损关闭' })
      setEnabledDraft(false)
      setExecEnabledDraft(false)
      setNotice(`Sandbox 已关闭；数据已保留，当前活动运行 ${response.data.active_run_count || 0} 个。`)
      await onChanged()
      setEnabledDraft(null)
      setExecEnabledDraft(null)
    } catch (killError) {
      setError(formatApiError(killError, 'Sandbox kill switch 调用失败'))
    } finally {
      setSaving(false)
    }
  }

  const activeGrants = grants.filter(item => item.status === 'active').length
  const activeRuns = runs.filter(item => ['pending', 'running'].includes(item.status)).length
  const pendingOperations = operations.filter(item => ['pending', 'running'].includes(item.status)).length
  const infrastructureAllowed = feature.infrastructure_enable_allowed === true

  return (
    <div className="space-y-4">
      {!infrastructureAllowed && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs leading-5 text-amber-200">
          宿主基础设施硬上限当前关闭。Web 只能继续保持关闭，不能越过 root 管理的
          <code className="mx-1">sandbox.infrastructure_enable_allowed</code>。
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <MiniStat label="显式授权会话" value={activeGrants} tone={activeGrants ? 'emerald' : 'slate'} />
        <MiniStat label="Workspace" value={workspaces.length} />
        <MiniStat label="工作区占用" value={formatBytes(usage.workspace_used_bytes)} />
        <MiniStat label="活动运行" value={activeRuns} tone={activeRuns ? 'amber' : 'slate'} />
        <MiniStat label="待处理操作" value={pendingOperations} tone={pendingOperations ? 'amber' : 'slate'} />
      </div>

      <Card className="p-4">
        <SectionHeader title="运行门禁" description="宿主硬上限是不可由 Web 修改的上界；群聊首期固定关闭。" />
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
            <input
              type="checkbox"
              checked={enabled}
              disabled={!infrastructureAllowed || saving}
              onChange={event => {
                setEnabledDraft(event.target.checked)
                if (!event.target.checked) setExecEnabledDraft(false)
              }}
              className="mt-0.5"
            />
            <span><strong className="block text-slate-200">Sandbox 总开关</strong><span className="mt-1 block text-slate-500">只影响能力是否可执行，不删除授权或数据。</span></span>
          </label>
          <label className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
            <input
              type="checkbox"
              checked={execEnabled}
              disabled={!infrastructureAllowed || !enabled || saving}
              onChange={event => setExecEnabledDraft(event.target.checked)}
              className="mt-0.5"
            />
            <span><strong className="block text-slate-200">Exec 独立开关</strong><span className="mt-1 block text-slate-500">仅控制 sandbox_exec；Workspace 与 Assets 可先行灰度。</span></span>
          </label>
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
            <strong className="block text-slate-200">群聊 Sandbox</strong>
            <span className="mt-1 block text-slate-500">固定关闭，Web 不提供编辑入口。</span>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
            <strong className="block text-slate-200">宿主硬上限</strong>
            <span className="mt-1 block"><StatusBadge value={infrastructureAllowed ? 'active' : 'disabled'} label={infrastructureAllowed ? '允许启用' : '禁止启用'} /></span>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <ActionButton type="button" tone="emerald" onClick={saveFeatures} disabled={saving || !infrastructureAllowed}>保存业务开关</ActionButton>
          <ActionButton type="button" tone="red" onClick={killSwitch} disabled={saving}>Kill switch（无损关闭）</ActionButton>
        </div>
      </Card>

      <Card className="p-4">
        <SectionHeader title="sandboxd 与隔离前检" description="状态仅表示控制面探测结果；真实隔离仍须由服务器 Docker smoke test 验证。" />
        <div className="grid gap-2 md:grid-cols-3">
          <InfoItem label="进程健康" value={<StatusBadge value={controller.health?.ok ? 'active' : 'error'} label={controller.health?.ok ? '健康' : '不可用'} />} />
          <InfoItem label="控制面 Ready" value={<StatusBadge value={ready.ok ? 'ready' : 'error'} label={ready.ok ? 'Ready' : '未就绪'} />} />
          <InfoItem label="Docker" value={<StatusBadge value={ready.docker ? 'active' : 'error'} label={ready.docker ? '可用' : '不可用'} />} />
          <InfoItem label="镜像 ID" value={<code className="break-all text-[11px]">{ready.image_id || '-'}</code>} />
          <InfoItem label="AppArmor" value={<code className="break-all text-[11px]">{ready.apparmor_profile || '-'}</code>} />
          <InfoItem label="磁盘" value={`${ready.disk_used_percent ?? '-'}% · 可用 ${formatBytes(ready.disk_free_bytes)}`} />
          <InfoItem label="磁盘硬水位" value={`${status?.disk_watermark?.max_used_percent ?? '-'}% / 保留 ${formatBytes(status?.disk_watermark?.min_free_bytes)}`} />
          <InfoItem label="Asset 物理占用" value={formatBytes(usage.asset_physical_bytes)} />
          <InfoItem label="Workspace 已分配配额" value={formatBytes(usage.workspace_quota_bytes)} />
          <InfoItem label="Workspace 默认配额" value={formatBytes(limits.workspace_default_quota_bytes)} />
          <InfoItem label="Workspace 配额总预算" value={formatBytes(limits.total_quota_bytes)} />
          <InfoItem label="单个 Asset 上限" value={formatBytes(limits.asset_max_bytes)} />
        </div>
      </Card>
    </div>
  )
}

function InfoItem({ label, value }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-1 min-w-0 text-xs text-slate-300">{value}</div>
    </div>
  )
}

function AccessTab({ sessions, grants, defaultQuotaBytes, onAccepted, setError, setNotice }) {
  const [search, setSearch] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const defaultQuotaMiB = quotaBytesToMiB(defaultQuotaBytes)
  const normalizedSearch = search.trim()
  const [draft, setDraft] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!normalizedSearch) return undefined
    let stopped = false
    const timer = globalThis.setTimeout(async () => {
      try {
        const response = await api.get('/sandbox/sessions', {
          params: { search: normalizedSearch, limit: 100 },
        })
        if (!stopped) setSearchResults(response.data.items || [])
      } catch (searchError) {
        if (!stopped) setError(formatApiError(searchError, '搜索真实会话失败'))
      }
    }, 250)
    return () => {
      stopped = true
      globalThis.clearTimeout(timer)
    }
  }, [normalizedSearch, setError])

  const results = normalizedSearch ? searchResults : sessions
  const selected = results.find(item => item.chat_stream_id === selectedId)
    || sessions.find(item => item.chat_stream_id === selectedId)
  const grant = grants.find(item => item.chat_stream_id === selectedId)
  const grantQuotaBytes = grant?.quota?.desired_quota_bytes
    ?? grant?.workspace?.quota_bytes
  const formKey = [
    selectedId,
    grant?.version || 0,
    grant?.capability || 'off',
    grantQuotaBytes || 0,
    defaultQuotaMiB,
  ].join(':')
  const formDefaults = {
    key: formKey,
    capability: grant?.capability || 'off',
    quotaMiB: grantQuotaBytes
      ? quotaBytesToMiB(grantQuotaBytes)
      : defaultQuotaMiB,
    reason: grant?.reason || '',
  }
  const form = draft?.key === formKey ? draft : formDefaults
  const { capability, quotaMiB, reason } = form
  const updateDraft = patch => {
    setDraft(current => ({
      ...(current?.key === formKey ? current : formDefaults),
      ...patch,
      key: formKey,
    }))
  }

  const submit = async event => {
    event.preventDefault()
    if (!selected) {
      setError('请先选择一个真实 canonical session。')
      return
    }
    const parsedQuota = Number(quotaMiB)
    if (capability !== 'off' && (!Number.isInteger(parsedQuota) || parsedQuota < 1)) {
      setError('Workspace 配额必须是至少 1 MiB 的整数。')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const body = {
        request_id: requestId('sbx_access'),
        platform: selected.platform,
        chat_type: 'private',
        session_id: selected.session_id,
        capability,
        quota_bytes: capability === 'off' ? null : parsedQuota * MIB,
        reason,
      }
      if (grant?.version) body.expected_version = grant.version
      const response = await api.post('/sandbox/access-grants', body)
      const operation = response.data.operation
      setNotice(`请求已入队：${operation.operation_id}`)
      onAccepted(operation)
    } catch (submitError) {
      setError(formatApiError(submitError, 'Sandbox 会话授权失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(18rem,0.9fr)_minmax(24rem,1.4fr)]">
      <Card className="p-4">
        <SectionHeader title="选择真实会话" description="只能选择服务端实际见过的私聊 session；手填未知 ID 不会创建授权。" />
        <Field id="sandbox-session-search" label="搜索昵称、user_id、session_id 或 canonical ID">
          <input
            id="sandbox-session-search"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="输入关键词"
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500"
          />
        </Field>
        <div className="mt-3 max-h-[34rem] space-y-2 overflow-y-auto pr-1">
          {results.map(session => {
            const itemGrant = grants.find(item => item.chat_stream_id === session.chat_stream_id)
            const active = selectedId === session.chat_stream_id
            return (
              <button
                key={session.chat_stream_id}
                type="button"
                onClick={() => {
                  setSelectedId(session.chat_stream_id)
                  setDraft(null)
                }}
                className={`block w-full rounded-lg border p-3 text-left transition-colors ${active ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-slate-800 bg-slate-950 hover:border-slate-700'}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 truncate text-xs font-medium text-slate-200">{session.label || session.session_id}</span>
                  <StatusBadge value={itemGrant?.capability || 'off'} label={CAPABILITY_OPTIONS.find(item => item.value === itemGrant?.capability)?.label || '关闭'} />
                </div>
                <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{session.chat_stream_id}</div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-600">
                  <span>user_id（仅显示）：{session.actor_user_id || '-'}</span>
                  <span>最近：{formatDate(session.recent_at)}</span>
                </div>
              </button>
            )
          })}
          {results.length === 0 && <EmptyState>没有匹配的真实私聊 session</EmptyState>}
        </div>
      </Card>

      <Card className="p-4">
        <SectionHeader title="能力与初始配额" description="一次保存原子提交会话能力和 Workspace 期望配额；宿主应用结果通过 operation 异步确认。" />
        {!selected ? (
          <EmptyState>请从左侧选择一个 canonical session</EmptyState>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-3">
              <div className="text-[11px] text-slate-500">唯一授权键</div>
              <code className="mt-1 block break-all text-xs text-emerald-300">{selected.chat_stream_id}</code>
              <div className="mt-2 text-[11px] text-slate-500">
                显示用户：{selected.actor_user_id || '-'}；该字段不会写入授权条件。
              </div>
            </div>

            <fieldset>
              <legend className="mb-2 text-[11px] font-medium text-slate-400">能力等级（单调包含）</legend>
              <div className="grid gap-2 md:grid-cols-2">
                {CAPABILITY_OPTIONS.map(option => (
                  <label key={option.value} className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${capability === option.value ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-slate-800 bg-slate-950'}`}>
                    <input type="radio" name="sandbox-capability" value={option.value} checked={capability === option.value} onChange={() => updateDraft({ capability: option.value })} className="mt-0.5" />
                    <span><strong className="block text-xs text-slate-200">{option.label}</strong><span className="mt-1 block text-[11px] leading-4 text-slate-500">{option.description}</span></span>
                  </label>
                ))}
              </div>
            </fieldset>

            <Field
              id="sandbox-access-quota"
              label="Workspace 配额（MiB）"
              hint={capability === 'off' ? '关闭能力不会删除 Workspace，也不会回收 project ID。' : `当前输入：${formatBytes(Number(quotaMiB) * MIB)}`}
            >
              <input
                id="sandbox-access-quota"
                type="number"
                min="1"
                step="1"
                value={quotaMiB}
                disabled={capability === 'off'}
                onChange={event => updateDraft({ quotaMiB: event.target.value })}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 disabled:opacity-50"
              />
            </Field>
            <Field id="sandbox-access-reason" label="变更原因">
              <input
                id="sandbox-access-reason"
                value={reason}
                maxLength={255}
                onChange={event => updateDraft({ reason: event.target.value })}
                placeholder="用于审计，可选"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
              />
            </Field>

            {grant && (
              <div className="grid gap-2 md:grid-cols-2">
                <InfoItem label="授权状态 / 版本" value={<><StatusBadge value={grant.status} /> <span className="ml-2">v{grant.version}</span></>} />
                <InfoItem label="Workspace" value={<code className="break-all text-[11px]">{grant.workspace_id || '-'}</code>} />
                <InfoItem label="期望 / 已应用配额" value={`${formatBytes(grant.quota?.desired_quota_bytes)} / ${formatBytes(grant.quota?.applied_quota_bytes)}`} />
                <InfoItem label="project ID / generation" value={`${grant.quota?.project_id ?? '-'} / ${grant.quota?.generation ?? '-'}`} />
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <ActionButton type="submit" tone={capability === 'off' ? 'red' : 'emerald'} disabled={saving}>
                {saving ? '提交中…' : capability === 'off' ? '保存并关闭能力' : '保存授权与配额'}
              </ActionButton>
              <span className="text-[11px] text-slate-500">降级立即收窄；升级等待 sandboxd 和 project quota 确认。</span>
            </div>
          </form>
        )}
      </Card>
    </div>
  )
}

function WorkspacesTab({ workspaces, onAccepted, setError, setNotice }) {
  const [editingId, setEditingId] = useState('')
  const [quotaMiB, setQuotaMiB] = useState('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)

  const beginEdit = workspace => {
    setEditingId(workspace.workspace_id)
    setQuotaMiB(String(Math.ceil(Number(workspace.desired_quota_bytes || workspace.quota_bytes || 0) / MIB)))
    setReason('')
  }

  const save = async workspace => {
    const parsed = Number(quotaMiB)
    if (!Number.isInteger(parsed) || parsed < 1) {
      setError('Workspace 配额必须是至少 1 MiB 的整数。')
      return
    }
    if (parsed * MIB < Number(workspace.used_bytes || 0)) {
      setError('新配额不能低于 Workspace 当前占用。')
      return
    }
    setSaving(true)
    setError('')
    try {
      const response = await api.post(`/sandbox/workspaces/${encodeURIComponent(workspace.workspace_id)}/quota`, {
        request_id: requestId('sbx_quota'),
        quota_bytes: parsed * MIB,
        reason,
      })
      const operation = response.data.operation
      setEditingId('')
      setNotice(`配额变更已入队：${operation.operation_id}`)
      onAccepted(operation)
    } catch (saveError) {
      setError(formatApiError(saveError, 'Workspace 配额变更失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-800 bg-slate-900 p-3 text-xs leading-5 text-slate-400">
        配额可随时修改；缩容不能低于当前占用。project ID 由数据库分配且只读。首期不提供删除或 Workspace 合并按钮。
      </div>
      {workspaces.map(workspace => (
        <Card key={workspace.workspace_id} className="p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <code className="break-all text-xs text-slate-200">{workspace.workspace_id}</code>
                <StatusBadge value={workspace.status} />
                <StatusBadge value={workspace.quota_status} label={`quota: ${workspace.quota_status}`} />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                <span>占用 {formatBytes(workspace.used_bytes)}</span>
                <span>逻辑配额 {formatBytes(workspace.quota_bytes)}</span>
                <span>期望 {formatBytes(workspace.desired_quota_bytes)}</span>
                <span>已应用 {formatBytes(workspace.applied_quota_bytes)}</span>
                <span>project {workspace.project_id ?? '-'}</span>
                <span>generation {workspace.quota_generation ?? '-'}</span>
              </div>
              <div className="mt-2 text-[11px] text-slate-600">
                绑定 session：{workspace.sessions?.length ? workspace.sessions.join('，') : '无'}
              </div>
              {workspace.last_error_code && (
                <div className="mt-2 text-xs text-red-300">{workspace.last_error_code}：{workspace.last_error_summary || '配额应用失败'}</div>
              )}
            </div>
            <ActionButton type="button" onClick={() => beginEdit(workspace)}>修改配额</ActionButton>
          </div>
          {editingId === workspace.workspace_id && (
            <div className="mt-4 grid gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3 md:grid-cols-[minmax(10rem,0.5fr)_minmax(14rem,1fr)_auto] md:items-end">
              <Field id={`quota-${workspace.workspace_id}`} label="新配额（MiB）" hint={`当前占用 ${formatBytes(workspace.used_bytes)}`}>
                <input id={`quota-${workspace.workspace_id}`} type="number" min="1" step="1" value={quotaMiB} onChange={event => setQuotaMiB(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs" />
              </Field>
              <Field id={`quota-reason-${workspace.workspace_id}`} label="变更原因">
                <input id={`quota-reason-${workspace.workspace_id}`} value={reason} maxLength={255} onChange={event => setReason(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs" />
              </Field>
              <div className="flex gap-2">
                <ActionButton type="button" tone="emerald" disabled={saving} onClick={() => save(workspace)}>提交</ActionButton>
                <ActionButton type="button" disabled={saving} onClick={() => setEditingId('')}>取消</ActionButton>
              </div>
            </div>
          )}
        </Card>
      ))}
      {workspaces.length === 0 && <Card><EmptyState>尚无 Workspace；首次授予非 off 能力时由管理服务创建。</EmptyState></Card>}
    </div>
  )
}

function RunsTab({ runs, onChanged, setError, setNotice }) {
  const [cancelling, setCancelling] = useState('')

  const cancel = async run => {
    if (!globalThis.confirm(`确认取消运行 ${run.run_id}？`)) return
    setCancelling(run.run_id)
    setError('')
    try {
      await api.post(`/sandbox/runs/${encodeURIComponent(run.run_id)}/cancel`)
      setNotice(`已提交取消：${run.run_id}`)
      await onChanged()
    } catch (cancelError) {
      setError(formatApiError(cancelError, '取消 Sandbox 运行失败'))
    } finally {
      setCancelling('')
    }
  }

  return (
    <Card className="overflow-x-auto p-4">
      <SectionHeader title="运行记录" description="仅展示安全账本元数据，不展示命令、stdout、stderr 或宿主路径。" />
      <table className="w-full min-w-[70rem] text-xs">
        <thead><tr className="border-b border-slate-800 text-left text-slate-500">
          <th className="px-2 py-2">运行</th><th className="px-2 py-2">状态</th><th className="px-2 py-2">终止原因</th><th className="px-2 py-2">Workspace</th><th className="px-2 py-2">资源</th><th className="px-2 py-2">输出元数据</th><th className="px-2 py-2">时间</th><th className="px-2 py-2">操作</th>
        </tr></thead>
        <tbody>
          {runs.map(run => (
            <tr key={run.run_id} className="border-b border-slate-800/60 align-top">
              <td className="px-2 py-3 font-mono text-slate-300">{run.run_id}</td>
              <td className="px-2 py-3"><StatusBadge value={run.status} /></td>
              <td className="px-2 py-3 text-slate-400">{run.termination_reason || '-'}{run.exit_code !== null && run.exit_code !== undefined ? ` / exit ${run.exit_code}` : ''}</td>
              <td className="px-2 py-3 font-mono text-[11px] text-slate-500">{run.workspace_id}</td>
              <td className="px-2 py-3 text-slate-500">CPU {run.cpu_time_ms || 0}ms<br />峰值 {formatBytes(run.peak_memory_bytes)}</td>
              <td className="px-2 py-3 text-slate-500">stdout {formatBytes(run.stdout_bytes)}{run.stdout_truncated ? '（截断）' : ''}<br />stderr {formatBytes(run.stderr_bytes)}{run.stderr_truncated ? '（截断）' : ''}</td>
              <td className="px-2 py-3 text-slate-500">{formatDate(run.started_at || run.created_at)}<br />{formatDate(run.finished_at)}</td>
              <td className="px-2 py-3">
                {['pending', 'running'].includes(run.status)
                  ? <ActionButton type="button" tone="red" disabled={cancelling === run.run_id} onClick={() => cancel(run)}>取消</ActionButton>
                  : <span className="text-slate-600">-</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs.length === 0 && <EmptyState>暂无 Sandbox 运行记录</EmptyState>}
    </Card>
  )
}

function OperationsTab({ operations, auditLogs }) {
  const [view, setView] = useState('operations')
  const operationCounts = useMemo(() => ({
    pending: operations.filter(item => item.status === 'pending').length,
    running: operations.filter(item => item.status === 'running').length,
    failed: operations.filter(item => item.status === 'failed').length,
  }), [operations])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <ActionButton type="button" tone={view === 'operations' ? 'emerald' : 'slate'} onClick={() => setView('operations')}>操作账本</ActionButton>
        <ActionButton type="button" tone={view === 'audit' ? 'emerald' : 'slate'} onClick={() => setView('audit')}>管理审计</ActionButton>
        <span className="self-center text-[11px] text-slate-500">pending {operationCounts.pending} · running {operationCounts.running} · failed {operationCounts.failed}</span>
      </div>
      {view === 'operations' ? (
        <Card className="overflow-x-auto p-4">
          <SectionHeader title="持久化 operation" description="runner 使用租约、幂等 request ID、代际 fencing、有限重试和重启恢复。" />
          <table className="w-full min-w-[76rem] text-xs">
            <thead><tr className="border-b border-slate-800 text-left text-slate-500">
              <th className="px-2 py-2">操作</th><th className="px-2 py-2">类型</th><th className="px-2 py-2">状态 / 步骤</th><th className="px-2 py-2">目标</th><th className="px-2 py-2">期望</th><th className="px-2 py-2">重试</th><th className="px-2 py-2">错误</th><th className="px-2 py-2">时间</th>
            </tr></thead>
            <tbody>
              {operations.map(operation => (
                <tr key={operation.operation_id} className="border-b border-slate-800/60 align-top">
                  <td className="px-2 py-3"><code className="text-slate-300">{operation.operation_id}</code><div className="mt-1 font-mono text-[10px] text-slate-600">{operation.request_id}</div></td>
                  <td className="px-2 py-3 text-slate-400">{operation.operation_type}</td>
                  <td className="px-2 py-3"><StatusBadge value={operation.status} /><div className="mt-1 text-slate-500">{operation.step || '-'}</div></td>
                  <td className="px-2 py-3 max-w-[18rem] break-all font-mono text-[11px] text-slate-500">{operation.chat_stream_id || operation.workspace_id || '-'}</td>
                  <td className="px-2 py-3 text-slate-500">{operation.desired_capability || '-'}<br />{operation.desired_quota_bytes ? formatBytes(operation.desired_quota_bytes) : '-'}</td>
                  <td className="px-2 py-3 text-slate-500">{operation.attempt_count}/{operation.max_attempts}<br />{formatDate(operation.next_attempt_at)}</td>
                  <td className="px-2 py-3 max-w-[16rem] text-red-300">{operation.error_code ? `${operation.error_code}：${operation.error_summary}` : '-'}</td>
                  <td className="px-2 py-3 text-slate-500">{formatDate(operation.created_at)}<br />{formatDate(operation.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {operations.length === 0 && <EmptyState>暂无 Sandbox 管理操作</EmptyState>}
        </Card>
      ) : (
        <Card className="overflow-x-auto p-4">
          <SectionHeader title="管理审计" description="记录授权、配额、开关、取消运行和 kill switch 操作。" />
          <table className="w-full min-w-[58rem] text-xs">
            <thead><tr className="border-b border-slate-800 text-left text-slate-500"><th className="px-2 py-2">时间</th><th className="px-2 py-2">动作</th><th className="px-2 py-2">目标</th><th className="px-2 py-2">管理员</th><th className="px-2 py-2">详情</th></tr></thead>
            <tbody>
              {auditLogs.map(log => (
                <tr key={log.id} className="border-b border-slate-800/60 align-top">
                  <td className="px-2 py-3 text-slate-500">{formatDate(log.created_at)}</td>
                  <td className="px-2 py-3"><Badge tone="blue">{log.action}</Badge></td>
                  <td className="px-2 py-3 max-w-[18rem] break-all font-mono text-[11px] text-slate-400">{log.target_type}:{log.target_id}</td>
                  <td className="px-2 py-3 text-slate-500">{log.admin_user || '-'}<br />{log.ip_address || '-'}</td>
                  <td className="px-2 py-3"><pre className="max-w-[26rem] whitespace-pre-wrap break-all text-[11px] text-slate-500">{JSON.stringify(log.detail || {}, null, 2)}</pre></td>
                </tr>
              ))}
            </tbody>
          </table>
          {auditLogs.length === 0 && <EmptyState>暂无 Sandbox 管理审计</EmptyState>}
        </Card>
      )}
    </div>
  )
}
