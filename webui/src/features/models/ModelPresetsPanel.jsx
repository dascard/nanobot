import { useEffect, useMemo, useState } from 'react'
import {
  Beaker,
  Braces,
  ChevronRight,
  Copy,
  Gauge,
  Plus,
  Save,
  SlidersHorizontal,
  Trash2,
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
  PricingPills,
  RailItem,
  StatePill,
  Toggle,
  formatApiError,
  inputClass,
} from './modelConsoleUi'

const RETRY_CLASSES = ['rate_limit', 'server', 'transient', 'overflow']

function defaultRetryPolicy() {
  return { max_retries: 3, base_delay: 1, max_delay: 30, jitter: 0.25, retry_classes: ['rate_limit', 'server', 'transient'] }
}

function emptyPreset(provider) {
  const driver = provider?.driver_type || 'openai'
  return {
    id: '', display_name: '', provider_id: provider?.id || '', model: '', enabled: true,
    max_context: 128000, max_output: 16384, temperature: driver === 'codex' ? null : 1,
    reasoning_effort: driver === 'codex' ? 'medium' : '', service_tier: '', timeout: driver === 'codex' ? 300 : 120,
    cost_input_1m: null, cost_output_1m: null,
    enable_thinking: 'auto', capabilities: { supports_stream: true, supports_tools: true, supports_image: false },
    retry_policy: defaultRetryPolicy(), driver_options: driver === 'openai' ? { echo_reasoning: true } : {},
    extra_headers: {}, extra_body: {}, variation_groups: {}, creating: true,
    _extra_headers_text: '{}', _extra_body_text: '{}', _variation_groups_text: '{}',
  }
}

function presetDraft(preset) {
  if (!preset) return null
  return {
    ...preset,
    retry_policy: { ...defaultRetryPolicy(), ...(preset.retry_policy || {}) },
    capabilities: { supports_stream: true, supports_tools: true, supports_image: false, ...(preset.capabilities || {}) },
    driver_options: { ...(preset.driver_options || {}) },
    creating: false,
    _extra_headers_text: JSON.stringify(preset.extra_headers || {}, null, 2),
    _extra_body_text: JSON.stringify(preset.extra_body || {}, null, 2),
    _variation_groups_text: JSON.stringify(preset.variation_groups || {}, null, 2),
  }
}

