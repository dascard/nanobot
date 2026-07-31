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
  RailItem,
  StatePill,
  formatApiError,
  inputClass,
} from './modelConsoleUi'

function initialCandidates(binding) {
  const source = binding?.binding?.candidates || []
  return source.map(item => ({ preset_id: item.preset_id, selected_variations: { ...(item.selected_variations || {}) } }))
}

export function RouteBindingsPanel({ bindings, presets, statusRoutes, onChanged }) {
  const [selectedKey, setSelectedKey] = useState(bindings[0]?.route_key || '')
  const [draftCandidates, setDraftCandidates] = useState(() => initialCandidates(bindings[0]))
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  const selected = bindings.find(item => item.route_key === selectedKey) || bindings[0]
  const routeStatus = selected ? statusRoutes?.[selected.route_key] : null

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const current = bindings.find(item => item.route_key === selectedKey)
      if (current) setDraftCandidates(initialCandidates(current))
      else if (bindings[0]) {
        setSelectedKey(bindings[0].route_key)
        setDraftCandidates(initialCandidates(bindings[0]))
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [bindings, selectedKey])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return bindings
    return bindings.filter(item => `${item.route_key} ${item.label} ${item.owner} ${item.domain}`.toLowerCase().includes(needle))
  }, [bindings, query])

  const compatiblePresets = useMemo(() => {
    if (!selected) return []
    return presets.filter(preset => selected.supported_driver_types?.includes(preset.driver_type))
  }, [presets, selected])

  const chooseRoute = binding => {
    setSelectedKey(binding.route_key)
    setDraftCandidates(initialCandidates(binding))
    setMessage(null)
  }

  const addCandidate = () => {
    const available = compatiblePresets.find(preset => !draftCandidates.some(item => item.preset_id === preset.id))
    if (!available) { setMessage({ tone: 'amber', text: '没有更多兼容且未添加的 Preset。' }); return }
    setDraftCandidates(current => [...current, { preset_id: available.id, selected_variations: {} }])
  }

  const updateCandidate = (index, patch) => setDraftCandidates(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item))
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
    if (!selected || draftCandidates.length === 0) { setMessage({ tone: 'amber', text: '至少添加一个 Primary Preset。' }); return }
    setSaving(true)
    setMessage(null)
    try {
      await api.put(`/models/bindings/${encodeURIComponent(selected.route_key)}`, { candidates: draftCandidates })
      setMessage({ tone: 'emerald', text: 'Route Binding 已保存；候选顺序即运行时 fallback 顺序。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Route Binding 保存失败') })
    } finally { setSaving(false) }
  }

  const clear = async () => {
    if (!selected?.binding || !window.confirm(`清除 Route「${selected.route_key}」的 Preset Binding 并恢复 Legacy 配置？`)) return
    setSaving(true)
    try {
      await api.delete(`/models/bindings/${encodeURIComponent(selected.route_key)}`)
      setMessage({ tone: 'emerald', text: '已清除直接 Binding，Route 恢复 Legacy 或继承配置。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Binding 清除失败') })
    } finally { setSaving(false) }
  }

  const migrate = async () => {
    if (!selected) return
    setSaving(true)
    setMessage(null)
    try {
      await api.post(`/models/routes/${encodeURIComponent(selected.route_key)}/migrate-to-preset`, {})
      setMessage({ tone: 'emerald', text: 'Legacy Route 已迁移为独立 Preset，并完成 Primary Binding。' })
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Route 迁移失败') })
    } finally { setSaving(false) }
  }

  return (
    <div className="grid overflow-hidden rounded-lg border border-slate-800 bg-slate-900 lg:h-[calc(100dvh-15.5rem)] lg:min-h-[38rem] lg:grid-cols-[18rem_minmax(0,1fr)]">
      <ConsoleRail title="Route Bindings" count={bindings.length} query={query} onQuery={setQuery}>
        {filtered.map(item => {
          const candidates = item.effective_binding?.candidates || []
          return <RailItem key={item.route_key} active={selected?.route_key === item.route_key} icon={GitBranch} title={item.label || item.route_key} subtitle={item.route_key} meta={candidates.length ? `${candidates.length} 个 Preset 候选` : `Legacy · ${item.legacy?.model || '未配置'}`} onClick={() => chooseRoute(item)} badges={<>{item.binding ? <StatePill ok>已绑定</StatePill> : item.effective_binding ? <StatePill neutral>继承 Binding</StatePill> : <StatePill ok={false}>Legacy</StatePill>}<span className="rounded border border-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">{item.route_type}</span></>} />
        })}
      </ConsoleRail>

      {!selected ? <EmptyEditor title="选择一个 Route" description="Route Binding 将业务用途映射到一个 Primary Preset 和有序 fallback Presets。" /> : (
        <div className="min-w-0 overflow-y-auto">
          <header className="sticky top-0 z-10 flex flex-col gap-3 border-b border-slate-800 bg-slate-900/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold text-white">{selected.label}</h2><span className="font-mono text-[10px] text-slate-500">{selected.route_key}</span><StatePill neutral>{selected.route_type}</StatePill></div><p className="mt-1 text-[11px] text-slate-500">Route 只管理业务关系；模型参数回到 Model Preset 编辑。</p></div>
            <div className="flex shrink-0 flex-wrap gap-2">{!selected.effective_binding && <ActionButton type="button" onClick={migrate} disabled={saving}>迁移 Legacy</ActionButton>}{selected.binding && <ActionButton type="button" tone="red" onClick={clear} disabled={saving} className="gap-1.5"><Unlink className="h-3.5 w-3.5" aria-hidden="true" />清除 Binding</ActionButton>}<ActionButton type="button" tone="emerald" onClick={save} disabled={saving || draftCandidates.length === 0} className="gap-1.5"><Save className="h-3.5 w-3.5" aria-hidden="true" />{saving ? '保存中...' : '保存 Binding'}</ActionButton></div>
          </header>

          <div className="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_20rem]">
            <main className="min-w-0 p-4 sm:p-5 xl:border-r xl:border-slate-800">
              {message && <div className="mb-4"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
              {!selected.binding && selected.effective_binding?.inherited_from && <div className="mb-4"><InlineNotice tone="blue">当前 Binding 继承自 <span className="font-mono">{selected.effective_binding.inherited_from}</span>。保存后会为当前 Route 建立独立覆盖。</InlineNotice></div>}
              {!selected.effective_binding && <div className="mb-4"><InlineNotice tone="amber">当前仍使用 Legacy 的 provider + model + route params。点击“迁移 Legacy”会生成一个等价 Preset，再建立 Binding，不会删除旧设置。</InlineNotice></div>}

              <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="text-xs font-semibold text-slate-200">候选链</h3><p className="mt-1 text-[11px] text-slate-500">从上到下尝试；熔断或调用失败后进入下一个 Preset。</p></div><ActionButton type="button" onClick={addCandidate} disabled={compatiblePresets.length === 0} className="gap-1.5"><Plus className="h-3.5 w-3.5" aria-hidden="true" />添加 fallback</ActionButton></div>

              {draftCandidates.length === 0 ? <EmptyEditor title="尚未建立 Preset Binding" description="迁移当前 Legacy 配置，或从已有兼容 Preset 中添加 Primary 候选。" action={<ActionButton tone="blue" onClick={addCandidate} disabled={compatiblePresets.length === 0}>添加 Primary Preset</ActionButton>} /> : (
                <div className="space-y-3">
                  {draftCandidates.map((candidate, index) => {
                    const preset = presets.find(item => item.id === candidate.preset_id)
                    const groups = preset?.variation_groups || {}
                    return (
                      <section key={`${candidate.preset_id}-${index}`} className="rounded-lg border border-slate-800 bg-slate-950/50">
                        <div className="flex flex-col gap-3 border-b border-slate-800 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex min-w-0 items-center gap-3"><span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold ${index === 0 ? 'bg-indigo-500/20 text-indigo-200' : 'bg-slate-800 text-slate-400'}`}>{index + 1}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-slate-200">{index === 0 ? 'Primary' : `Fallback ${index}`}</span>{preset && <DriverBadge driver={preset.driver_type} />}{preset && (!preset.enabled || !preset.provider_enabled) && <StatePill ok={false}>不可用</StatePill>}</div><div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">{preset ? `${preset.provider_id}/${preset.model}` : 'Preset 不存在'}</div></div></div>
                          <div className="flex shrink-0 gap-1"><button type="button" onClick={() => moveCandidate(index, -1)} disabled={index === 0} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30" aria-label="上移候选"><ArrowUp className="h-3.5 w-3.5" /></button><button type="button" onClick={() => moveCandidate(index, 1)} disabled={index === draftCandidates.length - 1} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30" aria-label="下移候选"><ArrowDown className="h-3.5 w-3.5" /></button><button type="button" onClick={() => removeCandidate(index)} className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-slate-500 hover:bg-red-500/10 hover:text-red-300" aria-label="移除候选"><Trash2 className="h-3.5 w-3.5" /></button></div>
                        </div>
                        <div className="grid gap-3 p-3 sm:grid-cols-2">
                          <FormField id={`route-binding-preset-${index}`} label="Model Preset"><select id={`route-binding-preset-${index}`} value={candidate.preset_id} onChange={event => updateCandidate(index, { preset_id: event.target.value, selected_variations: {} })} className={inputClass}>{compatiblePresets.map(item => <option key={item.id} value={item.id}>{item.display_name || item.id} · {item.provider_id}/{item.model}</option>)}</select></FormField>
                          {Object.keys(groups).length === 0 ? <div className="flex items-end pb-2 text-[11px] text-slate-600">此 Preset 没有 Variation Groups</div> : Object.entries(groups).map(([group, options]) => <FormField key={group} id={`route-binding-${index}-${group}`} label={`Variation · ${group}`}><select id={`route-binding-${index}-${group}`} value={candidate.selected_variations?.[group] || ''} onChange={event => updateCandidate(index, { selected_variations: { ...(candidate.selected_variations || {}), [group]: event.target.value } })} className={inputClass}><option value="">不覆盖</option>{Object.keys(options || {}).map(option => <option key={option} value={option}>{option}</option>)}</select></FormField>)}
                        </div>
                      </section>
                    )
                  })}
                </div>
              )}
            </main>

            <aside className="min-w-0 bg-slate-950/40 p-4">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">关系与诊断</h3>
              <div className="mt-3 space-y-3">
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="flex items-center gap-2 text-xs text-slate-200"><Link2 className="h-3.5 w-3.5 text-indigo-300" aria-hidden="true" />有效解析</div><dl className="mt-2 grid grid-cols-[5rem_minmax(0,1fr)] gap-y-1 text-[10px]"><dt className="text-slate-600">来源</dt><dd className="text-slate-400">{selected.legacy?.source || '-'}</dd><dt className="text-slate-600">Preset</dt><dd className="truncate font-mono text-slate-300">{selected.legacy?.profile_id || 'Legacy direct'}</dd><dt className="text-slate-600">Provider</dt><dd className="truncate font-mono text-slate-300">{selected.legacy?.provider_id || '-'}</dd><dt className="text-slate-600">Model</dt><dd className="truncate font-mono text-slate-300" title={selected.legacy?.model}>{selected.legacy?.model || '-'}</dd></dl></div>
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">支持的 Driver</div><div className="mt-2 flex flex-wrap gap-1">{selected.supported_driver_types?.map(driver => <DriverBadge key={driver} driver={driver} />)}</div><p className="mt-2 text-[10px] leading-4 text-slate-600">`reply` 由 KT Agent Runtime 执行，可使用三种 Driver；同步分类/任务 Route 目前只接受 OpenAI-compatible。</p></div>
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
