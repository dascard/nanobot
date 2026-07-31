import { useDeferredValue, useEffect, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import { DriverBadge, InlineNotice, StatePill, formatApiError, formatTime, inputClass } from './modelConsoleUi'

export function ModelCatalogPanel({ providers, onChanged }) {
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query)
  const [providerId, setProviderId] = useState('')
  const [catalog, setCatalog] = useState([])
  const [references, setReferences] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const [catalogResponse, referenceResponse] = await Promise.all([
        api.get('/models/catalog', { params: { provider: providerId || undefined, q: deferredQuery || undefined, limit: 500 } }),
        api.get('/models/route-references'),
      ])
      setCatalog(catalogResponse.data.catalog || [])
      setReferences(referenceResponse.data.route_references || [])
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Model Catalog 加载失败') })
    } finally { setLoading(false) }
  }

  useEffect(() => {
    const timer = window.setTimeout(load, 0)
    return () => window.clearTimeout(timer)
  }, [providerId, deferredQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      const endpoint = providerId ? `/models/providers/${encodeURIComponent(providerId)}/catalog/refresh` : '/models/catalog/refresh'
      const response = await api.post(endpoint)
      const failed = response.data.results?.filter(item => !item.ok) || []
      setMessage({ tone: failed.length ? 'amber' : 'emerald', text: failed.length ? `${failed.length} 个 Provider 同步失败，已保留旧快照。` : 'Model Catalog 同步完成。' })
      await load()
      await onChanged?.()
    } catch (error) {
      setMessage({ tone: 'red', text: formatApiError(error, 'Model Catalog 同步失败') })
    } finally { setRefreshing(false) }
  }

  const usedBy = new Map()
  references.forEach(item => {
    const key = item.id
    usedBy.set(key, [...(usedBy.get(key) || []), item.route_key])
  })

  return (
    <section className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900">
      <header className="flex flex-col gap-3 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-end sm:justify-between sm:px-5">
        <div><h2 className="text-sm font-semibold text-slate-100">Model Catalog Snapshot</h2><p className="mt-1 text-[11px] text-slate-500">Catalog 是上游 `/models` 的发现快照，不承担 Preset 参数配置；stale 时保留最后一次成功结果。</p></div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end"><label htmlFor="model-catalog-query" className="block"><span className="text-[10px] text-slate-500">搜索模型</span><span className="relative mt-1 block"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-600" aria-hidden="true" /><input id="model-catalog-query" value={query} onChange={event => setQuery(event.target.value)} className={`${inputClass} min-w-52 pl-8`} /></span></label><label htmlFor="model-catalog-provider" className="block"><span className="text-[10px] text-slate-500">Provider</span><select id="model-catalog-provider" value={providerId} onChange={event => setProviderId(event.target.value)} className={`${inputClass} mt-1 min-w-48`}><option value="">全部 OpenAI Catalog</option>{providers.filter(item => item.model_discovery_supported).map(item => <option key={item.id} value={item.id}>{item.display_name || item.id}</option>)}</select></label><ActionButton tone="emerald" onClick={refresh} disabled={refreshing} className="gap-1.5"><RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />{refreshing ? '同步中...' : '同步目录'}</ActionButton></div>
      </header>
      {message && <div className="px-4 pt-4 sm:px-5"><InlineNotice tone={message.tone} role={message.tone === 'red' ? 'alert' : undefined}>{message.text}</InlineNotice></div>}
      <div className="overflow-x-auto p-4 sm:p-5">
        <table className="min-w-[760px] w-full text-left text-xs"><thead className="border-b border-slate-800 text-[10px] uppercase tracking-wide text-slate-600"><tr><th className="px-3 py-2">Model ID</th><th className="px-3 py-2">Provider</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">Route 引用</th><th className="px-3 py-2">更新时间</th></tr></thead><tbody>{loading && <tr><td colSpan="5" className="px-3 py-8 text-center text-slate-600">加载中...</td></tr>}{!loading && catalog.length === 0 && <tr><td colSpan="5" className="px-3 py-8 text-center text-slate-600">目录为空；请同步支持 Model Discovery 的 Provider。</td></tr>}{catalog.map(model => {
          const provider = providers.find(item => item.id === model.provider)
          return <tr key={model.id} className="border-b border-slate-800/70 transition-colors last:border-0 hover:bg-slate-800/30"><td className="px-3 py-2.5 font-mono text-slate-200">{model.model}</td><td className="px-3 py-2.5"><div className="flex items-center gap-2"><span className="text-slate-400">{model.provider}</span>{provider && <DriverBadge driver={provider.driver_type} />}</div></td><td className="px-3 py-2.5">{model.stale ? <StatePill ok={false}>stale</StatePill> : <StatePill ok>verified</StatePill>}</td><td className="px-3 py-2.5 text-slate-500">{(usedBy.get(model.id) || []).join(', ') || '-'}</td><td className="px-3 py-2.5 text-[10px] text-slate-600">{formatTime(model.updated_at)}</td></tr>
        })}</tbody></table>
      </div>
    </section>
  )
}
