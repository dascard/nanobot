import { useDeferredValue, useEffect, useMemo, useState } from 'react'
import { RefreshCw, Save, Search } from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import {
  DriverBadge,
  FormField,
  InlineNotice,
  PricingPills,
  StatePill,
  formatApiError,
  formatTime,
  inputClass,
} from './modelConsoleUi'

const defaultRetryPolicy = {
  max_retries: 3,
  base_delay: 1,
  max_delay: 30,
  jitter: 0.25,
  retry_classes: ['rate_limit', 'server', 'transient'],
}

function numberOr(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function parseObject(text, label) {
  let value
  try {
    value = JSON.parse(text || '{}')
  } catch (error) {
    throw new Error(`${label} 不是合法 JSON：${error.message}`, { cause: error })
  }
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${label} 必须是 JSON Object`)
  }
  return value
}

function parseList(text, fallback) {
  const values = String(text || '')
    .split(',')
    .map(item => item.trim().toLowerCase().replace(/^\/+/, ''))
    .filter(Boolean)
  return values.length ? Array.from(new Set(values)) : fallback
}

function draftFor(model, configured) {
  const current = configured || model?.default_config || {}
  const retryPolicy = { ...defaultRetryPolicy, ...(current.retry_policy || {}) }
  return {
    provider_id: model?.provider || current.provider_id || '',
    model: model?.model || current.model || '',
    display_name: current.display_name || model?.model || '',
    enabled: current.enabled !== false,
    max_context: current.max_context || 128000,
    max_output: current.max_output || 16384,
    temperature: current.temperature === null ? '' : (current.temperature ?? 1),
    reasoning_effort: current.reasoning_effort || '',
    service_tier: current.service_tier || '',
    cost_input_1m: current.cost_input_1m ?? '',
    cost_output_1m: current.cost_output_1m ?? '',
    intelligence: current.intelligence || 0,
    fallback_only: Boolean(current.fallback_only),
    timeout: current.timeout || 120,
    enable_thinking: current.enable_thinking || 'auto',
    capabilities: {
      supports_stream: Boolean(current.capabilities?.supports_stream),
      supports_tools: Boolean(current.capabilities?.supports_tools),
      supports_image: Boolean(current.capabilities?.supports_image),
    },
    extra_headers: current.extra_headers || {},
    extra_body: current.extra_body || {},
    retry_policy: retryPolicy,
    variation_groups: current.variation_groups || {},
    driver_options: current.driver_options || {},
    input_modalities: current.input_modalities || ['text'],
    output_modalities: current.output_modalities || ['text'],
    supported_endpoints: current.supported_endpoints || ['chat/completions'],
    _extra_headers_text: JSON.stringify(current.extra_headers || {}, null, 2),
    _extra_body_text: JSON.stringify(current.extra_body || {}, null, 2),
    _driver_options_text: JSON.stringify(current.driver_options || {}, null, 2),
    _retry_classes_text: retryPolicy.retry_classes.join(', '),
    _input_modalities_text: (current.input_modalities || ['text']).join(', '),
    _output_modalities_text: (current.output_modalities || ['text']).join(', '),
    _supported_endpoints_text: (current.supported_endpoints || ['chat/completions']).join(', '),
  }
}

function modelKey(providerId, model) {
  return `${providerId}::${model}`
}

export function ModelCatalogPanel({ providers, modelDefaults, onChanged }) {
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query)
  const [providerId, setProviderId] = useState('')
  const [catalog, setCatalog] = useState([])
  const [selectedKey, setSelectedKey] = useState('')
  const [draft, setDraft] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  const defaultsByModel = useMemo(() => new Map(
    modelDefaults.map(item => [modelKey(item.provider_id, item.model), item]),
  ), [modelDefaults])

  const load = async () => {
    setLoading(true)
    try {
      const response = await api.get('/models/catalog', {
        params: {
          provider: providerId || undefined,
          q: deferredQuery || undefined,
          limit: 500,
        },
      })
      const items = response.data.catalog || []
      setCatalog(items)
      if (!selectedKey && items[0]) setSelectedKey(items[0].id)
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, '模型目录加载失败') })
    } finally { setLoading(false) }
  }

  useEffect(() => {
    const timer = window.setTimeout(load, 0)
    return () => window.clearTimeout(timer)
  }, [providerId, deferredQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  const selected = catalog.find(item => item.id === selectedKey) || catalog[0]
  useEffect(() => {
    if (!selected) return undefined
    const timer = window.setTimeout(() => {
      const configured = defaultsByModel.get(modelKey(selected.provider, selected.model))
      setDraft(draftFor(selected, configured))
    }, 0)
    return () => window.clearTimeout(timer)
  }, [selected?.id, defaultsByModel]) // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      const endpoint = providerId
        ? `/models/providers/${encodeURIComponent(providerId)}/catalog/refresh`
        : '/models/catalog/refresh'
      const response = await api.post(endpoint)
      const failed = response.data.results?.filter(item => !item.ok) || []
      setMessage({
        tone: failed.length ? 'amber' : 'emerald',
        text: failed.length
          ? `${failed.length} 个 Provider 同步失败，已保留旧快照。`
          : '模型目录同步完成。',
      })
      await load()
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, '模型目录同步失败') })
    } finally { setRefreshing(false) }
  }

  const save = async () => {
    if (!draft) return
    setSaving(true)
    setMessage(null)
    try {
      const capabilities = { ...draft.capabilities }
      const configuredInputModalities = parseList(draft._input_modalities_text, ['text'])
      const inputModalities = capabilities.supports_image
        ? Array.from(new Set(['text', 'image', ...configuredInputModalities]))
        : configuredInputModalities.filter(item => item !== 'image')
      await api.put('/models/defaults', {
        provider_id: draft.provider_id,
        model: draft.model,
        display_name: draft.display_name,
        enabled: draft.enabled,
        max_context: numberOr(draft.max_context, 128000),
        max_output: numberOr(draft.max_output, 16384),
        temperature: draft.temperature === '' ? null : numberOr(draft.temperature, 1),
        reasoning_effort: draft.reasoning_effort,
        service_tier: draft.service_tier,
        cost_input_1m: draft.cost_input_1m === '' ? null : numberOr(draft.cost_input_1m, 0),
        cost_output_1m: draft.cost_output_1m === '' ? null : numberOr(draft.cost_output_1m, 0),
        intelligence: numberOr(draft.intelligence, 0),
        fallback_only: draft.fallback_only,
        timeout: numberOr(draft.timeout, 120),
        enable_thinking: draft.enable_thinking,
        capabilities,
        extra_headers: parseObject(draft._extra_headers_text, 'Extra Headers'),
        extra_body: parseObject(draft._extra_body_text, 'Extra Body'),
        retry_policy: {
          max_retries: numberOr(draft.retry_policy.max_retries, 3),
          base_delay: numberOr(draft.retry_policy.base_delay, 1),
          max_delay: numberOr(draft.retry_policy.max_delay, 30),
          jitter: numberOr(draft.retry_policy.jitter, 0.25),
          retry_classes: parseList(draft._retry_classes_text, defaultRetryPolicy.retry_classes),
        },
        variation_groups: draft.variation_groups,
        driver_options: parseObject(draft._driver_options_text, 'Driver Options'),
        input_modalities: inputModalities,
        output_modalities: parseList(draft._output_modalities_text, ['text']),
        supported_endpoints: parseList(draft._supported_endpoints_text, ['chat/completions']),
      })
      setMessage({ tone: 'emerald', text: '模型默认配置已保存，路由未覆盖的字段会继承这里。' })
      await onChanged?.()
      await load()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, '模型默认配置保存失败') })
    } finally { setSaving(false) }
  }

  return (
    <section className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900">
      <header className="flex flex-col gap-3 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-end sm:justify-between sm:px-5">
        <div><h2 className="text-sm font-semibold text-slate-100">模型目录与默认配置</h2><p className="mt-1 text-[11px] text-slate-500">上游目录只确认 Model ID 存在；价格、能力和路由资格必须由运营配置形成可验证证据。</p></div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end"><label htmlFor="model-catalog-query" className="block"><span className="text-[10px] text-slate-500">搜索模型</span><span className="relative mt-1 block"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-600" aria-hidden="true" /><input id="model-catalog-query" value={query} onChange={event => setQuery(event.target.value)} className={`${inputClass} min-w-52 pl-8`} /></span></label><label htmlFor="model-catalog-provider" className="block"><span className="text-[10px] text-slate-500">Provider</span><select id="model-catalog-provider" value={providerId} onChange={event => setProviderId(event.target.value)} className={`${inputClass} mt-1 min-w-48`}><option value="">全部 Provider</option>{providers.filter(item => item.model_discovery_supported).map(item => <option key={item.id} value={item.id}>{item.display_name || item.id}</option>)}</select></label><ActionButton tone="emerald" onClick={refresh} disabled={refreshing} className="gap-1.5"><RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />{refreshing ? '同步中...' : '同步目录'}</ActionButton></div>
      </header>
      {message && <div className="px-4 pt-4 sm:px-5"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
      <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_25rem]">
        <div className="overflow-x-auto p-4 sm:p-5 lg:border-r lg:border-slate-800">
          <table className="min-w-[760px] w-full text-left text-xs"><thead className="border-b border-slate-800 text-[10px] uppercase tracking-wide text-slate-600"><tr><th className="px-3 py-2">Model ID</th><th className="px-3 py-2">Provider</th><th className="px-3 py-2">默认价格</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">路由</th><th className="px-3 py-2">更新时间</th></tr></thead><tbody>{loading && <tr><td colSpan="6" className="px-3 py-8 text-center text-slate-600">加载中...</td></tr>}{!loading && catalog.length === 0 && <tr><td colSpan="6" className="px-3 py-8 text-center text-slate-600">目录为空；请先同步 Provider。</td></tr>}{catalog.map(model => {
            const provider = providers.find(item => item.id === model.provider)
            const configured = defaultsByModel.get(modelKey(model.provider, model.model))
            return <tr key={model.id} onClick={() => setSelectedKey(model.id)} className={`cursor-pointer border-b border-slate-800/70 transition-colors last:border-0 ${selected?.id === model.id ? 'bg-indigo-500/10' : 'hover:bg-slate-800/30'}`}><td className="px-3 py-2.5 font-mono text-slate-200">{model.model}</td><td className="px-3 py-2.5"><div className="flex items-center gap-2"><span className="text-slate-400">{model.provider}</span>{provider && <DriverBadge driver={provider.driver_type} />}</div></td><td className="px-3 py-2.5">{configured ? <PricingPills inputCost={configured.cost_input_1m} outputCost={configured.cost_output_1m} /> : <span className="text-amber-400/80">待配置</span>}</td><td className="px-3 py-2.5"><div className="flex flex-wrap gap-1">{model.stale ? <StatePill ok={false}>目录过期</StatePill> : <StatePill ok>目录已确认</StatePill>}{model.routing_evidence?.verified ? <StatePill ok>路由已验证</StatePill> : <StatePill ok={false}>仅目录身份</StatePill>}{configured?.fallback_only && <StatePill neutral>仅兜底</StatePill>}</div></td><td className="px-3 py-2.5 text-slate-500">{configured?.route_references?.join(', ') || '-'}</td><td className="px-3 py-2.5 text-[10px] text-slate-600">{formatTime(model.updated_at)}</td></tr>
          })}</tbody></table>
        </div>

        <aside className="min-w-0 bg-slate-950/40 p-4 sm:p-5">
          {!draft ? <p className="text-xs text-slate-600">选择一个目录模型进行配置。</p> : <div className="space-y-4">
            <div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-slate-100">模型默认值</h3><StatePill ok={defaultsByModel.has(modelKey(draft.provider_id, draft.model))}> {defaultsByModel.has(modelKey(draft.provider_id, draft.model)) ? '已配置' : '新配置'} </StatePill></div><p className="mt-1 break-all font-mono text-[10px] text-slate-500">{draft.provider_id}/{draft.model}</p></div>
            <InlineNotice tone={selected?.routing_evidence?.verified ? 'emerald' : 'amber'}>
              {selected?.routing_evidence?.verified
                ? `路由证据：${selected.routing_evidence.source || 'operator_model_config'}。能力标签来自显式模型配置。`
                : '当前只有上游目录身份，不能据 Model ID 猜测能力，也不会直接进入运行候选；请核验后保存显式配置。'}
            </InlineNotice>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <FormField id="model-default-name" label="显示名称"><input id="model-default-name" value={draft.display_name} onChange={event => setDraft(current => ({ ...current, display_name: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-intelligence" label="智能度 0-15"><input id="model-default-intelligence" type="number" min="0" max="15" value={draft.intelligence} onChange={event => setDraft(current => ({ ...current, intelligence: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-context" label="上下文上限"><input id="model-default-context" type="number" min="1024" value={draft.max_context} onChange={event => setDraft(current => ({ ...current, max_context: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-output" label="默认输出上限"><input id="model-default-output" type="number" min="1" value={draft.max_output} onChange={event => setDraft(current => ({ ...current, max_output: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-input-cost" label="输入 $/M"><input id="model-default-input-cost" type="number" min="0" step="0.000001" value={draft.cost_input_1m} onChange={event => setDraft(current => ({ ...current, cost_input_1m: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-output-cost" label="输出 $/M"><input id="model-default-output-cost" type="number" min="0" step="0.000001" value={draft.cost_output_1m} onChange={event => setDraft(current => ({ ...current, cost_output_1m: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-temperature" label="默认温度"><input id="model-default-temperature" type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={event => setDraft(current => ({ ...current, temperature: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-timeout" label="默认超时（秒）"><input id="model-default-timeout" type="number" min="1" value={draft.timeout} onChange={event => setDraft(current => ({ ...current, timeout: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-thinking" label="默认思考"><select id="model-default-thinking" value={draft.enable_thinking} onChange={event => setDraft(current => ({ ...current, enable_thinking: event.target.value }))} className={inputClass}><option value="auto">自动</option><option value="true">开启</option><option value="false">关闭</option></select></FormField>
              <FormField id="model-default-reasoning" label="Reasoning Effort"><input id="model-default-reasoning" value={draft.reasoning_effort} onChange={event => setDraft(current => ({ ...current, reasoning_effort: event.target.value }))} className={inputClass} /></FormField>
              <FormField id="model-default-service-tier" label="Service Tier"><input id="model-default-service-tier" value={draft.service_tier} onChange={event => setDraft(current => ({ ...current, service_tier: event.target.value }))} placeholder="留空使用 Provider 默认值" className={inputClass} /></FormField>
            </div>
            <div className="grid gap-2 rounded-md border border-slate-800 bg-slate-950 p-3 text-[11px] text-slate-400 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <label className="flex items-center gap-2"><input type="checkbox" checked={draft.enabled} onChange={event => setDraft(current => ({ ...current, enabled: event.target.checked }))} />启用模型</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={draft.fallback_only} onChange={event => setDraft(current => ({ ...current, fallback_only: event.target.checked }))} />仅作最后兜底</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={draft.capabilities.supports_stream} onChange={event => setDraft(current => ({ ...current, capabilities: { ...current.capabilities, supports_stream: event.target.checked } }))} />流式输出</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={draft.capabilities.supports_tools} onChange={event => setDraft(current => ({ ...current, capabilities: { ...current.capabilities, supports_tools: event.target.checked } }))} />工具调用</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={draft.capabilities.supports_image} onChange={event => setDraft(current => ({ ...current, capabilities: { ...current.capabilities, supports_image: event.target.checked } }))} />图像输入</label>
            </div>
            <details className="rounded-md border border-slate-800 bg-slate-950/70 p-3">
              <summary className="cursor-pointer text-xs font-medium text-slate-300">高级默认配置</summary>
              <div className="mt-3 space-y-3">
                <FormField id="model-default-input-modalities" label="输入模态" hint="逗号分隔，例如 text, image"><input id="model-default-input-modalities" value={draft._input_modalities_text} onChange={event => setDraft(current => ({ ...current, _input_modalities_text: event.target.value }))} className={inputClass} /></FormField>
                <FormField id="model-default-output-modalities" label="输出模态" hint="逗号分隔，例如 text, image"><input id="model-default-output-modalities" value={draft._output_modalities_text} onChange={event => setDraft(current => ({ ...current, _output_modalities_text: event.target.value }))} className={inputClass} /></FormField>
                <FormField id="model-default-endpoints" label="支持的 Endpoint" hint="逗号分隔，例如 chat/completions"><input id="model-default-endpoints" value={draft._supported_endpoints_text} onChange={event => setDraft(current => ({ ...current, _supported_endpoints_text: event.target.value }))} className={inputClass} /></FormField>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                  <FormField id="model-default-retry-count" label="最大重试"><input id="model-default-retry-count" type="number" min="0" max="10" value={draft.retry_policy.max_retries} onChange={event => setDraft(current => ({ ...current, retry_policy: { ...current.retry_policy, max_retries: event.target.value } }))} className={inputClass} /></FormField>
                  <FormField id="model-default-retry-base" label="基础退避秒数"><input id="model-default-retry-base" type="number" min="0" step="0.1" value={draft.retry_policy.base_delay} onChange={event => setDraft(current => ({ ...current, retry_policy: { ...current.retry_policy, base_delay: event.target.value } }))} className={inputClass} /></FormField>
                  <FormField id="model-default-retry-max" label="最大退避秒数"><input id="model-default-retry-max" type="number" min="0" step="0.5" value={draft.retry_policy.max_delay} onChange={event => setDraft(current => ({ ...current, retry_policy: { ...current.retry_policy, max_delay: event.target.value } }))} className={inputClass} /></FormField>
                  <FormField id="model-default-retry-jitter" label="退避抖动"><input id="model-default-retry-jitter" type="number" min="0" max="1" step="0.05" value={draft.retry_policy.jitter} onChange={event => setDraft(current => ({ ...current, retry_policy: { ...current.retry_policy, jitter: event.target.value } }))} className={inputClass} /></FormField>
                </div>
                <FormField id="model-default-retry-classes" label="重试错误类型" hint="rate_limit, server, transient, overflow"><input id="model-default-retry-classes" value={draft._retry_classes_text} onChange={event => setDraft(current => ({ ...current, _retry_classes_text: event.target.value }))} className={inputClass} /></FormField>
                <FormField id="model-default-extra-headers" label="Extra Headers JSON"><textarea id="model-default-extra-headers" value={draft._extra_headers_text} onChange={event => setDraft(current => ({ ...current, _extra_headers_text: event.target.value }))} rows="5" spellCheck="false" className={`${inputClass} resize-y font-mono`} /></FormField>
                <FormField id="model-default-extra-body" label="Extra Body JSON"><textarea id="model-default-extra-body" value={draft._extra_body_text} onChange={event => setDraft(current => ({ ...current, _extra_body_text: event.target.value }))} rows="5" spellCheck="false" className={`${inputClass} resize-y font-mono`} /></FormField>
                <FormField id="model-default-driver-options" label="Driver Options JSON"><textarea id="model-default-driver-options" value={draft._driver_options_text} onChange={event => setDraft(current => ({ ...current, _driver_options_text: event.target.value }))} rows="4" spellCheck="false" className={`${inputClass} resize-y font-mono`} /></FormField>
              </div>
            </details>
            <ActionButton type="button" tone="emerald" onClick={save} disabled={saving} className="w-full justify-center gap-1.5"><Save className="h-3.5 w-3.5" aria-hidden="true" />{saving ? '保存中...' : '保存模型默认配置'}</ActionButton>
          </div>}
        </aside>
      </div>
    </section>
  )
}
