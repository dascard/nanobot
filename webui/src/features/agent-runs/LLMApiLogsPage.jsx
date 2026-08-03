import React, { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import {
  ActionButton,
  Card,
  Field,
  MiniStat,
  PageHeader,
  Toolbar,
  ViewportPage,
} from '../../components/ui'
import { LLMApiLogViewer } from '../../components/TraceView'
import { safeJsonParse } from '../../components/traceUtils'

const CACHE_STATUS_META = {
  hit: { label: '命中', className: 'bg-emerald-500/10 text-emerald-300' },
  miss: { label: '未命中', className: 'bg-amber-500/10 text-amber-300' },
  not_reported: { label: '未上报', className: 'bg-slate-500/10 text-slate-400' },
  pending: { label: '处理中', className: 'bg-blue-500/10 text-blue-300' },
  error: { label: '调用失败', className: 'bg-red-500/10 text-red-300' },
}

// ── LLM API 日志独立页面 ──
export function LLMApiLogsPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState(null)
  const [page, setPage] = useState(1)
  const [runFilter, setRunFilter] = useState('')
  const [traceFilter, setTraceFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [cacheFilter, setCacheFilter] = useState('')
  const [openId, setOpenId] = useState(null)
  const [detailsById, setDetailsById] = useState({})
  const [loadingDetailId, setLoadingDetailId] = useState(null)
  const [detailErrors, setDetailErrors] = useState({})
  const limit = 30
  const load = useCallback(() => {
    const params = { page, limit }
    if (runFilter) params.run_id = runFilter
    if (traceFilter) params.trace_id = traceFilter
    if (sourceFilter) params.source = sourceFilter
    if (modelFilter) params.model = modelFilter
    if (statusFilter) params.status = statusFilter
    if (cacheFilter) params.cache_status = cacheFilter
    api.get('/llm-api-logs', { params }).then(r => { setItems(r.data.items || []); setTotal(r.data.total || 0); setStats(r.data.stats || null) }).catch(() => {})
  }, [page, runFilter, traceFilter, sourceFilter, modelFilter, statusFilter, cacheFilter])
  useEffect(() => { load() }, [load])
  const openLog = (logId) => {
    if (openId === logId) {
      setOpenId(null)
      return
    }
    setOpenId(logId)
    if (detailsById[logId]) return
    setLoadingDetailId(logId)
    setDetailErrors(prev => ({ ...prev, [logId]: '' }))
    api.get(`/llm-api-logs/${logId}`).then(r => {
      setDetailsById(prev => ({ ...prev, [logId]: r.data }))
    }).catch(e => {
      setDetailErrors(prev => ({ ...prev, [logId]: e?.response?.data?.detail || e.message || '加载失败' }))
    }).finally(() => {
      setLoadingDetailId(current => current === logId ? null : current)
    })
  }
  const totalPages = Math.max(1, Math.ceil(total / limit))
  const pageStats = stats || items.reduce((acc, item) => {
    const status = item.status || 'created'
    acc.total += 1
    acc[status] = (acc[status] || 0) + 1
    if (status === 'success' || status === 'stream_success') acc.success += 1
    if (status === 'failed' || status === 'error' || status === 'stream_error') acc.failed += 1
    if (status === 'created' || status === 'stream_created') acc.created += 1
    const cacheStatus = item.cache_status || 'pending'
    if (cacheStatus === 'hit') acc.cacheHit += 1
    if (cacheStatus === 'miss') acc.cacheMiss += 1
    if (cacheStatus === 'not_reported') acc.cacheNotReported += 1
    if (!item.run_id) acc.unbound += 1
    const latency = Number(item.latency_ms || 0)
    if (latency > 0) {
      acc.latencyTotal += latency
      acc.latencyCount += 1
    }
    return acc
  }, { total: 0, success: 0, failed: 0, created: 0, cacheHit: 0, cacheMiss: 0, cacheNotReported: 0, unbound: 0, latencyTotal: 0, latencyCount: 0 })
  const avgLatency = stats ? (stats.avg_latency_ms || 0) : (pageStats.latencyCount ? Math.round(pageStats.latencyTotal / pageStats.latencyCount) : 0)
  return (
    <ViewportPage>
      <PageHeader
        title="LLM API 日志"
        description="发往模型网关的完整请求记录；筛选区、日志表和展开详情在当前视口内独立工作。"
        meta={(
          <>
            <span>筛选结果：{pageStats.total}</span>
            <span>每页：{limit}</span>
          </>
        )}
      />
      <Toolbar className="shrink-0">
        <Field id="llm-log-run-filter" label="run_id" className="w-full sm:w-44">
          <input id="llm-log-run-filter" value={runFilter} onChange={e => { setRunFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
        </Field>
        <Field id="llm-log-trace-filter" label="trace_id" className="w-full sm:w-44">
          <input id="llm-log-trace-filter" value={traceFilter} onChange={e => { setTraceFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
        </Field>
        <Field id="llm-log-source-filter" label="source" className="w-full sm:w-32">
          <input id="llm-log-source-filter" value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
        </Field>
        <Field id="llm-log-model-filter" label="model" className="w-full sm:w-44">
          <input id="llm-log-model-filter" value={modelFilter} onChange={e => { setModelFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
        </Field>
        <Field id="llm-log-status-filter" label="status" className="w-full sm:w-40">
          <select id="llm-log-status-filter" value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
            <option value="">全部状态</option>
            <option value="created">created</option>
            <option value="success">success</option>
            <option value="failed">failed</option>
            <option value="error">error</option>
            <option value="stream_created">stream_created</option>
            <option value="stream_success">stream_success</option>
            <option value="stream_error">stream_error</option>
          </select>
        </Field>
        <Field id="llm-log-cache-filter" label="缓存" className="w-full sm:w-32">
          <select id="llm-log-cache-filter" value={cacheFilter} onChange={e => { setCacheFilter(e.target.value); setPage(1) }}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
            <option value="">全部缓存状态</option>
            <option value="hit">命中</option>
            <option value="miss">未命中</option>
            <option value="not_reported">未上报</option>
            <option value="pending">处理中</option>
            <option value="error">调用失败</option>
          </select>
        </Field>
        <ActionButton onClick={load} className="mb-0.5">刷新</ActionButton>
      </Toolbar>
      <div className="mb-4 grid shrink-0 grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-11">
        <MiniStat label={stats ? '筛选总数' : '当前页总数'} value={pageStats.total} />
        <MiniStat label="success" value={pageStats.success} tone="emerald" />
        <MiniStat label="failed/error" value={pageStats.failed_error ?? pageStats.failed} tone={(pageStats.failed_error ?? pageStats.failed) ? 'red' : 'slate'} />
        <MiniStat label="created" value={pageStats.created} tone={pageStats.created ? 'amber' : 'slate'} />
        <MiniStat label="缓存命中" value={pageStats.cache_hit ?? pageStats.cacheHit} tone={(pageStats.cache_hit ?? pageStats.cacheHit) ? 'emerald' : 'slate'} />
        <MiniStat label="缓存未命中" value={pageStats.cache_miss ?? pageStats.cacheMiss} tone={(pageStats.cache_miss ?? pageStats.cacheMiss) ? 'amber' : 'slate'} />
        <MiniStat label="缓存未上报" value={pageStats.cache_not_reported ?? pageStats.cacheNotReported} />
        <MiniStat label="未命中 tokens" value={pageStats.cache_miss_tokens ?? 0} tone={(pageStats.cache_miss_tokens ?? 0) ? 'amber' : 'slate'} />
        <MiniStat label="Token 命中率" value={pageStats.cache_hit_token_ratio == null ? '-' : `${(pageStats.cache_hit_token_ratio * 100).toFixed(1)}%`} tone={(pageStats.cache_hit_token_ratio ?? 0) >= 0.5 ? 'emerald' : 'amber'} />
        <MiniStat label="平均延迟" value={avgLatency ? `${avgLatency}ms` : '-'} />
        <MiniStat label="未绑定 run" value={pageStats.unbound_run_count ?? pageStats.unbound} tone={(pageStats.unbound_run_count ?? pageStats.unbound) ? 'amber' : 'slate'} />
      </div>
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="viewport-scroll min-h-0 flex-1 overflow-auto">
        <table className="min-w-[980px] w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-900"><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3">状态</th><th className="py-2 px-3">缓存</th><th className="py-2 px-3">source</th><th className="py-2 px-3">model</th><th className="py-2 px-3">run</th><th className="py-2 px-3">耗时</th><th className="py-2 px-3">时间</th><th className="py-2 px-3">摘要</th>
          </tr></thead>
          <tbody>
            {items.map(ll => {
              const isIncomplete = (ll.status === 'created') && (ll.latency_ms === 0 || !ll.latency_ms)
              const statusTone = ll.status === 'success' ? 'emerald' : ll.status === 'stream_success' ? 'blue' : ll.status === 'error' || ll.status === 'failed' || ll.status === 'stream_error' ? 'red' : ll.status === 'stream_created' ? 'blue' : 'slate'
              const cacheMeta = CACHE_STATUS_META[ll.cache_status] || CACHE_STATUS_META.pending
              const detail = detailsById[ll.id]
              const request = detail ? safeJsonParse(detail.request_json, {}) : {}
              const messagesCount = request.messages?.length || 0
              const toolsCount = request.tools?.length || 0
              const previewText = String(ll.request_preview || ll.response_preview || ll.error || '').replace(/\s+/g, ' ').slice(0, 90)
              return (
              <React.Fragment key={ll.id}>
                <tr className="border-b border-slate-800/50 cursor-pointer hover:bg-slate-800/30"
                  onClick={() => openLog(ll.id)}>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${statusTone === 'emerald' ? 'bg-emerald-500/10 text-emerald-300' : statusTone === 'blue' ? 'bg-blue-500/10 text-blue-300' : statusTone === 'red' ? 'bg-red-500/10 text-red-300' : 'bg-slate-500/10 text-slate-400'}`}>{ll.status || '-'}</span></td>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs whitespace-nowrap ${cacheMeta.className}`}>{cacheMeta.label}{ll.cache_status === 'hit' ? ` · ${ll.cache_hit_tokens || 0}` : ll.cache_status === 'miss' ? ` · ${ll.cache_miss_tokens || 0}` : ''}</span></td>
                  <td className="py-2 px-3 text-slate-200">{ll.source || '-'}</td>
                  <td className="py-2 px-3 text-slate-400 max-w-40 truncate">{ll.model || '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-32 truncate font-mono">{ll.run_id ? ll.run_id.slice(0, 16) : <span className="text-amber-500">未绑定</span>}</td>
                  <td className="py-2 px-3 text-slate-400">{isIncomplete ? <span className="text-amber-500 text-xs" title="未完成或未回写响应">-</span> : `${ll.latency_ms || 0}ms`}</td>
                  <td className="py-2 px-3 text-xs text-slate-500">{String(ll.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-[340px] truncate">
                    {messagesCount > 0 && `${messagesCount} msgs`}{messagesCount > 0 && toolsCount > 0 && ' · '}{toolsCount > 0 && `${toolsCount} tools`}
                    {!messagesCount && previewText}
                    {ll.summary_only && !detail && <span className="text-slate-600 ml-1">· 点开加载详情</span>}
                    {ll.error && <span className="text-red-400 ml-1">· error</span>}
                  </td>
                </tr>
                {openId === ll.id && (
                <tr className="border-b border-slate-800/50 bg-slate-900/50">
                  <td colSpan={8} className="p-4">
                    {loadingDetailId === ll.id && (
                      <div className="py-8 text-center text-sm text-slate-500">
                        正在加载完整 request_json / response_json...
                      </div>
                    )}
                    {detailErrors[ll.id] && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-300">
                        {detailErrors[ll.id]}
                      </div>
                    )}
                    {detail && <LLMApiLogViewer log={detail} />}
                  </td>
                </tr>
                )}
              </React.Fragment>
            )})}
          </tbody>
        </table>
        </div>
        {!items.length && <div className="py-16 text-center text-sm text-slate-600">暂无 API 请求日志</div>}
        {total > limit && (
          <div className="flex shrink-0 items-center justify-between border-t border-slate-800 p-3 text-xs">
            <span className="text-slate-500">共 {total} 条 | 第 {page}/{totalPages} 页</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">上一页</button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">下一页</button>
            </div>
          </div>
        )}
      </Card>
    </ViewportPage>
  )
}
