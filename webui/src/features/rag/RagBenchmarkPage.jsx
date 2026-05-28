import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, RefreshCw, Save, Trash2 } from 'lucide-react'

import { api } from '../../api'
import { ActionButton, Badge, Card, Field, IconButton, JsonBlock, MiniStat, Modal, Pagination, Spinner } from '../../components/ui'

const emptyCase = {
  id: '',
  suite: 'rag_benchmark',
  source_type: 'memory',
  case_type: 'positive',
  status: 'enabled',
  query: '',
  filters: {},
  expected: { candidate_ids: [], hit_at: 5 },
  meta: { origin: 'manual' },
}

const sourceTypes = ['memory', 'group_memory', 'sticker', 'knowledge']
const caseTypes = ['positive', 'negative', 'constraint_only']

function pct(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(1)}%`
}

function ms(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '-'
  return n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`
}

function sourceTone(source) {
  return source === 'generated' ? 'blue' : 'emerald'
}

function CaseEditor({ item, status, onClose, onSaved }) {
  const editable = item?.editable !== false
  const [raw, setRaw] = useState(JSON.stringify(item?.case || emptyCase, null, 2))
  const [saving, setSaving] = useState(false)
  const manualWritable = status?.manual_dir_writable !== false

  const save = () => {
    let parsed
    try {
      parsed = JSON.parse(raw)
    } catch {
      alert('JSON 格式错误')
      return
    }
    if (!parsed.id) {
      alert('case.id 不能为空')
      return
    }
    setSaving(true)
    api.put(`/rag/benchmark/cases/${encodeURIComponent(parsed.id)}`, { case: parsed })
      .then(() => { onSaved(); onClose() })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setSaving(false))
  }

  const remove = () => {
    const parsed = JSON.parse(raw || '{}')
    if (!parsed.id || !confirm(`确认删除 ${parsed.id}？`)) return
    api.delete(`/rag/benchmark/cases/${encodeURIComponent(parsed.id)}`)
      .then(() => { onSaved(); onClose() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  return (
    <Modal onClose={onClose} wide>
      <div className="space-y-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-white">Benchmark Case</h2>
            <p className="mt-1 text-xs text-slate-500">{editable ? 'Manual case 可编辑；generated case 只读。' : 'generated case 只读'}</p>
          </div>
          <Badge tone={editable ? 'emerald' : 'blue'}>{editable ? 'manual' : 'generated'}</Badge>
        </div>
        <textarea
          value={raw}
          onChange={e => setRaw(e.target.value)}
          readOnly={!editable || !manualWritable}
          rows={18}
          className="w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-200 outline-none focus:border-emerald-500 disabled:opacity-50"
        />
        {!manualWritable && <div className="text-xs text-amber-300">manual case 目录不可写，保存和删除已禁用。</div>}
        <div className="flex flex-wrap justify-end gap-2">
          {editable && (
            <ActionButton onClick={remove} disabled={!manualWritable} tone="red">
              <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              删除 Manual Case
            </ActionButton>
          )}
          {editable && (
            <ActionButton onClick={save} disabled={!manualWritable || saving} tone="emerald">
              <Save className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              保存 Manual Case
            </ActionButton>
          )}
        </div>
      </div>
    </Modal>
  )
}

export function RagBenchmarkPage() {
  const [status, setStatus] = useState(null)
  const [cases, setCases] = useState({ items: [], total: 0, page: 1, limit: 50 })
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({
    source_type: '',
    case_type: '',
    origin: '',
    case_status: 'all',
    result_status: '',
    q: '',
  })
  const [runOptions, setRunOptions] = useState({
    provider_mode: 'deterministic',
    sample_before_run: false,
    include_manual: true,
    include_generated: true,
    source_types: [],
    case_types: [],
    per_source: 5,
    max_cases: 100,
    timeout_seconds: 60,
    per_case_timeout_seconds: 15,
    include_candidates_limit: 10,
  })
  const [runResult, setRunResult] = useState(null)
  const [latest, setLatest] = useState(null)
  const [latestMarkdown, setLatestMarkdown] = useState('')
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [sampling, setSampling] = useState(false)
  const [editing, setEditing] = useState(null)

  const loadStatus = useCallback(() => {
    api.get('/rag/benchmark/status').then(r => setStatus(r.data)).catch(() => setStatus(null))
  }, [])

  const loadCases = useCallback(() => {
    setLoading(true)
    api.get('/rag/benchmark/cases', { params: { ...filters, page, limit: 50 } })
      .then(r => setCases(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }, [filters, page])

  const loadLatest = useCallback(() => {
    api.get('/rag/benchmark/reports/latest')
      .then(r => {
        setLatest(r.data)
        setLatestMarkdown(r.data.markdown || '')
      })
      .catch(() => { setLatest(null); setLatestMarkdown('') })
  }, [])

  useEffect(() => {
    loadStatus()
    loadLatest()
  }, [loadStatus, loadLatest])

  useEffect(() => { loadCases() }, [loadCases])

  const openCase = (item) => {
    api.get(`/rag/benchmark/cases/${encodeURIComponent(item.id)}`)
      .then(r => setEditing(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const newCase = () => {
    setEditing({ case: emptyCase, origin: 'manual', editable: true })
  }

  const sample = () => {
    setSampling(true)
    api.post('/rag/benchmark/sample', { per_source: Number(runOptions.per_source) || 5 })
      .then(r => {
        if (!r.data.ok) alert((r.data.preflight?.errors || ['preflight failed']).join('\n'))
        loadStatus()
        loadCases()
      })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setSampling(false))
  }

  const run = () => {
    setRunning(true)
    api.post('/rag/benchmark/run', {
      ...runOptions,
      per_source: Number(runOptions.per_source) || 5,
      max_cases: Number(runOptions.max_cases) || 100,
      timeout_seconds: Number(runOptions.timeout_seconds) || 60,
      per_case_timeout_seconds: Number(runOptions.per_case_timeout_seconds) || 15,
      include_candidates_limit: Number(runOptions.include_candidates_limit) || 10,
    })
      .then(r => {
        setRunResult(r.data)
        loadLatest()
        loadCases()
      })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }

  const metrics = runResult?.metrics || latest?.metrics || {}
  const overall = metrics.overall || {}
  const failedScores = runResult?.failed_scores || latest?.failed_scores || []
  const staleCount = runResult?.stale_generated_cases?.length || 0
  const runWarnings = runResult?.warnings || []
  const preflight = status?.preflight || runResult?.preflight || {}

  const toggleRunArray = (key, value, checked) => {
    setRunOptions(current => ({
      ...current,
      [key]: checked
        ? Array.from(new Set([...(current[key] || []), value]))
        : (current[key] || []).filter(item => item !== value),
    }))
  }

  const statusMeta = useMemo(() => [
    <span key="manual">manual: {status?.manual_dir_writable === false ? '不可写' : '可写'}</span>,
    <span key="generated">generated: {status?.generated_dir_writable === false ? '不可写' : '可写'}</span>,
    <span key="reports">reports: {status?.reports_dir_writable === false ? '不可写' : '可写'}</span>,
    <span key="readonly">readonly DB: {status?.db_readonly_supported ? 'ok' : 'unsupported'}</span>,
  ], [status])

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">RAG Benchmark</h1>
          <p className="mt-1 text-xs text-slate-500">管理 RAG 回归用例、刷新 generated cases、运行只读 benchmark。</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">{statusMeta}</div>
        </div>
        <div className="flex gap-2">
          <IconButton label="刷新" icon={RefreshCw} onClick={() => { loadStatus(); loadCases(); loadLatest() }} />
          <ActionButton onClick={newCase} disabled={status?.manual_dir_writable === false}>新增 Manual Case</ActionButton>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MiniStat label="total" value={overall.total_cases ?? 0} />
        <MiniStat label="pass rate" value={pct(overall.pass_rate)} tone={overall.pass_rate === 1 ? 'emerald' : 'amber'} />
        <MiniStat label="hit@1" value={pct(overall['hit@1'])} />
        <MiniStat label="hit@3" value={pct(overall['hit@3'])} />
        <MiniStat label="hit@5" value={pct(overall['hit@5'])} />
        <MiniStat label="MRR" value={Number(overall.mrr || 0).toFixed(3)} />
        <MiniStat label="degraded" value={pct(overall.degraded_rate)} tone={overall.degraded_rate ? 'amber' : 'emerald'} />
        <MiniStat label="p95 latency" value={ms(overall.p95_latency_ms)} />
      </div>

      <Card className="p-3">
        <div className="grid gap-3 lg:grid-cols-[repeat(7,minmax(0,1fr))_auto] lg:items-end">
          <Field id="rag-benchmark-provider-mode" label="provider_mode">
            <select id="rag-benchmark-provider-mode" value={runOptions.provider_mode}
              onChange={e => setRunOptions({ ...runOptions, provider_mode: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
              <option value="deterministic">deterministic</option>
              <option value="no_reranker_baseline">no_reranker_baseline</option>
              <option value="runtime">runtime</option>
            </select>
          </Field>
          <Field id="rag-benchmark-per-source" label="per_source">
            <input id="rag-benchmark-per-source" type="number" value={runOptions.per_source}
              onChange={e => setRunOptions({ ...runOptions, per_source: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          </Field>
          <Field id="rag-benchmark-max-cases" label="max_cases">
            <input id="rag-benchmark-max-cases" type="number" value={runOptions.max_cases}
              onChange={e => setRunOptions({ ...runOptions, max_cases: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          </Field>
          <Field id="rag-benchmark-timeout" label="timeout_seconds">
            <input id="rag-benchmark-timeout" type="number" value={runOptions.timeout_seconds}
              onChange={e => setRunOptions({ ...runOptions, timeout_seconds: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          </Field>
          <Field id="rag-benchmark-per-case-timeout" label="per_case_timeout">
            <input id="rag-benchmark-per-case-timeout" type="number" value={runOptions.per_case_timeout_seconds}
              onChange={e => setRunOptions({ ...runOptions, per_case_timeout_seconds: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          </Field>
          <Field id="rag-benchmark-candidate-limit" label="candidate_limit">
            <input id="rag-benchmark-candidate-limit" type="number" value={runOptions.include_candidates_limit}
              onChange={e => setRunOptions({ ...runOptions, include_candidates_limit: e.target.value })}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          </Field>
          <label className="flex h-9 items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 text-xs text-slate-300">
            <input type="checkbox" checked={runOptions.sample_before_run}
              onChange={e => setRunOptions({ ...runOptions, sample_before_run: e.target.checked })} />
            sample_before_run
          </label>
          <div className="flex gap-2">
            <ActionButton onClick={sample} disabled={sampling || preflight.ok === false}>
              {sampling ? '采样中' : '刷新 generated'}
            </ActionButton>
            <ActionButton onClick={run} disabled={running || preflight.ok === false} tone="emerald">
              <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {running ? '运行中' : '运行'}
            </ActionButton>
          </div>
        </div>
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          <label className="flex min-h-9 items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 text-xs text-slate-300">
            <input type="checkbox" checked={runOptions.include_manual}
              onChange={e => setRunOptions({ ...runOptions, include_manual: e.target.checked })} />
            include manual
          </label>
          <label className="flex min-h-9 items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 text-xs text-slate-300">
            <input type="checkbox" checked={runOptions.include_generated}
              onChange={e => setRunOptions({ ...runOptions, include_generated: e.target.checked })} />
            include generated
          </label>
          <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
            <div className="mb-2 text-[11px] text-slate-500">source_types</div>
            <div className="flex flex-wrap gap-2">
              {sourceTypes.map(source => (
                <label key={source} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={(runOptions.source_types || []).includes(source)}
                    onChange={e => toggleRunArray('source_types', source, e.target.checked)} />
                  {source}
                </label>
              ))}
            </div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
            <div className="mb-2 text-[11px] text-slate-500">case_types</div>
            <div className="flex flex-wrap gap-2">
              {caseTypes.map(type => (
                <label key={type} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={(runOptions.case_types || []).includes(type)}
                    onChange={e => toggleRunArray('case_types', type, e.target.checked)} />
                  {type}
                </label>
              ))}
            </div>
          </div>
        </div>
        {runOptions.provider_mode === 'runtime' && (
          <div className="mt-2 text-[11px] text-amber-300">runtime 会触发真实 provider，可能加载模型；timeout 是软超时，单个调用不保证强杀。</div>
        )}
        {preflight.ok === false && <div className="mt-2 text-xs text-red-300">preflight: {(preflight.errors || []).join(' / ')}</div>}
        {staleCount > 0 && <div className="mt-2 text-xs text-amber-300">stale generated cases: {staleCount}，请重新刷新 generated。</div>}
        {runWarnings.length > 0 && <div className="mt-2 text-xs text-amber-300">benchmark warnings: {runWarnings.join(' / ')}</div>}
      </Card>

      <Card className="p-3">
        <div className="grid gap-2 md:grid-cols-6">
          <input value={filters.q} onChange={e => { setFilters({ ...filters, q: e.target.value }); setPage(1) }}
            placeholder="搜索 case/query" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200" />
          <select value={filters.source_type} onChange={e => { setFilters({ ...filters, source_type: e.target.value }); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
            <option value="">全部 source</option>
            <option value="memory">memory</option>
            <option value="group_memory">group_memory</option>
            <option value="sticker">sticker</option>
            <option value="knowledge">knowledge</option>
          </select>
          <select value={filters.case_type} onChange={e => { setFilters({ ...filters, case_type: e.target.value }); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
            <option value="">全部 case_type</option>
            <option value="positive">positive</option>
            <option value="negative">negative</option>
            <option value="constraint_only">constraint_only</option>
          </select>
          <select value={filters.origin} onChange={e => { setFilters({ ...filters, origin: e.target.value }); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
            <option value="">全部 origin</option>
            <option value="manual">manual</option>
            <option value="generated">generated</option>
          </select>
          <select value={filters.case_status} onChange={e => { setFilters({ ...filters, case_status: e.target.value }); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
            <option value="all">全部 case_status</option>
            <option value="enabled">enabled</option>
            <option value="disabled">disabled</option>
          </select>
          <select value={filters.result_status} onChange={e => { setFilters({ ...filters, result_status: e.target.value }); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
            <option value="">全部 result_status</option>
            <option value="stale">stale</option>
          </select>
        </div>
      </Card>

      {loading ? <Spinner /> : (
        <Card className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left text-slate-500">
                <th className="px-3 py-2">case</th>
                <th className="px-3 py-2">source</th>
                <th className="px-3 py-2">origin</th>
                <th className="px-3 py-2">case_type</th>
                <th className="px-3 py-2">query_preview</th>
                <th className="px-3 py-2">expected</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {cases.items.map(item => (
                <tr key={`${item.origin}:${item.id}`} className="border-b border-slate-800/60 align-top hover:bg-slate-800/30">
                  <td className="px-3 py-2 font-mono text-slate-300">{item.id}</td>
                  <td className="px-3 py-2">{item.source_type}</td>
                  <td className="px-3 py-2"><Badge tone={sourceTone(item.origin)}>{item.origin}</Badge></td>
                  <td className="px-3 py-2 text-slate-400">{item.case_type}</td>
                  <td className="max-w-[360px] px-3 py-2 text-slate-300">{item.query_preview}</td>
                  <td className="max-w-[260px] px-3 py-2 font-mono text-slate-500">{(item.expected_candidate_ids || []).join(', ') || '-'}</td>
                  <td className="px-3 py-2">
                    {item.stale ? <Badge tone="amber">stale</Badge> : <Badge>{item.case_status}</Badge>}
                  </td>
                  <td className="px-3 py-2">
                    <button onClick={() => openCase(item)} className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700">
                      {item.editable ? '编辑' : '查看'}
                    </button>
                  </td>
                </tr>
              ))}
              {cases.items.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-slate-600">暂无 case</td></tr>
              )}
            </tbody>
          </table>
        </Card>
      )}
      <Pagination page={page} total={cases.total || 0} limit={50} onChange={setPage} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        <Card className="p-3">
          <h2 className="mb-3 text-sm font-medium text-slate-200">失败明细</h2>
          {failedScores.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-600">暂无失败 case</div>
          ) : (
            <div className="space-y-2">
              {failedScores.map(score => (
                <div key={score.case_id} className="rounded-lg border border-slate-800 bg-slate-950 p-3">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <div className="font-mono text-xs text-slate-200">{score.case_id}</div>
                    <Badge tone="red">failed</Badge>
                  </div>
                  <div className="text-xs text-red-300">{(score.errors || []).join(' / ')}</div>
                  <JsonBlock value={score} className="mt-2 max-h-48" />
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card className="p-3">
          <h2 className="mb-3 text-sm font-medium text-slate-200">Latest Markdown</h2>
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs leading-5 text-slate-300">
            {latestMarkdown || '暂无报告'}
          </pre>
        </Card>
      </div>

      {editing && (
        <CaseEditor
          item={editing}
          status={status}
          onClose={() => setEditing(null)}
          onSaved={() => { loadCases(); loadStatus() }}
        />
      )}
    </div>
  )
}
