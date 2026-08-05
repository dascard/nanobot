import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Play,
  RefreshCw,
  RotateCw,
  Save,
} from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  Field,
  JsonBlock,
  MiniStat,
  PageHeader,
  SectionHeader,
  Spinner,
  Toolbar,
} from '../../components/ui'

const SETTING_LABELS = {
  'proactive_outreach.enabled': '启用主动外呼',
  'proactive_outreach.fallback_interval_min': '心跳间隔',
  'proactive_outreach.min_interval_min': '最小间隔',
  'proactive_outreach.max_check_interval_min': '最晚再考虑',
  'proactive_outreach.max_silence_min': '最长沉默',
  'proactive_outreach.ambiguous_hold_min': '投递不确定冻结',
  'proactive_outreach.repeat_topic_cooldown_min': '重复话题冷却',
  'proactive_outreach.daily_delivery_quota': '每日投递配额',
  'proactive_outreach.allow_early_surge': '允许提前评估',
  'proactive_outreach.surge_min_prob': '冲击下限',
  'proactive_outreach.surge_max_prob': '冲击上限',
}

const STATUS_OPTIONS = ['', 'pending', 'sending', 'sent', 'failed']
const LLM_SOURCES = ['', 'classifier.timing_proactive', 'classifier.reply']

function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item?.msg || item?.message || JSON.stringify(item)).join('\n')
  if (detail && typeof detail === 'object') return detail.message || detail.error || JSON.stringify(detail)
  return error?.message || fallback
}

function formatTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

function statusTone(status) {
  if (status === 'sent') return 'emerald'
  if (status === 'pending' || status === 'sending') return 'blue'
  if (status === 'failed') return 'red'
  return 'slate'
}

function boolLabel(value) {
  return value ? '开启' : '关闭'
}

function settingInputType(setting) {
  if (setting?.value_type === 'int' || setting?.value_type === 'float') return 'number'
  return 'text'
}

function settingStep(setting) {
  return setting?.value_type === 'float' ? '0.01' : '1'
}

function SettingControl({ setting, value, saving, onChange, onSave }) {
  if (!setting) return null
  const id = `proactive-setting-${setting.key}`
  if (setting.value_type === 'bool') {
    const checked = value === true || value === 'true'
    return (
      <div className="flex min-h-[68px] items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium text-slate-200">{SETTING_LABELS[setting.key] || setting.key}</div>
          <div className="mt-1 truncate text-[11px] text-slate-500">{setting.key}</div>
        </div>
        <button
          type="button"
          onClick={() => onSave(setting.key, !checked)}
          disabled={saving}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? 'bg-emerald-600' : 'bg-slate-700'}`}
          aria-label={SETTING_LABELS[setting.key] || setting.key}
          title={SETTING_LABELS[setting.key] || setting.key}
        >
          <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-transform ${checked ? 'left-6' : 'left-1'}`} />
        </button>
      </div>
    )
  }

  return (
    <Field
      id={id}
      label={SETTING_LABELS[setting.key] || setting.key}
      hint={`${setting.key}${setting.restart_required ? '，需重启后台线程生效' : ''}`}
    >
      <div className="flex gap-2">
        <input
          id={id}
          type={settingInputType(setting)}
          min={setting.min_value ?? undefined}
          max={setting.max_value ?? undefined}
          step={settingStep(setting)}
          value={value ?? ''}
          onChange={e => onChange(setting.key, e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
        />
        <ActionButton type="button" tone="emerald" onClick={() => onSave(setting.key, value)} disabled={saving} className="gap-1">
          <Save className="h-3.5 w-3.5" /> 保存
        </ActionButton>
      </div>
    </Field>
  )
}

function LogRow({ item, expanded, onToggle }) {
  return (
    <>
      <tr className="border-b border-slate-800/80 align-top">
        <td className="px-3 py-2 text-xs text-slate-500">#{item.id}</td>
        <td className="px-3 py-2 text-xs text-slate-400">{formatTime(item.created_at)}</td>
        <td className="px-3 py-2 font-mono text-xs text-slate-300">{item.target_fingerprint || '-'}</td>
        <td className="px-3 py-2"><Badge tone={statusTone(item.status)}>{item.status || '-'}</Badge></td>
        <td className="px-3 py-2 text-xs text-slate-400">{item.forced ? '是' : '否'}</td>
        <td className="px-3 py-2 text-xs text-slate-400">{formatTime(item.next_check_at)}</td>
        <td className="px-3 py-2 text-xs text-slate-300">
          <div className="max-w-[26rem] truncate" title={item.message || item.judge_reason || item.next_intent || ''}>
            {item.message || item.judge_reason || item.next_intent || '-'}
          </div>
        </td>
        <td className="px-3 py-2 text-right">
          <button
            type="button"
            onClick={() => onToggle(item.id)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
            aria-label="展开 grounding"
            title="展开 grounding"
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-slate-800/80">
          <td colSpan={8} className="px-3 py-3">
            <JsonBlock value={item.grounding || {}} className="max-h-80" />
          </td>
        </tr>
      )}
    </>
  )
}

