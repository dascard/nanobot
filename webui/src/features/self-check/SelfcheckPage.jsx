import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck } from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  MiniStat,
  PageHeader,
  Spinner,
} from '../../components/ui'


const STATUS_LABELS = {
  passed: '通过',
  degraded: '降级',
  failed: '失败',
  inconclusive: '证据不足',
  skipped: '已跳过',
  running: '运行中',
}

const STATUS_TONES = {
  passed: 'emerald',
  degraded: 'amber',
  failed: 'red',
  inconclusive: 'blue',
  skipped: 'slate',
  running: 'blue',
}

const SEVERITY_TONES = {
  critical: 'red',
  high: 'amber',
  medium: 'blue',
  low: 'slate',
}

const WATCHDOG_SETTING_KEYS = new Set([
  'selfcheck.watchdog_enabled',
  'selfcheck.watchdog_interval_seconds',
  'selfcheck.model_canary_enabled',
])


function errorMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item?.msg || String(item)).join('；')
  return error?.message || '自检请求失败'
}

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function shortHash(value) {
  return value ? `${value.slice(0, 12)}…` : '-'
}

function ResultCard({ result }) {
  return (
    <Card className={`p-4 ${result.status === 'failed' ? 'border-red-500/30' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONES[result.status]}>
              {STATUS_LABELS[result.status] || result.status}
            </Badge>
            <span className="font-mono text-xs text-slate-200">{result.check_id}</span>
            <Badge tone={SEVERITY_TONES[result.severity]}>{result.severity}</Badge>
            <Badge>{result.level}</Badge>
          </div>
          <div className="mt-2 text-sm text-slate-200">{result.message}</div>
          <div className="mt-1 font-mono text-[11px] text-slate-500">
            {result.detail_code} · {result.duration_ms}ms
          </div>
        </div>
        <div className="text-right text-[11px] text-slate-500">
          <div>{result.category}</div>
          <div>{(result.capability_ids || []).length} 项能力</div>
        </div>
      </div>
      {(Object.keys(result.metrics || {}).length > 0 || Object.keys(result.evidence || {}).length > 0) && (
        <details className="mt-3 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
          <summary className="cursor-pointer text-xs text-slate-400">指标与脱敏证据</summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <pre className="overflow-auto whitespace-pre-wrap text-[11px] text-slate-300">
              {JSON.stringify(result.metrics || {}, null, 2)}
            </pre>
            <pre className="overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">
              {JSON.stringify(result.evidence || {}, null, 2)}
            </pre>
          </div>
        </details>
      )}
    </Card>
  )
}


export function SelfcheckPage() {
  const [capabilities, setCapabilities] = useState(null)
  const [probes, setProbes] = useState([])
  const [runs, setRuns] = useState([])
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [includeModel, setIncludeModel] = useState(false)
  const [watchdogSettings, setWatchdogSettings] = useState({})
  const [settingsVersion, setSettingsVersion] = useState(0)
  const [intervalDraft, setIntervalDraft] = useState('900')
  const [updatingSetting, setUpdatingSetting] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const [capabilityResponse, probeResponse, runResponse, settingsResponse] = await Promise.all([
        api.get('/self-check/capabilities'),
        api.get('/self-check/probes'),
        api.get('/self-check/runs?limit=20'),
        api.get('/settings'),
      ])
      setCapabilities(capabilityResponse.data)
      setProbes(probeResponse.data?.items || [])
      const nextRuns = runResponse.data?.items || []
      setRuns(nextRuns)
      const nextSettings = Object.fromEntries(
        (settingsResponse.data?.settings || [])
          .filter(item => WATCHDOG_SETTING_KEYS.has(item.key))
          .map(item => [item.key, item]),
      )
      setWatchdogSettings(nextSettings)
      setSettingsVersion(settingsResponse.data?.version || 0)
      setIntervalDraft(String(
        nextSettings['selfcheck.watchdog_interval_seconds']?.value ?? 900,
      ))
      if (nextRuns.length > 0) {
        const detail = await api.get(`/self-check/runs/${nextRuns[0].run_id}`)
        setReport(detail.data)
      } else {
        setReport(null)
      }
    } catch (loadError) {
      setError(errorMessage(loadError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { load() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [load])

  const runSelfcheck = async () => {
    setRunning(true)
    setError('')
    try {
      const response = await api.post('/self-check/runs', {
        trigger: 'manual',
        allow_model_checks: includeModel,
      })
      setReport(response.data)
      setRuns(current => [
        {
          run_id: response.data.run_id,
          trigger: response.data.trigger,
          environment: response.data.environment,
          status: response.data.status,
          summary: response.data.summary,
          started_at: response.data.started_at,
          completed_at: response.data.completed_at,
        },
        ...current.filter(item => item.run_id !== response.data.run_id),
      ].slice(0, 20))
    } catch (runError) {
      setError(errorMessage(runError))
    } finally {
      setRunning(false)
    }
  }

  const openRun = async (runId) => {
    setError('')
    try {
      const response = await api.get(`/self-check/runs/${runId}`)
      setReport(response.data)
    } catch (detailError) {
      setError(errorMessage(detailError))
    }
  }

  const updateWatchdogSetting = async (key, value) => {
    setUpdatingSetting(key)
    setError('')
    try {
      const response = await api.put(`/settings/${encodeURIComponent(key)}`, { value })
      setWatchdogSettings(current => ({
        ...current,
        [key]: { ...current[key], ...response.data },
      }))
      setSettingsVersion(response.data?.version || settingsVersion)
      if (key === 'selfcheck.watchdog_interval_seconds') {
        setIntervalDraft(String(response.data?.value ?? value))
      }
    } catch (settingError) {
      setError(errorMessage(settingError))
      if (key === 'selfcheck.watchdog_interval_seconds') {
        setIntervalDraft(String(watchdogSettings[key]?.value ?? 900))
      }
    } finally {
      setUpdatingSetting('')
    }
  }

  const saveInterval = () => {
    const value = Number.parseInt(intervalDraft, 10)
    if (!Number.isInteger(value)) {
      setIntervalDraft(String(
        watchdogSettings['selfcheck.watchdog_interval_seconds']?.value ?? 900,
      ))
      return
    }
    if (value === watchdogSettings['selfcheck.watchdog_interval_seconds']?.value) return
    updateWatchdogSetting('selfcheck.watchdog_interval_seconds', value)
  }

  const categories = useMemo(() => (
    [...new Set(probes.map(probe => probe.category))].sort()
  ), [probes])
  const filteredResults = useMemo(() => (
    (report?.results || []).filter(result => (
      (statusFilter === 'all' || result.status === statusFilter)
      && (categoryFilter === 'all' || result.category === categoryFilter)
    ))
  ), [categoryFilter, report, statusFilter])
  const coverage = capabilities?.coverage || {}
  const summary = report?.summary || {}

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeader
        title="系统自检"
        description="从能力清单出发检查 API、WebUI、Agent、模型、工具、RAG、数据库、Worker、定时推送和主动外呼。安全自检不访问外部网络；模型 Canary 仅在显式勾选后执行。"
        actions={(
          <>
            <ActionButton type="button" onClick={() => load({ silent: true })} disabled={running}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" />刷新
            </ActionButton>
            <ActionButton type="button" tone="emerald" onClick={runSelfcheck} disabled={running}>
              <ShieldCheck className="mr-1 h-3.5 w-3.5" />
              {running ? '运行中…' : '运行自检'}
            </ActionButton>
          </>
        )}
        meta={(
          <>
            <span>Capability {shortHash(capabilities?.registry?.sha256)}</span>
            <span>Probe {shortHash(report?.probe_registry_sha256)}</span>
            <span>{probes.length} 个 Probe</span>
          </>
        )}
      />

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      <Card className="mb-4 p-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-slate-200">周期 Watchdog</div>
                <div className="mt-1 text-[11px] leading-4 text-slate-500">
                  周期运行安全自检并保存结果。设置由 Worker 每轮重新读取，保存后无需重启。
                </div>
              </div>
              <button
                type="button"
                aria-label="周期 Watchdog"
                aria-pressed={Boolean(watchdogSettings['selfcheck.watchdog_enabled']?.value)}
                disabled={updatingSetting === 'selfcheck.watchdog_enabled' || watchdogSettings['selfcheck.watchdog_enabled']?.readonly}
                onClick={() => updateWatchdogSetting(
                  'selfcheck.watchdog_enabled',
                  !watchdogSettings['selfcheck.watchdog_enabled']?.value,
                )}
                className={`shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium ${watchdogSettings['selfcheck.watchdog_enabled']?.value ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400'} disabled:cursor-not-allowed disabled:opacity-50`}
              >
                {watchdogSettings['selfcheck.watchdog_enabled']?.value ? '已开启' : '已关闭'}
              </button>
            </div>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label htmlFor="selfcheck-watchdog-interval" className="text-[11px] text-slate-400">
                巡检间隔（秒）
                <input
                  id="selfcheck-watchdog-interval"
                  type="number"
                  min="60"
                  max="86400"
                  value={intervalDraft}
                  disabled={updatingSetting === 'selfcheck.watchdog_interval_seconds' || watchdogSettings['selfcheck.watchdog_interval_seconds']?.readonly}
                  onChange={event => setIntervalDraft(event.target.value)}
                  onBlur={saveInterval}
                  onKeyDown={event => {
                    if (event.key === 'Enter') event.currentTarget.blur()
                  }}
                  className="mt-1 block w-32 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 disabled:opacity-50"
                />
              </label>
              <span className="pb-1.5 text-[11px] text-slate-600">
                配置版本 {settingsVersion || '-'}
              </span>
            </div>
          </div>

          <div className="border-t border-slate-800 pt-4 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                aria-label="本次运行包含模型 Canary"
                checked={includeModel}
                onChange={event => setIncludeModel(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-950 text-emerald-500"
              />
              <span>
                <span className="block text-sm text-slate-200">本次包含模型 Canary</span>
                <span className="mt-1 block text-[11px] leading-4 text-slate-500">
                  手动运行时产生一次 fast reply Route 调用，验证 API 连通性、响应合同和结构化语义；默认关闭。
                </span>
              </span>
            </label>
            <label className="mt-4 flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                aria-label="周期巡检包含模型 Canary"
                checked={Boolean(watchdogSettings['selfcheck.model_canary_enabled']?.value)}
                disabled={updatingSetting === 'selfcheck.model_canary_enabled' || watchdogSettings['selfcheck.model_canary_enabled']?.readonly}
                onChange={event => updateWatchdogSetting(
                  'selfcheck.model_canary_enabled',
                  event.target.checked,
                )}
                className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-950 text-amber-500 disabled:opacity-50"
              />
              <span>
                <span className="block text-sm text-slate-200">周期巡检包含模型 Canary</span>
                <span className="mt-1 block text-[11px] leading-4 text-amber-300/70">
                  每轮会调用一次模型并产生费用；仅在需要持续监测模型 API 时开启。
                </span>
              </span>
            </label>
          </div>
        </div>
      </Card>

      <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MiniStat
          label="最近运行"
          value={STATUS_LABELS[report?.status] || report?.status || '尚未运行'}
          tone={STATUS_TONES[report?.status] || 'slate'}
        />
        <MiniStat label="通过" value={summary.passed || 0} tone="emerald" />
        <MiniStat label="降级" value={summary.degraded || 0} tone="amber" />
        <MiniStat label="失败" value={summary.failed || 0} tone="red" />
        <MiniStat label="证据不足" value={summary.inconclusive || 0} tone="blue" />
        <MiniStat label="跳过" value={summary.skipped || 0} />
        <MiniStat label="能力覆盖" value={`${coverage.covered || 0}/${coverage.total || 0}`} tone="blue" />
        <MiniStat
          label="必检缺口"
          value={coverage.required_unverified || 0}
          tone={coverage.required_unverified ? 'red' : 'emerald'}
        />
      </div>

      {report && (
        <Card className="mb-4 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                {report.status === 'failed'
                  ? <AlertTriangle className="h-4 w-4 text-red-400" />
                  : <CheckCircle2 className="h-4 w-4 text-emerald-400" />}
                <span className="font-mono text-xs text-slate-200">{report.run_id}</span>
                <Badge tone={STATUS_TONES[report.status]}>
                  {STATUS_LABELS[report.status] || report.status}
                </Badge>
              </div>
              <div className="mt-1 text-[11px] text-slate-500">
                {formatTime(report.started_at)} · {report.trigger} · {report.environment}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <select
                aria-label="状态筛选"
                value={statusFilter}
                onChange={event => setStatusFilter(event.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300"
              >
                <option value="all">全部状态</option>
                {Object.entries(STATUS_LABELS).filter(([status]) => status !== 'running').map(([status, label]) => (
                  <option key={status} value={status}>{label}</option>
                ))}
              </select>
              <select
                aria-label="分类筛选"
                value={categoryFilter}
                onChange={event => setCategoryFilter(event.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300"
              >
                <option value="all">全部分类</option>
                {categories.map(category => <option key={category} value={category}>{category}</option>)}
              </select>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="space-y-3">
          {filteredResults.map(result => <ResultCard key={result.check_id} result={result} />)}
          {report && filteredResults.length === 0 && (
            <Card className="py-14 text-center text-sm text-slate-600">当前筛选没有结果</Card>
          )}
          {!report && (
            <Card className="py-14 text-center text-sm text-slate-600">尚无自检记录，点击“运行自检”开始。</Card>
          )}
        </div>

        <div className="space-y-4">
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-200">能力覆盖</h2>
            <div className="mt-3 space-y-2">
              {Object.entries(coverage.by_kind || {}).map(([kind, item]) => (
                <div key={kind} className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-300">{kind}</span>
                    <span className="font-mono text-slate-500">{item.covered}/{item.total}</span>
                  </div>
                  {item.unverified > 0 && (
                    <div className="mt-1 text-[11px] text-amber-300">{item.unverified} 项未验证</div>
                  )}
                </div>
              ))}
            </div>
          </Card>

          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-200">运行历史</h2>
            <div className="mt-3 space-y-2">
              {runs.map(item => (
                <button
                  type="button"
                  key={item.run_id}
                  onClick={() => openRun(item.run_id)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950/50 p-2.5 text-left transition-colors hover:border-slate-700 hover:bg-slate-800/60"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-slate-300">{item.run_id}</span>
                    <Badge tone={STATUS_TONES[item.status]}>{STATUS_LABELS[item.status] || item.status}</Badge>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-600">{formatTime(item.started_at)}</div>
                </button>
              ))}
              {runs.length === 0 && <div className="text-xs text-slate-600">暂无记录</div>}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
