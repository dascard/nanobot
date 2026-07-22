import { useCallback, useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { api } from '../../api'
import { ActionButton, Badge, Card, Field, JsonBlock, MiniStat } from '../../components/ui'
import { LLMApiRequestLogsBlock } from '../../components/TraceView'

function formatRate(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(1)}%`
}

function formatCount(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return String(n)
}

// ── Reply Test / Eval ──
function replyEvalTone(value) {
  if (value === true || value === 1 || value === 'reply' || value === 'ok' || value === 'retry_success') return 'emerald'
  if (value === 'no_reply' || value === false || value === 0) return 'slate'
  if (value === 'error' || value === 'no_tool_call' || value === 'fake_tool_call_claim' || value === 'suppressed') return 'red'
  if (value === 'prompt_only' || value === 'code_retry' || value === 'baseline') return 'blue'
  return 'slate'
}

function splitCsv(text) {
  return String(text || '').split(',').map(x => x.trim()).filter(Boolean)
}

function caseToDraft(c = {}) {
  return {
    case_id: c.case_id || '',
    title: c.title || '',
    chat_type: c.chat_type || 'group',
    input_text: c.input_text || '',
    expected_action: c.expected_action || 'any',
    tagsText: (c.tags || []).join(', '),
    expectedKeywordsText: (c.expected_keywords || []).join(', '),
    forbiddenKeywordsText: (c.forbidden_keywords || []).join(', '),
    contextText: JSON.stringify(c.context || {}, null, 2),
    enabled: c.enabled ?? 1,
  }
}

function draftToPayload(draft) {
  const context = (() => {
    try {
      return JSON.parse(draft.contextText || '{}')
    } catch {
      throw new Error('context JSON 格式错误')
    }
  })()
  return {
    case_id: draft.case_id,
    title: draft.title,
    chat_type: draft.chat_type || 'group',
    input_text: draft.input_text,
    context,
    expected_action: draft.expected_action || 'any',
    expected_keywords: splitCsv(draft.expectedKeywordsText),
    forbidden_keywords: splitCsv(draft.forbiddenKeywordsText),
    tags: splitCsv(draft.tagsText),
    enabled: draft.enabled ? 1 : 0,
  }
}

function ReplyAttemptCard({ title, attempt }) {
  if (!attempt) return null
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-xs font-medium text-slate-300">{title}</div>
        <Badge tone={replyEvalTone(attempt.result)}>{attempt.result || '-'}</Badge>
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        <Badge tone={attempt.called_reply ? 'emerald' : 'slate'}>reply {attempt.called_reply ? 'yes' : 'no'}</Badge>
        <Badge tone={attempt.called_no_reply ? 'emerald' : 'slate'}>no_reply {attempt.called_no_reply ? 'yes' : 'no'}</Badge>
        <Badge tone={attempt.structured_fallback ? 'blue' : 'slate'}>fallback {attempt.structured_fallback ? 'yes' : 'no'}</Badge>
        <Badge tone={Number(attempt.total_final_action_count || 0) === 1 ? 'emerald' : 'amber'}>total_final_action_count {attempt.total_final_action_count || 0}</Badge>
        <Badge>reply_tool_call_count {attempt.reply_tool_call_count || 0}</Badge>
      </div>
      <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-900 p-2 text-[11px] text-slate-400">{attempt.raw_output || '-'}</pre>
    </div>
  )
}

function ReplyEvalMetricsTable({ metrics = {} }) {
  const rows = [
    ['reply_call_rate', 'reply/no_reply 调用率'],
    ['valid_action_rate', '有效动作率'],
    ['expected_action_accuracy', '预期动作准确率'],
    ['retry_used_rate', '重试使用率'],
    ['retry_success_rate', '重试成功率'],
    ['no_tool_call_rate', '无工具调用率'],
    ['fake_tool_claim_rate', '假工具声明率'],
    ['empty_output_rate', '空输出率'],
  ]
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map(([key, label]) => (
          <tr key={key} className="border-b border-slate-800/60 last:border-0">
            <td className="py-1.5 pr-3 text-slate-500">{label}</td>
            <td className="py-1.5 text-right font-mono text-slate-200">{formatRate(metrics[key])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ReplyEvalResultsTable({ results = [] }) {
  if (!results.length) return <div className="py-8 text-center text-xs text-slate-600">暂无逐条结果</div>
  return (
    <div className="max-h-96 overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-900">
          <tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-2">case</th>
            <th className="py-2 px-2">预期</th>
            <th className="py-2 px-2">实际</th>
            <th className="py-2 px-2">合约</th>
            <th className="py-2 px-2">retry</th>
            <th className="py-2 px-2">结果</th>
            <th className="py-2 px-2">追溯</th>
          </tr>
        </thead>
        <tbody>
          {results.map(r => (
            <tr key={r.id || r.case_id} className="border-b border-slate-800/50 align-top">
              <td className="py-2 px-2 font-mono text-slate-400 max-w-44 truncate" title={r.case_id}>{r.case_id}</td>
              <td className="py-2 px-2"><Badge>{r.expected_action || '-'}</Badge></td>
              <td className="py-2 px-2"><Badge tone={replyEvalTone(r.actual_action)}>{r.actual_action || '-'}</Badge></td>
              <td className="py-2 px-2">{r.called_reply_or_no_reply ? <Badge tone="emerald">ok</Badge> : <Badge tone="red">miss</Badge>}</td>
              <td className="py-2 px-2">{r.retry_used ? <Badge tone="amber">used</Badge> : <span className="text-slate-600">-</span>}</td>
              <td className="py-2 px-2">{r.passed ? <Badge tone="emerald">pass</Badge> : <Badge tone="red">fail</Badge>}</td>
              <td className="py-2 px-2">
                {r.agent_run_id ? <NavLink to={`/agent-runs/${r.agent_run_id}`} className="text-blue-300 hover:text-blue-200">AgentRun</NavLink> : <span className="text-slate-600">-</span>}
                {r.trace_id && <div className="font-mono text-[10px] text-slate-600 truncate max-w-36" title={r.trace_id}>{r.trace_id.slice(0, 16)}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ReplyTrafficPanel({ traffic, hours, includeTest, loading, onHoursChange, onIncludeTestChange, onRefresh }) {
  const sessionRows = traffic?.session_breakdown || []
  const failures = traffic?.recent_failures || []
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-sm font-medium text-slate-300">真实流量</h2>
          <p className="text-[11px] text-slate-600">来自 /reply-eval/traffic 的 reply 合约日志；默认排除 reply-eval / smoke 测试 session</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-500">窗口
            <select value={hours} onChange={e => onHoursChange(Number(e.target.value))} className="ml-2 rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5 text-xs text-slate-200">
              <option value={1}>1h</option>
              <option value={6}>6h</option>
              <option value={24}>24h</option>
              <option value={72}>72h</option>
              <option value={168}>7d</option>
            </select>
          </label>
          <label className="flex items-center gap-2 pb-1 text-xs text-slate-400">
            <input type="checkbox" checked={includeTest} onChange={e => onIncludeTestChange(e.target.checked)} className="accent-emerald-500" />
            包含测试 session
          </label>
          <ActionButton onClick={onRefresh} disabled={loading}>{loading ? '加载中' : '刷新真实流量'}</ActionButton>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MiniStat label="总运行数" value={formatCount(traffic?.total_runs)} tone="blue" />
        <MiniStat label="合约成功率" value={formatRate(traffic?.contract_ok_rate)} tone={Number(traffic?.contract_ok_rate || 0) >= 0.9 ? 'emerald' : 'amber'} />
        <MiniStat label="首次成功率" value={formatRate(traffic?.first_attempt_ok_rate)} tone="emerald" />
        <MiniStat label="提示词补救次数" value={formatCount(traffic?.prompt_miss_count)} tone={Number(traffic?.prompt_miss_count || 0) ? 'amber' : 'slate'} />
        <MiniStat label="补救成功率" value={formatRate(traffic?.retry_success_rate)} tone="blue" />
        <MiniStat label="retry_failed_after_prompt_count" value={formatCount(traffic?.retry_failed_after_prompt_count)} tone={Number(traffic?.retry_failed_after_prompt_count || 0) ? 'red' : 'slate'} />
        <MiniStat label="总工具调用" value={formatCount(traffic?.total_final_action_count)} />
        <MiniStat label="reply 调用" value={formatCount(traffic?.reply_tool_call_count)} />
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded-lg border border-slate-800 bg-slate-950/40">
          <div className="border-b border-slate-800 px-3 py-2 text-xs text-slate-500">按 session 分布</div>
          <div className="max-h-64 overflow-auto">
            {sessionRows.length ? (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-950 text-slate-500">
                  <tr className="border-b border-slate-800">
                    <th className="px-3 py-2 text-left">session</th>
                    <th className="px-2 py-2 text-right">runs</th>
                    <th className="px-2 py-2 text-right">ok</th>
                    <th className="px-2 py-2 text-right">miss</th>
                    <th className="px-2 py-2 text-right">retry ok</th>
                  </tr>
                </thead>
                <tbody>
                  {sessionRows.map(row => (
                    <tr key={row.session_id} className="border-b border-slate-800/50 last:border-0">
                      <td className="max-w-56 truncate px-3 py-2 font-mono text-slate-300" title={row.session_id}>{row.session_id || '-'}</td>
                      <td className="px-2 py-2 text-right text-slate-400">{row.total_runs}</td>
                      <td className="px-2 py-2 text-right text-emerald-300">{formatRate(row.contract_ok_rate)}</td>
                      <td className="px-2 py-2 text-right text-amber-300">{row.prompt_miss_count}</td>
                      <td className="px-2 py-2 text-right text-blue-300">{row.retry_success_runs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="py-8 text-center text-xs text-slate-600">暂无真实流量</div>
            )}
          </div>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950/40">
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <span className="text-xs text-slate-500">最近失败/异常样本</span>
            {traffic?.truncated && <Badge tone="amber">已截断</Badge>}
          </div>
          <div className="max-h-64 overflow-auto">
            {failures.length ? (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-950 text-slate-500">
                  <tr className="border-b border-slate-800">
                    <th className="px-3 py-2 text-left">时间</th>
                    <th className="px-2 py-2 text-left">session</th>
                    <th className="px-2 py-2 text-left">run</th>
                    <th className="px-2 py-2 text-right">attempt</th>
                    <th className="px-2 py-2 text-left">result</th>
                    <th className="px-2 py-2 text-left">输出预览</th>
                  </tr>
                </thead>
                <tbody>
                  {failures.map(item => (
                    <tr key={item.id || `${item.run_id}:${item.attempt}`} className="border-b border-slate-800/50 align-top last:border-0">
                      <td className="whitespace-nowrap px-3 py-2 text-slate-500">{String(item.created_at || '').replace('T', ' ').slice(5, 19) || '-'}</td>
                      <td className="max-w-40 truncate px-2 py-2 font-mono text-slate-400" title={item.session_id}>{item.session_id || '-'}</td>
                      <td className="max-w-40 truncate px-2 py-2 font-mono text-blue-300" title={item.run_id || item.trace_id}>
                        {item.run_id ? <NavLink to={`/agent-runs/${item.run_id}`} className="hover:text-blue-200">{item.run_id}</NavLink> : (item.trace_id || '-')}
                      </td>
                      <td className="px-2 py-2 text-right text-slate-400">{item.attempt}</td>
                      <td className="px-2 py-2"><Badge tone={replyEvalTone(item.result)}>{item.result || '-'}</Badge></td>
                      <td className="max-w-sm px-2 py-2 text-slate-400">
                        <div className="line-clamp-2 whitespace-pre-wrap break-all" title={item.raw_output_preview || ''}>{item.raw_output_preview || '-'}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="py-8 text-center text-xs text-slate-600">暂无失败样本</div>
            )}
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-600">
        <span>日志 {traffic?.total_logs ?? 0} 条</span>
        <span>no_reply 调用 {traffic?.no_reply_tool_call_count ?? 0}</span>
        <span>结构化兜底 {traffic?.structured_fallback_count ?? 0}</span>
      </div>
    </Card>
  )
}

export function ReplyEvalPage() {
  const [form, setForm] = useState({
    chat_type: 'group',
    session_id: 'reply-test',
    sender_id: 'admin',
    sender_name: 'admin',
    message: '你在吗',
    variant: 'v2_code_retry',
    enable_reply_contract_retry: true,
  })
  const [testResult, setTestResult] = useState(null)
  const [cases, setCases] = useState([])
  const [preview, setPreview] = useState([])
  const [previewSelected, setPreviewSelected] = useState(new Set())
  const [selectedCases, setSelectedCases] = useState(new Set())
  const [runs, setRuns] = useState([])
  const [runResult, setRunResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [evalVariant, setEvalVariant] = useState('v2_code_retry')
  const [evalLimit, setEvalLimit] = useState(50)
  const [newCase, setNewCase] = useState(caseToDraft({}))
  const [editingCase, setEditingCase] = useState(null)
  const [traffic, setTraffic] = useState(null)
  const [trafficHours, setTrafficHours] = useState(24)
  const [trafficIncludeTest, setTrafficIncludeTest] = useState(false)
  const [trafficLoading, setTrafficLoading] = useState(false)

  const loadCases = useCallback(() => {
    api.get('/reply-eval/cases').then(r => setCases(r.data.items || [])).catch(() => setCases([]))
  }, [])
  const loadRuns = useCallback(() => {
    api.get('/reply-eval/runs').then(r => setRuns(r.data.items || [])).catch(() => setRuns([]))
  }, [])
  const loadTraffic = useCallback(() => {
    setTrafficLoading(true)
    api.get('/reply-eval/traffic', {
      params: {
        hours: Number(trafficHours) || 24,
        limit: 50,
        include_test_sessions: trafficIncludeTest,
      },
    })
      .then(r => setTraffic(r.data))
      .catch(() => setTraffic(null))
      .finally(() => setTrafficLoading(false))
  }, [trafficHours, trafficIncludeTest])
  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadCases()
      loadRuns()
      loadTraffic()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadCases, loadRuns, loadTraffic])

  const setField = (key, value) => setForm(v => ({ ...v, [key]: value }))
  const runManualTest = (realSend = false) => {
    if (realSend && !confirm('确认执行真实发送/运行？这不是 dry-run，可能写入真实 session 状态。')) return
    setLoading(true)
    api.post('/reply-test/run', { ...form, dry_run: !realSend })
      .then(r => setTestResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const createCase = () => {
    let payload
    try { payload = draftToPayload(newCase) } catch (e) { alert(e.message); return }
    api.post('/reply-eval/cases', payload)
      .then(() => { setNewCase(caseToDraft({})); loadCases() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
  const generatePreview = () => {
    api.post('/reply-eval/generate-preview', {}).then(r => {
      const items = r.data.items || []
      setPreview(items)
      setPreviewSelected(new Set(items.map(x => x.case_id)))
    })
  }
  const savePreview = () => {
    const items = preview.filter(item => previewSelected.has(item.case_id))
    if (!items.length) {
      alert('请先选择要保存的预览用例')
      return
    }
    api.post('/reply-eval/save-generated', { items }).then(() => { setPreview([]); setPreviewSelected(new Set()); loadCases() })
  }
  const runEval = (variant = evalVariant) => {
    setLoading(true)
    api.post('/reply-eval/run', {
      variant,
      limit: Number(evalLimit) || 50,
      case_ids: Array.from(selectedCases),
    })
      .then(r => { setRunResult(r.data); loadRuns() })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const loadRunDetail = (id) => {
    setLoading(true)
    api.get(`/reply-eval/runs/${encodeURIComponent(id)}`)
      .then(r => setRunResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const delCase = (caseId) => {
    if (!confirm(`确认删除 ${caseId}?`)) return
    api.delete(`/reply-eval/cases/${encodeURIComponent(caseId)}`).then(loadCases)
  }
  const saveCaseEdit = () => {
    let payload
    try { payload = draftToPayload(editingCase) } catch (e) { alert(e.message); return }
    api.put(`/reply-eval/cases/${encodeURIComponent(editingCase.case_id)}`, payload)
      .then(() => { setEditingCase(null); loadCases() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
  const updatePreviewItem = (caseId, patch) => {
    setPreview(prev => prev.map(item => item.case_id === caseId ? { ...item, ...patch } : item))
  }
  const togglePreviewSelected = (caseId, checked) => {
    setPreviewSelected(prev => {
      const next = new Set(prev)
      checked ? next.add(caseId) : next.delete(caseId)
      return next
    })
  }
  const toggleCaseSelected = (caseId, checked) => {
    setSelectedCases(prev => {
      const next = new Set(prev)
      checked ? next.add(caseId) : next.delete(caseId)
      return next
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold mb-1">Reply 测试</h1>
          <p className="text-xs text-slate-500">单条合约检查、测试集管理与 reply/no_reply A/B 评估</p>
        </div>
        <ActionButton onClick={() => { loadCases(); loadRuns(); loadTraffic() }}>刷新</ActionButton>
      </div>

      <ReplyTrafficPanel
        traffic={traffic}
        hours={trafficHours}
        includeTest={trafficIncludeTest}
        loading={trafficLoading}
        onHoursChange={setTrafficHours}
        onIncludeTestChange={setTrafficIncludeTest}
        onRefresh={loadTraffic}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(480px,0.9fr)]">
        <Card className="p-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div>
              <h2 className="text-sm font-medium text-slate-300">单条调用测试</h2>
              <p className="text-[11px] text-slate-600">dry-run 不写真实发送状态；真实发送入口单独确认</p>
            </div>
            <Badge tone={form.enable_reply_contract_retry ? 'emerald' : 'slate'}>retry {form.enable_reply_contract_retry ? 'on' : 'off'}</Badge>
          </div>
          <div className="grid grid-cols-1 gap-2 mb-3 sm:grid-cols-2 xl:grid-cols-4">
            <label className="text-[11px] text-slate-500">chat_type
              <select aria-label="chat_type" value={form.chat_type} onChange={e => setField('chat_type', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="group">group</option>
                <option value="private">private</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">variant
              <select aria-label="variant" value={form.variant} onChange={e => setField('variant', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="v2_code_retry">v2_code_retry</option>
                <option value="v2_prompt_only">v2_prompt_only</option>
                <option value="baseline">baseline</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">session_id
              <input aria-label="session_id" value={form.session_id} onChange={e => setField('session_id', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">sender_id
              <input aria-label="sender_id" value={form.sender_id} onChange={e => setField('sender_id', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">sender_name
              <input aria-label="sender_name" value={form.sender_name} onChange={e => setField('sender_name', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">character_name
              <input aria-label="character_name" value={form.character_name || ''} onChange={e => setField('character_name', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400 self-end">
              <input aria-label="启用合约重试" type="checkbox" checked={form.enable_reply_contract_retry} onChange={e => setField('enable_reply_contract_retry', e.target.checked)} className="accent-emerald-500" />
              启用合约重试
            </label>
          </div>
          <label className="block text-[11px] text-slate-500 mb-3">message
            <textarea aria-label="message" value={form.message} onChange={e => setField('message', e.target.value)} className="mt-1 w-full h-24 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
            <label className="text-[11px] text-slate-500">recent_context
              <textarea aria-label="recent_context" value={form.recent_context || ''} onChange={e => setField('recent_context', e.target.value)} className="mt-1 w-full h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
            </label>
            <label className="text-[11px] text-slate-500">persona_text
              <textarea aria-label="persona_text" value={form.persona_text || ''} onChange={e => setField('persona_text', e.target.value)} className="mt-1 w-full h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ActionButton tone="emerald" onClick={() => runManualTest(false)} disabled={loading}>运行 dry-run</ActionButton>
            <ActionButton tone="red" onClick={() => runManualTest(true)} disabled={loading}>真实发送</ActionButton>
            <span className="text-[11px] text-slate-600">真实发送会使用非 dry-run metadata；执行前会二次确认。</span>
          </div>
          {testResult && (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
                <MiniStat label="final" value={testResult.final?.action || '-'} tone={testResult.final?.action === 'reply' ? 'emerald' : 'slate'} />
                <MiniStat label="retry" value={testResult.metrics?.retry_used ? 'used' : 'no'} tone={testResult.metrics?.retry_used ? 'amber' : 'slate'} />
                <MiniStat label="contract" value={testResult.metrics?.reply_contract_ok ? 'ok' : 'miss'} tone={testResult.metrics?.reply_contract_ok ? 'emerald' : 'red'} />
                <MiniStat label="LLM logs" value={(testResult.llm_api_request_logs || []).length} tone="blue" />
                <MiniStat label="run_id" value={(testResult.run_id || '').slice(0, 12) || '-'} />
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
                <ReplyAttemptCard title="first_attempt" attempt={testResult.first_attempt} />
                <ReplyAttemptCard title="retry_attempt" attempt={testResult.retry_attempt} />
              </div>
              {testResult.final?.content && <JsonBlock value={testResult.final.content} className="max-h-40" />}
              <details className="rounded-lg border border-slate-800">
                <summary className="cursor-pointer px-3 py-2 text-xs text-slate-400 hover:bg-slate-800/40">完整结果 JSON</summary>
                <JsonBlock value={testResult} className="m-2 max-h-96" />
              </details>
              {(testResult.llm_api_request_logs || []).length > 0 && <LLMApiRequestLogsBlock logs={testResult.llm_api_request_logs} />}
            </div>
          )}
        </Card>

        <Card className="p-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div>
              <h2 className="text-sm font-medium text-slate-300">A/B 评估</h2>
              <p className="text-[11px] text-slate-600">选择用例后只跑选中项；未选择时跑全部启用项</p>
            </div>
            <Badge tone="blue">{selectedCases.size ? `${selectedCases.size} selected` : 'all enabled'}</Badge>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
            <label className="text-[11px] text-slate-500">variant
              <select aria-label="评估 variant" value={evalVariant} onChange={e => setEvalVariant(e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="v2_code_retry">v2_code_retry</option>
                <option value="v2_prompt_only">v2_prompt_only</option>
                <option value="baseline">baseline</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">limit
              <input aria-label="评估 limit" type="number" min="1" max="200" value={evalLimit} onChange={e => setEvalLimit(e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <div className="self-end flex gap-2 md:col-span-2">
              <ActionButton tone="emerald" onClick={() => runEval(evalVariant)} disabled={loading || !cases.length}>运行评估</ActionButton>
              <ActionButton onClick={() => setSelectedCases(new Set())}>清空选择</ActionButton>
            </div>
          </div>
          <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-2 text-[11px] text-emerald-200 mb-3">
            所有 variant 都使用当前 canonical Prompt Runtime；prompt_only 仅关闭合约重试。
          </div>
          {runResult && (
            <div className="grid grid-cols-1 xl:grid-cols-[220px_1fr] gap-3 mb-4">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Badge tone={replyEvalTone(runResult.variant)}>{runResult.variant}</Badge>
                  <Badge tone={runResult.failed ? 'red' : 'emerald'}>{runResult.passed || 0}/{runResult.total || 0}</Badge>
                </div>
                <ReplyEvalMetricsTable metrics={runResult.metrics || {}} />
              </div>
              <ReplyEvalResultsTable results={runResult.results || []} />
            </div>
          )}
          <h3 className="text-xs text-slate-500 mb-2">最近评估</h3>
          <div className="space-y-1 max-h-64 overflow-auto">
            {runs.map(r => (
              <button key={r.id} onClick={() => loadRunDetail(r.id)} className="w-full flex items-center gap-2 rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-xs text-left hover:bg-slate-800/60 transition-colors">
                <Badge tone="blue">{r.variant}</Badge>
                <span className="text-slate-300">total {r.total}</span>
                <span className="text-emerald-300">acc {formatRate(r.metrics?.expected_action_accuracy)}</span>
                <span className="text-slate-500 text-xs ml-auto">{r.created_at}</span>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300">测试集</h2>
          <div className="flex gap-2">
            <ActionButton onClick={generatePreview}>生成预览</ActionButton>
            {preview.length > 0 && <ActionButton tone="emerald" onClick={savePreview}>保存选中 {previewSelected.size}</ActionButton>}
          </div>
        </div>
        {preview.length > 0 && (
          <div className="mb-4 rounded-lg border border-slate-800 overflow-hidden">
            <div className="flex items-center justify-between bg-slate-950/70 px-3 py-2 text-xs text-slate-400">
              <span>生成预览 {preview.length} 条</span>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={preview.length > 0 && previewSelected.size === preview.length} onChange={e => setPreviewSelected(e.target.checked ? new Set(preview.map(x => x.case_id)) : new Set())} className="accent-emerald-500" />
                全选
              </label>
            </div>
            <div className="max-h-96 overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-900 text-slate-500">
                  <tr className="border-b border-slate-800"><th className="py-2 px-2 w-8"></th><th className="py-2 px-2">标题</th><th className="py-2 px-2">输入</th><th className="py-2 px-2">预期</th><th className="py-2 px-2">tags</th></tr>
                </thead>
                <tbody>
                  {preview.map(item => (
                    <tr key={item.case_id} className="border-b border-slate-800/50 align-top">
                      <td className="py-2 px-2"><input type="checkbox" checked={previewSelected.has(item.case_id)} onChange={e => togglePreviewSelected(item.case_id, e.target.checked)} className="accent-emerald-500" /></td>
                      <td className="py-2 px-2"><input value={item.title || ''} onChange={e => updatePreviewItem(item.case_id, { title: e.target.value })} className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs" /></td>
                      <td className="py-2 px-2"><textarea value={item.input_text || ''} onChange={e => updatePreviewItem(item.case_id, { input_text: e.target.value })} className="w-full h-12 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs resize-none" /></td>
                      <td className="py-2 px-2">
                        <select value={item.expected_action || 'any'} onChange={e => updatePreviewItem(item.case_id, { expected_action: e.target.value })} className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs">
                          <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
                        </select>
                      </td>
                      <td className="py-2 px-2"><input value={(item.tags || []).join(', ')} onChange={e => updatePreviewItem(item.case_id, { tags: splitCsv(e.target.value) })} className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 gap-2 mb-4 md:grid-cols-6">
          <Field id="reply-new-case-id" label="case_id">
            <input id="reply-new-case-id" value={newCase.case_id} onChange={e => setNewCase(v => ({ ...v, case_id: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
          </Field>
          <Field id="reply-new-case-title" label="标题">
            <input id="reply-new-case-title" value={newCase.title} onChange={e => setNewCase(v => ({ ...v, title: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
          </Field>
          <Field id="reply-new-case-input" label="输入" className="md:col-span-2">
            <input id="reply-new-case-input" value={newCase.input_text} onChange={e => setNewCase(v => ({ ...v, input_text: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
          </Field>
          <Field id="reply-new-case-expected-action" label="预期动作">
            <select id="reply-new-case-expected-action" value={newCase.expected_action} onChange={e => setNewCase(v => ({ ...v, expected_action: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
              <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
            </select>
          </Field>
          <ActionButton tone="emerald" onClick={createCase} disabled={!newCase.input_text}>新增</ActionButton>
        </div>
        {editingCase && (
          <div className="mb-4 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-medium text-blue-200">编辑 {editingCase.case_id}</h3>
              <ActionButton onClick={() => setEditingCase(null)}>关闭</ActionButton>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-6">
              <Field id="reply-edit-title" label="标题">
                <input id="reply-edit-title" value={editingCase.title} onChange={e => setEditingCase(v => ({ ...v, title: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
              </Field>
              <Field id="reply-edit-chat-type" label="chat_type">
                <select id="reply-edit-chat-type" value={editingCase.chat_type} onChange={e => setEditingCase(v => ({ ...v, chat_type: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
                  <option value="group">group</option><option value="private">private</option>
                </select>
              </Field>
              <Field id="reply-edit-expected-action" label="预期动作">
                <select id="reply-edit-expected-action" value={editingCase.expected_action} onChange={e => setEditingCase(v => ({ ...v, expected_action: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
                  <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
                </select>
              </Field>
              <Field id="reply-edit-tags" label="tags">
                <input id="reply-edit-tags" value={editingCase.tagsText} onChange={e => setEditingCase(v => ({ ...v, tagsText: e.target.value }))} className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
              </Field>
              <label className="flex items-center gap-2 self-end text-xs text-slate-400"><input type="checkbox" checked={Boolean(editingCase.enabled)} onChange={e => setEditingCase(v => ({ ...v, enabled: e.target.checked ? 1 : 0 }))} className="accent-emerald-500" /> enabled</label>
              <ActionButton tone="emerald" onClick={saveCaseEdit} className="self-end">保存</ActionButton>
              <Field id="reply-edit-input" label="输入" className="md:col-span-3">
                <textarea id="reply-edit-input" value={editingCase.input_text} onChange={e => setEditingCase(v => ({ ...v, input_text: e.target.value }))} className="h-20 w-full resize-none rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs" />
              </Field>
              <Field id="reply-edit-context" label="上下文 JSON" className="md:col-span-3">
                <textarea id="reply-edit-context" value={editingCase.contextText} onChange={e => setEditingCase(v => ({ ...v, contextText: e.target.value }))} className="h-20 w-full resize-none rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-xs" />
              </Field>
            </div>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3 w-8"><input aria-label="选择全部测试用例" type="checkbox" checked={cases.length > 0 && selectedCases.size === cases.length} onChange={e => setSelectedCases(e.target.checked ? new Set(cases.map(c => c.case_id)) : new Set())} className="accent-emerald-500" /></th><th className="py-2 px-3">case_id</th><th className="py-2 px-3">标题</th><th className="py-2 px-3">输入</th><th className="py-2 px-3">预期</th><th className="py-2 px-3">tags</th><th className="py-2 px-3">操作</th></tr></thead>
            <tbody>{cases.map(c => (
              <tr key={c.case_id} className="border-b border-slate-800/50">
                <td className="py-2 px-3"><input aria-label="选择测试用例" type="checkbox" checked={selectedCases.has(c.case_id)} onChange={e => toggleCaseSelected(c.case_id, e.target.checked)} className="accent-emerald-500" /></td>
                <td className="py-2 px-3 text-xs text-slate-500">{c.case_id}</td>
                <td className="py-2 px-3">{c.title}</td>
                <td className="py-2 px-3 max-w-md truncate">{c.input_text}</td>
                <td className="py-2 px-3"><Badge tone={replyEvalTone(c.expected_action)}>{c.expected_action}</Badge></td>
                <td className="py-2 px-3 text-xs text-slate-500">{(c.tags || []).join(', ')}</td>
                <td className="py-2 px-3 text-right">
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setEditingCase(caseToDraft(c))} className="text-xs text-blue-300 hover:text-blue-200">编辑</button>
                    <button onClick={() => delCase(c.case_id)} className="text-xs text-red-300 hover:text-red-200">删除</button>
                  </div>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
