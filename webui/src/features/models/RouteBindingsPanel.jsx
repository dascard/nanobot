import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  GitBranch,
  Link2,
  Plus,
  Save,
  Trash2,
  Unlink,
} from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import {
  ConsoleRail,
  DriverBadge,
  EmptyEditor,
  FormField,
  InlineNotice,
  PricingPills,
  RailItem,
  StatePill,
  formatApiError,
  inputClass,
} from './modelConsoleUi'

function identity(item) {
  return `${item.provider_id}::${item.model}`
}

function initialCandidates(binding) {
  const source = binding?.binding?.candidates || []
  return source.filter(item => item.provider_id && item.model).map(item => ({
    provider_id: item.provider_id,
    model: item.model,
    overrides: { ...(item.overrides || {}) },
  }))
}

function initialPolicy(binding) {
  return {
    min_intelligence: binding?.binding?.min_intelligence ?? 0,
    sort_policy: binding?.binding?.sort_policy || 'cost_modality_quality',
  }
}

function cleanOverrides(overrides) {
  return Object.fromEntries(Object.entries(overrides || {}).filter(([, value]) => (
    value !== '' && value !== null && value !== undefined
  )))
}

export function RouteBindingsPanel({ bindings, modelDefaults, statusRoutes, onChanged }) {
  const [selectedKey, setSelectedKey] = useState(bindings[0]?.route_key || '')
  const [draftCandidates, setDraftCandidates] = useState(() => initialCandidates(bindings[0]))
  const [policy, setPolicy] = useState(() => initialPolicy(bindings[0]))
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  const selected = bindings.find(item => item.route_key === selectedKey) || bindings[0]
  const routeStatus = selected ? statusRoutes?.[selected.route_key] : null

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const current = bindings.find(item => item.route_key === selectedKey)
      if (current) {
        setDraftCandidates(initialCandidates(current))
        setPolicy(initialPolicy(current))
      } else if (bindings[0]) {
        setSelectedKey(bindings[0].route_key)
        setDraftCandidates(initialCandidates(bindings[0]))
        setPolicy(initialPolicy(bindings[0]))
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [bindings, selectedKey])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return bindings
    return bindings.filter(item => `${item.route_key} ${item.label} ${item.owner} ${item.domain}`.toLowerCase().includes(needle))
  }, [bindings, query])

  const compatibleModels = useMemo(() => {
    if (!selected) return []
    return modelDefaults.filter(item => (
      item.enabled !== false
      && selected.supported_driver_types?.includes(item.driver_type)
      && item.supported_endpoints?.includes('chat/completions')
      && Object.entries(selected.required_model_capabilities || {}).every(([name, required]) => (
        !required || item.capabilities?.[name] === true
      ))
      && (selected.required_input_modalities || []).every(modality => (
        item.input_modalities?.includes(modality)
      ))
    ))
  }, [modelDefaults, selected])

  const chooseRoute = binding => {
    setSelectedKey(binding.route_key)
    setDraftCandidates(initialCandidates(binding))
    setPolicy(initialPolicy(binding))
    setMessage(null)
  }

  const addCandidate = () => {
    const available = compatibleModels.find(model => (
      !draftCandidates.some(item => identity(item) === identity(model))
    ))
    if (!available) {
      setMessage({ tone: 'amber', text: '没有更多兼容且未添加的模型。' })
      return
    }
    setDraftCandidates(current => [...current, {
      provider_id: available.provider_id,
      model: available.model,
      overrides: {},
    }])
  }

  const updateCandidate = (index, patch) => setDraftCandidates(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item))
  const updateOverride = (index, field, value) => setDraftCandidates(current => current.map((item, itemIndex) => {
    if (itemIndex !== index) return item
    const overrides = { ...(item.overrides || {}) }
    if (value === '') delete overrides[field]
    else overrides[field] = value
    return { ...item, overrides }
  }))
  const removeCandidate = index => setDraftCandidates(current => current.filter((_, itemIndex) => itemIndex !== index))
  const moveCandidate = (index, offset) => setDraftCandidates(current => {
    const target = index + offset
    if (target < 0 || target >= current.length) return current
    const copy = [...current]
    const [item] = copy.splice(index, 1)
    copy.splice(target, 0, item)
    return copy
  })

  const save = async () => {
    if (!selected || draftCandidates.length === 0) {
      setMessage({ tone: 'amber', text: '至少添加一个候选模型。' })
      return
    }
    setSaving(true)
    setMessage(null)
    try {
      await api.put(`/models/bindings/${encodeURIComponent(selected.route_key)}`, {
        candidates: draftCandidates.map(item => ({
          ...item,
          overrides: cleanOverrides(item.overrides),
        })),
        min_intelligence: Number(policy.min_intelligence) || 0,
        sort_policy: policy.sort_policy,
      })
      setMessage({ tone: 'emerald', text: '路由已保存；运行时会先过滤能力，再按质量、价格和模态排序。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, '路由绑定保存失败') })
    } finally { setSaving(false) }
  }

  const clear = async () => {
    if (!selected?.binding || !window.confirm(`清除 Route「${selected.route_key}」的模型绑定并恢复 Legacy 配置？`)) return
    setSaving(true)
    try {
      await api.delete(`/models/bindings/${encodeURIComponent(selected.route_key)}`)
      setMessage({ tone: 'emerald', text: '已清除直接绑定，Route 恢复 Legacy 或继承配置。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, '路由绑定清除失败') })
    } finally { setSaving(false) }
  }

  return (
    <div className="grid overflow-hidden rounded-lg border border-slate-800 bg-slate-900 lg:h-[calc(100dvh-15.5rem)] lg:min-h-[38rem] lg:grid-cols-[18rem_minmax(0,1fr)]">
      <ConsoleRail title="Route Bindings" count={bindings.length} query={query} onQuery={setQuery}>
        {filtered.map(item => {
          const candidates = item.effective_binding?.candidates || []
          return <RailItem key={item.route_key} active={selected?.route_key === item.route_key} icon={GitBranch} title={item.label || item.route_key} subtitle={item.route_key} meta={candidates.length ? `${candidates.length} 个模型候选` : `Legacy · ${item.legacy?.model || '未配置'}`} onClick={() => chooseRoute(item)} badges={<>{item.binding ? <StatePill ok>已绑定</StatePill> : item.effective_binding ? <StatePill neutral>继承</StatePill> : <StatePill ok={false}>Legacy</StatePill>}<span className="rounded border border-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">{item.route_type}</span></>} />
        })}
      </ConsoleRail>

      {!selected ? <EmptyEditor title="选择一个 Route" description="Route Binding 直接选择目录模型，并保存业务局部覆盖。" /> : (
        <div className="min-w-0 overflow-y-auto">
          <header className="sticky top-0 z-10 flex flex-col gap-3 border-b border-slate-800 bg-slate-900/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-white">{selected.label}</h2><span className="font-mono text-[10px] text-slate-500">{selected.route_key}</span><StatePill neutral>{selected.route_type}</StatePill></div><p className="mt-1 text-[11px] text-slate-500">模型默认值来自目录；这里只配置候选关系和 Route 差异。</p></div>
            <div className="flex shrink-0 flex-wrap gap-2">{selected.binding && <ActionButton type="button" tone="red" onClick={clear} disabled={saving} className="gap-1.5"><Unlink className="h-3.5 w-3.5" aria-hidden="true" />清除绑定</ActionButton>}<ActionButton type="button" tone="emerald" onClick={save} disabled={saving || draftCandidates.length === 0} className="gap-1.5"><Save className="h-3.5 w-3.5" aria-hidden="true" />{saving ? '保存中...' : '保存路由'}</ActionButton></div>
          </header>

          <div className="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_20rem]">
            <main className="min-w-0 p-4 sm:p-5 xl:border-r xl:border-slate-800">
              {message && <div className="mb-4"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
              {!selected.binding && selected.effective_binding?.inherited_from && <div className="mb-4"><InlineNotice tone="blue">当前绑定继承自 <span className="font-mono">{selected.effective_binding.inherited_from}</span>；保存后建立独立覆盖。</InlineNotice></div>}
              {!selected.effective_binding && <div className="mb-4"><InlineNotice tone="amber">当前仍使用 Legacy 配置。直接添加目录模型并保存即可切换到新路由语义。</InlineNotice></div>}

              <section className="mb-4 grid gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3 sm:grid-cols-2">
                <FormField id="route-min-intelligence" label="最低智能度"><input id="route-min-intelligence" type="number" min="0" max="15" value={policy.min_intelligence} onChange={event => setPolicy(current => ({ ...current, min_intelligence: event.target.value }))} className={inputClass} /></FormField>
                <FormField id="route-sort-policy" label="候选排序"><select id="route-sort-policy" value={policy.sort_policy} onChange={event => setPolicy(current => ({ ...current, sort_policy: event.target.value }))} className={inputClass}><option value="cost_modality_quality">质量门槛 → 价格 → 模态</option><option value="manual">完全手工顺序</option></select></FormField>
                <p className="sm:col-span-2 text-[10px] leading-4 text-slate-600">自动策略中，低于门槛的付费模型后置；低智能免费或标记“仅兜底”的模型排在最后。图片等必需模态始终先做硬过滤。</p>
              </section>

              <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="text-xs font-semibold text-slate-200">候选模型</h3><p className="mt-1 text-[11px] text-slate-500">列表顺序作为同分时的最终顺序；运行时熔断后继续下一个候选。</p></div><ActionButton type="button" onClick={addCandidate} disabled={compatibleModels.length === 0} className="gap-1.5"><Plus className="h-3.5 w-3.5" aria-hidden="true" />添加候选</ActionButton></div>

              {draftCandidates.length === 0 ? <EmptyEditor title="尚未绑定模型" description="从已配置的模型目录中添加候选。" action={<ActionButton tone="blue" onClick={addCandidate} disabled={compatibleModels.length === 0}>添加模型</ActionButton>} /> : (
                <div className="space-y-3">
                  {draftCandidates.map((candidate, index) => {
                    const configured = modelDefaults.find(item => identity(item) === identity(candidate))
                    return <section key={`${identity(candidate)}-${index}`} className="rounded-lg border border-slate-800 bg-slate-950/50">
                      <div className="flex flex-col gap-3 border-b border-slate-800 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex min-w-0 items-center gap-3"><span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold ${index === 0 ? 'bg-indigo-500/20 text-indigo-200' : 'bg-slate-800 text-slate-400'}`}>{index + 1}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-slate-200">{index === 0 ? '候选 1' : `候选 ${index + 1}`}</span>{configured && <DriverBadge driver={configured.driver_type} />}{configured?.fallback_only && <StatePill neutral>最后兜底</StatePill>}{configured && (!configured.enabled || !configured.provider_enabled) && <StatePill ok={false}>不可用</StatePill>}</div><div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">{candidate.provider_id}/{candidate.model}</div></div></div>
                        <div className="flex shrink-0 gap-1"><button type="button" onClick={() => moveCandidate(index, -1)} disabled={index === 0} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30" aria-label="上移候选"><ArrowUp className="h-3.5 w-3.5" /></button><button type="button" onClick={() => moveCandidate(index, 1)} disabled={index === draftCandidates.length - 1} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30" aria-label="下移候选"><ArrowDown className="h-3.5 w-3.5" /></button><button type="button" onClick={() => removeCandidate(index)} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-red-500/10 hover:text-red-300" aria-label="移除候选"><Trash2 className="h-3.5 w-3.5" /></button></div>
                      </div>
                      <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3">
                        <FormField id={`route-binding-model-${index}`} label="目录模型"><select id={`route-binding-model-${index}`} value={identity(candidate)} onChange={event => { const next = compatibleModels.find(item => identity(item) === event.target.value); if (next) updateCandidate(index, { provider_id: next.provider_id, model: next.model, overrides: {} }) }} className={inputClass}>{compatibleModels.map(item => <option key={identity(item)} value={identity(item)}>{item.provider_id}/{item.model}</option>)}</select></FormField>
                        <FormField id={`route-binding-output-${index}`} label="输出上限（留空继承）"><input id={`route-binding-output-${index}`} type="number" min="1" value={candidate.overrides?.max_output ?? ''} placeholder={String(configured?.max_output || '')} onChange={event => updateOverride(index, 'max_output', event.target.value === '' ? '' : Number(event.target.value))} className={inputClass} /></FormField>
                        <FormField id={`route-binding-temperature-${index}`} label="温度（留空继承）"><input id={`route-binding-temperature-${index}`} type="number" min="0" max="2" step="0.1" value={candidate.overrides?.temperature ?? ''} placeholder={String(configured?.temperature ?? '')} onChange={event => updateOverride(index, 'temperature', event.target.value === '' ? '' : Number(event.target.value))} className={inputClass} /></FormField>
                        <FormField id={`route-binding-timeout-${index}`} label="超时秒数（留空继承）"><input id={`route-binding-timeout-${index}`} type="number" min="1" value={candidate.overrides?.timeout ?? ''} placeholder={String(configured?.timeout || '')} onChange={event => updateOverride(index, 'timeout', event.target.value === '' ? '' : Number(event.target.value))} className={inputClass} /></FormField>
                        <FormField id={`route-binding-thinking-${index}`} label="思考覆盖"><select id={`route-binding-thinking-${index}`} value={candidate.overrides?.enable_thinking ?? ''} onChange={event => updateOverride(index, 'enable_thinking', event.target.value)} className={inputClass}><option value="">继承默认</option><option value="auto">自动</option><option value="true">开启</option><option value="false">关闭</option></select></FormField>
                        <FormField id={`route-binding-reasoning-${index}`} label="Reasoning Effort"><input id={`route-binding-reasoning-${index}`} value={candidate.overrides?.reasoning_effort ?? ''} placeholder={configured?.reasoning_effort || '继承默认'} onChange={event => updateOverride(index, 'reasoning_effort', event.target.value)} className={inputClass} /></FormField>
                      </div>
                      {configured && <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 px-3 py-2 text-[10px] text-slate-500"><PricingPills inputCost={configured.cost_input_1m} outputCost={configured.cost_output_1m} /><span>智能度 {configured.intelligence}</span><span>{configured.input_modalities?.join('+')} → {configured.output_modalities?.join('+')}</span></div>}
                    </section>
                  })}
                </div>
              )}
            </main>

            <aside className="min-w-0 bg-slate-950/40 p-4">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">最终解析顺序</h3>
              <div className="mt-3 space-y-3">
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="flex items-center gap-2 text-xs text-slate-200"><Link2 className="h-3.5 w-3.5 text-indigo-300" aria-hidden="true" />运行时候选</div><ol className="mt-2 space-y-2">{selected.resolved_candidates?.map((item, index) => <li key={`${identity(item)}-${index}`} className="rounded border border-slate-800 px-2 py-1.5"><div className="flex items-center justify-between gap-2"><span className="font-mono text-[10px] text-slate-300">{index + 1}. {item.provider_id}/{item.model}</span>{item.fallback_only && <StatePill neutral>兜底</StatePill>}</div><div className="mt-1 text-[10px] text-slate-600">智能度 {item.intelligence} · ${(Number(item.cost_input_1m || 0) + Number(item.cost_output_1m || 0)).toFixed(3)}/M 总价</div></li>)}</ol></div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-y-1 text-[10px]"><dt className="text-slate-600">来源</dt><dd className="text-slate-400">{selected.legacy?.source || '-'}</dd><dt className="text-slate-600">Provider</dt><dd className="truncate font-mono text-slate-300">{selected.legacy?.provider_id || '-'}</dd><dt className="text-slate-600">Model</dt><dd className="truncate font-mono text-slate-300" title={selected.legacy?.model}>{selected.legacy?.model || '-'}</dd></dl></div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">支持的 Driver</div><div className="mt-2 flex flex-wrap gap-1">{selected.supported_driver_types?.map(driver => <DriverBadge key={driver} driver={driver} />)}</div></div>
                {routeStatus?.task_contracts?.length > 0 && <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">Task / Output Contract</div><div className="mt-2 space-y-2">{routeStatus.task_contracts.map(contract => <div key={contract.task_key} className="rounded border border-slate-800 px-2 py-1.5"><div className="font-mono text-[10px] text-slate-300">{contract.task_key}</div><div className="mt-1 text-[10px] text-slate-600">{contract.output_contract_id} · {contract.output_failure_policy}</div></div>)}</div></div>}
                {selected.legacy?.binding_error && <InlineNotice tone="red">{selected.legacy.binding_error}</InlineNotice>}
              </div>
            </aside>
          </div>
        </div>
      )}
    </div>
  )
}
