import { useEffect, useState } from 'react'

import { api } from '../../api'
import { ActionButton, Badge, Card, Field, Modal, Spinner, Toolbar } from '../../components/ui'

// ── Models ──
export function ModelsPage() {
  const tabs = [
    { key: 'catalog', label: '模型列表' },
    { key: 'routes', label: '路由配置' },
    { key: 'providers', label: '供应商' },
    { key: 'local', label: '本地组件' },
  ]
  const [tab, setTab] = useState('catalog')
  const [status, setStatus] = useState(null)
  const [testResult, setTestResult] = useState({})
  const [localResult, setLocalResult] = useState({})
  const load = () => api.get('/models/status').then(r => setStatus(r.data))
  useEffect(() => { load() }, [])

  const handleTest = async (key, mode = 'ping') => {
    setTestResult(p => ({ ...p, [key]: { loading: true, mode } }))
    try {
      const config = mode === 'vision' ? { params: { mode } } : undefined
      const r = await api.post(`/models/routes/${key}/test`, null, config)
      setTestResult(p => ({ ...p, [key]: r.data }))
    }
    catch (e) { setTestResult(p => ({ ...p, [key]: { ok: false, error: e.message } })) }
  }
  const handleLocal = async (comp, action) => {
    setLocalResult(p => ({ ...p, [comp]: { loading: true } }))
    try { const r = await api.post(`/models/local/${comp}/${action}`); setLocalResult(p => ({ ...p, [comp]: r.data })) }
    catch (e) { setLocalResult(p => ({ ...p, [comp]: { ok: false, error: e.message } })) }
  }

  if (!status) return <Spinner />

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">模型路由</h1>
      <div className="flex gap-2 mb-6 border-b border-slate-800 pb-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'catalog' && <ModelCatalogTab providers={status.providers || []} />}
      {tab === 'routes' && <RoutesTab routes={status.routes || {}} providers={status.providers || []} testResult={testResult} onTest={handleTest} onSaved={load} />}
      {tab === 'providers' && <ProvidersTab providers={status.providers || []} />}
      {tab === 'local' && <LocalComponentsTab components={status.local_components || {}} localResult={localResult} onAction={handleLocal} />}
    </div>
  )
}

