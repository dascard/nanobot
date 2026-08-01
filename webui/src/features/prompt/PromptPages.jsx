import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { api } from '../../api'
import { ActionButton, Badge, Card, JsonBlock, MiniStat, SectionHeader } from '../../components/ui'
import { LLMApiRequestLogsBlock, MessageAccordion, RawJsonAccordion } from '../../components/TraceView'

const PROMPT_V2_RUNTIME_NODES = [
  { key: 'runtime_context', label: '运行上下文', role: 'system' },
  { key: 'persona_reference', label: '用户画像参考', role: 'system' },
  { key: 'conversation_context_header', label: '会话上下文头', role: 'system' },
  { key: 'history_messages', label: '历史消息', role: 'history' },
  { key: 'group_context', label: '群画像 / 表情 / 黑话', role: 'system' },
  { key: 'effort_constraint', label: '回复长度约束', role: 'system' },
  { key: 'runtime_tool_prompt', label: '工具运行说明', role: 'system' },
  { key: 'current_user_event', label: '当前用户输入', role: 'user' },
]

const PROMPT_V2_TOOL_TEMPLATE_LABELS = {
  reply: { tool: 'reply', title: '最终回复工具模板' },
  no_reply: { tool: 'no_reply', title: '不回复工具模板' },
  sql_analysis: { tool: 'sql_analysis', title: 'SQL 分析工具模板' },
  python_sandbox: { tool: 'python_sandbox', title: 'Python 数据分析工具模板' },
  ai_daily: { tool: 'ai_daily', title: 'AI 日报工具模板' },
  image_summary: { tool: 'image_summary', title: '图片摘要工具模板' },
  persona_update: { tool: 'persona_update', title: '画像更新工具模板' },
  schedule_task: { tool: 'schedule_task', title: '定时任务工具模板' },
  group_analysis: { tool: 'group_analysis', title: '群聊日报工具模板' },
  sticker_search: { tool: 'sticker_search', title: '表情包搜索工具模板' },
  reply_contract_retry: { tool: 'reply / no_reply', title: '回复合约重试模板' },
  memory_extract: { tool: 'memory_extract', title: '记忆抽取任务模板' },
  timing_gate: { tool: 'timing_gate', title: '发言时机判断模板' },
}

function promptV2Path(templateKey) {
  return String(templateKey || '').split('/').map(encodeURIComponent).join('/')
}

function promptV2TemplateKind(item) {
  if (item?.kind) return item.kind
  const key = item?.template_key || ''
  if (key.startsWith('chat/') || key.startsWith('chat_') || key === 'identity_context') return 'chat'
  if (key.startsWith('tasks/')) return 'task'
  return 'tool'
}

function promptV2ToolName(item) {
  if (item?.tool_name) return item.tool_name
  const key = item?.template_key || ''
  const parts = key.split('/')
  if (parts[0] === 'tools' && parts[1]) return parts[1]
  if (parts[0] === 'tasks' && parts[1]) return PROMPT_V2_TOOL_TEMPLATE_LABELS[parts[1]]?.tool || parts[1]
  return PROMPT_V2_TOOL_TEMPLATE_LABELS[key]?.tool || key
}

function promptV2TemplatePart(item) {
  const key = item?.template_key || ''
  const parts = key.split('/')
  return parts[parts.length - 1] || key
}

function promptV2TemplateTitle(item) {
  const key = item?.template_key || ''
  const parts = key.split('/')
  const labelKey = parts[0] === 'tools' || parts[0] === 'tasks' ? parts[1] : key
  const title = item?.name || PROMPT_V2_TOOL_TEMPLATE_LABELS[labelKey]?.title || key
  const part = promptV2TemplatePart(item)
  if (parts[0] === 'tools' && part && part !== 'usage') return `${title} / ${part}`
  if (parts[0] === 'tasks') return `${title} / ${part}`
  return title
}

function runtimeNodeLabel(key) {
  return PROMPT_V2_RUNTIME_NODES.find(item => item.key === key)?.label || key
}

function runtimeNodeRole(key) {
  return PROMPT_V2_RUNTIME_NODES.find(item => item.key === key)?.role || 'system'
}

function flowAppliesToChat(item, chatType) {
  const types = item?.chat_types
  if (!Array.isArray(types) || !types.length) return true
  return types.includes(chatType)
}

function flowScopeValue(item) {
  const types = item?.chat_types
  if (!Array.isArray(types) || !types.length) return 'all'
  if (types.includes('group') && !types.includes('private')) return 'group'
  if (types.includes('private') && !types.includes('group')) return 'private'
  return 'all'
}

function flowScopePatch(value) {
  return value === 'all' ? undefined : [value]
}

function orderedFlowNodes(flow, chatType) {
  return selectedPromptFlowPath(flow, chatType).nodes
}

function selectedPromptFlowPath(flow, chatType) {
  const nodes = (flow?.nodes || []).filter(node => flowAppliesToChat(node, chatType))
  const ids = new Set(nodes.map(node => node.id))
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const allEdges = (flow?.edges || [])
    .map((edge, edgeIndex) => ({ edge, edgeIndex }))
    .filter(({ edge }) => ids.has(edge.from) && ids.has(edge.to))
  const edges = allEdges.filter(({ edge }) => flowAppliesToChat(edge, chatType))
  const allIncident = new Map(nodes.map(node => [node.id, false]))
  const activeIncident = new Map(nodes.map(node => [node.id, false]))
  allEdges.forEach(({ edge }) => {
    allIncident.set(edge.from, true)
    allIncident.set(edge.to, true)
  })
  edges.forEach(({ edge }) => {
    activeIncident.set(edge.from, true)
    activeIncident.set(edge.to, true)
  })
  const relevantNodes = nodes.filter(node => activeIncident.get(node.id) || !allIncident.get(node.id))
  const relevantIds = new Set(relevantNodes.map(node => node.id))
  const incoming = new Map(relevantNodes.map(node => [node.id, new Set()]))
  const outgoing = new Map(relevantNodes.map(node => [node.id, []]))
  edges.forEach(({ edge, edgeIndex }) => {
    if (!relevantIds.has(edge.from) || !relevantIds.has(edge.to)) return
    incoming.get(edge.to)?.add(edge.from)
    outgoing.get(edge.from)?.push({ edge, edgeIndex })
  })
  const ready = [...incoming.entries()]
    .filter(([, from]) => from.size === 0)
    .map(([id]) => id)
    .sort((a, b) => (index.get(a) || 0) - (index.get(b) || 0))
  const entryNodeIds = [...ready]
  const result = []
  const edgeKeys = []
  while (ready.length) {
    const currentId = ready.shift()
    if (!currentId || result.includes(currentId)) continue
    result.push(currentId)
    const choices = [...(outgoing.get(currentId) || [])]
      .sort((a, b) => {
        const ai = index.get(a.edge.to) ?? 0
        const bi = index.get(b.edge.to) ?? 0
        return ai === bi ? a.edgeIndex - b.edgeIndex : ai - bi
      })
    choices.forEach(next => {
      edgeKeys.push(promptFlowEdgeKey(next.edge, next.edgeIndex))
      const targetIncoming = incoming.get(next.edge.to)
      targetIncoming?.delete(currentId)
      if (targetIncoming?.size === 0 && !result.includes(next.edge.to) && !ready.includes(next.edge.to)) {
        ready.push(next.edge.to)
      }
    })
    ready.sort((a, b) => (index.get(a) || 0) - (index.get(b) || 0))
  }
  const hasCycle = result.length !== relevantNodes.length
  const byId = new Map(nodes.map(node => [node.id, node]))
  return {
    entryNodeId: entryNodeIds[0] || '',
    entryNodeIds,
    entryCandidateIds: entryNodeIds,
    missingNodeIds: relevantNodes.map(node => node.id).filter(id => !result.includes(id)),
    hasCycle,
    nodeIds: result,
    edgeKeys,
    nodes: result.map(id => byId.get(id)).filter(Boolean),
  }
}

function promptFlowEdgeKey(edge, index = 0) {
  return `${edge?.from || ''}->${edge?.to || ''}:${(edge?.chat_types || []).join(',')}:${index}`
}

const FLOW_NODE_WIDTH = 220
const FLOW_NODE_HEIGHT = 96
const PROMPT_V2_DEFAULT_NODE_POSITIONS = {
  base_contract: { x: 80, y: 220 },
  group_policy: { x: 360, y: 80 },
  private_policy: { x: 360, y: 360 },
  runtime_context: { x: 660, y: 220 },
  identity_context: { x: 940, y: 220 },
  persona_reference: { x: 1220, y: 220 },
  conversation_context_header: { x: 1500, y: 220 },
  history_messages: { x: 1780, y: 220 },
  group_context: { x: 2060, y: 80 },
  effort_constraint: { x: 2340, y: 220 },
  runtime_tool_prompt: { x: 2620, y: 220 },
  current_user_event: { x: 2900, y: 220 },
}

function nodeCanvasPosition(node, index = 0) {
  const raw = node?.position || PROMPT_V2_DEFAULT_NODE_POSITIONS[node?.id] || {}
  const fallbackX = 80 + (index % 5) * 280
  const fallbackY = 120 + Math.floor(index / 5) * 180
  const x = Number(raw.x)
  const y = Number(raw.y)
  return {
    x: Number.isFinite(x) ? x : fallbackX,
    y: Number.isFinite(y) ? y : fallbackY,
  }
}