function parseObject(text, label) {
  let value
  try { value = JSON.parse(text || '{}') } catch (error) { throw new Error(`${label} 不是合法 JSON：${error.message}`, { cause: error }) }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label} 必须是 JSON Object`)
  return value
}

function copyJson(value) {
  navigator.clipboard?.writeText(JSON.stringify(value || {}, null, 2)).catch(() => {})
}

function optionalNumber(value) {
  return value === '' || value == null ? null : Number(value)
}

export function ModelPresetsPanel({ presets, providers, driverSchemas, codexStatus, onChanged, onOpenKt }) {
  const [selectedId, setSelectedId] = useState(presets[0]?.id || '')
  const [draft, setDraft] = useState(() => presetDraft(presets[0]))
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)
  const [preview, setPreview] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [selections, setSelections] = useState({})
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    if (draft?.creating) return
    const timer = window.setTimeout(() => {
      const selected = presets.find(item => item.id === selectedId)
      if (selected) setDraft(presetDraft(selected))
      else if (presets[0]) {
        setSelectedId(presets[0].id)
        setDraft(presetDraft(presets[0]))
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [presets, selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!draft || draft.creating) return undefined
    let cancelled = false
    const timer = window.setTimeout(() => {
      setPreviewLoading(true)
      api.post(`/models/presets/${encodeURIComponent(draft.id)}/resolve`, { selected_variations: selections })
        .then(response => { if (!cancelled) setPreview(response.data) })
        .catch(error => { if (!cancelled) setPreview({ error: formatApiError(error, 'Preset 解析失败') }) })
        .finally(() => { if (!cancelled) setPreviewLoading(false) })
    }, 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [draft?.id, draft?.creating, selections]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return presets
    return presets.filter(item => `${item.id} ${item.display_name} ${item.model} ${item.provider_id}`.toLowerCase().includes(needle))
  }, [presets, query])

  const provider = providers.find(item => item.id === draft?.provider_id)
  const driver = provider?.driver_type || draft?.driver_type || 'openai'
  const schema = driverSchemas.find(item => item.id === driver) || {}
  const update = patch => setDraft(current => ({ ...current, ...patch }))
  const variationGroups = useMemo(() => {
    try { return parseObject(draft?._variation_groups_text || '{}', 'Variation Groups') } catch { return {} }
  }, [draft?._variation_groups_text])

  const choosePreset = preset => {
    setSelectedId(preset.id)
    setDraft(presetDraft(preset))
    setSelections({})
    setMessage(null)
    setTestResult(null)
  }

  const startCreate = () => {
    const preferred = providers.find(item => item.enabled) || providers[0]
    setSelectedId('')
    setDraft(emptyPreset(preferred))
    setSelections({})
    setPreview(null)
    setMessage(null)
    setTestResult(null)
  }

  const changeProvider = providerId => {
    const nextProvider = providers.find(item => item.id === providerId)
    const nextDriver = nextProvider?.driver_type || 'openai'
    update({
      provider_id: providerId,
      driver_type: nextDriver,
      temperature: nextDriver === 'codex' ? null : (draft.temperature ?? 1),
      reasoning_effort: nextDriver === 'codex' ? (draft.reasoning_effort || 'medium') : nextDriver === 'anthropic' ? '' : draft.reasoning_effort,
      enable_thinking: nextDriver === 'openai' ? draft.enable_thinking : 'auto',
      timeout: nextDriver === 'codex' && draft.timeout === 120 ? 300 : draft.timeout,
      driver_options: nextDriver === 'openai' ? { echo_reasoning: true } : {},
      _extra_headers_text: nextDriver === 'codex' ? '{}' : draft._extra_headers_text,
      _extra_body_text: nextDriver === 'codex' ? '{}' : draft._extra_body_text,
    })
  }

  const payloadFromDraft = () => ({
    display_name: draft.display_name,
    provider_id: draft.provider_id,
    model: draft.model,
    enabled: draft.enabled,
    max_context: Number(draft.max_context),
    max_output: Number(draft.max_output),
    temperature: draft.temperature === '' || draft.temperature == null ? null : Number(draft.temperature),
    reasoning_effort: draft.reasoning_effort || '',
    service_tier: draft.service_tier || '',
    cost_input_1m: optionalNumber(draft.cost_input_1m),
    cost_output_1m: optionalNumber(draft.cost_output_1m),
    timeout: Number(draft.timeout),
    enable_thinking: draft.enable_thinking,
    capabilities: draft.capabilities,
    extra_headers: parseObject(draft._extra_headers_text, 'Extra Headers'),
    extra_body: parseObject(draft._extra_body_text, 'Extra Body'),
    retry_policy: {
      max_retries: Number(draft.retry_policy.max_retries),
      base_delay: Number(draft.retry_policy.base_delay),
      max_delay: Number(draft.retry_policy.max_delay),
      jitter: Number(draft.retry_policy.jitter),
      retry_classes: draft.retry_policy.retry_classes,
    },
    variation_groups: parseObject(draft._variation_groups_text, 'Variation Groups'),
    driver_options: draft.driver_options,
  })

  const save = async event => {
    event.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      const payload = payloadFromDraft()
      const response = draft.creating
        ? await api.post('/models/presets', { id: draft.id, ...payload })
        : await api.put(`/models/presets/${encodeURIComponent(draft.id)}`, payload)
      const saved = response.data.preset
      setSelectedId(saved.id)
      setDraft(presetDraft(saved))
      setMessage({ tone: 'emerald', text: 'Model Preset 已保存，运行时会在下一次 Route 解析时读取。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: error?.response ? formatApiError(error, 'Preset 保存失败') : error.message })
    } finally {
      setSaving(false)
    }
  }

  const resolveNow = async () => {
    if (draft.creating) { setMessage({ tone: 'amber', text: '请先保存 Preset，再查看服务端解析结果。' }); return }
    setPreviewLoading(true)
    try {
      const response = await api.post(`/models/presets/${encodeURIComponent(draft.id)}/resolve`, { selected_variations: selections })
      setPreview(response.data)
    } catch (error) {
      setPreview({ error: formatApiError(error, 'Preset 解析失败') })
    } finally { setPreviewLoading(false) }
  }

  const testPreset = async () => {
    if (draft.creating) { setMessage({ tone: 'amber', text: '请先保存 Preset，再运行真实 KT Driver 测试。' }); return }
    setTestResult({ loading: true })
    try {
      const response = await api.post(`/models/presets/${encodeURIComponent(draft.id)}/test`, { selected_variations: selections })
      setTestResult(response.data)
    } catch (error) {
      setTestResult({ ok: false, error: formatApiError(error, 'Preset 测试失败') })
    }
  }

  const remove = async () => {
    if (draft.creating || !window.confirm(`确定删除 Model Preset「${draft.id}」？`)) return
    setSaving(true)
    try {
      await api.delete(`/models/presets/${encodeURIComponent(draft.id)}`)
      setDraft(null)
      setSelectedId('')
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Preset 删除失败') })
    } finally { setSaving(false) }
  }

  const setRetry = patch => update({ retry_policy: { ...draft.retry_policy, ...patch } })
  const setCapability = (key, value) => update({ capabilities: { ...draft.capabilities, [key]: value } })
  const setDriverOption = (key, value) => update({ driver_options: { ...draft.driver_options, [key]: value } })

  return (
    <div className="grid overflow-hidden rounded-lg border border-slate-800 bg-slate-900 lg:h-[calc(100dvh-15.5rem)] lg:min-h-[38rem] lg:grid-cols-[18rem_minmax(0,1fr)]">
      <ConsoleRail title="Model Presets" count={presets.length} query={query} onQuery={setQuery} action={<button type="button" onClick={startCreate} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md bg-indigo-500/15 text-indigo-300 transition-colors hover:bg-indigo-500/25" aria-label="新增 Model Preset" title="新增 Model Preset"><Plus className="h-3.5 w-3.5" aria-hidden="true" /></button>}>
        {filtered.map(item => (
          <RailItem key={item.id} active={!draft?.creating && selectedId === item.id} icon={SlidersHorizontal} title={item.display_name || item.id} subtitle={`${item.provider_id}/${item.model}`} meta={`${item.max_context?.toLocaleString()} ctx · ${item.max_output?.toLocaleString()} out`} onClick={() => choosePreset(item)} badges={<><DriverBadge driver={item.driver_type} /><PricingPills inputCost={item.cost_input_1m} outputCost={item.cost_output_1m} />{item.route_references?.length > 0 && <StatePill neutral>{item.route_references.length} Route</StatePill>}{!item.enabled && <StatePill ok={false}>停用</StatePill>}</>} />
        ))}
      </ConsoleRail>

      {!draft ? <EmptyEditor title="选择一个 Model Preset" description="Preset 是可复用的完整模型请求配置；Route 只绑定 Preset，不再重复编辑模型参数。" action={<ActionButton tone="blue" onClick={startCreate}>新增 Preset</ActionButton>} /> : (
        <form onSubmit={save} className="min-w-0 overflow-y-auto">
          <header className="sticky top-0 z-10 flex flex-col gap-3 border-b border-slate-800 bg-slate-900/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-sm font-semibold text-white">{draft.creating ? '新增 Model Preset' : draft.display_name || draft.id}</h2><DriverBadge driver={driver} /><PricingPills inputCost={draft.cost_input_1m} outputCost={draft.cost_output_1m} />{!draft.creating && <span className="font-mono text-[10px] text-slate-500">{draft.id}</span>}</div><p className="mt-1 text-[11px] text-slate-500">所有参数由 KT Driver 消费；右侧显示 variation 解析后的最终请求。</p></div>
            <div className="flex shrink-0 flex-wrap gap-2"><ActionButton type="button" onClick={resolveNow} disabled={previewLoading}>解析请求</ActionButton><ActionButton type="button" tone="blue" onClick={testPreset} disabled={testResult?.loading} className="gap-1.5"><Beaker className="h-3.5 w-3.5" aria-hidden="true" />{testResult?.loading ? '测试中...' : '真实测试'}</ActionButton><ActionButton type="submit" tone="emerald" disabled={saving} className="gap-1.5"><Save className="h-3.5 w-3.5" aria-hidden="true" />{saving ? '保存中...' : '保存'}</ActionButton></div>
          </header>

          <div className="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_21rem]">
            <div className="min-w-0 xl:border-r xl:border-slate-800">
              {message && <div className="px-4 pt-4 sm:px-5"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
              <EditorSection title="基本信息" description="Preset ID 是 Route Binding 使用的稳定引用；Model ID 是发给上游的真实名称。">
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormField id="model-preset-id" label="Preset ID" required><input id="model-preset-id" value={draft.id} disabled={!draft.creating} onChange={event => update({ id: event.target.value })} className={inputClass} /></FormField>
                  <FormField id="model-preset-display-name" label="显示名称" required><input id="model-preset-display-name" value={draft.display_name || ''} onChange={event => update({ display_name: event.target.value })} className={inputClass} /></FormField>
                  <FormField id="model-preset-provider" label="Provider Connection" required><select id="model-preset-provider" value={draft.provider_id} onChange={event => changeProvider(event.target.value)} className={inputClass}>{providers.map(item => <option key={item.id} value={item.id}>{item.display_name || item.id} · {item.driver_type}{item.enabled ? '' : '（停用）'}</option>)}</select></FormField>
                  <FormField id="model-preset-model" label="Model ID" required><input id="model-preset-model" value={draft.model} onChange={event => update({ model: event.target.value })} placeholder={driver === 'codex' ? 'gpt-5.4' : driver === 'anthropic' ? 'claude-opus-4-1' : 'deepseek-chat'} className={`${inputClass} font-mono`} /></FormField>
                </div>
                <div className="mt-3"><Toggle id="model-preset-enabled" checked={Boolean(draft.enabled)} onChange={enabled => update({ enabled })} label="启用此 Preset" /></div>
              </EditorSection>

              <EditorSection title="价格与路由成本" description="单位为 USD / 1M tokens；留空表示价格未知，0 表示免费。价格用于列表标签、预算判断和 Preset fallback 候选元数据，不会发送给模型 Provider。">
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormField id="model-preset-cost-input" label="输入价格"><input id="model-preset-cost-input" type="number" min="0" step="0.000001" value={draft.cost_input_1m ?? ''} onChange={event => update({ cost_input_1m: event.target.value })} placeholder="例如 2.5" className={inputClass} /></FormField>
                  <FormField id="model-preset-cost-output" label="输出价格"><input id="model-preset-cost-output" type="number" min="0" step="0.000001" value={draft.cost_output_1m ?? ''} onChange={event => update({ cost_output_1m: event.target.value })} placeholder="例如 10" className={inputClass} /></FormField>
                </div>
              </EditorSection>

              <EditorSection title="上下文、输出与能力" description="上下文窗口参与预算判断；能力声明决定 Route 候选是否满足图片、工具和流式请求。">
                <div className="grid gap-3 sm:grid-cols-3">
                  <FormField id="model-preset-max-context" label="Max Context" required><input id="model-preset-max-context" type="number" min="1024" value={draft.max_context} onChange={event => update({ max_context: event.target.value })} className={inputClass} /></FormField>
                  <FormField id="model-preset-max-output" label="Max Output" required><input id="model-preset-max-output" type="number" min="1" value={draft.max_output} onChange={event => update({ max_output: event.target.value })} className={inputClass} /></FormField>
                  <FormField id="model-preset-timeout" label="Timeout（秒）" required><input id="model-preset-timeout" type="number" min="1" step="1" value={draft.timeout} onChange={event => update({ timeout: event.target.value })} className={inputClass} /></FormField>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-3"><Toggle id="model-preset-cap-stream" checked={Boolean(draft.capabilities.supports_stream)} onChange={value => setCapability('supports_stream', value)} label="Streaming" /><Toggle id="model-preset-cap-tools" checked={Boolean(draft.capabilities.supports_tools)} onChange={value => setCapability('supports_tools', value)} label="Tool Calls" /><Toggle id="model-preset-cap-image" checked={Boolean(draft.capabilities.supports_image)} onChange={value => setCapability('supports_image', value)} label="Image Input" /></div>
              </EditorSection>

              <EditorSection title="采样、推理与服务层级" description={`${schema.label || driver} 只显示实际支持的字段，避免保存后被 Driver 静默忽略。`}>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {driver !== 'codex' && <FormField id="model-preset-temperature" label="Temperature"><input id="model-preset-temperature" type="number" min="0" max="2" step="0.05" value={draft.temperature ?? ''} onChange={event => update({ temperature: event.target.value === '' ? null : event.target.value })} className={inputClass} /></FormField>}
                  {driver !== 'anthropic' && <FormField id="model-preset-reasoning" label="Reasoning Effort"><select id="model-preset-reasoning" value={draft.reasoning_effort || ''} onChange={event => update({ reasoning_effort: event.target.value })} className={inputClass}>{(schema.reasoning_efforts || ['', 'none', 'low', 'medium', 'high', 'xhigh', 'max']).map(value => <option key={value || 'default'} value={value}>{value || '默认'}</option>)}</select></FormField>}
                  <FormField id="model-preset-service-tier" label="Service Tier"><select id="model-preset-service-tier" value={draft.service_tier || ''} onChange={event => update({ service_tier: event.target.value })} className={inputClass}>{(schema.service_tiers || ['']).map(value => <option key={value || 'default'} value={value}>{value || '默认'}</option>)}</select></FormField>
                  {driver === 'openai' && <FormField id="model-preset-thinking" label="Enable Thinking"><select id="model-preset-thinking" value={draft.enable_thinking} onChange={event => update({ enable_thinking: event.target.value })} className={inputClass}><option value="auto">自动</option><option value="true">启用</option><option value="false">禁用</option></select></FormField>}
                </div>
                {driver === 'openai' && <div className="mt-3"><Toggle id="model-preset-echo-reasoning" checked={draft.driver_options.echo_reasoning !== false} onChange={value => setDriverOption('echo_reasoning', value)} label="回传 reasoning_content 以保持推理链状态" /></div>}
                {driver === 'anthropic' && <div className="mt-3 grid gap-2 sm:grid-cols-2"><Toggle id="model-preset-anthropic-bearer" checked={Boolean(draft.driver_options.auth_as_bearer)} onChange={value => setDriverOption('auth_as_bearer', value)} label="使用 Bearer 认证" /><Toggle id="model-preset-anthropic-cache" checked={!draft.driver_options.disable_prompt_caching} onChange={value => setDriverOption('disable_prompt_caching', !value)} label="Anthropic Prompt Caching" /></div>}
                {driver === 'codex' && <div className="mt-3"><InlineNotice tone={codexStatus?.authenticated && !codexStatus?.expired ? 'emerald' : 'amber'}>{codexStatus?.authenticated ? (codexStatus.expired ? 'Codex Token 已过期，真实调用时会尝试刷新。' : 'Codex OAuth 已登录，可运行真实测试。') : 'Codex 尚未登录。'} <button type="button" onClick={onOpenKt} className="ml-1 cursor-pointer underline underline-offset-2">打开账号配置</button></InlineNotice></div>}
              </EditorSection>

              <EditorSection title="可靠性策略" description="KT SDK retry 之外的 Provider 边界重试；熔断仍由 Nanobot ModelFailureTracker 管理。">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><FormField id="model-preset-retry-count" label="Max Retries"><input id="model-preset-retry-count" type="number" min="0" max="10" value={draft.retry_policy.max_retries} onChange={event => setRetry({ max_retries: event.target.value })} className={inputClass} /></FormField><FormField id="model-preset-retry-base" label="Base Delay"><input id="model-preset-retry-base" type="number" min="0" step="0.1" value={draft.retry_policy.base_delay} onChange={event => setRetry({ base_delay: event.target.value })} className={inputClass} /></FormField><FormField id="model-preset-retry-max" label="Max Delay"><input id="model-preset-retry-max" type="number" min="0" step="0.5" value={draft.retry_policy.max_delay} onChange={event => setRetry({ max_delay: event.target.value })} className={inputClass} /></FormField><FormField id="model-preset-retry-jitter" label="Jitter"><input id="model-preset-retry-jitter" type="number" min="0" max="1" step="0.05" value={draft.retry_policy.jitter} onChange={event => setRetry({ jitter: event.target.value })} className={inputClass} /></FormField></div>
                <div className="mt-3 flex flex-wrap gap-2">{RETRY_CLASSES.map(name => <label key={name} className="flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-800 bg-slate-950 px-2.5 py-1.5 font-mono text-[11px] text-slate-300"><input type="checkbox" checked={draft.retry_policy.retry_classes.includes(name)} onChange={event => setRetry({ retry_classes: event.target.checked ? [...draft.retry_policy.retry_classes, name] : draft.retry_policy.retry_classes.filter(item => item !== name) })} className="accent-indigo-500" />{name}</label>)}</div>
              </EditorSection>

              <EditorSection title="高级请求参数" description={driver === 'codex' ? 'Codex 请求由 KT Responses Driver 固定生成，不接受任意 Extra Body/Headers。' : '认证 Header 被禁止写入这里；请始终放在 Provider Connection。'}>
                <div className="grid gap-3 lg:grid-cols-2"><FormField id="model-preset-extra-headers" label="Extra Headers JSON"><textarea id="model-preset-extra-headers" value={draft._extra_headers_text} disabled={driver === 'codex'} onChange={event => update({ _extra_headers_text: event.target.value })} rows="7" spellCheck="false" className={`${inputClass} resize-y font-mono text-xs leading-5`} /></FormField><FormField id="model-preset-extra-body" label="Extra Body JSON"><textarea id="model-preset-extra-body" value={draft._extra_body_text} disabled={driver === 'codex'} onChange={event => update({ _extra_body_text: event.target.value })} rows="7" spellCheck="false" className={`${inputClass} resize-y font-mono text-xs leading-5`} /></FormField></div>
              </EditorSection>

              <EditorSection title="Variation Groups" description="为同一 Preset 定义 reasoning / speed / thinking 等旋钮；Route Binding 选择具体 option。允许覆盖 temperature、reasoning_effort、service_tier、max_context、max_output、extra_body、retry_policy。">
                <FormField id="model-preset-variations" label="Variation Groups JSON" hint={'示例：{"reasoning":{"high":{"reasoning_effort":"high"},"fast":{"reasoning_effort":"low","max_output":4096}}}'}><textarea id="model-preset-variations" value={draft._variation_groups_text} onChange={event => { update({ _variation_groups_text: event.target.value }); setSelections({}) }} rows="10" spellCheck="false" className={`${inputClass} resize-y font-mono text-xs leading-5`} /></FormField>
              </EditorSection>

              {!draft.creating && <EditorSection title="危险操作" description="被 Route Binding 引用的 Preset 无法删除。"><ActionButton type="button" tone="red" onClick={remove} disabled={saving} className="gap-1.5"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" />删除 Preset</ActionButton></EditorSection>}
            </div>

            <aside className="min-w-0 bg-slate-950/40 p-4 xl:sticky xl:top-[4.25rem] xl:h-[calc(100dvh-19.75rem)] xl:overflow-y-auto">
              <div className="flex items-center justify-between"><h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Resolved Request</h3><button type="button" onClick={() => copyJson(preview?.request_preview)} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-200" aria-label="复制请求预览" title="复制请求预览"><Copy className="h-3.5 w-3.5" aria-hidden="true" /></button></div>
              {Object.keys(variationGroups).length > 0 && <div className="mt-3 space-y-2 rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-500">Variation Selection</div>{Object.entries(variationGroups).map(([group, options]) => <FormField key={group} id={`model-preset-variation-${group}`} label={group}><select id={`model-preset-variation-${group}`} value={selections[group] || ''} onChange={event => setSelections(current => ({ ...current, [group]: event.target.value }))} className={inputClass}><option value="">不覆盖</option>{Object.keys(options || {}).map(option => <option key={option} value={option}>{option}</option>)}</select></FormField>)}</div>}
              {previewLoading ? <div className="mt-3 text-xs text-slate-500">正在解析...</div> : preview?.error ? <div className="mt-3"><InlineNotice tone="red">{preview.error}</InlineNotice></div> : preview?.request_preview ? <div className="mt-3 space-y-3"><div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="flex items-center gap-2"><Gauge className="h-3.5 w-3.5 text-indigo-300" aria-hidden="true" /><span className="text-xs text-slate-200">{preview.request_preview.driver_type}</span></div><div className="mt-2 break-all font-mono text-[10px] leading-4 text-slate-500">{preview.request_preview.endpoint}</div></div><pre className="max-h-[28rem] overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-[10px] leading-5 text-slate-300">{JSON.stringify(preview.request_preview, null, 2)}</pre></div> : <div className="mt-3"><InlineNotice>保存后可查看服务端使用 KT variation 解析器生成的最终请求。</InlineNotice></div>}
              {testResult && !testResult.loading && <div className="mt-3" aria-live="polite"><InlineNotice tone={testResult.ok ? 'emerald' : 'red'}>{testResult.ok ? <><span className="font-medium">真实 KT Driver 测试通过</span><span className="mt-1 block">{testResult.driver_type} · {testResult.model} · {testResult.latency_ms}ms</span>{testResult.output && <span className="mt-1 block text-slate-400">{testResult.output}</span>}</> : testResult.error || '测试失败'}</InlineNotice></div>}
              <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3"><div className="flex items-center gap-2 text-[10px] text-slate-500"><Braces className="h-3.5 w-3.5" aria-hidden="true" />参数继承顺序</div><ol className="mt-2 space-y-1 text-[10px] text-slate-500"><li className="flex items-center gap-1">Provider Connection <ChevronRight className="h-3 w-3" /> Preset</li><li className="flex items-center gap-1">Preset <ChevronRight className="h-3 w-3" /> Variation</li><li className="flex items-center gap-1">Resolved Request <ChevronRight className="h-3 w-3" /> KT Driver</li></ol></div>
            </aside>
          </div>
        </form>
      )}
    </div>
  )
}
