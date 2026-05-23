import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import { Card } from '../../components/ui'

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
    <div>
      <h1 className="text-2xl font-bold mb-4">工具调用</h1>
      <div className="flex gap-2 mb-4">
        <input value={runFilter} onChange={e => { setRunFilter(e.target.value); setPage(1) }} placeholder="run_id..." className="w-40 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <input value={toolFilter} onChange={e => { setToolFilter(e.target.value); setPage(1) }} placeholder="tool_name..." className="w-32 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }} className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
          <option value="">全部状态</option>
          <option value="success">success</option>
          <option value="error">error</option>
        </select>
        <button onClick={() => { setRunFilter(''); setToolFilter(''); setStatusFilter(''); setPage(1) }} className="px-2 py-1 bg-slate-700 rounded text-xs">清除</button>
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
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
        {total > limit && (
          <div className="flex justify-between p-3 text-xs border-t border-slate-800">
            <span className="text-slate-500">共 {total} 条 | 第 {page}/{totalPages} 页</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">上一页</button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">下一页</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
