import { useEffect, useState } from 'react'
import { NavLink, useParams } from 'react-router-dom'

import { api } from '../../api'
import { Card, JsonBlock, MiniStat, Spinner } from '../../components/ui'
import { LLMApiRequestLogsBlock } from '../../components/TraceView'

// ── Agent Run 详情（深链 /agent-runs/:runId） ──
export function AgentRunDetailPage() {
  const { runId } = useParams()
  const [detail, setDetail] = useState(null)
  const [toolDetail, setToolDetail] = useState(null)
  useEffect(() => {
    api.get(`/agent-runs/${encodeURIComponent(runId)}`).then(r => setDetail(r.data)).catch(() => setDetail(null))
  }, [runId])
  if (!detail || !detail.run) return <Card className="p-12 text-center text-slate-500"><Spinner /></Card>
  const r = detail.run
  const replyLogs = detail.reply_contract_check_logs || []
  const prompt_miss_count = replyLogs.filter(log => Number(log.attempt || 0) === 0 && !log.has_reply_tool && !log.has_no_reply_tool && !log.has_structured_fallback).length
  const total_final_action_count = replyLogs.reduce((sum, log) => sum + Number(log.total_final_action_count || 0), 0)
  return (
    <div>
      <NavLink to="/agent-runs" className="text-xs text-slate-500 hover:text-slate-300 mb-4 inline-block">← 运行追踪</NavLink>
      <h1 className="text-xl font-semibold mb-4">运行详情</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <MiniStat label="run_id" value={r.run_id || ''} />
        <MiniStat label="status" value={r.status || ''} tone={r.status === 'success' ? 'emerald' : r.status === 'error' ? 'red' : 'slate'} />
        <MiniStat label="延迟" value={r.latency_ms ? `${r.latency_ms}ms` : '-'} />
        <MiniStat label="trace_id" value={(r.trace_id || '').slice(0, 16)} />
        <MiniStat label="prompt_key" value={r.prompt_key || '-'} />
        <MiniStat label="prompt_mode" value={r.prompt_mode || '-'} />
        <MiniStat label="prompt_source" value={r.prompt_source || '-'} />
        <MiniStat label="prompt_sha" value={(r.prompt_sha256 || '').slice(0, 16) || '-'} />
        <MiniStat label="model" value={r.model || '-'} />
        <MiniStat label="session_id" value={(r.session_id || '').slice(0, 16) || '-'} />
        <MiniStat label="user_id" value={r.user_id || '-'} />
      </div>
      {(r.prompt_runtime_path || r.prompt_default_path) && (
        <Card className="p-4 mb-4">
          <h3 className="text-sm font-medium text-slate-400 mb-2">Prompt 来源</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div><div className="text-xs text-slate-500 mb-1">运行时路径</div><JsonBlock value={r.prompt_runtime_path || '-'} className="max-h-24" /></div>
            <div><div className="text-xs text-slate-500 mb-1">默认路径</div><JsonBlock value={r.prompt_default_path || '-'} className="max-h-24" /></div>
          </div>
        </Card>
      )}
      {r.error && <div className="p-3 bg-red-500/10 text-red-400 rounded-lg mb-4 text-sm">{r.error}</div>}
      {r.input_preview && <Card className="p-4 mb-4"><h3 className="text-sm font-medium text-slate-400 mb-2">输入摘要</h3><pre className="text-xs text-slate-300 whitespace-pre-wrap">{r.input_preview}</pre></Card>}
      {r.output_preview && <Card className="p-4 mb-4"><h3 className="text-sm font-medium text-slate-400 mb-2">输出摘要</h3><pre className="text-xs text-slate-300 whitespace-pre-wrap">{r.output_preview}</pre></Card>}

      {/* Tool Calls */}
      <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">工具调用 ({detail.tool_calls?.length || 0})</h2>
      {detail.tool_calls?.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-3">工具</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">延迟</th><th className="py-2 px-3">时间</th>
            </tr></thead>
            <tbody>
              {detail.tool_calls.map(tc => (
                <tr key={tc.tool_call_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer" onClick={() => setToolDetail(toolDetail?.tool_call_id === tc.tool_call_id ? null : tc)}>
                  <td className="py-2 px-3 text-slate-200">{tc.tool_name}</td>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${tc.status === 'success' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>{tc.status}</span></td>
                  <td className="py-2 px-3 text-slate-400">{tc.latency_ms ? `${tc.latency_ms}ms` : '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500">{tc.started_at || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {toolDetail && (
            <div className="p-3 border-t border-slate-800">
              <h3 className="text-sm font-medium text-slate-400 mb-2">{toolDetail.tool_name} 详情</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div><div className="text-xs text-slate-500 mb-1">参数</div><JsonBlock value={toolDetail.args_json} className="max-h-72" /></div>
                <div><div className="text-xs text-slate-500 mb-1">结果/错误</div><JsonBlock value={toolDetail.result_preview || toolDetail.error} className="max-h-72" /></div>
              </div>
            </div>
          )}
        </Card>
      )}
      {detail.tool_calls?.length === 0 && <p className="text-xs text-slate-500">无工具调用</p>}

      {/* Prompt Render Logs */}
      {detail.prompt_render_logs?.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">Prompt 渲染日志</h2>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
            <table className="min-w-[640px] w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="py-2 px-3">prompt_key</th><th className="py-2 px-3">mode</th><th className="py-2 px-3">source</th><th className="py-2 px-3">sha</th><th className="py-2 px-3">tokens</th><th className="py-2 px-3">警告</th>
              </tr></thead>
              <tbody>
                {detail.prompt_render_logs.map(pr => (
                  <tr key={pr.id} className="border-b border-slate-800/50">
                    <td className="py-2 px-3 text-slate-200">{pr.prompt_key}</td>
                    <td className="py-2 px-3 text-slate-400">{pr.mode || '-'}</td>
                    <td className="py-2 px-3 text-slate-400 max-w-56 truncate">{pr.prompt_source || '-'}</td>
                    <td className="py-2 px-3 text-slate-500 font-mono text-xs">{(pr.prompt_sha256 || '').slice(0, 12) || '-'}</td>
                    <td className="py-2 px-3 text-slate-400">{pr.token_estimate || '-'}</td>
                    <td className="py-2 px-3 text-xs text-amber-400">{pr.warnings_json || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>
        </>
      )}

      {/* Reply Contract Checks */}
      {detail.reply_contract_check_logs?.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">Reply 调用检查</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
            <MiniStat label="首轮失败" value={prompt_miss_count} tone={prompt_miss_count ? 'red' : 'emerald'} />
            <MiniStat label="最终动作总数" value={total_final_action_count} tone={total_final_action_count === 1 ? 'emerald' : 'amber'} />
            <MiniStat label="reply 次数" value={replyLogs.reduce((sum, log) => sum + Number(log.reply_tool_call_count || 0), 0)} />
            <MiniStat label="no_reply 次数" value={replyLogs.reduce((sum, log) => sum + Number(log.no_reply_tool_call_count || 0), 0)} />
          </div>
          <Card>
            <div className="divide-y divide-slate-800">
              {detail.reply_contract_check_logs.map(log => (
                <details key={log.id} className="group">
                  <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-sm flex gap-3">
                    <span className="text-slate-400 w-16">#{log.attempt}</span>
                    <span className="text-slate-200 w-32">{log.result || '-'}</span>
                    <span className="text-slate-500 w-20">reply:{log.has_reply_tool || 0}</span>
                    <span className="text-slate-500 w-24">no_reply:{log.has_no_reply_tool || 0}</span>
                    <span className="text-slate-500 w-24">count:{log.total_final_action_count || 0}</span>
                    <span className="text-slate-500 flex-1 truncate">{log.created_at || '-'}</span>
                  </summary>
                  <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 m-2 rounded text-slate-300 max-h-72 overflow-auto">{log.raw_output_preview || ''}</pre>
                </details>
              ))}
            </div>
          </Card>
        </>
      )}

      {/* LLM API Requests */}
      <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">API 请求</h2>
      <LLMApiRequestLogsBlock logs={detail.llm_api_request_logs || []} />
    </div>
  )
}
