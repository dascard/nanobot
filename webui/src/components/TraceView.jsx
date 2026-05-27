import { useState } from 'react'

import { Badge, Card, InfoGrid, MiniStat } from './ui'
import { safeJsonParse } from './traceUtils'

function formatBytes(n) {
  if (!n || n < 1024) return `${n || 0}B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / 1048576).toFixed(1)}MB`
}

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return '-'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)}s`
  return `${Math.round(n)}ms`
}

function compactJson(value) {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try { return JSON.stringify(value) } catch { return String(value) }
}

const REASONING_KEYS = ['reasoning_content', 'reasoning', 'reasoning_text', 'thinking', 'thinking_content']

function collectMessageText(message = {}, keys = REASONING_KEYS) {
  return keys.map(key => compactJson(message?.[key])).filter(Boolean).join('')
}

function collectChoiceMessageText(response = {}, keys = REASONING_KEYS) {
  const parts = []
  for (const choice of response.choices || []) {
    const msg = choice?.message || {}
    const delta = choice?.delta || {}
    const text = collectMessageText(msg, keys) || collectMessageText(delta, keys)
    if (text) parts.push(text)
  }
  return parts.join('')
}

function extractUsage(response = {}) {
  if (response.usage) return response.usage
  for (let i = (response.chunks_sample || []).length - 1; i >= 0; i -= 1) {
    const usage = response.chunks_sample[i]?.usage
    if (usage) return usage
  }
  return null
}

function extractReasoningTrace(response = {}) {
  const streamMetrics = response.stream_metrics || {}
  const usage = extractUsage(response)
  const usageDetails = usage?.completion_tokens_details || usage?.output_tokens_details || {}
  const reasoningTokens = usageDetails.reasoning_tokens ?? usage?.reasoning_tokens
  const reasoningText = compactJson(response.reasoning_content) || collectChoiceMessageText(response)
  const hasMetrics = Object.keys(streamMetrics).length > 0
  return {
    has: Boolean(reasoningText || hasMetrics || reasoningTokens !== undefined),
    reasoningText,
    reasoningTokens,
    streamMetrics,
  }
}

function summarizeDataUrl(url = '') {
  if (!url || !url.startsWith('data:')) return null
  const match = url.match(/^data:([^;,]+)?(;base64)?,/)
  const mime = match?.[1] || 'unknown'
  const isBase64 = Boolean(match?.[2])
  const payload = url.slice(url.indexOf(',') + 1)
  const sizeBytes = isBase64 ? Math.floor(payload.length * 0.75) : payload.length
  return { mime, isBase64, sizeBytes, sizeText: formatBytes(sizeBytes) }
}

export function CopyButton({ text, label = '复制', className = '' }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={e => { e.stopPropagation(); navigator.clipboard.writeText(text || '').then(() => { setOk(true); setTimeout(() => setOk(false), 1000) }) }}
      className={`px-2 py-0.5 rounded text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 ${ok ? 'text-emerald-400' : ''} ${className}`}>
      {ok ? '已复制' : label}
    </button>
  )
}

export function MessageAccordion({ message, index, source }) {
  const content = message.content
  const isArray = Array.isArray(content)
  const charCount = isArray ? JSON.stringify(content).length : (typeof content === 'string' ? content.length : 0)
  const tokenEst = Math.round(charCount * (isArray ? 0.4 : 0.35))
  const hasToolCalls = message.tool_calls?.length > 0
  const sourceName = source?.source || ''

  return (
    <details className="border border-slate-700/50 rounded-lg group">
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs flex items-center gap-2">
        <span className="text-slate-400 font-mono w-6">[{index}]</span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${message.role === 'system' ? 'bg-purple-500/15 text-purple-300' : message.role === 'user' ? 'bg-blue-500/15 text-blue-300' : message.role === 'assistant' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-500/15 text-slate-400'}`}>{message.role}</span>
        {sourceName && <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] font-mono text-slate-400">{sourceName}</span>}
        <span className="text-slate-500">· {charCount} chars · ~{tokenEst} tokens</span>
        {hasToolCalls && <span className="text-amber-400 text-[10px]">· tool_calls: {message.tool_calls.map(t => t.function?.name || '?').join(', ')}</span>}
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <ContentBlockViewer content={content} />
        {hasToolCalls && (
          <div className="mt-3 space-y-2">
            {message.tool_calls.map((tc, j) => {
              let argsText
              try { argsText = JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2) } catch { argsText = tc.function?.arguments || '{}' }
              return (
                <details key={j} className="border border-amber-500/20 rounded">
                  <summary className="py-1.5 px-3 cursor-pointer hover:bg-amber-500/10 text-xs text-amber-400">tool_call[{j}] {tc.function?.name || '?'} · id: {(tc.id || '').slice(0, 24)}</summary>
                  <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto m-2">{argsText}</pre>
                </details>
              )
            })}
          </div>
        )}
      </div>
    </details>
  )
}