function PromptFlowCanvas({
  flow,
  chatType,
  selectedNodeId,
  viewport,
  onViewportChange,
  onResetViewport,
  onSelectNode,
  onMoveNode,
  onDeleteNode,
  onConnectNode,
  selectedEdgeKey,
  onSelectEdge,
}) {
  const canvasRef = useRef(null)
  const dragRef = useRef(null)
  const panRef = useRef(null)
  const connectionRef = useRef(null)
  const [connectionDraft, setConnectionDraft] = useState(null)
  const nodes = flow?.nodes || []
  const nodeById = new Map(nodes.map((node, idx) => [node.id, { node, idx, pos: nodeCanvasPosition(node, idx) }]))
  const selectedPath = selectedPromptFlowPath(flow, chatType)
  const activeNodeIds = new Set(selectedPath.nodeIds)
  const activeEdgeKeys = new Set(selectedPath.edgeKeys)
  const edges = (flow?.edges || []).filter(edge => nodeById.has(edge.from) && nodeById.has(edge.to))
  const zoom = Math.min(1.8, Math.max(0.45, Number(viewport?.zoom) || 1))
  const viewX = Number(viewport?.x) || 0
  const viewY = Number(viewport?.y) || 0
  const entryStatus = selectedPath.hasCycle
    ? `存在环或无法排序：${selectedPath.missingNodeIds.join(', ')}`
    : selectedPath.missingNodeIds.length
      ? `未排序节点：${selectedPath.missingNodeIds.join(', ')}`
      : ''
  const entryLabel = selectedPath.entryNodeIds.length ? selectedPath.entryNodeIds.join(', ') : '未确定'

  const worldFromClient = event => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return {
      x: (event.clientX - rect.left - viewX) / zoom,
      y: (event.clientY - rect.top - viewY) / zoom,
    }
  }

  const nodePortPosition = (node, idx, side) => {
    const pos = nodeCanvasPosition(node, idx)
    return {
      x: pos.x + (side === 'out' ? FLOW_NODE_WIDTH : 0),
      y: pos.y + FLOW_NODE_HEIGHT / 2,
    }
  }

  const startDragNode = (event, node) => {
    if (event.button !== 0) return
    if (event.target?.closest?.('[data-flow-port="true"], [data-flow-control="true"]')) return
    event.preventDefault()
    const idx = nodes.findIndex(item => item.id === node.id)
    const pos = nodeCanvasPosition(node, idx)
    const point = worldFromClient(event)
    dragRef.current = {
      nodeId: node.id,
      offsetX: point.x - pos.x,
      offsetY: point.y - pos.y,
    }
  }

  const startConnection = (event, node) => {
    if (event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()
    const idx = nodes.findIndex(item => item.id === node.id)
    const from = nodePortPosition(node, idx, 'out')
    const draft = { fromNodeId: node.id, from, to: from }
    connectionRef.current = draft
    setConnectionDraft(draft)
  }

  const completeConnection = (event, targetNodeId) => {
    if (!connectionRef.current) return
    event.preventDefault()
    event.stopPropagation()
    const fromNodeId = connectionRef.current.fromNodeId
    if (fromNodeId && fromNodeId !== targetNodeId) {
      onConnectNode(fromNodeId, targetNodeId)
    }
    connectionRef.current = null
    setConnectionDraft(null)
  }

  const startPanCanvas = event => {
    if (event.button !== 0 && event.button !== 1) return
    if (event.target?.closest?.('[data-flow-node="true"], [data-flow-control="true"], [data-flow-port="true"]')) return
    event.preventDefault()
    panRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: viewX,
      originY: viewY,
    }
  }

  const handleCanvasWheel = useCallback(event => {
    event.preventDefault()
    event.stopPropagation()
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const factor = event.deltaY > 0 ? 0.9 : 1.1
    const nextZoom = Math.min(1.8, Math.max(0.45, zoom * factor))
    const pointerX = event.clientX - rect.left
    const pointerY = event.clientY - rect.top
    const worldX = (pointerX - viewX) / zoom
    const worldY = (pointerY - viewY) / zoom
    onViewportChange(prev => ({
      ...prev,
      zoom: nextZoom,
      x: Math.round(pointerX - worldX * nextZoom),
      y: Math.round(pointerY - worldY * nextZoom),
    }))
  }, [onViewportChange, viewX, viewY, zoom])

  useEffect(() => {
    const node = canvasRef.current
    if (!node) return undefined
    node.addEventListener('wheel', handleCanvasWheel, { passive: false })
    return () => node.removeEventListener('wheel', handleCanvasWheel)
  }, [handleCanvasWheel])

  const changeZoom = factor => {
    onViewportChange(prev => {
      const current = Math.min(1.8, Math.max(0.45, Number(prev.zoom) || 1))
      return { ...prev, zoom: Math.min(1.8, Math.max(0.45, current * factor)) }
    })
  }

  const handleMouseMove = event => {
    if (connectionRef.current) {
      const point = worldFromClient(event)
      const next = { ...connectionRef.current, to: point }
      connectionRef.current = next
      setConnectionDraft(next)
      return
    }
    if (dragRef.current) {
      const point = worldFromClient(event)
      onMoveNode(dragRef.current.nodeId, {
        x: Math.max(20, Math.round(point.x - dragRef.current.offsetX)),
        y: Math.max(20, Math.round(point.y - dragRef.current.offsetY)),
      })
      return
    }
    if (panRef.current) {
      const pan = panRef.current
      onViewportChange(prev => ({
        ...prev,
        x: Math.round(pan.originX + event.clientX - pan.startX),
        y: Math.round(pan.originY + event.clientY - pan.startY),
      }))
    }
  }

  const stopInteractions = () => {
    dragRef.current = null
    panRef.current = null
    connectionRef.current = null
    setConnectionDraft(null)
  }

  return (
    <div
      ref={canvasRef}
      data-testid="prompt-flow-canvas"
      onMouseDown={startPanCanvas}
      onMouseMove={handleMouseMove}
      onMouseUp={stopInteractions}
      onMouseLeave={stopInteractions}
      className="prompt-flow-scrollbar prompt-v2-canvas relative h-full min-h-[32rem] overflow-hidden rounded-lg border border-slate-800 bg-slate-950 xl:min-h-0"
      style={{
        backgroundImage: 'radial-gradient(circle, rgba(148, 163, 184, 0.16) 1px, transparent 1px)',
        backgroundSize: `${28 * zoom}px ${28 * zoom}px`,
        backgroundPosition: `${viewX}px ${viewY}px`,
      }}
    >
      <div className="absolute left-3 top-3 z-20 flex max-w-[calc(100%-17rem)] flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/90 px-2.5 py-1.5 text-[11px]" data-flow-control="true">
        <span className="text-slate-500">拓扑入口</span>
        <span className={selectedPath.entryNodeIds.length ? 'font-mono text-emerald-300' : 'text-red-300'}>
          {entryLabel}
        </span>
        {entryStatus && <span className="text-amber-300">{entryStatus}</span>}
      </div>
      <div className="absolute right-3 top-3 z-20 flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/90 p-1" data-flow-control="true">
        <button title="缩小" onClick={() => changeZoom(0.9)} className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">缩小</button>
        <span className="min-w-12 text-center text-[11px] text-slate-400">{Math.round(zoom * 100)}%</span>
        <button title="放大" onClick={() => changeZoom(1.1)} className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">放大</button>
        <button title="重置视图" onClick={onResetViewport} className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">重置视图</button>
      </div>

      <div
        data-testid="prompt-flow-viewport"
        className="absolute left-0 top-0"
        style={{
          width: 3220,
          height: 760,
          transform: `translate(${viewX}px, ${viewY}px) scale(${zoom})`,
          transformOrigin: '0 0',
        }}
      >
        <svg data-testid="prompt-flow-edge-layer" width="3220" height="760" className="absolute inset-0">
          <defs>
            <marker id="prompt-flow-arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#34d399" />
            </marker>
            <marker id="prompt-flow-arrow-muted" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
            </marker>
          </defs>
          {edges.map((edge, idx) => {
            const from = nodeById.get(edge.from)
            const to = nodeById.get(edge.to)
            const edgeKey = promptFlowEdgeKey(edge, idx)
            const active = activeEdgeKeys.has(edgeKey)
            const selected = selectedEdgeKey === edgeKey
            const x1 = from.pos.x + FLOW_NODE_WIDTH
            const y1 = from.pos.y + FLOW_NODE_HEIGHT / 2
            const x2 = to.pos.x
            const y2 = to.pos.y + FLOW_NODE_HEIGHT / 2
            const mid = Math.max(70, Math.abs(x2 - x1) / 2)
            const path = `M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}`
            return (
              <g key={edgeKey}>
                <path
                  d={path}
                  fill="none"
                  stroke={selected ? '#f59e0b' : active ? '#34d399' : '#475569'}
                  strokeWidth={selected ? 3.5 : active ? 2.5 : 1.5}
                  strokeDasharray={active ? '0' : '5 6'}
                  markerEnd={active ? 'url(#prompt-flow-arrow-active)' : 'url(#prompt-flow-arrow-muted)'}
                  opacity={selected ? 1 : active ? 0.95 : 0.45}
                  style={{ pointerEvents: 'none' }}
                />
                <path
                  d={path}
                  fill="none"
                  stroke="transparent"
                  strokeWidth="18"
                  style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
                  onMouseDown={event => { event.stopPropagation(); onSelectEdge(edge, idx) }}
                />
              </g>
            )
          })}
          {connectionDraft && (
            <path
              d={`M ${connectionDraft.from.x} ${connectionDraft.from.y} C ${connectionDraft.from.x + 100} ${connectionDraft.from.y}, ${connectionDraft.to.x - 100} ${connectionDraft.to.y}, ${connectionDraft.to.x} ${connectionDraft.to.y}`}
              fill="none"
              stroke="#f59e0b"
              strokeWidth="2"
              strokeDasharray="6 5"
              opacity="0.95"
            />
          )}
        </svg>

        {nodes.map((node, idx) => {
          const pos = nodeCanvasPosition(node, idx)
          const active = activeNodeIds.has(node.id)
          const selected = selectedNodeId === node.id
          const runtimeLabel = runtimeNodeLabel(node.runtime_key)
          const runtimeRole = runtimeNodeRole(node.runtime_key)
          return (
            <div
              key={node.id}
              data-flow-node="true"
              className={`absolute rounded-lg border bg-slate-900/95 ${selected ? 'border-emerald-400 ring-1 ring-emerald-500/40' : active ? 'border-slate-700' : 'border-slate-800 opacity-50'} cursor-default`}
              style={{ left: pos.x, top: pos.y, width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT }}
              onMouseDown={e => startDragNode(e, node)}
              onClick={() => onSelectNode(node)}
            >
              <button
                title="连接端口：拖到这里完成连线"
                data-flow-port="true"
                onMouseUp={e => completeConnection(e, node.id)}
                onMouseDown={e => e.stopPropagation()}
                className="absolute -left-2 top-1/2 h-4 w-4 -translate-y-1/2 rounded-full border border-slate-500 bg-slate-950 hover:border-emerald-300 hover:bg-emerald-500/20"
              />
              <button
                title="连接端口：从这里拖出连线"
                data-flow-port="true"
                onMouseDown={e => startConnection(e, node)}
                className="absolute -right-2 top-1/2 h-4 w-4 -translate-y-1/2 rounded-full border border-slate-500 bg-slate-950 hover:border-amber-300 hover:bg-amber-500/20"
              />
              <div className="flex h-full flex-col p-3">
                <div className="flex items-start gap-2 min-w-0">
                  <span className={`mt-0.5 h-2.5 w-2.5 rounded-full ${node.type === 'template' ? 'bg-emerald-400' : 'bg-blue-400'}`} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-100">{node.label || node.id}</div>
                    <div className="mt-1 truncate text-[10px] text-slate-500">{node.type === 'template' ? node.template_key : `${runtimeRole}: ${runtimeLabel}`}</div>
                  </div>
                  {selectedPath.entryNodeIds.includes(node.id) && <Badge tone="amber">入口</Badge>}
                  <Badge tone={node.type === 'template' ? 'emerald' : 'blue'}>{node.chat_types?.[0] || 'all'}</Badge>
                </div>
                <div className="mt-auto flex items-center gap-1">
                  <button onMouseDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); onDeleteNode(node.id) }}
                    className="ml-auto px-2 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-[10px] text-red-300">
                    删除节点
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function PromptFlowMobileList({
  flow,
  chatType,
  selectedNodeId,
  onSelectNode,
  selectedEdgeKey,
  onSelectEdge,
  onDeleteEdge,
}) {
  const nodes = flow?.nodes || []
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const selectedPath = selectedPromptFlowPath(flow, chatType)
  const orderedNodes = selectedPath.nodes
  const edges = (flow?.edges || []).filter(edge => nodeById.has(edge.from) && nodeById.has(edge.to))
  const entryStatus = selectedPath.hasCycle
    ? `存在环或无法排序：${selectedPath.missingNodeIds.join(', ')}`
    : selectedPath.missingNodeIds.length
      ? `未排序节点：${selectedPath.missingNodeIds.join(', ')}`
      : ''
  const entryLabel = selectedPath.entryNodeIds.length ? selectedPath.entryNodeIds.join(', ') : '未确定'
  return (
    <Card data-testid="prompt-flow-mobile-list" className="p-3 lg:hidden">
      <SectionHeader
        title="结构化编排"
        description="小屏使用节点和连线列表维护编排；桌面端可使用 canvas 拖拽。"
      />
      <div className="mb-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs">
        <span className="text-slate-500">拓扑入口：</span>
        <span className={selectedPath.entryNodeIds.length ? 'font-mono text-emerald-300' : 'text-red-300'}>{entryLabel}</span>
        {entryStatus && <div className="mt-1 text-amber-300">{entryStatus}</div>}
      </div>
      <div className="space-y-2">
        {orderedNodes.map((node, idx) => (
          <button
            key={node.id}
            type="button"
            onClick={() => onSelectNode(node)}
            className={`w-full rounded-lg border px-3 py-2 text-left text-xs transition-colors ${selectedNodeId === node.id ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-100' : 'border-slate-800 bg-slate-950 text-slate-300 hover:bg-slate-800'}`}
          >
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-800 font-mono text-[10px] text-slate-400">{idx + 1}</span>
              <span className="min-w-0 flex-1 truncate">{node.label || node.id}</span>
              {selectedPath.entryNodeIds.includes(node.id) && <Badge tone="amber">入口</Badge>}
              <Badge tone={node.type === 'template' ? 'emerald' : 'blue'}>{node.type === 'template' ? '模板' : '运行时'}</Badge>
            </div>
            <div className="mt-1 truncate pl-7 font-mono text-[10px] text-slate-500">
              {node.type === 'template' ? node.template_key : node.runtime_key}
            </div>
          </button>
        ))}
        {!orderedNodes.length && <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 text-center text-xs text-slate-500">当前路径没有节点</div>}
      </div>
      <div className="mt-4 border-t border-slate-800 pt-3">
        <div className="mb-2 text-xs font-medium text-slate-300">连线</div>
        <div className="space-y-2">
          {edges.map((edge, idx) => {
            const edgeKey = promptFlowEdgeKey(edge, idx)
            const active = flowAppliesToChat(edge, chatType)
            return (
              <div key={edgeKey} className={`rounded-lg border px-3 py-2 text-xs ${selectedEdgeKey === edgeKey ? 'border-amber-500/50 bg-amber-500/10' : 'border-slate-800 bg-slate-950'}`}>
                <button type="button" onClick={() => onSelectEdge(edge, idx)} className="flex w-full items-center gap-2 text-left">
                  <span className={active ? 'text-emerald-300' : 'text-slate-500'}>{active ? '当前路径' : '其他路径'}</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">{flowScopeValue(edge)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-slate-400">{edge.from} → {edge.to}</span>
                </button>
                {selectedEdgeKey === edgeKey && (
                  <ActionButton tone="red" className="mt-2" onClick={() => onDeleteEdge(edgeKey)}>删除连线</ActionButton>
                )}
              </div>
            )
          })}
          {!edges.length && <div className="text-xs text-slate-500">暂无连线</div>}
        </div>
      </div>
    </Card>
  )
}

export function PromptV2TemplatesPage() {
  const [chatType, setChatType] = useState('group')
  const [templates, setTemplates] = useState([])
  const [templateTree, setTemplateTree] = useState({ chat: [], tools: {}, tasks: [] })
  const [selected, setSelected] = useState('chat/main')
  const [selectedNodeId, setSelectedNodeId] = useState('')
  const [detail, setDetail] = useState(null)
  const [content, setContent] = useState('')
  const [variables, setVariables] = useState([])
  const [flow, setFlow] = useState({ version: 1, nodes: [], edges: [] })
  const [flowSource, setFlowSource] = useState('')
  const [flowPath, setFlowPath] = useState('')
  const [defaultDir, setDefaultDir] = useState('')
  const [runtimeDir, setRuntimeDir] = useState('')
  const [templateWorkspace, setTemplateWorkspace] = useState('chat')
  const [selectedToolTemplateKey, setSelectedToolTemplateKey] = useState('')
  const [selectedTaskTemplateKey, setSelectedTaskTemplateKey] = useState('')
  const [newTemplateKey, setNewTemplateKey] = useState('tools/custom_tool/usage')
  const [runtimeToAdd, setRuntimeToAdd] = useState('runtime_context')
  const [canvasViewport, setCanvasViewport] = useState({ x: 24, y: 24, zoom: 1 })
  const [selectedEdgeKey, setSelectedEdgeKey] = useState('')
  const [isLargeTemplateEditorOpen, setIsLargeTemplateEditorOpen] = useState(false)
  const [identitySettings, setIdentitySettings] = useState({
    'bot.character_name': '',
    'bot.alias_names': '',
  })
  const [toolSchemaConfig, setToolSchemaConfig] = useState(null)
  const [schemaEditText, setSchemaEditText] = useState('')
  const [schemaError, setSchemaError] = useState('')
  const [toast, setToast] = useState('')
  const nodeIdSeq = useRef(0)
  const chatTemplates = templates.filter(item => promptV2TemplateKind(item) === 'chat')
  const toolTemplates = templates.filter(item => promptV2TemplateKind(item) === 'tool')
  const taskTemplates = templates.filter(item => promptV2TemplateKind(item) === 'task')
  const orderedNodes = orderedFlowNodes(flow, chatType)
  const allNodes = flow?.nodes || []
  const selectedNode = allNodes.find(node => node.id === selectedNodeId) || orderedNodes[0] || allNodes[0] || null
  const selectedTemplateKey = selectedNode?.type === 'template' ? (selectedNode.template_key || selected) : ''
  const activeTemplateKey = templateWorkspace === 'tools' ? selectedToolTemplateKey : templateWorkspace === 'tasks' ? selectedTaskTemplateKey : selectedTemplateKey
  const selectedToolTemplate = toolTemplates.find(item => item.template_key === selectedToolTemplateKey) || toolTemplates[0] || null
  const selectedTaskTemplate = taskTemplates.find(item => item.template_key === selectedTaskTemplateKey) || taskTemplates[0] || null
  const selectedResourceTemplate = templateWorkspace === 'tasks' ? selectedTaskTemplate : selectedToolTemplate
  const schemaToolName = promptV2ToolName(selectedToolTemplate) || ''
  const activeToolSchema = templateWorkspace === 'tools' ? (toolSchemaConfig?.tool_schema || detail?.tool_schema || selectedToolTemplate?.tool_schema || null) : null
  const schemaJson = activeToolSchema ? JSON.stringify(activeToolSchema, null, 2) : ''
  const schemaName = activeToolSchema?.function?.name || promptV2ToolName(selectedToolTemplate) || '-'
  const schemaDescription = activeToolSchema?.function?.description || selectedToolTemplate?.description || ''
  const selectedEdgeIndex = (flow.edges || []).findIndex((edge, idx) => promptFlowEdgeKey(edge, idx) === selectedEdgeKey)
  const selectedEdge = selectedEdgeIndex >= 0 ? (flow.edges || [])[selectedEdgeIndex] : null

  const loadTemplates = useCallback(() => {
    api.get('/prompt/templates').then(r => {
      const list = r.data.items || []
      setTemplates(list)
      setTemplateTree(r.data.tree || { chat: [], tools: {}, tasks: [] })
      setDefaultDir(r.data.default_dir || '')
      setRuntimeDir(r.data.runtime_dir || '')
      const chatKeys = list.filter(item => promptV2TemplateKind(item) === 'chat').map(item => item.template_key)
      const toolKeys = list.filter(item => promptV2TemplateKind(item) === 'tool').map(item => item.template_key)
      const taskKeys = list.filter(item => promptV2TemplateKind(item) === 'task').map(item => item.template_key)
      setSelected(prev => chatKeys.includes(prev) ? prev : (chatKeys.includes('chat/main') ? 'chat/main' : chatKeys[0] || ''))
      setSelectedToolTemplateKey(prev => toolKeys.includes(prev) ? prev : (toolKeys[0] || ''))
      setSelectedTaskTemplateKey(prev => taskKeys.includes(prev) ? prev : (taskKeys[0] || ''))
    }).catch(e => alert(e.response?.data?.detail || '加载模板失败'))
  }, [])

  const loadFlow = useCallback(() => {
    api.get('/prompt/flow').then(r => {
      setFlow(r.data.flow || { version: 1, nodes: [], edges: [] })
      setFlowSource(r.data.source || '')
      setFlowPath(r.data.path || '')
    }).catch(e => alert(e.response?.data?.detail || '加载编排图失败'))
  }, [])

  const loadIdentitySettings = useCallback(() => {
    api.get('/settings').then(r => {
      const next = {
        'bot.character_name': '',
        'bot.alias_names': '',
      }
      for (const item of r.data.settings || []) {
        if (Object.prototype.hasOwnProperty.call(next, item.key)) next[item.key] = String(item.value ?? '')
      }
      setIdentitySettings(next)
    }).catch(() => {})
  }, [])

  const saveIdentitySetting = (key, value) => {
    api.put(`/settings/${encodeURIComponent(key)}`, { value }).then(() => {
      setToast(`已保存 ${key}`)
      loadIdentitySettings()
    }).catch(e => alert(e.response?.data?.detail || '保存身份变量失败'))
  }

  useEffect(() => {
    loadTemplates()
    loadFlow()
    loadIdentitySettings()
    api.get('/prompt/variables')
      .then(r => setVariables(r.data.items || []))
      .catch(() => setVariables([]))
  }, [loadTemplates, loadFlow, loadIdentitySettings])

  const loadToolSchemaConfig = useCallback((toolName) => {
    if (!toolName) return
    api.get(`/tools/${encodeURIComponent(toolName)}/schema`).then(r => {
      setToolSchemaConfig(r.data)
      setSchemaEditText(JSON.stringify(r.data.editable_schema || r.data.tool_schema || {}, null, 2))
      setSchemaError('')
    }).catch(e => {
      setToolSchemaConfig(null)
      setSchemaEditText('')
      setSchemaError(e.response?.data?.detail || '加载工具 schema 失败')
    })
  }, [])

  useEffect(() => {
    if (templateWorkspace !== 'tools' || !schemaToolName) return
    loadToolSchemaConfig(schemaToolName)
  }, [templateWorkspace, schemaToolName, loadToolSchemaConfig])

  const loadTemplateDetail = useCallback((templateKey) => {
    const key = templateKey || activeTemplateKey
    if (!key) return
    api.get(`/prompt/templates/${promptV2Path(key)}`).then(r => {
      setDetail(r.data)
      setContent(r.data.content || '')
    }).catch(e => alert(e.response?.data?.detail || '加载模板失败'))
  }, [activeTemplateKey])

  useEffect(() => {
    if (!activeTemplateKey) return
    loadTemplateDetail(activeTemplateKey)
  }, [activeTemplateKey, loadTemplateDetail])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 2500)
    return () => clearTimeout(t)
  }, [toast])

  const save = () => {
    if (!activeTemplateKey) return
    api.put(`/prompt/templates/${promptV2Path(activeTemplateKey)}`, { content }).then(r => {
      setToast(`已保存 ${activeTemplateKey} · ${r.data.after_hash?.slice(0, 12) || ''}`)
      loadTemplates()
    }).catch(e => alert(e.response?.data?.detail || '保存模板失败'))
  }

  const createRuntimeTemplate = () => {
    const key = newTemplateKey.trim()
    if (!key) return
    const parts = key.split('/')
    const kind = parts[0] === 'chat' ? 'chat' : parts[0] === 'tasks' ? 'task' : 'tool'
    const toolName = parts[0] === 'tools' ? parts[1] || '' : ''
    api.post('/prompt/templates', {
      template_key: key,
      kind,
      tool_name: toolName,
      name: key,
      content: '',
    }).then(() => {
      setToast(`已新建运行时模板 ${key}`)
      setTemplateWorkspace(kind === 'chat' ? 'chat' : kind === 'task' ? 'tasks' : 'tools')
      if (kind === 'chat') setSelected(key)
      if (kind === 'task') setSelectedTaskTemplateKey(key)
      if (kind === 'tool') setSelectedToolTemplateKey(key)
      loadTemplates()
    }).catch(e => alert(e.response?.data?.detail || '新建模板失败'))
  }

  const deleteRuntimeOverride = () => {
    if (!activeTemplateKey) return
    api.delete(`/prompt/templates/${promptV2Path(activeTemplateKey)}`).then(() => {
      setToast(`已删除运行时覆盖 ${activeTemplateKey}`)
      loadTemplates()
      // 编辑器必须同步回源模板内容,否则残留的旧覆盖正文一经保存会
      // 静默重建刚删除的覆盖。
      loadTemplateDetail(activeTemplateKey)
    }).catch(e => alert(e.response?.data?.detail || '删除运行时覆盖失败'))
  }

  const resetRuntimeOverride = () => {
    if (!activeTemplateKey) return
    api.post(`/prompt/templates/${promptV2Path(activeTemplateKey)}/reset`).then(() => {
      setToast(`已重置覆盖 ${activeTemplateKey}`)
      loadTemplates()
      loadTemplateDetail(activeTemplateKey)
    }).catch(e => alert(e.response?.data?.detail || '重置覆盖失败'))
  }

  const saveFlow = () => {
    api.put('/prompt/flow', { flow }).then(r => {
      setToast(`已保存编排图 · ${r.data.runtime_path || ''}`)
      loadFlow()
    }).catch(e => alert(e.response?.data?.detail || '保存编排图失败'))
  }

  const saveToolSchema = () => {
    if (!schemaToolName) return
    let parsed
    try {
      parsed = JSON.parse(schemaEditText || '{}')
    } catch (e) {
      setSchemaError(`JSON 格式错误: ${e.message}`)
      return
    }
    api.put(`/tools/${encodeURIComponent(schemaToolName)}/schema`, { schema: parsed }).then(r => {
      setToolSchemaConfig(r.data)
      setSchemaEditText(JSON.stringify(r.data.editable_schema || r.data.tool_schema || {}, null, 2))
      setSchemaError('')
      setToast(`已保存 ${schemaToolName} schema`)
      loadTemplates()
    }).catch(e => setSchemaError(e.response?.data?.detail || '保存工具 schema 失败'))
  }

  const resetToolSchema = () => {
    if (!schemaToolName) return
    api.delete(`/tools/${encodeURIComponent(schemaToolName)}/schema`).then(r => {
      setToolSchemaConfig(r.data)
      setSchemaEditText(JSON.stringify(r.data.editable_schema || r.data.tool_schema || {}, null, 2))
      setSchemaError('')
      setToast(`已重置 ${schemaToolName} schema`)
      loadTemplates()
    }).catch(e => setSchemaError(e.response?.data?.detail || '重置工具 schema 失败'))
  }

  const updateNode = (nodeId, patch) => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).map(node => node.id === nodeId ? { ...node, ...patch } : node),
    }))
    if (patch.template_key) setSelected(patch.template_key)
  }

  const addNodeAfterSelection = node => {
    const anchorId = selectedNode?.id || orderedNodes[orderedNodes.length - 1]?.id || ''
    setFlow(prev => {
      const nextNodes = [...(prev.nodes || []), node]
      const nextEdges = [...(prev.edges || [])]
      if (anchorId && anchorId !== node.id) {
        nextEdges.push({ from: anchorId, to: node.id, chat_types: [chatType] })
      }
      return { ...prev, nodes: nextNodes, edges: nextEdges }
    })
    setSelectedNodeId(node.id)
    setSelectedEdgeKey('')
    if (node.type === 'template') setSelected(node.template_key)
  }

  const selectNode = node => {
    setSelectedNodeId(node.id)
    setSelectedEdgeKey('')
    if (node.type === 'template') setSelected(node.template_key || '')
  }

  const moveNode = (nodeId, position) => {
    updateNode(nodeId, { position })
  }

  const createNodeId = prefix => {
    const existing = new Set((flow.nodes || []).map(node => node.id))
    while (true) {
      nodeIdSeq.current += 1
      const id = `${prefix}_${nodeIdSeq.current}`
      if (!existing.has(id)) return id
    }
  }

  const addTemplateNode = () => {
    const key = (
      chatTemplates.find(item => item.template_key === 'chat/identity_context')?.template_key
      || chatTemplates.find(item => item.template_key === 'chat/main')?.template_key
      || chatTemplates[0]?.template_key
    )
    if (!key) return
    const template = chatTemplates.find(item => item.template_key === key)
    const id = createNodeId(key)
    addNodeAfterSelection({
      id,
      type: 'template',
      label: `模板: ${template?.name || key}`,
      template_key: key,
      chat_types: [chatType],
    })
  }

  const addRuntimeNode = () => {
    const option = PROMPT_V2_RUNTIME_NODES.find(item => item.key === runtimeToAdd) || PROMPT_V2_RUNTIME_NODES[0]
    if (!option) return
    const id = createNodeId(option.key)
    addNodeAfterSelection({
      id,
      type: 'runtime',
      label: option.label,
      runtime_key: option.key,
      chat_types: [chatType],
    })
  }

  const deleteNode = nodeId => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).filter(node => node.id !== nodeId),
      edges: (prev.edges || []).filter(edge => edge.from !== nodeId && edge.to !== nodeId),
    }))
    setSelectedNodeId('')
    setSelectedEdgeKey('')
  }

  const connectNode = (fromId, toId) => {
    if (!toId) return
    setFlow(prev => {
      const nextEdge = { from: fromId, to: toId, chat_types: [chatType] }
      const duplicate = (prev.edges || []).some(edge =>
        edge.from === nextEdge.from
        && edge.to === nextEdge.to
        && flowScopeValue(edge) === flowScopeValue(nextEdge)
      )
      if (duplicate) return prev
      return {
        ...prev,
        edges: [...(prev.edges || []), nextEdge],
      }
    })
    setSelectedEdgeKey('')
  }

  const selectEdge = (edge, idx) => {
    setSelectedEdgeKey(promptFlowEdgeKey(edge, idx))
  }

  const deleteEdge = edgeKey => {
    setFlow(prev => ({
      ...prev,
      edges: (prev.edges || []).filter((edge, idx) => promptFlowEdgeKey(edge, idx) !== edgeKey),
    }))
    setSelectedEdgeKey('')
  }

  const updateEdgeScope = value => {
    if (!selectedEdge || selectedEdgeIndex < 0) return
    const nextEdge = { ...selectedEdge, chat_types: flowScopePatch(value) }
    if (!nextEdge.chat_types) delete nextEdge.chat_types
    setFlow(prev => ({
      ...prev,
      edges: (prev.edges || []).map((edge, idx) => idx === selectedEdgeIndex ? nextEdge : edge),
    }))
    setSelectedEdgeKey(promptFlowEdgeKey(nextEdge, selectedEdgeIndex))
  }

  const autoLayoutFlow = () => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).map((node, idx) => ({
        ...node,
        position: nodeCanvasPosition({ ...node, position: undefined }, idx),
      })),
    }))
  }

  const updateSelectedScope = value => {
    if (!selectedNode) return
    updateNode(selectedNode.id, { chat_types: flowScopePatch(value) })
  }

  const updateSelectedTemplate = value => {
    if (!selectedNode || selectedNode.type !== 'template') return
    const template = templates.find(item => item.template_key === value)
    setSelected(value)
    updateNode(selectedNode.id, { template_key: value, label: `模板: ${template?.name || value}` })
  }

  const updateSelectedRuntime = value => {
    if (!selectedNode || selectedNode.type !== 'runtime') return
    const option = PROMPT_V2_RUNTIME_NODES.find(item => item.key === value)
    updateNode(selectedNode.id, { runtime_key: value, label: option?.label || value })
  }

  return (
    <div className="prompt-v2-page flex h-full min-h-0 flex-col">
      {toast && <div className="mb-3 shrink-0 px-4 py-2 bg-emerald-500/15 border border-emerald-500/30 rounded-lg text-sm text-emerald-400">{toast}</div>}
      <div className="mb-3 flex shrink-0 flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold mb-1 text-slate-50">Prompt 模板</h1>
          <p className="max-w-5xl text-slate-500 text-xs leading-5">聊天主流程用画布编排；工具提示词按工具拆分成独立模板。变量是全局白名单，当前输入仍只作为 user event 注入一次</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-slate-600">
            <span>默认模板目录: <span className="font-mono text-slate-500">{defaultDir || '-'}</span></span>
            <span>运行时模板目录: <span className="font-mono text-slate-500">{runtimeDir || '-'}</span></span>
            <span>编排图: <span className="font-mono text-slate-500">{flowPath || '-'}</span></span>
          </div>
        </div>
        <div className="flex w-full flex-wrap gap-2 md:w-auto md:justify-end">
          <NavLink to="/prompt-preview" className="whitespace-nowrap rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700">运行预览</NavLink>
          <button onClick={saveFlow} disabled={templateWorkspace !== 'chat'} className="whitespace-nowrap rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium hover:bg-blue-500 disabled:opacity-50">保存编排图</button>
          <button onClick={save} disabled={!activeTemplateKey} className="whitespace-nowrap rounded-lg bg-emerald-600 px-4 py-2 text-xs font-medium hover:bg-emerald-500 disabled:opacity-50">保存模板</button>
        </div>
      </div>

      <div data-testid="prompt-v2-workbench" className="viewport-scroll grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto xl:grid-cols-[200px_minmax(0,1fr)_320px] xl:items-stretch xl:overflow-hidden 2xl:grid-cols-[224px_minmax(0,1fr)_360px]">
        <div data-testid="prompt-v2-left-rail" className="min-w-0 space-y-3 xl:h-full xl:overflow-y-auto xl:pr-1 prompt-flow-scrollbar">
          <Card className="p-3">
            <div className="text-xs font-medium text-slate-300 mb-2">模板工作区</div>
            <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-950 p-1">
              <button onClick={() => setTemplateWorkspace('chat')}
                className={`rounded-md px-2 py-1.5 text-xs ${templateWorkspace === 'chat' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                聊天编排
              </button>
              <button onClick={() => setTemplateWorkspace('tools')}
                className={`rounded-md px-2 py-1.5 text-xs ${templateWorkspace === 'tools' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                工具模板
              </button>
              <button onClick={() => setTemplateWorkspace('tasks')}
                className={`rounded-md px-2 py-1.5 text-xs ${templateWorkspace === 'tasks' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>
                任务模板
              </button>
            </div>
          </Card>

          <Card className="p-3">
            <div className="text-xs font-medium text-slate-300 mb-1">资源树</div>
            <div className="text-[11px] text-slate-600 mb-2">按工作区筛选模板，减少左侧噪音。</div>
            <div className="space-y-2 max-h-[360px] overflow-auto prompt-flow-scrollbar xl:max-h-none">
              {templateWorkspace === 'chat' && (
                <div>
                  <div className="mb-1 text-[11px] text-slate-500">chat</div>
                  <div className="space-y-1">
                    {(templateTree.chat || chatTemplates).map(item => (
                      <button key={item.template_key} onClick={() => selectedNode?.type === 'template' ? updateSelectedTemplate(item.template_key) : setSelected(item.template_key)}
                        className={`w-full text-left rounded-lg border px-2 py-2 text-xs ${selectedTemplateKey === item.template_key || selected === item.template_key ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200' : 'border-slate-800 hover:bg-slate-800 text-slate-300'}`}>
                        <div className="font-medium truncate">{item.name || item.template_key}</div>
                        <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">{item.template_key}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {templateWorkspace === 'tools' && Object.entries(templateTree.tools || {}).map(([toolName, items]) => (
                <div key={toolName}>
                  <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                    <span className="font-mono">{toolName}</span>
                    <span>{items.length}</span>
                  </div>
                  <div className="space-y-1">
                    {items.map(item => (
                      <button key={item.template_key} onClick={() => setSelectedToolTemplateKey(item.template_key)}
                        className={`w-full text-left rounded-lg border px-2 py-2 text-xs ${selectedToolTemplateKey === item.template_key ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200' : 'border-slate-800 hover:bg-slate-800 text-slate-300'}`}>
                        <div className="font-medium truncate">{promptV2TemplatePart(item)}</div>
                        <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">{item.template_key}</div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {templateWorkspace === 'tasks' && (
                <div>
                  <div className="mb-1 text-[11px] text-slate-500">tasks</div>
                  <div className="space-y-1">
                    {(templateTree.tasks || taskTemplates).map(item => (
                      <button key={item.template_key} onClick={() => setSelectedTaskTemplateKey(item.template_key)}
                        className={`w-full text-left rounded-lg border px-2 py-2 text-xs ${selectedTaskTemplateKey === item.template_key ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200' : 'border-slate-800 hover:bg-slate-800 text-slate-300'}`}>
                        <div className="font-medium truncate">{item.name || item.template_key}</div>
                        <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">{item.template_key}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </Card>

          <Card className="overflow-hidden">
            <details>
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800/40">运行时覆盖</summary>
              <div className="space-y-2 border-t border-slate-800 p-3">
                <input aria-label="新建模板路径" value={newTemplateKey} onChange={e => setNewTemplateKey(e.target.value)}
                  placeholder="tools/custom_tool/usage"
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-200 outline-none focus:border-emerald-500" />
                <button onClick={createRuntimeTemplate} className="w-full rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700">新建模板</button>
                <div className="grid grid-cols-2 gap-2">
                  <button onClick={resetRuntimeOverride} disabled={!activeTemplateKey}
                    className="rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-500/20 disabled:opacity-40">
                    重置覆盖
                  </button>
                  <button onClick={deleteRuntimeOverride} disabled={!activeTemplateKey}
                    className="rounded-lg bg-red-500/10 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-40">
                    删除覆盖
                  </button>
                </div>
              </div>
            </details>
          </Card>

          {templateWorkspace === 'chat' ? (
            <>
          <Card className="p-3">
            <div className="text-xs font-medium text-slate-300 mb-2">Canvas 编排</div>
            <select value={chatType} onChange={e => setChatType(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200">
              <option value="group">高亮群聊路径</option>
              <option value="private">高亮私聊路径</option>
            </select>
            <div className="mt-2 text-[11px] text-slate-600">source: {flowSource || '-'} · 灰色节点不参与当前路径</div>
            <button onClick={autoLayoutFlow} className="mt-3 w-full px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">自动布局</button>
          </Card>

          <Card className="overflow-hidden">
            <details>
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800/40">添加节点</summary>
              <div className="space-y-3 border-t border-slate-800 p-3">
                <div>
                  <div className="mb-2 text-[11px] text-slate-600">添加节点后在右侧选择模板；新节点会接到当前选中节点后面。</div>
                  <button onClick={addTemplateNode} className="w-full px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-200">添加模板节点</button>
                </div>
                <div>
                  <select value={runtimeToAdd} onChange={e => setRuntimeToAdd(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                    {PROMPT_V2_RUNTIME_NODES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
                  </select>
                  <button onClick={addRuntimeNode} className="mt-2 w-full px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-200">添加运行时</button>
                </div>
              </div>
            </details>
          </Card>

          <Card className="overflow-hidden">
            <details>
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800/40">当前路径顺序</summary>
              <div className="space-y-1 max-h-56 overflow-auto border-t border-slate-800 p-3 prompt-flow-scrollbar">
                {orderedNodes.map((node, idx) => (
                  <button key={node.id} onClick={() => selectNode(node)}
                    className={`w-full text-left rounded px-2 py-1.5 text-xs ${selectedNode?.id === node.id ? 'bg-emerald-500/15 text-emerald-300' : 'hover:bg-slate-800 text-slate-400'}`}>
                    <span className="text-slate-600 mr-2">{idx + 1}</span>{node.label || node.id}
                  </button>
                ))}
              </div>
            </details>
          </Card>
            </>
          ) : null}
        </div>

        <div data-testid="prompt-v2-canvas-column" className="min-w-0 xl:h-full">
          {templateWorkspace === 'chat' ? (
            <>
              <div className="hidden lg:block h-full">
                <PromptFlowCanvas
                  flow={flow}
                  chatType={chatType}
                  selectedNodeId={selectedNode?.id || ''}
                  viewport={canvasViewport}
                  onViewportChange={setCanvasViewport}
                  onResetViewport={() => setCanvasViewport({ x: 24, y: 24, zoom: 1 })}
                  onSelectNode={selectNode}
                  onMoveNode={moveNode}
                  onDeleteNode={deleteNode}
                  onConnectNode={connectNode}
                  selectedEdgeKey={selectedEdgeKey}
                  onSelectEdge={selectEdge}
                />
              </div>
              <PromptFlowMobileList
                flow={flow}
                chatType={chatType}
                selectedNodeId={selectedNode?.id || ''}
                onSelectNode={selectNode}
                selectedEdgeKey={selectedEdgeKey}
                onSelectEdge={selectEdge}
                onDeleteEdge={deleteEdge}
              />
            </>
          ) : (
            <Card className="flex min-h-[42rem] flex-col p-4 xl:h-full xl:min-h-0">
              <div className="flex items-start justify-between gap-4 mb-4">
                <div className="min-w-0">
                  <div className="text-xs text-slate-500 mb-1">{templateWorkspace === 'tools' ? '工具提示词正文' : '任务提示词正文'}</div>
                  <h2 className="text-lg font-semibold text-emerald-300 truncate">{promptV2TemplateTitle(selectedResourceTemplate) || '未选择模板'}</h2>
                  <div className="mt-1 text-xs text-slate-500 truncate">
                    {templateWorkspace === 'tools' ? (
                      <>
                        工具: <span className="font-mono text-slate-400">{promptV2ToolName(selectedToolTemplate) || '-'}</span>
                        <span className="mx-2 text-slate-700">/</span>
                        schema: <span className="font-mono text-slate-400">{schemaName}</span>
                      </>
                    ) : (
                      <>模板: <span className="font-mono text-slate-400">{selectedTaskTemplateKey || '-'}</span></>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Badge tone={detail?.source === 'runtime' ? 'emerald' : 'slate'}>{detail?.source || 'default'}</Badge>
                  <Badge tone="blue">{detail?.sha256?.slice(0, 12) || '-'}</Badge>
                </div>
              </div>
              <div className="mb-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[11px] text-slate-500 truncate">
                {activeTemplateKey ? detail?.active_path || '' : '从左侧选择一个工具模板'}
              </div>
              <textarea aria-label="模板正文" value={content} onChange={e => setContent(e.target.value)}
                className="prompt-flow-scrollbar min-h-[35rem] w-full flex-1 resize-none rounded-lg border border-slate-800 bg-slate-950 p-4 font-mono text-sm leading-relaxed text-slate-300 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 xl:min-h-0" />
            </Card>
          )}
        </div>

        <div data-testid="prompt-v2-side-panel" className="min-w-0 space-y-3 xl:h-full xl:overflow-y-auto xl:pr-1 prompt-flow-scrollbar">
          <Card className="p-4">
            {templateWorkspace === 'chat' ? (
              <>
                {selectedEdge && (
                  <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                    <div className="text-xs text-amber-300 mb-1">当前连线</div>
                    <div className="text-xs text-slate-300 break-all">{selectedEdge.from} → {selectedEdge.to}</div>
                    <label className="mt-3 block text-xs text-slate-500">连线作用范围
                      <select value={flowScopeValue(selectedEdge)} onChange={e => updateEdgeScope(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5 text-xs text-slate-200">
                        <option value="all">全局</option>
                        <option value="group">仅群聊</option>
                        <option value="private">仅私聊</option>
                      </select>
                    </label>
                    <button onClick={() => deleteEdge(selectedEdgeKey)}
                      className="mt-3 w-full rounded-lg bg-red-500/10 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/20">
                      删除连线
                    </button>
                  </div>
                )}
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="min-w-0">
                    <div className="text-xs text-slate-500 mb-1">当前节点</div>
                    <h2 className="text-sm font-medium text-emerald-300 truncate">{selectedNode?.label || '未选择节点'}</h2>
                    <div className="text-[11px] text-slate-600 truncate">节点编号: {selectedNode?.id || '-'}</div>
                  </div>
                  {selectedNode && <Badge tone={selectedNode.type === 'template' ? 'emerald' : 'blue'}>{selectedNode.type === 'template' ? '模板' : '运行时数据'}</Badge>}
                </div>

                {selectedNode ? (
                  <div className="space-y-3">
                    <label className="block text-xs text-slate-500">节点名称
                      <input aria-label="节点名称" value={selectedNode.label || ''} onChange={e => updateNode(selectedNode.id, { label: e.target.value })}
                        className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200" />
                    </label>
                    <label className="block text-xs text-slate-500">作用范围
                      <select value={selectedNode.chat_types?.[0] || 'all'} onChange={e => updateSelectedScope(e.target.value)}
                        className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                        <option value="all">全局</option>
                        <option value="group">仅群聊</option>
                        <option value="private">仅私聊</option>
                      </select>
                    </label>
                    {selectedNode.type === 'template' ? (
                      <label className="block text-xs text-slate-500">节点模板切换
                        <select value={selectedTemplateKey || ''} onChange={e => updateSelectedTemplate(e.target.value)}
                          className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                          {chatTemplates.map(t => <option key={t.template_key} value={t.template_key}>{t.name || t.template_key}</option>)}
                        </select>
                        <span className="mt-1 block text-[11px] text-slate-600">当前节点使用的模板: {selectedTemplateKey || '-'}</span>
                      </label>
                    ) : (
                      <label className="block text-xs text-slate-500">运行时数据
                        <select value={selectedNode.runtime_key || ''} onChange={e => updateSelectedRuntime(e.target.value)}
                          className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                          {PROMPT_V2_RUNTIME_NODES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
                        </select>
                        <span className="mt-1 block text-[11px] text-slate-600">由 compiler 注入真实上下文，不直接写在模板文件里。</span>
                      </label>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-slate-600">点击画布节点开始编辑。</div>
                )}
              </>
            ) : (
              <>
                <div className="text-xs text-slate-500 mb-1">{templateWorkspace === 'tools' ? '工具元信息' : '任务元信息'}</div>
                <h2 className="text-sm font-medium text-emerald-300 truncate">{promptV2TemplateTitle(selectedResourceTemplate) || '未选择模板'}</h2>
                <div className="mt-1 text-[11px] text-slate-600">模板: {activeTemplateKey || '-'}</div>
                {templateWorkspace === 'tools' && <div className="mt-1 text-[11px] text-slate-600">工具: {promptV2ToolName(selectedToolTemplate) || '-'}</div>}
                {templateWorkspace === 'tools' && <div className="mt-1 text-[11px] text-slate-600">类别: {activeToolSchema?.category || '-'}</div>}
                {templateWorkspace === 'tools' && <div className="mt-1 text-[11px] text-slate-600">风险: {activeToolSchema?.risk_level || '-'}</div>}
                <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[11px] text-slate-500 leading-relaxed">
                  {templateWorkspace === 'tools' ? (schemaDescription || '当前模板没有匹配到真实工具 schema。') : (detail?.description || '任务模板用于离线/二次 LLM 调用，不直接暴露为 API tools schema。')}
                </div>
              </>
            )}
          </Card>

          {templateWorkspace === 'chat' ? (
            <Card className="p-4">
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-slate-300">模板内容</div>
                  <div className="text-[11px] text-slate-600 truncate">{activeTemplateKey ? detail?.active_path || '' : '当前节点不是模板文件'}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {selectedNode?.type !== 'runtime' && activeTemplateKey && (
                    <button onClick={() => setIsLargeTemplateEditorOpen(true)}
                      className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700">
                      打开大窗编辑
                    </button>
                  )}
                  <Badge tone={detail?.source === 'runtime' ? 'emerald' : 'slate'}>{detail?.source || 'default'}</Badge>
                  <Badge tone="blue">{detail?.sha256?.slice(0, 12) || '-'}</Badge>
                </div>
              </div>
              {selectedNode?.type === 'runtime' ? (
                <div className="min-h-[340px] rounded-lg bg-slate-950 border border-slate-800 p-4">
                  <div className="text-xs text-slate-500 mb-2">运行时注入节点</div>
                  <div className="text-lg text-slate-200 mb-1">{runtimeNodeLabel(selectedNode.runtime_key)}</div>
                  <div className="text-sm text-slate-500">这个节点由 compiler 注入真实运行数据，不在模板文件中编辑。</div>
                </div>
              ) : (
                <textarea aria-label="当前节点模板内容" value={content} onChange={e => setContent(e.target.value)}
                  className="prompt-flow-scrollbar w-full min-h-[420px] xl:min-h-[520px] p-4 rounded-lg bg-slate-950 border border-slate-800 text-sm font-mono text-slate-300 leading-relaxed resize-y focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none" />
              )}
            </Card>
          ) : templateWorkspace === 'tools' ? (
            <Card className="p-4">
              <div className="flex items-center justify-between gap-2 mb-3">
                <div>
                  <div className="text-xs font-medium text-slate-300">工具 Schema JSON</div>
                  <div className="text-[11px] text-slate-600">真实工具 Schema · {schemaToolName || '-'}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={toolSchemaConfig?.override_present ? 'emerald' : activeToolSchema?.source === 'package' ? 'blue' : 'amber'}>
                    {toolSchemaConfig?.override_present ? 'override' : activeToolSchema?.source || 'missing'}
                  </Badge>
                  <button onClick={resetToolSchema} disabled={!schemaToolName || !toolSchemaConfig?.override_present}
                    className="rounded-lg bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40">
                    重置
                  </button>
                  <button onClick={saveToolSchema} disabled={!schemaToolName}
                    className="rounded-lg bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40">
                    保存
                  </button>
                </div>
              </div>
              {schemaError && <div className="mb-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{schemaError}</div>}
              {schemaToolName ? (
                <div className="space-y-3">
                  <textarea aria-label="工具 Schema JSON" value={schemaEditText}
                    onChange={e => setSchemaEditText(e.target.value)}
                    className="prompt-flow-scrollbar h-[430px] w-full resize-none rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-300 outline-none focus:border-emerald-500" />
                  {schemaJson && (
                    <details className="rounded-lg border border-slate-800 bg-slate-950">
                      <summary className="cursor-pointer px-3 py-2 text-xs text-slate-400 hover:bg-slate-800/50">生效预览</summary>
                      <pre className="max-h-72 overflow-auto prompt-flow-scrollbar whitespace-pre-wrap break-words border-t border-slate-800 p-3 text-[11px] leading-relaxed text-slate-300">{schemaJson}</pre>
                    </details>
                  )}
                </div>
              ) : (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                  没有找到该工具的真实 schema。请检查模板 frontmatter 的 tool_name 是否和工具注册名一致。
                </div>
              )}
            </Card>
          ) : (
            <Card className="p-4">
              <div className="text-xs font-medium text-slate-300 mb-3">任务模板来源</div>
              <div className="space-y-2 text-[11px] text-slate-500">
                <div>默认路径: <span className="font-mono text-slate-400">{detail?.default_path || '-'}</span></div>
                <div>运行时路径: <span className="font-mono text-slate-400">{detail?.runtime_path || '-'}</span></div>
                <div>生效来源: <span className="font-mono text-slate-400">{detail?.source || '-'}</span></div>
              </div>
            </Card>
          )}

          <Card className="p-4">
            <div className="text-xs font-medium text-slate-300 mb-3">身份变量配置</div>
            <div className="space-y-3">
              <label className="block text-xs text-slate-500">character_name / name_hint
                <input value={identitySettings['bot.character_name'] || ''}
                  onChange={e => setIdentitySettings(prev => ({ ...prev, 'bot.character_name': e.target.value }))}
                  onBlur={e => saveIdentitySetting('bot.character_name', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-emerald-500" />
              </label>
              <label className="block text-xs text-slate-500">alias_names / bot_aliases
                <textarea value={identitySettings['bot.alias_names'] || ''}
                  onChange={e => setIdentitySettings(prev => ({ ...prev, 'bot.alias_names': e.target.value }))}
                  onBlur={e => saveIdentitySetting('bot.alias_names', e.target.value)}
                  className="prompt-flow-scrollbar mt-1 h-20 w-full resize-none rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-200 outline-none focus:border-emerald-500" />
              </label>
            </div>
          </Card>

          <Card className="p-4">
            <div className="text-xs font-medium text-slate-300 mb-2">全局可插入变量白名单</div>
            <div className="flex flex-wrap gap-2 max-h-48 overflow-auto prompt-flow-scrollbar">
              {variables.map(v => (
                <span key={v.name} title={`${v.description || ''}${v.example ? ` · 示例: ${v.example}` : ''}`} className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs">
                  <code className="text-emerald-300">{`{{ ${v.name} }}`}</code>
                  <span className="text-slate-500">{v.description}</span>
                </span>
              ))}
              {!variables.length && <span className="text-xs text-slate-600">没有开放变量</span>}
            </div>
          </Card>
        </div>
      </div>

      {isLargeTemplateEditorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-6 backdrop-blur-sm">
          <div data-testid="prompt-large-template-editor" className="flex h-[86vh] w-full max-w-6xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl shadow-black/50">
            <div className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
              <div className="min-w-0">
                <div className="text-xs text-slate-500">大窗编辑模板</div>
                <h2 className="mt-1 truncate text-lg font-semibold text-emerald-300">{detail?.name || activeTemplateKey || '模板内容'}</h2>
                <div className="mt-1 truncate text-[11px] text-slate-600">{detail?.active_path || ''}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone={detail?.source === 'runtime' ? 'emerald' : 'slate'}>{detail?.source || 'default'}</Badge>
                <Badge tone="blue">{detail?.sha256?.slice(0, 12) || '-'}</Badge>
                <button onClick={save} disabled={!activeTemplateKey}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50">
                  保存模板
                </button>
                <button onClick={() => setIsLargeTemplateEditorOpen(false)}
                  className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700">
                  关闭大窗
                </button>
              </div>
            </div>
            <textarea aria-label="大窗模板内容" value={content} onChange={e => setContent(e.target.value)}
              className="prompt-flow-scrollbar m-5 flex-1 resize-none rounded-lg border border-slate-800 bg-slate-950 p-5 font-mono text-sm leading-relaxed text-slate-200 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500" />
            <div className="border-t border-slate-800 px-5 py-3 text-[11px] text-slate-600">
              这里编辑的是同一份模板正文；关闭大窗后右侧小编辑框会保持同步。
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function EffectivePromptPreviewPage() {
  const [form, setForm] = useState({
    engine: 'prompt',
    chat_type: 'private',
    session_id: '',
    user_id: '',
    group_id: '',
    sender_name: '',
    prompt_key: '',
    mode: 'shadow',
    user_input: '你好',
  })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const update = (key, value) => setForm(prev => ({ ...prev, [key]: value }))
  const run = () => {
    setLoading(true)
    api.post('/prompt/effective-preview', form)
      .then(r => setResult(r.data))
      .catch(e => alert(e.response?.data?.detail || '预览失败'))
      .finally(() => setLoading(false))
  }
  const initialRunRef = useRef(run)
  useEffect(() => {
    const timer = window.setTimeout(() => { initialRunRef.current() }, 0)
    return () => window.clearTimeout(timer)
  }, [])
  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-2xl font-bold">Prompt Runtime</h1>
            <Badge tone="emerald">primary</Badge>
          </div>
          <p className="text-slate-500 text-sm">按 chat/session 调用真实 compiler，还原本轮实际发给模型的 messages、tools schema、section hash 和审计信息</p>
        </div>
        <NavLink to="/prompt-templates" className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300">
          模板
        </NavLink>
      </div>
      <Card className="p-4 mb-4 border-emerald-500/20 bg-emerald-500/5">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <MiniStat label="线上模式" value="prompt" tone="emerald" />
          <MiniStat label="当前页面默认" value="prompt" tone="emerald" />
          <MiniStat label="shadow / managed" value="已下线" tone="amber" />
          <MiniStat label="当前输入" value="只作为 user event" />
        </div>
      </Card>
      <Card className="p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <label className="text-xs text-slate-500">engine
            <select value={form.engine} onChange={e => update('engine', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
              <option value="prompt">prompt - 当前运行时</option>
              <option value="v2">v2 - 兼容别名</option>
              <option value="v1">v1 - 已下线</option>
            </select>
          </label>
          <label className="text-xs text-slate-500">chat_type
            <select value={form.chat_type} onChange={e => update('chat_type', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
              <option value="private">private</option>
              <option value="group">group</option>
            </select>
          </label>
          {form.engine === 'v1' ? (
            <label className="text-xs text-slate-500">v1 mode
              <select value={form.mode} onChange={e => update('mode', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
                <option value="shadow">shadow</option>
                <option value="managed">managed</option>
                <option value="legacy">legacy</option>
              </select>
            </label>
          ) : (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
              <div className="text-xs text-slate-500">prompt mode</div>
              <div className="text-sm text-emerald-300 mt-1">无 shadow / managed</div>
            </div>
          )}
          <label className="text-xs text-slate-500">session_id
            <input value={form.session_id} onChange={e => update('session_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">group_id
            <input value={form.group_id} onChange={e => update('group_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">user_id
            <input value={form.user_id} onChange={e => update('user_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">sender_name
            <input value={form.sender_name} onChange={e => update('sender_name', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">prompt_key
            <input value={form.prompt_key} onChange={e => update('prompt_key', e.target.value)} placeholder={form.chat_type === 'group' ? 'group_chat' : 'private_chat'} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
        </div>
        <label className="block text-xs text-slate-500 mt-3">user_input
          <textarea value={form.user_input} onChange={e => update('user_input', e.target.value)} className="mt-1 w-full h-24 bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200 resize-none" />
        </label>
        <div className="mt-3 flex gap-2">
          <button onClick={run} disabled={loading} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">{loading ? '生成中...' : '生成预览'}</button>
          {result?.recent_agent_run_id && <NavLink to={`/agent-runs/${result.recent_agent_run_id}`} className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">最近运行</NavLink>}
        </div>
      </Card>
      <Card className="p-3 mb-4 border-blue-500/20 bg-blue-500/5">
        <div className="flex gap-2">
          <span className="text-xs text-blue-400 mt-0.5">ℹ</span>
          <div className="text-xs text-slate-500">
            这是预览构造结果（根据 session_id 读取历史、画像和运行时工具说明模拟构造），<strong>不是从真实模型调用链路抓取的最终 payload</strong>。
            真实发送的 request 请以 <NavLink to="/llm-api-logs" className="text-blue-400 underline">LLM API 日志</NavLink> 中的 request_json 为准。
          </div>
        </div>
      </Card>
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <MiniStat label="prompt_source" value={result.prompt_source || '-'} />
            <MiniStat label="prompt_mode" value={result.prompt_mode || '-'} />
            <MiniStat label="prompt_key" value={result.prompt_key || '-'} />
            <MiniStat label="prompt_sha" value={(result.prompt_sha256 || '').slice(0, 16) || '-'} />
            <MiniStat label="messages" value={(result.messages || []).length} />
          </div>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Prompt 来源</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <JsonBlock value={result.prompt_runtime_path || '-'} className="max-h-24" />
              <JsonBlock value={result.prompt_default_path || '-'} className="max-h-24" />
            </div>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Messages</h2>
            <div className="space-y-1">{(result.messages || []).map((msg, i) => <MessageAccordion key={i} message={msg} index={i} />)}</div>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">上下文与工具</h2>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              <RawJsonAccordion label="runtime_context" text={result.runtime_context || ''} defaultOpen />
              <RawJsonAccordion label="persona_reference" text={result.persona_reference || ''} />
              <RawJsonAccordion label="conversation_context" text={result.history_context || ''} />
              <RawJsonAccordion label="运行时工具说明" text={result.runtime_tool_prompt || ''} />
            </div>
          </Card>
          <ToolSchemaPreview schemas={result.tool_schemas || result.effective_tool_schemas || []} disabledTools={result.disabled_tools || {}} />
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">完整 request_json</h2>
            <JsonBlock value={result.request_json} className="max-h-[700px]" />
          </Card>
          {(result.recent_llm_api_logs || []).length > 0 && <LLMApiRequestLogsBlock logs={result.recent_llm_api_logs} />}
        </div>
      )}
    </div>
  )
}

function ToolSchemaPreview({ schemas = [], disabledTools = {} }) {
  if (!schemas.length && !Object.keys(disabledTools || {}).length) return null
  const enabledNames = schemas.map(s => s.function?.name || s.name).filter(Boolean)
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-sm font-medium text-slate-300">实际 tools schema</h2>
        <div className="text-xs text-slate-500">启用 {enabledNames.length} 个 · request_json.tools 同步使用这里的结构</div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
        {schemas.map((schema, i) => {
          const fn = schema.function || {}
          const required = fn.parameters?.required || []
          const propNames = Object.keys(fn.parameters?.properties || {})
          return (
            <details key={`${fn.name || i}`} className="border border-slate-700/50 rounded-lg">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs flex items-center gap-2 min-w-0">
                <span className="font-mono text-emerald-300 truncate">{fn.name || schema.name}</span>
                {schema.source && <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-slate-500">{schema.source}</span>}
                {schema.risk_level && <span className="text-slate-600">{schema.risk_level}</span>}
                <span className="ml-auto text-slate-600">{propNames.length} params</span>
              </summary>
              <div className="p-3 border-t border-slate-700/50 space-y-2">
                <p className="text-xs leading-relaxed text-slate-400">{fn.description || '-'}</p>
                <div className="flex flex-wrap gap-1">
                  {propNames.map(name => (
                    <span key={name} className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${required.includes(name) ? 'bg-amber-500/15 text-amber-300' : 'bg-slate-800 text-slate-500'}`}>{name}</span>
                  ))}
                </div>
                <JsonBlock value={schema} className="max-h-72" />
              </div>
            </details>
          )
        })}
      </div>
      {Object.keys(disabledTools || {}).length > 0 && (
        <RawJsonAccordion label="禁用工具原因" text={JSON.stringify(disabledTools, null, 2)} />
      )}
    </Card>
  )
}
