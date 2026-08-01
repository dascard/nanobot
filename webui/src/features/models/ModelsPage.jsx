import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Blocks,
  Cable,
  ChevronRight,
  CircleAlert,
  Cpu,
  Database,
  GitBranch,
  RefreshCw,
} from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  PageHeader,
  Spinner,
  ViewportPage,
} from '../../components/ui'
import { KtDriversPanel } from './KtDriversPanel'
import { LocalComponentsPanel } from './LocalComponentsPanel'
import { ModelCatalogPanel } from './ModelCatalogPanel'
import { ProviderConnectionsPanel } from './ProviderConnectionsPanel'
import { RouteBindingsPanel } from './RouteBindingsPanel'
import { formatApiError } from './modelConsoleUi'

const WORKSPACES = [
  { key: 'connections', label: '连接', detail: 'Endpoint 与认证', icon: Cable },
  { key: 'catalog', label: '模型目录', detail: '模型默认配置', icon: Database },
  { key: 'bindings', label: '路由绑定', detail: '模型选择与局部覆盖', icon: GitBranch },
  { key: 'kt', label: 'KT / Codex', detail: 'Driver、OAuth 与用量', icon: Blocks },
  { key: 'local', label: '本地组件', detail: '分类与向量模型', icon: Cpu },
]

const RESOURCE_LABELS = {
  status: '运行状态',
  providers: 'Provider Connections',
  defaults: '模型默认配置',
  bindings: 'Route Bindings',
  nativeTools: 'KT Native Tools',
  codexStatus: 'Codex OAuth 状态',
}

