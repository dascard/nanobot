import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import { Badge, Card, JsonBlock, Pagination } from '../../components/ui'
import { LLMApiRequestLogsBlock } from '../../components/TraceView'

// ── Agent Runs ──
export function AgentRunsPage() {
  const [runs, setRuns] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState(null)
  const [toolDetail, setToolDetail] = useState(null)
  const [status, setStatus] = useState('')
  const [sessionFilter, setSessionFilter] = useState('')
  const [loadError, setLoadError] = useState('')
  const limit = 30

  const loadRuns = useCallback(() => {
    api.get('/agent-runs', { params: { page, limit, status, session_id: sessionFilter } }).then(r => {
      setRuns(r.data.items || [])
      setTotal(r.data.total || 0)
      setLoadError('')
      // functional update 消除对 selected 的依赖,否则每次点击列表行都会
      // 因 loadRuns 身份变化触发整页列表重拉。
      const firstRunId = r.data.items?.[0]?.run_id || ''
      if (firstRunId) setSelected(prev => prev || firstRunId)
    }).catch(e => {
      setRuns([])
      setLoadError(e?.response?.data?.detail || e?.message || '加载运行列表失败')
    })
  }, [page, status, sessionFilter])
  useEffect(() => { loadRuns() }, [loadRuns])
  useEffect(() => {
    if (!selected) return
    api.get(`/agent-runs/${encodeURIComponent(selected)}`).then(r => { setDetail(r.data); setToolDetail(null) }).catch(() => setDetail(null))
  }, [selected])

  const openTool = (id) => {
    api.get(`/tool-calls/${encodeURIComponent(id)}`).then(r => setToolDetail(r.data)).catch(() => setToolDetail(null))
  }
  const tone = (s) => s === 'success' ? 'emerald' : s === 'error' ? 'red' : s === 'running' ? 'blue' : s === 'no_reply' ? 'slate' : 'amber'

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">运行追踪</h1>
          <p className="text-slate-500 text-sm">Agent run、Prompt render 与 Tool call 的只读审计视图</p>
        </div>
        <button onClick={loadRuns} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">刷新</button>
      </div>
      <div className="flex gap-2 mb-4">
        <select value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}
          className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm">
          <option value="">全部状态</option>
          <option value="success">success</option>
          <option value="error">error</option>
          <option value="no_reply">no_reply</option>
          <option value="suppressed">suppressed</option>
          <option value="empty">empty</option>
        </select>
        <input value={sessionFilter} onChange={e => { setSessionFilter(e.target.value); setPage(1) }}
          placeholder="session_id" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500" />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <Card className="xl:col-span-5 overflow-hidden">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3">开始</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">会话</th><th className="py-2 px-3">模型</th></tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.run_id} onClick={() => setSelected(r.run_id)}
                  className={`border-b border-slate-800/50 cursor-pointer ${selected === r.run_id ? 'bg-emerald-500/10' : 'hover:bg-slate-800/40'}`}>
                  <td className="py-2 px-3 text-xs text-slate-400">{String(r.started_at || '').replace('T', ' ').slice(0, 19)}</td>
                  <td className="py-2 px-3"><Badge tone={tone(r.status)}>{r.status}</Badge></td>
                  <td className="py-2 px-3 text-xs text-slate-400 max-w-32 truncate">{r.session_id || '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-40 truncate">{r.model || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!runs.length && (
            loadError
              ? <div className="py-16 text-center text-sm text-red-400">{loadError}</div>
              : <div className="py-16 text-center text-sm text-slate-600">暂无运行记录</div>
          )}
          <div className="p-3"><Pagination page={page} total={total} limit={limit} onChange={setPage} /></div>
        </Card>
        <div className="xl:col-span-7 space-y-4 min-w-0">
          {detail ? (<>
              <Card className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h2 className="text-sm font-medium text-emerald-400 font-mono">{(detail.run || detail).run_id}</h2>
                    <div className="text-xs text-slate-600 font-mono">{(detail.run || detail).trace_id}</div>
                  </div>
                  <Badge tone={tone((detail.run || detail).status)}>{(detail.run || detail).status}</Badge>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-3">
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt</div><div className="text-white font-medium truncate">{(detail.run || detail).prompt_key || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Mode</div><div className="text-blue-300 font-medium truncate">{(detail.run || detail).prompt_mode || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt Source</div><div className="text-emerald-300 font-medium truncate">{(detail.run || detail).prompt_source || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt SHA</div><div className="text-slate-300 font-mono text-xs truncate">{((detail.run || detail).prompt_sha256 || '-').slice(0, 16)}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Latency</div><div className="text-white font-medium">{(detail.run || detail).latency_ms || 0}ms</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Tools</div><div className="text-emerald-300 font-medium">{(detail.tool_calls || []).length}</div></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
                  <div><div className="text-xs text-slate-500 mb-1">运行时路径</div><JsonBlock value={(detail.run || detail).prompt_runtime_path || '-'} className="max-h-20" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">默认路径</div><JsonBlock value={(detail.run || detail).prompt_default_path || '-'} className="max-h-20" /></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div><div className="text-xs text-slate-500 mb-1">输入预览</div><JsonBlock value={(detail.run || detail).input_preview} className="max-h-40" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">输出预览</div><JsonBlock value={(detail.run || detail).output_preview || (detail.run || detail).error} className="max-h-40" /></div>
                </div>
              </Card>
              <Card className="p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">工具调用</h2>
                <div className="space-y-2">
                  {(detail.tool_calls || []).map(t => (
                    <button key={t.tool_call_id} onClick={() => openTool(t.tool_call_id)}
                      className="w-full text-left p-3 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-200">{t.tool_name}</span>
                        <Badge tone={tone(t.status)}>{t.status}</Badge>
                      </div>
                      <div className="text-xs text-slate-600 mt-1">{t.latency_ms || 0}ms · {t.tool_call_id}</div>
                    </button>
                  ))}
                  {!(detail.tool_calls || []).length && <div className="py-8 text-center text-sm text-slate-600">本次没有工具调用</div>}
                </div>
              </Card>
              <Card className="p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">Prompt 渲染记录</h2>
                {(detail.prompt_render_logs || []).map(log => (
                  <div key={log.id} className="mb-3">
                    <div className="flex gap-2 mb-2 flex-wrap"><Badge tone="blue">{log.prompt_key}</Badge><Badge>{log.mode}</Badge><Badge>{log.prompt_source || '-'}</Badge><Badge>{log.token_estimate} tokens</Badge><Badge>{(log.prompt_sha256 || '').slice(0, 12) || '-'}</Badge></div>
                    <JsonBlock value={log.rendered_preview || log.error} className="max-h-44" />
                  </div>
                ))}
                {!(detail.prompt_render_logs || []).length && <div className="py-8 text-center text-sm text-slate-600">暂无 prompt 渲染记录</div>}
              </Card>
              <h2 className="text-sm font-medium text-slate-300 mb-3 mt-4">API 请求</h2>
              <LLMApiRequestLogsBlock logs={detail.llm_api_request_logs || []} />
              {toolDetail && (
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-medium text-slate-300">工具详情: {toolDetail.tool_name}</h2>
                    <button onClick={() => setToolDetail(null)} className="text-xs text-slate-500 hover:text-white">关闭</button>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div><div className="text-xs text-slate-500 mb-1">参数</div><JsonBlock value={toolDetail.args_json} className="max-h-72" /></div>
                    <div><div className="text-xs text-slate-500 mb-1">结果/错误</div><JsonBlock value={toolDetail.result_preview || toolDetail.error} className="max-h-72" /></div>
                  </div>
                </Card>
              )}
            </>
          ) : (
            <Card className="p-12 text-center text-slate-600 text-sm">选择一条运行记录查看详情</Card>
          )}
        </div>
      </div>
    </div>
  )
}
