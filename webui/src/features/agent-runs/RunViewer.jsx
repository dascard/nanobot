import { Badge, Card, MiniStat } from '../../components/ui'


const KIND_LABELS = {
  artifact: 'Artifact',
  cache: 'Cache',
  checkpoint: 'Checkpoint',
  delivery: 'Delivery',
  http: 'HTTP',
  llm: 'LLM',
  mcp: 'MCP',
  memory: 'Memory',
  prompt: 'Prompt',
  recovery: 'Recovery',
  run: 'Run',
  sandbox: 'Sandbox',
  side_effect: 'Side Effect',
  subagent: 'Subagent',
  task: 'Task',
  tool: 'Tool',
}

function statusTone(status) {
  if (status === 'succeeded') return 'emerald'
  if (['failed', 'ambiguous', 'cancelled', 'timed_out'].includes(status)) return 'red'
  if (status === 'running') return 'blue'
  return 'slate'
}

function formatDuration(value) {
  const milliseconds = Number(value || 0)
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '-'
  if (milliseconds >= 1000) return `${(milliseconds / 1000).toFixed(milliseconds >= 10000 ? 1 : 2)}s`
  return `${Math.round(milliseconds)}ms`
}

function formatNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0'
}

function formatCost(value) {
  const microusd = Number(value || 0)
  if (!Number.isFinite(microusd) || microusd <= 0) return '$0'
  return `$${(microusd / 1_000_000).toFixed(6)}`
}

function formatTime(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleTimeString('zh-CN', { hour12: false })
}