function ContentBlockViewer({ content }) {
  if (typeof content === 'string') {
    return (
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">text</span>
          <CopyButton text={content} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{content}</pre>
      </div>
    )
  }
  if (!Array.isArray(content)) return <pre className="text-xs text-slate-400">{JSON.stringify(content, null, 2)}</pre>

  return (
    <div className="space-y-2">
      {content.map((block, i) => {
        if (block.type === 'text') {
          return (
            <div key={i}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-slate-600">text block[{i}] · {String(block.text || '').length} chars</span>
                <CopyButton text={block.text || ''} />
              </div>
              <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{block.text || ''}</pre>
            </div>
          )
        }
        if (block.type === 'image_url') {
          const url = block.image_url?.url || ''
          const info = summarizeDataUrl(url)
          return (
            <details key={i} className="border border-slate-700/50 rounded">
              <summary className="py-1.5 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">
                image_url block[{i}] · {info ? `${info.mime} · ${info.sizeText}` : 'external URL'}
                {info?.isBase64 && <span className="text-amber-400 ml-1">(base64)</span>}
              </summary>
              <div className="p-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-600">完整 image_url</span>
                  <CopyButton text={url} label="复制 URL" />
                </div>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-96 overflow-auto">{url}</pre>
              </div>
            </details>
          )
        }
        return <pre key={i} className="text-xs text-slate-400">{JSON.stringify(block, null, 2)}</pre>
      })}
    </div>
  )
}

function ToolAccordion({ tool, index }) {
  const schema = tool.function || tool
  return (
    <details className="border border-slate-700/50 rounded-lg">
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-300">
        [{index}] {schema.name || tool.type || 'tool'} · {tool.type || 'function'}
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">function schema</span>
          <CopyButton text={JSON.stringify(schema, null, 2)} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{JSON.stringify(schema, null, 2)}</pre>
      </div>
    </details>
  )
}

export function RawJsonAccordion({ label, text, defaultOpen = false }) {
  if (!text || text === '{}' || text === '[]') return null
  return (
    <details className="border border-slate-700/50 rounded-lg" open={defaultOpen}>
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-500 flex items-center gap-2">
        {label} <span className="text-slate-600">({text.length} chars)</span>
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">{label}</span>
          <CopyButton text={text} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{text}</pre>
      </div>
    </details>
  )
}

