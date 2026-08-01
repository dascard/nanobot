import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import {
  ActionButton,
  Card,
  Field,
  PageHeader,
  Toolbar,
  ViewportPage,
} from '../../components/ui'

// ── Tool Calls 独立页面 ──
export function ToolCallsPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [runFilter, setRunFilter] = useState('')
  const [toolFilter, setToolFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const limit = 30
  const load = useCallback(() => {
    const params = { page, limit }
    if (runFilter) params.run_id = runFilter
    if (toolFilter) params.tool_name = toolFilter
    if (statusFilter) params.status = statusFilter
    api.get('/tool-calls', { params }).then(r => { setItems(r.data.items || []); setTotal(r.data.total || 0) }).catch(() => {})
  }, [page, runFilter, toolFilter, statusFilter])
  useEffect(() => { load() }, [load])
  const totalPages = Math.max(1, Math.ceil(total / limit))
  return (
    <ViewportPage>
      <PageHeader
        title="工具调用"
        description="按运行、工具和状态筛选 ToolCall Trace；表格在剩余视口内独立滚动。"
      />
      <Toolbar className="shrink-0">
        <Field id="tool-call-run-filter" label="run_id" className="w-full sm:w-48">
          <input id="tool-call-run-filter" value={runFilter} onChange={e => { setRunFilter(e.target.value); setPage(1) }} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs" />
        </Field>
        <Field id="tool-call-name-filter" label="tool_name" className="w-full sm:w-40">
          <input id="tool-call-name-filter" value={toolFilter} onChange={e => { setToolFilter(e.target.value); setPage(1) }} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs" />
        </Field>
        <Field id="tool-call-status-filter" label="状态" className="w-full sm:w-36">
          <select id="tool-call-status-filter" value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs">
            <option value="">全部状态</option>
            <option value="success">success</option>
            <option value="error">error</option>
          </select>
        </Field>
        <ActionButton onClick={() => { setRunFilter(''); setToolFilter(''); setStatusFilter(''); setPage(1) }}>清除筛选</ActionButton>
      </Toolbar>
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="viewport-scroll min-h-0 flex-1 overflow-auto">
          <table className="min-w-[46rem] w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-900"><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-3">工具</th><th className="py-2 px-3">run_id</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">延迟</th><th className="py-2 px-3">时间</th>
            </tr></thead>
            <tbody>
              {items.map(tc => (
                <tr key={tc.tool_call_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="py-2 px-3 font-mono text-slate-200">{tc.tool_name}</td>
                  <td className="py-2 px-3 text-slate-400 text-xs">{(tc.run_id || '').slice(0, 12)}</td>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${tc.status === 'success' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>{tc.status}</span></td>
                  <td className="py-2 px-3 text-slate-400">{tc.latency_ms ? `${tc.latency_ms}ms` : '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500">{tc.started_at || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && <div className="py-16 text-center text-sm text-slate-600">暂无工具调用</div>}
        </div>
        {total > limit && (
          <div className="flex shrink-0 justify-between border-t border-slate-800 p-3 text-xs">
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