export function ProactiveOutreachPage() {
  const [status, setStatus] = useState(null)
  const [logs, setLogs] = useState({ items: [], total: 0, page: 1, limit: 50 })
  const [formValues, setFormValues] = useState({})
  const [loading, setLoading] = useState(true)
  const [logsLoading, setLogsLoading] = useState(false)
  const [savingKey, setSavingKey] = useState('')
  const [runningMode, setRunningMode] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [logStatus, setLogStatus] = useState('')
  const [logTargetFingerprint, setLogTargetFingerprint] = useState('')
  const [expandedId, setExpandedId] = useState(null)
  const [runtimeQuery, setRuntimeQuery] = useState('Proactive outreach')
  const [runtimeLog, setRuntimeLog] = useState('')
  const [runtimeLoading, setRuntimeLoading] = useState(false)
  const [llmSource, setLlmSource] = useState('')

  const settingsByKey = useMemo(() => {
    const items = status?.settings || []
    return Object.fromEntries(items.map(item => [item.key, item]))
  }, [status])

  const filteredLlmLogs = useMemo(() => {
    const items = status?.llm_logs || []
    if (!llmSource) return items
    return items.filter(item => item.source === llmSource)
  }, [llmSource, status])

  const loadStatus = useCallback(async () => {
    setError('')
    const response = await api.get('/proactive-outreach/status')
    setStatus(response.data)
    setFormValues(Object.fromEntries((response.data.settings || []).map(item => [item.key, item.value ?? ''])))
  }, [])

  const loadLogs = useCallback(async () => {
    setLogsLoading(true)
    try {
      const response = await api.get('/proactive-outreach/logs', {
        params: {
          status: logStatus,
          target_fingerprint: logTargetFingerprint.trim(),
          limit: 50,
        },
      })
      setLogs(response.data)
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setLogsLoading(false)
    }
  }, [logStatus, logTargetFingerprint])

  const refreshAll = useCallback(async () => {
    setLoading(true)
    try {
      await loadStatus()
      await loadLogs()
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setLoading(false)
    }
  }, [loadLogs, loadStatus])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refreshAll()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [refreshAll])

  const loadRuntimeLog = useCallback(async () => {
    setRuntimeLoading(true)
    setError('')
    try {
      const response = await api.get('/logs/nanobot.log', {
        params: { lines: 500, q: runtimeQuery.trim() || 'Proactive outreach' },
      })
      setRuntimeLog(response.data.content || '')
    } catch (e) {
      setRuntimeLog('')
      setError(formatApiError(e, '读取运行日志失败'))
    } finally {
      setRuntimeLoading(false)
    }
  }, [runtimeQuery])

  const updateSetting = async (key, rawValue) => {
    setSavingKey(key)
    setError('')
    setNotice('')
    const setting = settingsByKey[key]
    let value = rawValue
    if (setting?.value_type === 'int') value = Number.parseInt(rawValue, 10)
    if (setting?.value_type === 'float') value = Number.parseFloat(rawValue)
    try {
      const response = await api.put(`/proactive-outreach/settings/${encodeURIComponent(key)}`, { value })
      setFormValues(prev => ({ ...prev, [key]: response.data.value }))
      setNotice(`${SETTING_LABELS[key] || key} 已保存`)
      await loadStatus()
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setSavingKey('')
    }
  }

  const reloadSettings = async () => {
    setError('')
    setNotice('')
    try {
      await api.post('/proactive-outreach/settings/reload')
      setNotice('配置缓存已重载')
      await loadStatus()
    } catch (e) {
      setError(formatApiError(e, '重载失败'))
    }
  }

  const runOnce = async (mode) => {
    setRunningMode(mode)
    setError('')
    setNotice('')
    try {
      const response = await api.post('/proactive-outreach/run-once', {
        mode,
      })
      setNotice(`检查完成：${response.data.result?.status || 'ok'}`)
      await refreshAll()
    } catch (e) {
      setError(formatApiError(e, '执行检查失败'))
    } finally {
      setRunningMode('')
    }
  }

  const stats = status?.stats || { total: 0, by_status: {} }
  const latest = status?.latest_logs?.[0]
  const superUserCount = status?.super_user_count || 0

  if (loading && !status) return <Spinner />

  return (
    <div className="space-y-4">
      <PageHeader
        title="主动外呼"
        description="单用户主动情感外呼的配置、调度记录、运行日志和模型调用。"
        actions={(
          <>
            <ActionButton type="button" onClick={refreshAll} className="gap-1">
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </ActionButton>
            <ActionButton type="button" tone="blue" onClick={reloadSettings} className="gap-1">
              <RotateCw className="h-3.5 w-3.5" /> 重载配置
            </ActionButton>
          </>
        )}
      />

      {error && <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}
      {notice && <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">{notice}</div>}

      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <MiniStat label="开关" value={boolLabel(status?.enabled)} tone={status?.enabled ? 'emerald' : 'slate'} />
        <MiniStat
          label="Superuser"
          value={status?.super_user_configured ? `已配置 ${superUserCount} 个` : '未配置'}
          tone={status?.super_user_configured ? 'blue' : 'amber'}
        />
        <MiniStat label="业务记录" value={stats.total || 0} tone="slate" />
        <MiniStat label="最近状态" value={latest?.status || '-'} tone={statusTone(latest?.status)} />
      </div>

      <Card className="p-4">
        <SectionHeader
          title="控制"
          description="手动触发会走现有运行链路，Judge 判定发送时会真实投递。"
          actions={(
            <Badge tone={status?.enabled ? 'emerald' : 'slate'}>
              {status?.enabled ? <CheckCircle2 className="mr-1 h-3 w-3" /> : <AlertTriangle className="mr-1 h-3 w-3" />}
              {boolLabel(status?.enabled)}
            </Badge>
          )}
        />
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <SettingControl
            setting={settingsByKey['proactive_outreach.enabled']}
            value={formValues['proactive_outreach.enabled']}
            saving={savingKey === 'proactive_outreach.enabled'}
            onChange={(key, value) => setFormValues(prev => ({ ...prev, [key]: value }))}
            onSave={updateSetting}
          />
          {[
            'proactive_outreach.fallback_interval_min',
            'proactive_outreach.min_interval_min',
            'proactive_outreach.max_check_interval_min',
            'proactive_outreach.max_silence_min',
            'proactive_outreach.ambiguous_hold_min',
            'proactive_outreach.repeat_topic_cooldown_min',
            'proactive_outreach.daily_delivery_quota',
            'proactive_outreach.allow_early_surge',
            'proactive_outreach.surge_min_prob',
            'proactive_outreach.surge_max_prob',
          ].map(key => (
            <SettingControl
              key={key}
              setting={settingsByKey[key]}
              value={formValues[key]}
              saving={savingKey === key}
              onChange={(changedKey, value) => setFormValues(prev => ({ ...prev, [changedKey]: value }))}
              onSave={updateSetting}
            />
          ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <ActionButton type="button" tone="amber" onClick={() => runOnce('due')} disabled={!!runningMode} className="gap-1">
            <Play className="h-3.5 w-3.5" /> {runningMode === 'due' ? '检查中...' : '执行一次到期检查'}
          </ActionButton>
          <ActionButton type="button" tone="blue" onClick={() => runOnce('check')} disabled={!!runningMode} className="gap-1">
            <Play className="h-3.5 w-3.5" /> {runningMode === 'check' ? '检查中...' : '直接执行 Judge 检查'}
          </ActionButton>
        </div>
      </Card>

      <Card className="p-4">
        <SectionHeader title="业务记录" description="proactive_outreach_log 中的调度、判定和投递结果。" />
        <Toolbar>
          <Field id="proactive-log-status" label="状态">
            <select
              id="proactive-log-status"
              value={logStatus}
              onChange={e => setLogStatus(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
            >
              {STATUS_OPTIONS.map(item => <option key={item || 'all'} value={item}>{item || '全部'}</option>)}
            </select>
          </Field>
          <Field id="proactive-log-target" label="目标指纹">
            <input
              id="proactive-log-target"
              value={logTargetFingerprint}
              onChange={e => setLogTargetFingerprint(e.target.value)}
              placeholder="sha256:..."
              className="w-52 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
            />
          </Field>
          <ActionButton type="button" onClick={loadLogs} disabled={logsLoading} className="gap-1">
            <RefreshCw className="h-3.5 w-3.5" /> {logsLoading ? '加载中...' : '筛选'}
          </ActionButton>
        </Toolbar>
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="min-w-full text-left">
            <thead className="bg-slate-950 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">ID</th>
                <th className="px-3 py-2 font-medium">时间</th>
                <th className="px-3 py-2 font-medium">目标指纹</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Forced</th>
                <th className="px-3 py-2 font-medium">Next</th>
                <th className="px-3 py-2 font-medium">内容</th>
                <th className="px-3 py-2 font-medium text-right">Grounding</th>
              </tr>
            </thead>
            <tbody>
              {(logs.items || []).map(item => (
                <LogRow
                  key={item.id}
                  item={item}
                  expanded={expandedId === item.id}
                  onToggle={id => setExpandedId(prev => (prev === id ? null : id))}
                />
              ))}
              {!logs.items?.length && (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-sm text-slate-500">暂无记录</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="mt-3 text-xs text-slate-500">共 {logs.total || 0} 条</div>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="p-4">
          <SectionHeader
            title="运行日志"
            description="默认按 nanobot.log 中的 Proactive outreach 片段过滤。"
            actions={(
              <ActionButton type="button" onClick={loadRuntimeLog} disabled={runtimeLoading} className="gap-1">
                <RefreshCw className="h-3.5 w-3.5" /> {runtimeLoading ? '读取中...' : '读取'}
              </ActionButton>
            )}
          />
          <Field id="proactive-runtime-query" label="过滤词">
            <input
              id="proactive-runtime-query"
              value={runtimeQuery}
              onChange={e => setRuntimeQuery(e.target.value)}
              className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
            />
          </Field>
          <pre className="max-h-[28rem] min-h-48 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs leading-relaxed text-slate-300 whitespace-pre-wrap">
            {runtimeLog || '暂无运行日志内容'}
          </pre>
        </Card>

        <Card className="p-4">
          <SectionHeader title="LLM 请求" description="主动外呼 Judge 与生成链路相关的模型请求摘要。" />
          <Field id="proactive-llm-source" label="Source">
            <select
              id="proactive-llm-source"
              value={llmSource}
              onChange={e => setLlmSource(e.target.value)}
              className="mb-3 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
            >
              {LLM_SOURCES.map(item => <option key={item || 'all'} value={item}>{item || '全部'}</option>)}
            </select>
          </Field>
          <div className="space-y-2">
            {filteredLlmLogs.map(item => (
              <div key={item.id} className="rounded-lg border border-slate-800 bg-slate-950 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge tone={statusTone(item.status)}>{item.status || '-'}</Badge>
                  <span className="text-xs text-slate-400">{item.source}</span>
                  <span className="text-xs text-slate-500">{item.model || item.provider || '-'}</span>
                  <span className="ml-auto text-xs text-slate-500">{formatTime(item.created_at)}</span>
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-900/70 p-2">
                    <div className="mb-1 text-[11px] text-slate-500">Request</div>
                    <div className="line-clamp-4 text-xs leading-5 text-slate-300">{item.request_preview || '-'}</div>
                  </div>
                  <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-900/70 p-2">
                    <div className="mb-1 text-[11px] text-slate-500">Response</div>
                    <div className="line-clamp-4 text-xs leading-5 text-slate-300">{item.response_preview || item.error || '-'}</div>
                  </div>
                </div>
              </div>
            ))}
            {!filteredLlmLogs.length && <div className="rounded-lg border border-slate-800 bg-slate-950 p-8 text-center text-sm text-slate-500">暂无 LLM 请求</div>}
          </div>
        </Card>
      </div>
    </div>
  )
}