export function LLMApiLogViewer({ log }) {
  if (!log) return <div className="py-8 text-center text-sm text-slate-600">无数据</div>
  const request = safeJsonParse(log.request_json, {})
  const response = safeJsonParse(log.response_json, {})
  const requestLint = safeJsonParse(log.request_lint_json, {})
  const lintIssues = Array.isArray(requestLint.issues) ? requestLint.issues : []
  const lintCounts = requestLint.severity_counts || {}
  const messageSources = safeJsonParse(log.message_sources_json, [])
  const actualSentTools = safeJsonParse(log.actual_sent_tools_json, requestLint.actual_sent_tools || [])
  const runtimeEnabledTools = safeJsonParse(log.runtime_enabled_tools_json, requestLint.runtime_enabled_tools || [])
  const runtimeDisabledTools = safeJsonParse(log.runtime_disabled_tools_json, requestLint.runtime_disabled_tools || [])
  const frameworkInjectedTools = safeJsonParse(log.framework_injected_tools_json, requestLint.framework_injected_tools || [])
  const messageSourceByIndex = new Map(messageSources.map(src => [src.index, src]))
  const isIncomplete = (log.status === 'created') && (log.latency_ms === 0 || !log.latency_ms)
  const statusTone = log.status === 'success' ? 'emerald' : log.status === 'stream_success' ? 'blue' : log.status === 'error' || log.status === 'failed' || log.status === 'stream_error' ? 'red' : log.status === 'stream_created' ? 'blue' : 'slate'
  const issueTone = (severity) => severity === 'P0' ? 'red' : severity === 'P1' ? 'amber' : 'slate'
  const reasoningTrace = extractReasoningTrace(response)

  return (
    <div className="space-y-4 text-sm">
      {isIncomplete && (
        <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
          该请求只有创建记录，没有响应回写。可能是出口未调用 finish_request，或进程中断。
        </div>
      )}

      <section>
        <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">基础信息</h3>
        <InfoGrid
          columns="md:grid-cols-4 xl:grid-cols-6"
          items={[
            { label: 'id', value: log.id || '-' },
            { label: 'source', value: log.source || '-', className: 'text-emerald-300' },
            { label: 'provider', value: log.provider || '-' },
            { label: 'model', value: log.model || '-' },
            { label: 'status', value: log.status || '-', className: statusTone === 'emerald' ? 'text-emerald-300' : statusTone === 'blue' ? 'text-blue-300' : statusTone === 'red' ? 'text-red-300' : 'text-slate-300' },
            { label: 'response_status', value: log.response_status || 0 },
            { label: 'latency', value: log.latency_ms ? `${log.latency_ms}ms` : '-' },
            { label: 'run_id', value: log.run_id ? log.run_id.slice(0, 16) : '未绑定 run', className: log.run_id ? 'text-slate-300' : 'text-amber-300' },
            { label: 'trace_id', value: log.trace_id ? log.trace_id.slice(0, 16) : '-' },
            { label: 'created_at', value: (log.created_at || '').replace('T', ' ').slice(0, 19) },
            { label: 'finished_at', value: log.finished_at ? log.finished_at.replace('T', ' ').slice(0, 19) : '-' },
            { label: 'URL', value: (log.url || '-').slice(0, 40) },
          ]}
        />
        {!log.run_id && <div className="mt-1 text-[10px] text-slate-600">可能是 classifier / background / direct HTTP 调用</div>}
        {log.error && <div className="mt-2 p-2 bg-red-500/10 border border-red-500/20 rounded text-xs text-red-300">{log.error}</div>}
      </section>

      {Object.keys(request).length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">请求参数</h3>
          <div className="flex flex-wrap gap-2">
            {request.model && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">model: <span className="text-slate-400">{request.model}</span></span>}
            {request.temperature !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">temperature: <span className="text-slate-400">{request.temperature}</span></span>}
            {request.top_p !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">top_p: <span className="text-slate-400">{request.top_p}</span></span>}
            {request.max_tokens !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">max_tokens: <span className="text-slate-400">{request.max_tokens}</span></span>}
            {request.stream !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">stream: <span className="text-slate-400">{String(request.stream)}</span></span>}
            {request.enable_thinking !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">enable_thinking: <span className="text-slate-400">{String(request.enable_thinking)}</span></span>}
            {request.thinking !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">thinking: <span className="text-slate-400">{compactJson(request.thinking)}</span></span>}
            {request.reasoning !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">reasoning: <span className="text-slate-400">{compactJson(request.reasoning)}</span></span>}
            <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">messages: <span className="text-slate-400">{request.messages?.length || 0}</span></span>
            <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">tools: <span className="text-slate-400">{request.tools?.length || 0}</span></span>
            {request.tool_choice && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">tool_choice: <span className="text-slate-400">{typeof request.tool_choice === 'string' ? request.tool_choice : JSON.stringify(request.tool_choice)}</span></span>}
          </div>
        </section>
      )}

      {(lintIssues.length > 0 || actualSentTools.length > 0 || messageSources.length > 0) && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Request Lint</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-2">
            <MiniStat label="P0" value={lintCounts.P0 || 0} tone={(lintCounts.P0 || 0) > 0 ? 'red' : 'emerald'} />
            <MiniStat label="P1" value={lintCounts.P1 || 0} tone={(lintCounts.P1 || 0) > 0 ? 'amber' : 'slate'} />
            <MiniStat label="P2" value={lintCounts.P2 || 0} />
            <MiniStat label="actual_tools" value={actualSentTools.length} />
            <MiniStat label="message_sources" value={messageSources.length} />
          </div>
          {lintIssues.length > 0 && (
            <div className="space-y-1 mb-2">
              {lintIssues.slice(0, 20).map((issue, i) => (
                <div key={i} className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950 p-2 text-xs">
                  <Badge tone={issueTone(issue.severity)}>{issue.severity || '-'}</Badge>
                  <div className="min-w-0">
                    <div className="text-slate-300 font-mono">{issue.code || '-'}</div>
                    <div className="text-slate-500 break-words">{issue.message || ''}</div>
                    {issue.details && <pre className="text-[10px] text-slate-600 whitespace-pre-wrap break-all mt-1">{JSON.stringify(issue.details, null, 2)}</pre>}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Actual Sent Tools</div>
              <div className="flex flex-wrap gap-1">{actualSentTools.length ? actualSentTools.map(name => <Badge key={name} tone="blue">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Runtime Enabled</div>
              <div className="flex flex-wrap gap-1">{runtimeEnabledTools.length ? runtimeEnabledTools.map(name => <Badge key={name} tone="emerald">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Runtime Disabled</div>
              <div className="flex flex-wrap gap-1">{runtimeDisabledTools.length ? runtimeDisabledTools.map(name => <Badge key={name} tone="amber">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Framework Docs</div>
              <div className="flex flex-wrap gap-1">{frameworkInjectedTools.length ? frameworkInjectedTools.map(name => <Badge key={name} tone="red">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
          </div>
          {messageSources.length > 0 && (
            <details className="border border-slate-700/50 rounded-lg mt-2">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">Message Sources ({messageSources.length})</summary>
              <div className="p-2 space-y-1 max-h-[360px] overflow-auto">
                {messageSources.map(src => (
                  <div key={src.index} className="grid grid-cols-[40px_70px_180px_1fr] gap-2 rounded bg-slate-950 px-2 py-1 text-xs">
                    <span className="text-slate-600">#{src.index}</span>
                    <span className="text-slate-500">{src.role || '-'}</span>
                    <span className="text-slate-300 font-mono truncate">{src.source || '-'}</span>
                    <span className="text-slate-600 truncate">{src.chars || 0} chars · {(src.sha256 || '').slice(0, 12)} · {src.preview || ''}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </section>
      )}

      {request.messages?.length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Messages ({request.messages.length})</h3>
          <div className="space-y-1">
            {request.messages.map((msg, i) => (
              <MessageAccordion key={i} message={msg} index={i} source={messageSourceByIndex.get(i)} />
            ))}
          </div>
        </section>
      )}

      {request.tools?.length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Tools ({request.tools.length})</h3>
          <div className="space-y-1">
            {request.tools.map((tool, i) => (
              <ToolAccordion key={i} tool={tool} index={i} />
            ))}
          </div>
        </section>
      )}

      {reasoningTrace.has && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">推理与流式指标</h3>
          <InfoGrid
            columns="md:grid-cols-4 xl:grid-cols-6"
            items={[
              { label: '请求总耗时', value: log.latency_ms ? formatMs(log.latency_ms) : '-' },
              { label: '首 chunk', value: formatMs(reasoningTrace.streamMetrics.first_chunk_ms) },
              { label: '首推理', value: formatMs(reasoningTrace.streamMetrics.first_reasoning_ms) },
              { label: '首正文', value: formatMs(reasoningTrace.streamMetrics.first_content_ms) },
              { label: '推理耗时', value: formatMs(reasoningTrace.streamMetrics.reasoning_elapsed_ms) },
              { label: '推理 tokens', value: reasoningTrace.reasoningTokens ?? '-' },
              { label: '推理字符', value: reasoningTrace.streamMetrics.reasoning_char_count ?? (reasoningTrace.reasoningText ? reasoningTrace.reasoningText.length : '-') },
              { label: '正文字符', value: reasoningTrace.streamMetrics.content_char_count ?? '-' },
              { label: 'chunk 数', value: reasoningTrace.streamMetrics.chunk_count ?? '-' },
            ]}
          />
          {reasoningTrace.reasoningText && (
            <details className="border border-slate-700/50 rounded-lg mt-2">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">
                reasoning_content ({reasoningTrace.reasoningText.length} chars)
              </summary>
              <div className="p-3 border-t border-slate-700/50">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-600">模型推理内容</span>
                  <CopyButton text={reasoningTrace.reasoningText} />
                </div>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{reasoningTrace.reasoningText}</pre>
              </div>
            </details>
          )}
        </section>
      )}

      {Object.keys(response).length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Response</h3>
          <div className="space-y-2">
            {response.choices?.map((choice, i) => {
              const msg = choice.message || {}
              const msgReasoning = collectMessageText(msg)
              return (
                <div key={i} className="border border-slate-700/50 rounded-lg p-3">
                  <div className="flex gap-3 mb-2 text-xs">
                    <span className="text-slate-500">finish_reason: <span className="text-slate-300">{choice.finish_reason || '-'}</span></span>
                  </div>
                  {msgReasoning && (
                    <div className="mb-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-slate-600">message.reasoning_content · {msgReasoning.length} chars</span>
                        <CopyButton text={msgReasoning} />
                      </div>
                      <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{msgReasoning}</pre>
                    </div>
                  )}
                  {msg.content && (
                    <div className="mb-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-slate-600">message.content · {String(msg.content).length} chars</span>
                        <CopyButton text={msg.content} />
                      </div>
                      <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{msg.content}</pre>
                    </div>
                  )}
                  {msg.tool_calls?.map((tc, j) => {
                    let argsText
                    try { argsText = JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2) } catch { argsText = tc.function?.arguments || '{}' }
                    return (
                      <details key={j} className="border border-amber-500/20 rounded mb-1">
                        <summary className="py-1.5 px-3 cursor-pointer hover:bg-amber-500/10 text-xs text-amber-400">tool_call[{j}] {tc.function?.name || '?'}</summary>
                        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto m-2">{argsText}</pre>
                      </details>
                    )
                  })}
                </div>
              )
            })}
            {response.content && (
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-600">content · {String(response.content).length} chars</span>
                  <CopyButton text={response.content} />
                </div>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{response.content}</pre>
              </div>
            )}
            {response.usage && (
              <details className="border border-slate-700/50 rounded-lg">
                <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">usage</summary>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 m-2">{JSON.stringify(response.usage, null, 2)}</pre>
              </details>
            )}
          </div>
        </section>
      )}

      <section>
        <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Raw JSON</h3>
        <div className="space-y-1">
          <RawJsonAccordion label="原始 request_json" text={log.request_json} />
          <RawJsonAccordion label="原始 response_json" text={log.response_json} />
          <RawJsonAccordion label="headers_json" text={log.headers_json} />
          <RawJsonAccordion label="request_lint_json" text={log.request_lint_json} />
          <RawJsonAccordion label="message_sources_json" text={log.message_sources_json} />
        </div>
      </section>
    </div>
  )
}

export function LLMApiRequestLogsBlock({ logs = [] }) {
  if (!logs.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-slate-500 text-sm mb-2">暂无 API 请求日志</p>
        <p className="text-slate-600 text-xs">可能原因：本次调用未绑定 run_id 或该模型出口未接入追踪</p>
      </Card>
    )
  }
  return (
    <Card>
      <div className="space-y-1">
        {logs.map(ll => {
          const isIncomplete = (ll.status === 'created') && (ll.latency_ms === 0 || !ll.latency_ms)
          const statusTone = ll.status === 'success' ? 'emerald' : ll.status === 'stream_success' ? 'blue' : ll.status === 'error' || ll.status === 'failed' || ll.status === 'stream_error' ? 'red' : ll.status === 'stream_created' ? 'blue' : 'slate'
          const requestLint = safeJsonParse(ll.request_lint_json, {})
          const p0Count = requestLint.severity_counts?.P0 || 0
          return (
            <details key={ll.id} className="border-b border-slate-800/50">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-sm flex gap-3 items-center">
                <Badge tone={statusTone}>{ll.status || '-'}</Badge>
                {p0Count > 0 && <Badge tone="red">P0 {p0Count}</Badge>}
                <span className="text-slate-200 w-16">{ll.source || '-'}</span>
                <span className="text-slate-400 w-32 truncate">{ll.model || '-'}</span>
                {isIncomplete ? (
                  <span className="text-amber-500 text-xs">未完成或未回写响应</span>
                ) : (
                  <>
                    <span className="text-slate-400 w-16">{ll.response_status || 0}</span>
                    <span className="text-slate-500 w-20">{ll.latency_ms || 0}ms</span>
                  </>
                )}
                <span className="text-slate-500 text-xs truncate flex-1">{ll.run_id ? ll.run_id.slice(0, 16) : <span className="text-amber-500">未绑定 run</span>}</span>
                <span className="text-xs text-slate-500">{ll.created_at || '-'}</span>
              </summary>
              <div className="p-4 border-t border-slate-800/50">
                <LLMApiLogViewer log={ll} />
              </div>
            </details>
          )
        })}
      </div>
    </Card>
  )
}
