import { useCallback, useEffect, useState } from 'react'
import { Database, RefreshCw, Search } from 'lucide-react'

import { api } from '../../api'
import { Badge, Card, IconButton, JsonBlock, MiniStat, Spinner } from '../../components/ui'

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '-'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)}s`
  return `${Math.round(n)}ms`
}

export function RagScoreBreakdown({ value }) {
  const data = value || {}
  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-slate-200">Score Breakdown</h2>
        <Badge tone={data.degraded ? 'amber' : 'emerald'}>{data.degraded ? 'degraded' : 'normal'}</Badge>
      </div>
      <JsonBlock value={data} className="max-h-72" />
    </Card>
  )
}

export function RagCandidateTable({ candidates = [] }) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-800 text-left text-slate-500">
            <th className="px-3 py-2">source</th>
            <th className="px-3 py-2">title</th>
            <th className="px-3 py-2">citation</th>
            <th className="px-3 py-2">trust</th>
            <th className="px-3 py-2">reranker</th>
            <th className="px-3 py-2">final</th>
            <th className="px-3 py-2">reason</th>
          </tr>
        </thead>
        <tbody>
          {candidates.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-3 py-10 text-center text-slate-600">暂无候选</td>
            </tr>
          ) : candidates.map(item => (
            <tr key={item.candidate_id || item.id} className="border-b border-slate-800/60 align-top">
              <td className="px-3 py-2 text-slate-400">{item.source_type || '-'}</td>
              <td className="max-w-[360px] px-3 py-2 text-slate-200">{item.title || item.text || '-'}</td>
              <td className="max-w-[260px] px-3 py-2 text-slate-400">
                {item.citation?.title || item.document_id || item.reply_token || '-'}
              </td>
              <td className="px-3 py-2 text-slate-400">{item.trust_level || item.citation?.trust_level || '-'}</td>
              <td className="px-3 py-2 font-mono text-slate-300">{item.reranker_score ?? '-'}</td>
              <td className="px-3 py-2 font-mono text-slate-300">{item.final_score ?? '-'}</td>
              <td className="px-3 py-2 text-slate-500">{item.skipped_reason || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

export function RagRerankerPanel({ pairs = [] }) {
  return (
    <Card className="p-3">
      <h2 className="mb-2 text-sm font-medium text-slate-200">Reranker Input</h2>
      {pairs.length === 0 ? (
        <div className="py-8 text-center text-xs text-slate-600">暂无 reranker 输入</div>
      ) : (
        <JsonBlock value={pairs} className="max-h-80" />
      )}
    </Card>
  )
}

export function RagDebugPage() {
  const [sourceType, setSourceType] = useState('memory')
  const [query, setQuery] = useState('端口冲突怎么解决')
  const [runs, setRuns] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [buildLoading, setBuildLoading] = useState(false)
  const [buildResult, setBuildResult] = useState(null)

  const loadRuns = useCallback(() => {
    api.get('/rag/debug/runs', { params: { limit: 20 } })
      .then(r => setRuns(r.data.items || []))
      .catch(() => setRuns([]))
  }, [])

  const loadStatus = useCallback(() => {
    setStatusLoading(true)
    api.get('/rag/debug/status', { params: { source_type: sourceType } })
      .then(r => setStatus(r.data))
      .catch(() => setStatus(null))
      .finally(() => setStatusLoading(false))
  }, [sourceType])

  useEffect(() => {
    const timer = window.setTimeout(() => { loadRuns() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadRuns])
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setBuildResult(null)
      loadStatus()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadStatus])

  const runDebug = () => {
    setLoading(true)
    api.post('/rag/debug/query', { source_type: sourceType, query, limit: 10 })
      .then(r => {
        setResult(r.data)
        loadRuns()
        loadStatus()
      })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }

  const buildIndex = () => {
    setBuildLoading(true)
    api.post('/rag/debug/build-index', { source_type: sourceType, limit_per_source: 500 })
      .then(r => {
        setBuildResult(r.data.result || {})
        setStatus({ source_type: sourceType, index: r.data.index, reranker: status?.reranker || {} })
      })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setBuildLoading(false))
  }

  const response = result?.response || {}
  const stages = response.stages || {}
  const indexStatus = status?.index || {}
  const rerankerStatus = status?.reranker || {}

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">RAG Debug</h1>
          <p className="mt-1 text-xs text-slate-500">统一查看召回、reranker、gate 和最终分数。</p>
        </div>
        <IconButton label="刷新运行记录" icon={RefreshCw} onClick={loadRuns} />
      </div>

      <Card className="p-3">
        <div className="grid gap-3 md:grid-cols-[180px_minmax(0,1fr)_auto] md:items-end">
          <label className="block text-[11px] font-medium text-slate-400">
            source
            <select value={sourceType} onChange={e => setSourceType(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500">
              <option value="memory">memory</option>
              <option value="group_memory">group_memory</option>
              <option value="sticker">sticker</option>
              <option value="knowledge">knowledge</option>
              <option value="group_analysis">group_analysis</option>
              <option value="all">all</option>
            </select>
          </label>
          <label className="block text-[11px] font-medium text-slate-400">
            query
            <input value={query} onChange={e => setQuery(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
          </label>
          <button onClick={runDebug} disabled={loading}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50">
            <Search className="h-4 w-4" aria-hidden="true" />
            {loading ? '运行中' : '运行'}
          </button>
        </div>
      </Card>

      <Card className="p-3">
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-center">
          <div>
            <div className="mb-1 flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-200">Reranker</h2>
              <Badge tone={rerankerStatus.configured ? 'emerald' : 'amber'}>
                {rerankerStatus.configured ? 'configured' : 'missing'}
              </Badge>
            </div>
            <div className="text-xs text-slate-500">
              {rerankerStatus.source || 'none'} · {rerankerStatus.model || '未指定'}
            </div>
            {rerankerStatus.url && <div className="mt-1 truncate font-mono text-[11px] text-slate-600">{rerankerStatus.url}</div>}
          </div>
          <div>
            <div className="mb-1 flex items-center gap-2">
              <h2 className="text-sm font-medium text-slate-200">Index</h2>
              <Badge tone={indexStatus.indexed_items > 0 ? 'emerald' : (indexStatus.buildable_chunks > 0 ? 'amber' : 'slate')}>
                {statusLoading ? 'loading' : `${indexStatus.indexed_items || 0} items`}
              </Badge>
            </div>
            <div className="text-xs text-slate-500">
              可构建 {indexStatus.buildable_chunks || 0} chunks · 来源 {indexStatus.source_types?.join(', ') || sourceType}
            </div>
            {buildResult && <div className="mt-1 text-[11px] text-emerald-400">已入队 {buildResult.enqueued || 0} 个索引任务</div>}
          </div>
          <button onClick={buildIndex} disabled={buildLoading || statusLoading || !indexStatus.buildable_chunks}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-slate-700 px-3 text-xs font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-50">
            <Database className="h-4 w-4" aria-hidden="true" />
            {buildLoading ? '入队中' : (indexStatus.indexed_items > 0 ? '刷新索引' : '构建索引')}
          </button>
        </div>
      </Card>

      {loading && <Spinner />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
            <MiniStat label="run_id" value={result.run_id} tone="blue" />
            <MiniStat label="degraded" value={response.score_breakdown?.degraded ? 'YES' : 'NO'} tone={response.score_breakdown?.degraded ? 'amber' : 'emerald'} />
            <MiniStat label="总耗时" value={formatMs(response.score_breakdown?.latency_ms)} />
            <MiniStat label="reranker 耗时" value={formatMs(response.score_breakdown?.reranker_latency_ms)} />
            <MiniStat label="merged" value={response.score_breakdown?.merged_candidates ?? '-'} />
            <MiniStat label="reranker 输入" value={response.score_breakdown?.reranker_candidates ?? '-'} />
            <MiniStat label="final" value={response.score_breakdown?.final_items ?? (response.candidates || []).length} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
            <div className="space-y-4">
              <RagCandidateTable candidates={response.candidates || []} />
              <RagRerankerPanel pairs={stages.reranker_input_pairs || []} />
            </div>
            <RagScoreBreakdown value={response.score_breakdown} />
          </div>
          <Card className="p-3">
            <h2 className="mb-2 text-sm font-medium text-slate-200">Trace</h2>
            <JsonBlock value={stages} className="max-h-96" />
          </Card>
        </>
      )}

      <Card className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-left text-slate-500">
              <th className="px-3 py-2">id</th>
              <th className="px-3 py-2">source</th>
              <th className="px-3 py-2">query</th>
              <th className="px-3 py-2">degraded</th>
              <th className="px-3 py-2">latency</th>
              <th className="px-3 py-2">created</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr key={run.id} className="border-b border-slate-800/60">
                <td className="px-3 py-2 text-slate-500">{run.id}</td>
                <td className="px-3 py-2">{run.source_type}</td>
                <td className="max-w-[420px] truncate px-3 py-2 text-slate-300">{run.query}</td>
                <td className="px-3 py-2">{run.degraded ? <Badge tone="amber">yes</Badge> : <Badge tone="emerald">no</Badge>}</td>
                <td className="px-3 py-2 text-slate-400">{run.latency_ms}ms</td>
                <td className="px-3 py-2 text-slate-500">{run.created_at}</td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-10 text-center text-slate-600">暂无运行记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
