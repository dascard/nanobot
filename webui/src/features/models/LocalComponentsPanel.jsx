import { useState } from 'react'
import { Cpu, Flame, TestTube2 } from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import { InlineNotice, StatePill, formatApiError } from './modelConsoleUi'

export function LocalComponentsPanel({ components }) {
  const [results, setResults] = useState({})

  const run = async (component, action) => {
    setResults(current => ({ ...current, [component]: { loading: true, action } }))
    try {
      const response = await api.post(`/models/local/${component}/${action}`)
      setResults(current => ({ ...current, [component]: response.data }))
    } catch (error) {
      setResults(current => ({ ...current, [component]: { ok: false, error: formatApiError(error) } }))
    }
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {Object.entries(components || {}).map(([key, component]) => {
        const result = results[key]
        return (
          <section key={key} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="flex min-w-0 items-start gap-3"><div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-800 text-slate-400"><Cpu className="h-4 w-4" aria-hidden="true" /></div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-xs font-semibold text-slate-200">{key}</h2><StatePill ok={component.configured}>{component.load_state || (component.configured ? 'configured' : 'unavailable')}</StatePill></div><div className="mt-1 truncate font-mono text-[10px] text-slate-500" title={component.model}>{component.model}</div></div></div><div className="flex shrink-0 gap-2"><ActionButton onClick={() => run(key, 'warmup')} disabled={result?.loading} className="gap-1"><Flame className="h-3.5 w-3.5" />预热</ActionButton><ActionButton tone="blue" onClick={() => run(key, 'test')} disabled={result?.loading} className="gap-1"><TestTube2 className="h-3.5 w-3.5" />测试</ActionButton></div></div>
            <dl className="mt-4 grid grid-cols-[5rem_minmax(0,1fr)] gap-y-1 text-[11px]"><dt className="text-slate-600">职责</dt><dd className="text-slate-400">{component.role || '-'}</dd><dt className="text-slate-600">加载方式</dt><dd className="text-slate-400">{component.loader || '-'}</dd><dt className="text-slate-600">触发</dt><dd className="text-slate-400">{component.trigger || '-'}</dd></dl>
            {component.note && <p className="mt-3 border-t border-slate-800 pt-3 text-[10px] leading-4 text-slate-600">{component.note}</p>}
            {result?.loading && <div className="mt-3 text-xs text-slate-500">{result.action === 'warmup' ? '预热中...' : '测试中...'}</div>}
            {result && !result.loading && <div className="mt-3" aria-live="polite"><InlineNotice tone={result.ok ? 'emerald' : 'red'}>{result.ok ? `操作成功${result.latency_ms != null ? ` · ${result.latency_ms}ms` : ''}${result.dim ? ` · dim ${result.dim}` : ''}` : result.error || '操作失败'}</InlineNotice></div>}
          </section>
        )
      })}
    </div>
  )
}