function Timeline({ viewer }) {
  const spans = viewer.timeline || []
  const maxEnd = Math.max(
    Number(viewer.summary?.duration_ms || 0),
    ...spans.map(span => Number(span.offset_ms || 0) + Number(span.duration_ms || 0)),
    1,
  )
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <h3 className="text-sm font-medium text-slate-200">脱敏时间线</h3>
        <p className="mt-1 text-[11px] text-slate-500">仅展示类型、状态、耗时和关联 ID，不包含正文或调用载荷。</p>
      </div>
      <div className="max-h-[560px] overflow-auto">
        {spans.map(span => {
          const left = Math.min(99, Math.max(0, Number(span.offset_ms || 0) / maxEnd * 100))
          const width = Math.max(0.6, Math.min(100 - left, Number(span.duration_ms || 0) / maxEnd * 100))
          return (
            <div key={span.span_id} className="grid min-w-[760px] grid-cols-[90px_210px_90px_1fr_80px] items-center gap-2 border-b border-slate-800/60 px-3 py-2 text-xs">
              <Badge tone="slate">{KIND_LABELS[span.kind] || span.kind}</Badge>
              <div className="min-w-0">
                <div className="truncate text-slate-200" title={span.name}>{span.name}</div>
                <div className="truncate font-mono text-[10px] text-slate-600" title={span.span_id}>{span.span_id}</div>
              </div>
              <Badge tone={statusTone(span.status)}>{span.status}</Badge>
              <div className="relative h-5 overflow-hidden rounded bg-slate-950">
                <div
                  className={`absolute top-1 h-3 rounded ${span.status === 'succeeded' ? 'bg-emerald-500/60' : span.status === 'running' ? 'bg-blue-500/60' : 'bg-red-500/60'}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`+${formatDuration(span.offset_ms)} / ${formatDuration(span.duration_ms)}`}
                />
              </div>
              <div className="text-right text-slate-500">{formatDuration(span.duration_ms)}</div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function DagView({ viewer }) {
  const nodes = viewer.dag?.nodes || []
  const containsEdges = (viewer.dag?.edges || []).filter(edge => edge.relation === 'contains')
  const retryEdges = (viewer.dag?.edges || []).filter(edge => edge.relation === 'retry')
  const parentByNode = new Map(containsEdges.map(edge => [edge.target, edge.source]))
  const depthOf = (nodeId) => {
    let depth = 0
    let current = nodeId
    const visited = new Set()
    while (parentByNode.has(current) && depth < 8 && !visited.has(current)) {
      visited.add(current)
      current = parentByNode.get(current)
      depth += 1
    }
    return depth
  }
  return (
    <Card className="p-4">
      <h3 className="text-sm font-medium text-slate-200">Run / Turn DAG</h3>
      <p className="mt-1 text-[11px] text-slate-500">缩进表示 contains 边；虚线重试关系单独列出。</p>
      <div className="mt-3 max-h-80 space-y-1 overflow-auto">
        {nodes.map(node => (
          <div key={node.id} className="flex min-w-0 items-center gap-2 rounded border border-slate-800 bg-slate-950 px-2 py-1.5" style={{ marginLeft: `${Math.min(depthOf(node.id), 6) * 12}px` }}>
            <Badge tone="slate">{KIND_LABELS[node.kind] || node.kind}</Badge>
            <span className="min-w-0 flex-1 truncate text-xs text-slate-300" title={node.name}>{node.name}</span>
            <Badge tone={statusTone(node.status)}>{node.status}</Badge>
          </div>
        ))}
      </div>
      {retryEdges.length > 0 && (
        <div className="mt-3 border-t border-slate-800 pt-3">
          <div className="mb-1 text-[11px] text-slate-500">Retry edges</div>
          {retryEdges.map((edge, index) => (
            <div key={`${edge.source}-${edge.target}-${index}`} className="truncate font-mono text-[10px] text-amber-300">
              {edge.source} ⇢ {edge.target}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

function Waterfall({ viewer }) {
  const totals = viewer.waterfall?.totals || {}
  const items = viewer.waterfall?.items || []
  const maxTokens = Math.max(
    ...items.map(item => Number(item.input_tokens || 0) + Number(item.output_tokens || 0)),
    1,
  )
  return (
    <Card className="p-4">
      <h3 className="text-sm font-medium text-slate-200">Token / Cost Waterfall</h3>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-5">
        <div><div className="text-[10px] text-slate-600">Input</div><div className="text-slate-200">{formatNumber(totals.input_tokens)}</div></div>
        <div><div className="text-[10px] text-slate-600">Output</div><div className="text-slate-200">{formatNumber(totals.output_tokens)}</div></div>
        <div><div className="text-[10px] text-slate-600">Cache hit</div><div className="text-emerald-300">{formatNumber(totals.cache_hit_tokens)}</div></div>
        <div><div className="text-[10px] text-slate-600">Cache miss</div><div className="text-amber-300">{formatNumber(totals.cache_miss_tokens)}</div></div>
        <div><div className="text-[10px] text-slate-600">Cost</div><div className="text-slate-200">{formatCost(totals.cost_microusd)}</div></div>
      </div>
      <div className="mt-4 space-y-3">
        {items.length === 0 && <p className="text-xs text-slate-600">本次 Run 没有已落库的模型用量。</p>}
        {items.map(item => {
          const inputWidth = Number(item.input_tokens || 0) / maxTokens * 100
          const outputWidth = Number(item.output_tokens || 0) / maxTokens * 100
          return (
            <div key={item.span_id}>
              <div className="mb-1 flex items-center gap-2 text-[11px]">
                <span className="min-w-0 flex-1 truncate text-slate-300" title={item.name}>{item.name}</span>
                <span className="text-slate-600">#{item.attempt || 1}</span>
                <span className="text-slate-500">{formatDuration(item.duration_ms)}</span>
                <span className="text-slate-500">{formatCost(item.cost_microusd)}</span>
              </div>
              <div className="flex h-3 overflow-hidden rounded bg-slate-950">
                <div className="bg-blue-500/70" style={{ width: `${inputWidth}%` }} title={`input ${item.input_tokens || 0}`} />
                <div className="bg-purple-500/70" style={{ width: `${outputWidth}%` }} title={`output ${item.output_tokens || 0}`} />
              </div>
              <div className="mt-1 text-[10px] text-slate-600">
                in {formatNumber(item.input_tokens)} · out {formatNumber(item.output_tokens)} · cache {formatNumber(item.cache_hit_tokens)} hit / {formatNumber(item.cache_miss_tokens)} miss
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function ContextManifest({ viewer }) {
  const container = viewer.context_manifest || {}
  const manifest = container.manifest || {}
  const entries = manifest.entries || []
  const budgets = manifest.layer_budgets || []
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium text-slate-200">Context Manifest</h3>
          <p className="mt-1 text-[11px] text-slate-500">只含层级、作用域、来源引用、token 和摘要，不保存消息正文。</p>
        </div>
        <Badge tone={container.available ? 'emerald' : 'amber'}>{container.source || 'not_recorded'}</Badge>
      </div>
      {!container.available && (
        <div className="mt-3 rounded border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-300">
          没有完整 Manifest；下方仅可使用 Run Ledger 指纹。新记录会保存完整无正文清单。
        </div>
      )}
      {Object.keys(container.fingerprint || {}).length > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
          <div><span className="text-slate-600">policy</span><div className="truncate text-slate-300">{container.fingerprint.manifest_policy_id || '-'}</div></div>
          <div><span className="text-slate-600">entries</span><div className="text-slate-300">{container.fingerprint.manifest_entry_count ?? '-'}</div></div>
          <div><span className="text-slate-600">tokens</span><div className="text-slate-300">{container.fingerprint.manifest_token_estimate ?? '-'}</div></div>
          <div><span className="text-slate-600">sha256</span><div className="truncate font-mono text-slate-300">{container.fingerprint.manifest_sha256 || '-'}</div></div>
        </div>
      )}
      {budgets.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-[11px] text-slate-500">Layer budgets</div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {budgets.map(budget => {
              const ratio = Math.min(100, Number(budget.used_tokens || 0) / Math.max(1, Number(budget.max_tokens || 1)) * 100)
              return (
                <div key={budget.layer} className="rounded border border-slate-800 bg-slate-950 p-2">
                  <div className="flex justify-between gap-2 text-[10px]"><span className="truncate text-slate-400">{budget.layer}</span><span className="text-slate-600">{budget.used_tokens}/{budget.max_tokens}</span></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-800"><div className="h-full bg-blue-500/70" style={{ width: `${ratio}%` }} /></div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {entries.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-[760px] w-full text-xs">
            <thead><tr className="border-b border-slate-800 text-left text-slate-600"><th className="py-2">entry</th><th>layer / scope</th><th>source</th><th>tokens</th><th>sha256</th></tr></thead>
            <tbody>{entries.map(entry => (
              <tr key={entry.entry_id} className="border-b border-slate-800/60">
                <td className="py-2 text-slate-300">{entry.entry_id}</td>
                <td className="text-slate-500">{entry.layer} / {entry.scope}</td>
                <td className="max-w-60 truncate text-slate-500" title={(entry.source_refs || []).join(', ')}>{entry.source_kind} · {(entry.source_refs || []).join(', ') || '-'}</td>
                <td className="text-slate-400">{entry.token_estimate}</td>
                <td className="font-mono text-[10px] text-slate-600">{String(entry.content_sha256 || '').slice(0, 16)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function EvidencePanel({ viewer }) {
  const failures = viewer.failures || []
  const retries = viewer.retries || []
  const recoveries = viewer.recoveries || []
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <Card className="p-4">
        <h3 className="text-sm font-medium text-slate-200">失败点 ({failures.length})</h3>
        <div className="mt-3 max-h-72 space-y-2 overflow-auto">
          {failures.length === 0 && <p className="text-xs text-slate-600">没有持久化失败点。</p>}
          {failures.map(item => (
            <div key={item.span_id} className="rounded border border-red-500/20 bg-red-500/5 p-2 text-xs">
              <div className="flex gap-2"><Badge tone="red">{item.kind}</Badge><span className="min-w-0 flex-1 truncate text-slate-300">{item.name}</span></div>
              <div className="mt-1 font-mono text-[10px] text-red-300">{item.code || item.status}</div>
              {item.retryable && <div className="mt-1 text-[10px] text-amber-300">可重试</div>}
            </div>
          ))}
        </div>
      </Card>
      <Card className="p-4">
        <h3 className="text-sm font-medium text-slate-200">重试 ({retries.length})</h3>
        <div className="mt-3 max-h-72 space-y-2 overflow-auto">
          {retries.length === 0 && <p className="text-xs text-slate-600">没有重试或路由回退。</p>}
          {retries.map((item, index) => (
            <div key={`${item.to_span_id}-${index}`} className="rounded border border-amber-500/20 bg-amber-500/5 p-2 text-xs">
              <div className="flex justify-between gap-2"><span className="text-amber-300">{item.kind}</span><span className="text-slate-600">attempt {item.attempt}</span></div>
              <div className="mt-1 truncate font-mono text-[10px] text-slate-500">{item.from_span_id || '起点未知'} ⇢ {item.to_span_id}</div>
              <div className="mt-1 text-[10px] text-slate-600">{item.reason_code}</div>
            </div>
          ))}
        </div>
      </Card>
      <Card className="p-4">
        <h3 className="text-sm font-medium text-slate-200">恢复证据 ({recoveries.length})</h3>
        <div className="mt-3 max-h-72 space-y-2 overflow-auto">
          {recoveries.length === 0 && <p className="text-xs text-slate-600">没有 Checkpoint、恢复操作或副作用回执。</p>}
          {recoveries.map(item => (
            <div key={item.span_id} className="rounded border border-slate-800 bg-slate-950 p-2 text-xs">
              <div className="flex gap-2"><Badge tone={statusTone(item.status)}>{item.kind}</Badge><span className="min-w-0 flex-1 truncate text-slate-300">{item.name}</span></div>
              <div className="mt-1 text-[10px] text-slate-600">{formatTime(item.started_at)} · {item.attributes.boundary || item.attributes.operation_kind || item.attributes.state || '-'}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function VersionPanel({ viewer }) {
  const versions = viewer.versions || {}
  const sections = [
    ['Prompt', versions.prompts],
    ['Model', versions.models],
    ['Runtime module', versions.runtime_modules],
    ['Registry', versions.registries],
    ['Sandbox image', versions.sandbox_images],
    ['Artifact', versions.artifacts],
  ]
  return (
    <Card className="p-4">
      <h3 className="text-sm font-medium text-slate-200">版本与脱敏合同</h3>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sections.map(([label, values]) => (
          <div key={label} className="rounded border border-slate-800 bg-slate-950 p-2">
            <div className="text-[10px] uppercase tracking-wide text-slate-600">{label}</div>
            {(values || []).length === 0
              ? <div className="mt-1 text-xs text-slate-700">未记录</div>
              : (values || []).map(value => <div key={value} className="mt-1 truncate font-mono text-[10px] text-slate-400" title={value}>{value}</div>)}
          </div>
        ))}
      </div>
      <div className="mt-4 rounded border border-emerald-500/20 bg-emerald-500/5 p-3 text-xs leading-5 text-emerald-200/80">
        隐藏推理、Prompt/消息正文、工具参数与结果、Sandbox 命令与输出、密钥凭据均已省略；Viewer 仅使用哈希、计数、状态、关联 ID 与版本证据。
      </div>
    </Card>
  )
}

export function RunViewer({ viewer }) {
  if (!viewer) return null
  const summary = viewer.summary || {}
  return (
    <section className="mt-6 space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-medium text-slate-100">统一离线 Run Viewer</h2>
          <Badge tone="emerald">persisted evidence</Badge>
          <Badge tone="slate">schema {viewer.schema_version || '-'}</Badge>
        </div>
        <p className="mt-1 text-xs text-slate-500">此视图从数据库证据离线重建，不调用模型、不恢复任务、不执行工具。</p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MiniStat label="状态" value={summary.status || '-'} tone={statusTone(summary.status)} />
        <MiniStat label="总耗时" value={formatDuration(summary.duration_ms)} />
        <MiniStat label="Span" value={summary.span_count || 0} />
        <MiniStat label="失败点" value={summary.failed_span_count || 0} tone={summary.failed_span_count ? 'red' : 'emerald'} />
        <MiniStat label="重试" value={summary.retry_count || 0} tone={summary.retry_count ? 'amber' : 'slate'} />
        <MiniStat label="恢复证据" value={summary.recovery_count || 0} />
      </div>
      <Timeline viewer={viewer} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <DagView viewer={viewer} />
        <Waterfall viewer={viewer} />
      </div>
      <ContextManifest viewer={viewer} />
      <EvidencePanel viewer={viewer} />
      <VersionPanel viewer={viewer} />
    </section>
  )
}