function WorkflowStep({ index, title, description, count, last }) {
  return (
    <li className="flex shrink-0 items-center">
      <div className="flex min-w-[10.5rem] items-center gap-2.5 px-3 py-2.5 sm:min-w-[11.5rem]">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-indigo-500/30 bg-indigo-500/10 font-mono text-[10px] font-semibold text-indigo-200">
          {index}
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-xs font-medium text-slate-200">
            {title}
            {count != null && <span className="font-mono text-[10px] text-slate-600">{count}</span>}
          </span>
          <span className="mt-0.5 block truncate text-[10px] text-slate-500">{description}</span>
        </span>
      </div>
      {!last && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-700" aria-hidden="true" />}
    </li>
  )
}

export function ModelsPage() {
  const [workspace, setWorkspace] = useState('connections')
  const [status, setStatus] = useState(null)
  const [providers, setProviders] = useState([])
  const [driverTypes, setDriverTypes] = useState([])
  const [modelDefaults, setModelDefaults] = useState([])
  const [driverSchemas, setDriverSchemas] = useState([])
  const [bindings, setBindings] = useState([])
  const [nativeTools, setNativeTools] = useState([])
  const [codexStatus, setCodexStatus] = useState(null)
  const [errors, setErrors] = useState({})
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const reload = useCallback(async () => {
    setRefreshing(true)
    const requests = [
      ['status', api.get('/models/status')],
      ['providers', api.get('/models/providers')],
      ['defaults', api.get('/models/defaults')],
      ['bindings', api.get('/models/bindings')],
      ['nativeTools', api.get('/models/kt/native-tools')],
      ['codexStatus', api.get('/models/codex/status')],
    ]
    const results = await Promise.allSettled(requests.map(([, request]) => request))
    const nextErrors = {}

    results.forEach((result, index) => {
      const key = requests[index][0]
      if (result.status === 'rejected') {
        nextErrors[key] = formatApiError(result.reason, `${RESOURCE_LABELS[key]}加载失败`)
        return
      }
      const data = result.value.data || {}
      if (key === 'status') setStatus(data)
      if (key === 'providers') {
        setProviders(data.providers || [])
        setDriverTypes(data.driver_types || [])
      }
      if (key === 'defaults') {
        setModelDefaults(data.defaults || [])
        setDriverSchemas(data.driver_schemas || [])
      }
      if (key === 'bindings') setBindings(data.bindings || [])
      if (key === 'nativeTools') setNativeTools(data.tools || [])
      if (key === 'codexStatus') setCodexStatus(data)
    })

    setErrors(nextErrors)
    setLoadedOnce(true)
    setRefreshing(false)
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(reload, 0)
    return () => window.clearTimeout(timer)
  }, [reload])

  const bindingCount = useMemo(
    () => bindings.filter(item => item.effective_binding).length,
    [bindings],
  )
  const errorEntries = Object.entries(errors)

  if (!loadedOnce) return <Spinner />

  return (
    <ViewportPage className="min-w-0">
      <PageHeader
        title="模型运行控制台"
        description="Provider 保存连接；模型目录保存默认配置；Route Binding 直接选模型并只记录业务差异，最终由 KT Runtime 合并与执行。"
        meta={(
          <>
            <span>{providers.length} 个连接</span>
            <span>{modelDefaults.length} 个已配置模型</span>
            <span>{bindingCount}/{bindings.length} 条有效绑定</span>
            <span>Codex {codexStatus?.authenticated && !codexStatus?.expired ? '已登录' : '未就绪'}</span>
          </>
        )}
        actions={(
          <ActionButton type="button" onClick={reload} disabled={refreshing} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            {refreshing ? '刷新中...' : '刷新状态'}
          </ActionButton>
        )}
      />

      <div className="mb-4 shrink-0 overflow-x-auto rounded-lg border border-slate-800 bg-slate-900" aria-label="模型运行配置链">
        <ol className="flex min-w-max items-center divide-x divide-transparent px-1">
          <WorkflowStep index="01" title="Provider Connection" description="Endpoint · Auth · KT Driver" count={providers.length} />
          <WorkflowStep index="02" title="Model Catalog" description="Defaults · Cost · Modality" count={modelDefaults.length} />
          <WorkflowStep index="03" title="Route Binding" description="Models · Overrides · Fallback" count={bindingCount} />
          <WorkflowStep index="04" title="KT Runtime" description="Driver Resolution · Circuit Breaker" count={driverSchemas.length} last />
        </ol>
      </div>

      {errorEntries.length > 0 && (
        <div role="alert" className="mb-4 shrink-0 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
          <div className="flex items-start gap-2">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
            <div className="min-w-0">
              <div className="text-xs font-medium text-amber-200">部分数据未加载，其他工作区仍可使用</div>
              <ul className="mt-1 space-y-0.5 text-[11px] leading-4 text-amber-300/80">
                {errorEntries.map(([key, message]) => <li key={key}><span className="font-medium">{RESOURCE_LABELS[key]}：</span>{message}</li>)}
              </ul>
            </div>
          </div>
        </div>
      )}

      <div className="mb-4 shrink-0 overflow-x-auto border-b border-slate-800" role="tablist" aria-label="模型控制台工作区">
        <div className="flex min-w-max gap-1 pb-2">
          {WORKSPACES.map(item => {
            const Icon = item.icon
            const selected = workspace === item.key
            return (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`model-workspace-${item.key}`}
                onClick={() => setWorkspace(item.key)}
                className={`group flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-indigo-400 ${selected ? 'border-indigo-500/30 bg-indigo-500/10 text-indigo-100' : 'border-transparent text-slate-500 hover:border-slate-800 hover:bg-slate-900 hover:text-slate-200'}`}
              >
                <Icon className={`h-3.5 w-3.5 ${selected ? 'text-indigo-300' : 'text-slate-600 group-hover:text-slate-400'}`} aria-hidden="true" />
                <span>
                  <span className="block text-xs font-medium">{item.label}</span>
                  <span className="mt-0.5 hidden text-[10px] text-slate-600 sm:block">{item.detail}</span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div id={`model-workspace-${workspace}`} role="tabpanel" tabIndex="0" className="viewport-scroll min-h-0 min-w-0 flex-1 overflow-y-auto outline-none">
        {workspace === 'connections' && (
          <ProviderConnectionsPanel
            providers={providers}
            driverTypes={driverTypes}
            nativeTools={nativeTools}
            onChanged={reload}
            onOpenKt={() => setWorkspace('kt')}
          />
        )}
        {workspace === 'bindings' && (
          <RouteBindingsPanel
            bindings={bindings}
            modelDefaults={modelDefaults}
            statusRoutes={status?.routes || {}}
            onChanged={reload}
          />
        )}
        {workspace === 'kt' && (
          <KtDriversPanel
            driverSchemas={driverSchemas}
            nativeTools={nativeTools}
            codexStatus={codexStatus}
            onChanged={reload}
          />
        )}
        {workspace === 'catalog' && <ModelCatalogPanel providers={providers} modelDefaults={modelDefaults} onChanged={reload} />}
        {workspace === 'local' && <LocalComponentsPanel components={status?.local_components || {}} />}
      </div>
    </ViewportPage>
  )
}