// ── Tab 1: 模型列表 ──
function ModelCatalogTab({ providers }) {
  const [catalog, setCatalog] = useState([])
  const [routeRefs, setRouteRefs] = useState([])
  const [catProvider, setCatProvider] = useState('')
  const [catQ, setCatQ] = useState('')
  const [catLoading, setCatLoading] = useState(false)
  const [refreshResult, setRefreshResult] = useState(null)
  const loadCatalog = () => {
    setCatLoading(true)
    const params = { limit: 200 }
    if (catProvider) params.provider = catProvider
    if (catQ) params.q = catQ
    api.get('/models/catalog', { params }).then(r => setCatalog(r.data.catalog || [])).catch(() => {}).finally(() => setCatLoading(false))
  }
  useEffect(() => {
    const id = setTimeout(loadCatalog, 0)
    return () => clearTimeout(id)
  }, [catProvider, catQ])
  useEffect(() => { api.get('/models/route-references').then(r => setRouteRefs(r.data.route_references || [])).catch(() => {}) }, [])
  const doRefresh = () => {
    setRefreshResult({ loading: true })
    api.post('/models/catalog/refresh').then(r => { setRefreshResult(r.data); loadCatalog() }).catch(() => setRefreshResult(null))
  }
  // 用 route_references 标记哪些 catalog 模型被路由使用
  const usedBy = {}
  routeRefs.forEach(ref => {
    if (ref.verified) {
      const k = ref.id
      if (!usedBy[k]) usedBy[k] = []
      usedBy[k].push(ref.route_key)
    }
  })
  const unverified = routeRefs.filter(ref => !ref.verified)
  return (
    <div>
      {/* 供应商模型列表 */}
      <p className="text-slate-500 text-sm mb-2">从供应商 /v1/models 同步的真实模型列表。</p>
      <Toolbar className="mb-3">
        <Field id="model-catalog-query" label="搜索模型" className="w-full sm:w-56">
          <input id="model-catalog-query" value={catQ} onChange={e => setCatQ(e.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs" />
        </Field>
        <Field id="model-catalog-provider" label="供应商" className="w-full sm:w-56">
          <select id="model-catalog-provider" value={catProvider} onChange={e => setCatProvider(e.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs">
            <option value="">全部供应商</option>
            {(providers || []).map(p => (
              <option key={p.id} value={p.id}>{p.id}{p.legacy_aliases?.length > 0 ? ` (原 ${p.legacy_aliases.join(',')})` : ''}</option>
            ))}
          </select>
        </Field>
        <ActionButton tone="emerald" onClick={doRefresh} disabled={refreshResult?.loading} className="mb-0.5">刷新供应商模型</ActionButton>
      </Toolbar>
      {refreshResult && !refreshResult.loading && (
        <div className="mb-3 text-xs">
          {refreshResult.results?.map(r => (
            <span key={r.provider} className={`mr-3 ${r.ok ? 'text-emerald-400' : 'text-red-400'}`}>{r.provider}: {r.ok ? `${r.models.length} 个` : r.error}</span>
          ))}
        </div>
      )}
      {catalog.length === 0 && !catLoading && (
        <div className="text-xs text-slate-500 mb-3 p-3 bg-slate-900 rounded-lg">
          模型列表为空，请点击「刷新供应商模型」从 provider /v1/models 同步。
        </div>
      )}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
        <table className="min-w-[520px] w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3">模型</th><th className="py-2 px-3">供应商</th><th className="py-2 px-3">路由使用</th>
          </tr></thead>
          <tbody>
            {catLoading && <tr><td colSpan={3} className="py-4 text-center text-slate-500">加载中...</td></tr>}
            {catalog.map(m => (
              <tr key={m.id || m.model} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 px-3 font-mono text-slate-200">
                  {m.model}
                  {m.stale && <span className="ml-1 px-1 py-0.5 rounded text-[10px] bg-red-900/30 text-red-400">stale</span>}
                </td>
                <td className="py-2 px-3 text-slate-400">{m.provider}</td>
                <td className="py-2 px-3 text-slate-400 text-xs">{(usedBy[m.id] || []).join(', ') || <span className="text-slate-600">-</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Card>

      {/* 路由引用异常：未在 provider catalog 中确认的模型 */}
      {unverified.length > 0 && (
        <div className="mt-6">
          <h2 className="text-sm font-medium text-amber-400 mb-2">路由引用异常</h2>
          <p className="text-xs text-slate-500 mb-2">以下模型被路由引用，但未在供应商 /v1/models 中找到——可能不存在于该 provider。</p>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
            <table className="min-w-[640px] w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="py-2 px-3">模型</th><th className="py-2 px-3">供应商</th><th className="py-2 px-3">路由</th><th className="py-2 px-3">类型</th>
              </tr></thead>
              <tbody>
                {unverified.map(ref => (
                  <tr key={ref.id} className="border-b border-slate-800/50 bg-amber-500/5">
                    <td className="py-2 px-3 font-mono text-amber-300">{ref.model}</td>
                    <td className="py-2 px-3 text-slate-400">{ref.provider}</td>
                    <td className="py-2 px-3 text-slate-400">{ref.route_key}</td>
                    <td className="py-2 px-3 text-xs text-slate-500">{ref.route_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}

// ── Tab 2: 路由配置 ──
function RoutesTab({ routes, providers, testResult, onTest, onSaved }) {
  const [editRoute, setEditRoute] = useState(null)
  const [resolvedData, setResolvedData] = useState({})
  const routeList = Object.entries(routes)
  const thinkingLabel = (v) => v === 'true' ? '启用' : (v === 'false' ? '禁用' : '自动')
  return (
    <div>
      <p className="text-slate-500 text-sm mb-2">每个 route 选择一个模型和供应商。base_url/API key 默认在「供应商」Tab 管理；route API key 只作为高级覆盖。</p>
      <p className="text-amber-400/80 text-xs mb-3">reply 是当前主回复运行路径；fast/smart 目前用于展示、测试、目录和后续统一路由，主流程暂不自动切换 fast/smart。主回复 controller 初始化仍来自 config.yaml/env；桥接层会在每次回复前同步 reply route 的 provider/base_url/model，其他 controller 初始化参数变更仍需重启或重建 bridge。</p>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {routeList.map(([key, r]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{r.label} <span className="text-xs text-slate-500 ml-1">{key}</span>{r.route_type && <span className={`ml-1 px-1 py-0.5 rounded text-[10px] ${r.route_type === 'controller' ? 'bg-blue-900/30 text-blue-400' : r.route_type === 'classifier' ? 'bg-purple-900/30 text-purple-400' : 'bg-cyan-900/30 text-cyan-400'}`}>{r.route_type}</span>}</h3>
                {r.inherited_from && <span className="text-xs text-amber-400">继承自 {r.inherited_from}{r.overridden_fields && Object.keys(r.overridden_fields).length > 0 ? ` (覆盖: ${Object.keys(r.overridden_fields).join(', ')})` : ''}</span>}
                {r.note && <span className="text-xs text-slate-600 block">{r.note}</span>}
              </div>
              <div className="flex gap-1">
                <button onClick={() => setEditRoute({ key, ...r })} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">编辑</button>
                <button onClick={() => onTest(key)} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
                {key === 'sticker_describe' && <button onClick={() => onTest(key, 'vision')} className="px-2 py-1 bg-cyan-700/50 hover:bg-cyan-700 rounded text-xs">视觉测试</button>}
                <button onClick={() => {
                  const k = key
                  setResolvedData(prev => ({ ...prev, [k]: { loading: true } }))
                  api.get(`/models/routes/${encodeURIComponent(k)}/resolved`).then(r => {
                    setResolvedData(prev => ({ ...prev, [k]: r.data }))
                  }).catch(e => {
                    setResolvedData(prev => ({ ...prev, [k]: { error: e.message } }))
                  })
                }} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">诊断</button>
              </div>
            </div>
            <div className="space-y-0.5 text-xs text-slate-400">
              <div>模型: <span className="text-slate-200">{r.model}</span></div>
              <div>供应商: <span className="text-slate-500">{r.provider_id}</span>{r.provider_enabled === false && <span className="text-red-400 ml-1">已禁用</span>}</div>
              <div>API key: {r.route_api_key_configured ? <span className="text-amber-400">route 覆盖</span> : <span className="text-slate-600">继承供应商</span>}</div>
              <div>max_tokens: {r.max_tokens} | timeout: {r.timeout}s | temp: {r.temperature} | thinking: {thinkingLabel(r.enable_thinking)}</div>
              {r.source && <div className="text-slate-600">source: {r.source}</div>}
            </div>
            {testResult[key] && !testResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${testResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {testResult[key].ok ? `✅ ${testResult[key].latency_ms}ms${testResult[key].vision_payload_ok ? ' | vision payload OK' : ''}` : `❌ ${testResult[key].error}`}
                {testResult[key].note && <div className="text-slate-400 mt-1">{testResult[key].note}</div>}
              </div>
            )}
            {resolvedData[key] && !resolvedData[key].loading && !resolvedData[key].error && (
              <div className="mt-2 p-2 rounded-lg bg-slate-800/50 text-xs space-y-0.5">
                <div className="text-slate-400">解析结果 <span className="text-slate-600">(diagnostic)</span></div>
                <div>base_url: <span className="text-slate-200 font-mono break-all">{resolvedData[key].base_url}</span></div>
                <div>registry_provider: <span className="text-slate-200">{resolvedData[key].registry_provider}</span></div>
                <div>source: <span className="text-slate-300">{resolvedData[key].source}</span></div>
                <div>api_key_source: <span className="text-slate-300">{resolvedData[key].api_key_source || '-'}</span></div>
                <div>enable_thinking: <span className="text-slate-300">{thinkingLabel(resolvedData[key].enable_thinking)}</span></div>
                {resolvedData[key].inherited_from && <div>继承自: <span className="text-amber-400">{resolvedData[key].inherited_from}</span></div>}
                {resolvedData[key].overridden_fields && Object.keys(resolvedData[key].overridden_fields || {}).length > 0 && (
                  <div>覆盖字段: <span className="text-amber-400">{Object.entries(resolvedData[key].overridden_fields).map(([k,v]) => `${k}=${v}`).join(', ')}</span></div>
                )}
                <div>provider_enabled: {resolvedData[key].provider_enabled ? <span className="text-emerald-400">是</span> : <span className="text-red-400">否</span>}</div>
              </div>
            )}
          </Card>
        ))}
      </div>
      {editRoute && (
        <RouteEditModalV2 route={editRoute} providers={providers} onClose={() => setEditRoute(null)} onSaved={() => { setEditRoute(null); onSaved() }} />
      )}
    </div>
  )
}

function RouteEditModalV2({ route, providers, onClose, onSaved }) {
  const routeKey = route.route_key || route.key
  const isChatRoute = ['reply', 'fast', 'smart'].includes(routeKey)
  const supportsRouteApiKey = !isChatRoute
  const [f, setF] = useState({
    provider_id: route.provider_id || '', model: route.model || '',
    max_tokens: route.max_tokens ?? 30, temperature: route.temperature ?? 0,
    timeout: route.timeout ?? 15, enable_thinking: route.enable_thinking || 'auto',
    api_key: '',
  })
  const [clearApiKey, setClearApiKey] = useState(false)
  const [catalog, setCatalog] = useState([])
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [modelSearch, setModelSearch] = useState('')
  const [showManual, setShowManual] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [saveError, setSaveError] = useState('')
  useEffect(() => {
    const params = { limit: modelSearch ? 50 : 200 }
    if (f.provider_id) params.provider = f.provider_id
    if (modelSearch) params.q = modelSearch
    let cancelled = false
    const id = setTimeout(() => {
      setCatalogLoading(true)
      api.get('/models/catalog', { params })
        .then(r => { if (!cancelled) setCatalog(r.data.catalog || []) })
        .catch(() => {})
        .finally(() => { if (!cancelled) setCatalogLoading(false) })
    }, 0)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [f.provider_id, modelSearch])
  const providerModels = catalog.slice(0, 100)
  const hasCurrentModel = f.model && providerModels.some(m => m.model === f.model)
  const save = () => {
    setSaveError('')
    const payload = {}
    if (f.model && f.model.trim()) payload.model = f.model.trim()
    if (f.provider_id) payload.provider = f.provider_id
    payload.max_tokens = String(f.max_tokens)
    payload.temperature = String(f.temperature)
    payload.timeout = String(f.timeout)
    payload.enable_thinking = f.enable_thinking || 'auto'
    if (supportsRouteApiKey && showAdvanced && (f.api_key.trim() || clearApiKey)) {
      payload.api_key = clearApiKey ? '' : f.api_key.trim()
    }
    api.put(`/models/routes/${routeKey}`, payload).then(onSaved).catch(e => setSaveError(e.response?.data?.detail || e.message))
  }
  const fromTimingGate = routeKey === 'private_decision' || routeKey === 'classifier_legacy'
  return (
    <Modal onClose={onClose}>
      <div className="p-6 max-w-md">
        <h2 className="text-lg font-bold mb-3">编辑 {route.label} ({routeKey})</h2>
        {fromTimingGate && <p className="text-xs text-amber-400 mb-3">继承自 timing_gate。仅需配置需要覆盖的字段。</p>}
        <div className="space-y-3">
          <div>
            <label className="text-xs text-slate-500">供应商</label>
            <select value={f.provider_id} onChange={e => { setModelSearch(''); setF({ ...f, provider_id: e.target.value, model: '' }) }} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
              <option value="">不指定</option>
              {(providers || []).map(p => <option key={p.id} value={p.id}>{p.id}{p.enabled === false ? ' (已禁用)' : ''}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500">模型</label>
            {catalog.length > 10 && <input value={modelSearch} onChange={e => setModelSearch(e.target.value)} placeholder="搜索过滤模型..." className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-xs mt-1" />}
            {providerModels.length > 0 && (
              <select value={f.model} onChange={e => setF({ ...f, model: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
                <option value="">请选择模型</option>
                {f.model && !hasCurrentModel && (
                  <option value={f.model}>当前配置：{f.model}（未在该 provider /models 中确认）</option>
                )}
                {providerModels.map(m => <option key={m.id} value={m.model}>{m.model}{m.stale ? ' (stale)' : ''}</option>)}
              </select>
            )}
            {catalogLoading && <div className="text-xs text-slate-500 mt-1">加载中...</div>}
            {!catalogLoading && catalog.length === 0 && <div className="text-xs text-slate-500 mt-1">模型目录为空，请先在「模型列表」Tab 刷新目录</div>}
            {!catalogLoading && f.provider_id && providerModels.length === 0 && catalog.length > 0 && <div className="text-xs text-slate-500 mt-1">该供应商下暂无目录模型</div>}
            <button type="button" onClick={() => setShowManual(!showManual)} className="text-xs text-slate-500 hover:text-slate-300 mt-1">
              {showManual ? '收起手动输入' : '手动输入模型 ID...'}
            </button>
            {showManual && <input value={f.model} onChange={e => setF({ ...f, model: e.target.value })} placeholder="手动输入模型 ID" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" />}
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div><label className="text-xs text-slate-500">max_tokens</label><input type="number" min="0" value={f.max_tokens} onChange={e => setF({ ...f, max_tokens: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">temp</label><input type="number" step="0.1" min="0" max="2" value={f.temperature} onChange={e => setF({ ...f, temperature: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">timeout</label><input type="number" min="1" value={f.timeout} onChange={e => setF({ ...f, timeout: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
          </div>
          <div>
            <label className="text-xs text-slate-500">enable_thinking</label>
            <select value={f.enable_thinking} onChange={e => setF({ ...f, enable_thinking: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
              <option value="auto">自动</option>
              <option value="true">启用</option>
              <option value="false">禁用</option>
            </select>
          </div>
          {isChatRoute && <p className="text-xs text-slate-600">reply 当前会在每次主回复前同步 provider/model/timeout/temperature/max_tokens；fast/smart 仍为预留配置。</p>}
          <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className="text-xs text-slate-500 hover:text-slate-300">
            {showAdvanced ? '收起高级' : '高级覆盖 ▼'}
          </button>
          {showAdvanced && (
            <div className="p-3 bg-slate-900 rounded-lg space-y-2 text-sm">
              {supportsRouteApiKey ? (
                <>
                  <p className="text-xs text-slate-500">Route API key 是高级覆盖；为空时继承供应商 API key。</p>
                  {route.route_api_key_configured && <p className="text-xs text-amber-400">当前已配置 route 级 API key 覆盖。</p>}
                  <input type="password" value={f.api_key} onChange={e => { setClearApiKey(false); setF({ ...f, api_key: e.target.value }) }} placeholder="填写后只覆盖此 route" className="w-full p-2 rounded-lg bg-slate-800 border border-slate-700 text-xs" />
                  {route.route_api_key_configured && (
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                      <input type="checkbox" checked={clearApiKey} onChange={e => setClearApiKey(e.target.checked)} />
                      清除 route 覆盖，恢复继承供应商 key
                    </label>
                  )}
                </>
              ) : (
                <p className="text-xs text-slate-500">reply/fast/smart API key 统一在供应商页管理。</p>
              )}
            </div>
          )}
        </div>
        {saveError && <div className="text-xs text-red-400 mt-2 p-2 bg-red-500/10 rounded-lg">{saveError}</div>}
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={save} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Tab 3: 供应商 ──
function ProvidersTab({ providers }) {
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const doSync = () => {
    setSyncing(true)
    api.post('/models/catalog/refresh').then(r => setSyncResult(r.data)).catch(() => {}).finally(() => setSyncing(false))
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-slate-500 text-sm">管理 API 供应商的 base_url 和 api_key。路由通过「路由配置」Tab 选择供应商和模型。</p>
        <button onClick={doSync} disabled={syncing} className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium">{syncing ? '同步中...' : '同步所有模型'}</button>
      </div>
      {syncResult && (
        <div className="mb-3 text-xs">
          {syncResult.results?.map(r => (
            <span key={r.provider} className={`mr-3 ${r.ok ? 'text-emerald-400' : 'text-red-400'}`}>{r.provider}: {r.ok ? `${r.models.length} 个` : r.error}</span>
          ))}
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {(providers || []).map(p => (
          <Card key={p.id} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium text-sm">{p.id}</h3>
              <Badge tone={p.enabled !== false ? 'emerald' : 'slate'}>{p.enabled !== false ? '启用' : '禁用'}</Badge>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>base_url: <span className="text-slate-500 font-mono break-all">{p.base_url || '未配置'}</span></div>
              <div>API key: {p.api_key_configured ? '✅ 已配置' : '❌ 未配置'}</div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}

// ── Tab 4: 本地组件 ──
function LocalComponentsTab({ components, localResult, onAction }) {
  return (
    <div>
      <p className="text-slate-500 text-sm mb-3">本地语义组件为按需懒加载，不属于 API 路由。通过预热/测试按钮触发加载。</p>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {Object.entries(components || {}).map(([key, c]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{key}</h3>
                <span className="text-xs text-slate-500">配置: {c.configured ? '已配置' : '未配置'} | 加载: {c.load_state === 'loaded' ? '已加载' : c.load_state === 'fallback' ? '降级' : c.load_state === 'unavailable' ? '不可用' : '未加载'}</span>
                {c.fallback && <span className="text-xs text-amber-400 ml-1">(降级: {c.fallback})</span>}
              </div>
              <div className="flex gap-1">
                <button onClick={() => onAction(key, 'warmup')} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">预热</button>
                <button onClick={() => onAction(key, 'test')} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
              </div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>模型: <span className="text-slate-200">{c.model}</span></div>
              <div>用途: {c.role}</div>
              <div className="text-slate-600">触发: {c.trigger}</div>
              {c.note && <div className="text-slate-600 italic">{c.note}</div>}
            </div>
            {localResult[key] && !localResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${localResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {localResult[key].ok ? `✅ ${localResult[key].latency_ms}ms${localResult[key].dim ? ' | dim=' + localResult[key].dim : ''}` : `❌ ${localResult[key].error || ''}`}
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
