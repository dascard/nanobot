import { useEffect, useMemo, useState } from 'react'
import {
  Cable,
  KeyRound,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  Unplug,
} from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import {
  ConsoleRail,
  DriverBadge,
  EditorSection,
  EmptyEditor,
  FormField,
  InlineNotice,
  RailItem,
  StatePill,
  Toggle,
  driverLabel,
  formatApiError,
  formatTime,
  inputClass,
} from './modelConsoleUi'

function emptyProvider() {
  return {
    id: '',
    display_name: '',
    driver_type: 'openai',
    base_url: '',
    enabled: true,
    registry_provider: '',
    model_discovery_enabled: true,
    provider_name: '',
    provider_native_tools: [],
    credential_action: 'keep',
    api_key: '',
    creating: true,
  }
}

function providerDraft(provider) {
  if (!provider) return emptyProvider()
  return {
    ...provider,
    provider_native_tools: [...(provider.provider_native_tools || [])],
    credential_action: 'keep',
    api_key: '',
    creating: false,
  }
}

export function ProviderConnectionsPanel({ providers, driverTypes, nativeTools, onChanged, onOpenKt }) {
  const [selectedId, setSelectedId] = useState(providers[0]?.id || '')
  const [draft, setDraft] = useState(() => providerDraft(providers[0]))
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)
  const [operation, setOperation] = useState(null)

  useEffect(() => {
    if (draft?.creating) return
    const timer = window.setTimeout(() => {
      const selected = providers.find(item => item.id === selectedId)
      if (selected) setDraft(providerDraft(selected))
      else if (providers[0]) {
        setSelectedId(providers[0].id)
        setDraft(providerDraft(providers[0]))
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [providers, selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return providers
    return providers.filter(item => `${item.id} ${item.display_name} ${item.driver_type}`.toLowerCase().includes(needle))
  }, [providers, query])

  const selectProvider = provider => {
    setSelectedId(provider.id)
    setDraft(providerDraft(provider))
    setMessage(null)
    setOperation(null)
  }

  const update = patch => setDraft(current => ({ ...current, ...patch }))
  const currentDriver = driverTypes.find(item => item.id === draft?.driver_type)

  const changeDriver = driver => {
    const defaults = driver === 'anthropic'
      ? { base_url: draft.base_url || 'https://api.anthropic.com', provider_name: draft.provider_name || 'anthropic' }
      : driver === 'codex'
        ? { base_url: '', provider_name: 'codex', credential_action: 'keep', api_key: '', provider_native_tools: ['image_gen'], model_discovery_enabled: false }
        : { provider_name: draft.provider_name || draft.id, model_discovery_enabled: true }
    update({ driver_type: driver, ...defaults })
  }

  const save = async event => {
    event.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      const payload = {
        display_name: draft.display_name,
        driver_type: draft.driver_type,
        base_url: draft.driver_type === 'codex' ? '' : draft.base_url,
        enabled: draft.enabled,
        registry_provider: draft.registry_provider,
        model_discovery_enabled: draft.driver_type === 'openai' && draft.model_discovery_enabled,
        provider_name: draft.provider_name,
        provider_native_tools: draft.provider_native_tools,
        credential_action: draft.credential_action,
      }
      if (draft.credential_action === 'replace') payload.api_key = draft.api_key
      const response = draft.creating
        ? await api.post('/models/providers', { id: draft.id, ...payload })
        : await api.put(`/models/providers/${encodeURIComponent(draft.id)}`, payload)
      const saved = response.data.provider
      setSelectedId(saved.id)
      setDraft(providerDraft(saved))
      setMessage({ tone: 'emerald', text: '连接配置已保存' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Provider 保存失败') })
    } finally {
      setSaving(false)
    }
  }

  const runOperation = async kind => {
    if (!draft || draft.creating) return
    setOperation({ kind, loading: true })
    try {
      const endpoint = kind === 'catalog'
        ? `/models/providers/${encodeURIComponent(draft.id)}/catalog/refresh`
        : `/models/providers/${encodeURIComponent(draft.id)}/test`
      const response = await api.post(endpoint)
      const ok = kind === 'catalog'
        ? response.data.results?.[0]?.ok !== false
        : response.data.ok
      setOperation({ kind, loading: false, ok, data: response.data })
      if (kind === 'catalog') await onChanged?.()
    } catch (error) {
      setOperation({ kind, loading: false, ok: false, error: formatApiError(error) })
    }
  }

  const remove = async () => {
    if (!draft || draft.creating || draft.builtin) return
    if (!window.confirm(`确定删除 Provider「${draft.id}」？`)) return
    setSaving(true)
    setMessage(null)
    try {
      await api.delete(`/models/providers/${encodeURIComponent(draft.id)}`)
      setDraft(null)
      setSelectedId('')
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Provider 删除失败') })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid overflow-hidden rounded-lg border border-slate-800 bg-slate-900 lg:h-[calc(100dvh-15.5rem)] lg:min-h-[38rem] lg:grid-cols-[17rem_minmax(0,1fr)]">
      <ConsoleRail
        title="Provider Connections"
        count={providers.length}
        query={query}
        onQuery={setQuery}
        action={(
          <button type="button" onClick={() => { setSelectedId(''); setDraft(emptyProvider()); setMessage(null) }} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md bg-indigo-500/15 text-indigo-300 transition-colors hover:bg-indigo-500/25" aria-label="新增 Provider" title="新增 Provider">
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      >
        {filtered.map(provider => (
          <RailItem
            key={provider.id}
            active={!draft?.creating && selectedId === provider.id}
            icon={Cable}
            title={provider.display_name || provider.id}
            subtitle={provider.id}
            meta={provider.base_url || (provider.driver_type === 'codex' ? 'KT OAuth' : '未配置 Endpoint')}
            onClick={() => selectProvider(provider)}
            badges={<><DriverBadge driver={provider.driver_type} /><StatePill ok={provider.enabled}>{provider.enabled ? '启用' : '停用'}</StatePill></>}
          />
        ))}
      </ConsoleRail>

      {!draft ? (
        <EmptyEditor title="选择一个 Provider" description="Provider 只管理 Endpoint、认证和 KT Driver 身份；模型参数在 Preset 中配置。" />
      ) : (
        <form onSubmit={save} className="min-w-0 overflow-y-auto">
          <header className="sticky top-0 z-10 flex flex-col gap-3 border-b border-slate-800 bg-slate-900/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-sm font-semibold text-white">{draft.creating ? '新增 Provider Connection' : draft.display_name || draft.id}</h2>
                {!draft.creating && <DriverBadge driver={draft.driver_type} />}
                {draft.builtin && <StatePill neutral>内置连接</StatePill>}
              </div>
              <p className="mt-1 text-[11px] text-slate-500">凭据不会返回到浏览器；保存时必须显式选择保持、替换或清除。</p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {!draft.creating && draft.driver_type === 'openai' && (
                <ActionButton type="button" onClick={() => runOperation('catalog')} disabled={operation?.loading}>同步目录</ActionButton>
              )}
              {!draft.creating && draft.driver_type === 'openai' && (
                <ActionButton type="button" tone="blue" onClick={() => runOperation('test')} disabled={operation?.loading}>测试连接</ActionButton>
              )}
              <ActionButton type="submit" tone="emerald" disabled={saving} className="gap-1.5"><Save className="h-3.5 w-3.5" aria-hidden="true" />{saving ? '保存中...' : '保存'}</ActionButton>
            </div>
          </header>

          <div className="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="min-w-0 xl:border-r xl:border-slate-800">
              {message && <div className="px-4 pt-4 sm:px-5"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
              <EditorSection title="连接身份" description="Provider Connection 是可复用的连接实例，不包含具体 Model ID。">
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormField id="model-provider-id" label="Provider ID" required hint="保存后不可修改，用于 Preset 引用。">
                    <input id="model-provider-id" value={draft.id} disabled={!draft.creating} onChange={event => update({ id: event.target.value })} autoComplete="off" className={inputClass} />
                  </FormField>
                  <FormField id="model-provider-display-name" label="显示名称" required>
                    <input id="model-provider-display-name" value={draft.display_name || ''} onChange={event => update({ display_name: event.target.value })} className={inputClass} />
                  </FormField>
                  <FormField id="model-provider-driver" label="KT Provider Driver" required hint={currentDriver?.transport || '决定实际协议适配器。'}>
                    <select id="model-provider-driver" value={draft.driver_type} disabled={draft.builtin} onChange={event => changeDriver(event.target.value)} className={inputClass}>
                      {(driverTypes || []).map(driver => <option key={driver.id} value={driver.id}>{driver.label}</option>)}
                    </select>
                  </FormField>
                  <FormField id="model-provider-registry" label="Catalog Provider Key" hint="OpenAI 目录与成本注册表使用的身份。">
                    <input id="model-provider-registry" value={draft.registry_provider || ''} onChange={event => update({ registry_provider: event.target.value })} className={inputClass} />
                  </FormField>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <Toggle id="model-provider-enabled" checked={Boolean(draft.enabled)} onChange={enabled => update({ enabled })} label="启用此连接" />
                  <Toggle id="model-provider-discovery" checked={Boolean(draft.model_discovery_enabled)} onChange={model_discovery_enabled => update({ model_discovery_enabled })} disabled={draft.driver_type !== 'openai'} label="同步 /models 目录" />
                </div>
              </EditorSection>

              <EditorSection title="传输与 KT 身份" description="Endpoint 与 Provider Name 决定网络传输和 provider-native tool 兼容身份。">
                {draft.driver_type !== 'codex' ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <FormField id="model-provider-base-url" label="Base URL" required hint={draft.driver_type === 'anthropic' ? '例如 https://api.anthropic.com' : '例如 https://gateway.example.com/v1'} className="sm:col-span-2">
                      <input id="model-provider-base-url" type="url" value={draft.base_url || ''} onChange={event => update({ base_url: event.target.value })} autoComplete="url" className={inputClass} />
                    </FormField>
                    <FormField id="model-provider-name" label="KT Provider Name" hint="原生工具按此身份判断兼容性。">
                      <input id="model-provider-name" value={draft.provider_name || ''} onChange={event => update({ provider_name: event.target.value })} className={inputClass} />
                    </FormField>
                  </div>
                ) : (
                  <InlineNotice tone="blue">Codex 使用 KT 的 Responses API 与全局 OAuth Token，不接受自定义 Base URL 或 API Key。账号登录在「KT / Codex」工作区完成。</InlineNotice>
                )}
                <div className="mt-4">
                  <div className="mb-2 text-[11px] font-medium text-slate-400">Provider Native Tools</div>
                  {nativeTools.length === 0 ? <p className="text-xs text-slate-600">当前 KT 没有登记原生工具。</p> : (
                    <div className="space-y-2">
                      {nativeTools.map(tool => {
                        const checked = (draft.provider_native_tools || []).includes(tool.name)
                        const supported = !tool.provider_support?.length || tool.provider_support.includes(draft.provider_name || draft.driver_type)
                        return (
                          <label key={tool.name} className={`flex items-start gap-2 rounded-md border px-3 py-2 ${supported ? 'cursor-pointer border-slate-800 bg-slate-950/60' : 'border-slate-800/60 bg-slate-950/30 opacity-50'}`}>
                            <input type="checkbox" checked={checked} disabled={!supported} onChange={event => update({ provider_native_tools: event.target.checked ? [...(draft.provider_native_tools || []), tool.name] : (draft.provider_native_tools || []).filter(name => name !== tool.name) })} className="mt-0.5 h-4 w-4 accent-indigo-500" />
                            <span className="min-w-0"><span className="font-mono text-xs text-slate-200">{tool.name}</span><span className="ml-2 text-[10px] text-slate-500">支持 {tool.provider_support?.join(', ') || '全部'}</span><span className="mt-1 block text-[10px] leading-4 text-slate-500">{tool.description}</span></span>
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              </EditorSection>

              <EditorSection title="认证" description={draft.driver_type === 'codex' ? 'Codex 凭据由 KT OAuth 管理。' : 'API Key 只写入服务端，页面仅显示是否已配置。'}>
                {draft.driver_type === 'codex' ? (
                  <div className="flex flex-col gap-3 rounded-md border border-violet-500/20 bg-violet-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-violet-300" aria-hidden="true" /><div><div className="text-xs text-slate-200">KT Codex OAuth</div><div className="mt-0.5 text-[10px] text-slate-500">{draft.credential_configured ? '已检测到 OAuth Token' : '尚未登录'}</div></div></div>
                    <ActionButton type="button" tone="blue" onClick={onOpenKt}>打开账号与用量</ActionButton>
                  </div>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <FormField id="model-provider-credential-action" label="凭据操作" hint={draft.api_key_configured ? '当前已配置 API Key。' : '当前未配置 API Key。'}>
                      <select id="model-provider-credential-action" value={draft.credential_action} onChange={event => update({ credential_action: event.target.value, api_key: '' })} className={inputClass}>
                        <option value="keep">保持不变</option><option value="replace">替换</option><option value="clear">清除</option>
                      </select>
                    </FormField>
                    <FormField id="model-provider-api-key" label="新 API Key" hint="只有选择“替换”时才会提交。">
                      <input id="model-provider-api-key" type="password" value={draft.api_key || ''} disabled={draft.credential_action !== 'replace'} onChange={event => update({ api_key: event.target.value })} autoComplete="new-password" className={inputClass} />
                    </FormField>
                  </div>
                )}
              </EditorSection>

              {!draft.creating && !draft.builtin && (
                <EditorSection title="危险操作" description="只有未被 Preset 和 Route 引用的自定义 Provider 才能删除。">
                  <ActionButton type="button" tone="red" onClick={remove} disabled={saving} className="gap-1.5"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" />删除 Provider</ActionButton>
                </EditorSection>
              )}
            </div>

            <aside className="min-w-0 bg-slate-950/40 p-4">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">连接诊断</h3>
              <div className="mt-3 space-y-2 text-xs">
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">Driver</div><div className="mt-1 text-slate-200">{driverLabel(draft.driver_type)}</div><div className="mt-2 flex flex-wrap gap-1"><StatePill ok={currentDriver?.agent_runtime_supported && currentDriver?.runtime_available}>{currentDriver?.runtime_available ? 'KT Agent 就绪' : 'KT 依赖缺失'}</StatePill><StatePill ok={currentDriver?.route_completion_supported}>同步 Route</StatePill></div>{currentDriver?.runtime_unavailable_reason && <div className="mt-2 text-[10px] text-amber-300">{currentDriver.runtime_unavailable_reason}</div>}</div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">Credential</div><div className="mt-1 flex items-center gap-2 text-slate-300"><KeyRound className="h-3.5 w-3.5" aria-hidden="true" />{draft.credential_configured || draft.api_key_configured ? '已配置' : '未配置'}</div><div className="mt-1 text-[10px] text-slate-600">source: {draft.credential_source || 'none'}</div></div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">Catalog Snapshot</div><div className="mt-1 text-slate-300">{draft.catalog?.model_count || 0} 个模型</div><div className="mt-1 text-[10px] text-slate-600">更新：{formatTime(draft.catalog?.updated_at)}</div>{draft.catalog?.stale && <div className="mt-2 text-[10px] text-amber-300">上次同步失败，当前为旧快照</div>}</div>
              </div>
              {operation && !operation.loading && (
                <div className="mt-3" aria-live="polite"><InlineNotice tone={operation.ok ? 'emerald' : 'red'}>{operation.ok ? (operation.kind === 'catalog' ? '模型目录同步完成' : `连接测试通过，${operation.data?.latency_ms || 0}ms`) : operation.error || operation.data?.error || '操作失败'}</InlineNotice></div>
              )}
              {operation?.loading && <div className="mt-3 flex items-center gap-2 text-xs text-slate-500"><RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />{operation.kind === 'catalog' ? '同步中...' : '测试中...'}</div>}
              {!draft.enabled && <div className="mt-3"><InlineNotice tone="amber"><Unplug className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />此连接已停用，绑定它的 Preset 不会进入运行候选。</InlineNotice></div>}
            </aside>
          </div>
        </form>
      )}
    </div>
  )
}
