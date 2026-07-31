/* eslint-disable react-refresh/only-export-components */
import { AlertCircle, CheckCircle2, DatabaseZap, Search } from 'lucide-react'

export const inputClass = 'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50'
export const compactInputClass = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 outline-none transition-colors focus:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50'

export function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item?.msg || item?.message || JSON.stringify(item)).join('\n')
  if (detail && typeof detail === 'object') return detail.message || detail.error || JSON.stringify(detail)
  return error?.message || fallback
}

export function formatTime(value) {
  if (!value) return '从未'
  return String(value).replace('T', ' ').replace(/\.\d+(?=[+-]|Z|$)/, '')
}

export function driverLabel(driver) {
  return {
    openai: 'OpenAI-compatible',
    anthropic: 'Anthropic Messages',
    codex: 'Codex OAuth',
  }[driver] || driver || '未知驱动'
}

export function DriverBadge({ driver }) {
  const tone = {
    openai: 'border-cyan-500/20 bg-cyan-500/10 text-cyan-300',
    anthropic: 'border-orange-500/20 bg-orange-500/10 text-orange-300',
    codex: 'border-violet-500/20 bg-violet-500/10 text-violet-300',
  }[driver] || 'border-slate-700 bg-slate-800 text-slate-300'
  return <span className={`inline-flex rounded border px-1.5 py-0.5 font-mono text-[10px] ${tone}`}>{driver || 'unknown'}</span>
}

export function StatePill({ ok, children, neutral = false }) {
  const tone = neutral
    ? 'border-slate-700 bg-slate-800/70 text-slate-300'
    : ok
      ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
      : 'border-amber-500/20 bg-amber-500/10 text-amber-300'
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${tone}`}>
      {!neutral && (ok ? <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> : <AlertCircle className="h-3 w-3" aria-hidden="true" />)}
      {children}
    </span>
  )
}

export function FormField({ id, label, hint, required, children, className = '' }) {
  return (
    <div className={className}>
      <label htmlFor={id} className="flex items-center gap-1 text-[11px] font-medium text-slate-400">
        {label}{required && <span className="text-rose-400">*</span>}
      </label>
      <div className="mt-1">{children}</div>
      {hint && <p className="mt-1 text-[10px] leading-4 text-slate-500">{hint}</p>}
    </div>
  )
}

export function EditorSection({ title, description, actions, children }) {
  return (
    <section className="border-b border-slate-800 px-4 py-4 last:border-b-0 sm:px-5">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-200">{title}</h3>
          {description && <p className="mt-1 max-w-2xl text-[11px] leading-4 text-slate-500">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

export function ConsoleRail({ title, count, query, onQuery, action, children }) {
  return (
    <aside className="flex min-h-[24rem] flex-col border-b border-slate-800 bg-slate-950/60 lg:min-h-0 lg:border-b-0 lg:border-r">
      <div className="border-b border-slate-800 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-xs font-semibold text-slate-200">{title}</h2>
            <p className="mt-0.5 text-[10px] text-slate-500">共 {count} 项</p>
          </div>
          {action}
        </div>
        {onQuery && (
          <label className="relative block">
            <span className="sr-only">搜索{title}</span>
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-600" aria-hidden="true" />
            <input value={query} onChange={event => onQuery(event.target.value)} placeholder="筛选..." className={`${compactInputClass} pl-8`} />
          </label>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">{children}</div>
    </aside>
  )
}

export function RailItem({ active, title, subtitle, meta, icon: Icon = DatabaseZap, onClick, badges }) {
  return (
    <button type="button" onClick={onClick} className={`w-full cursor-pointer rounded-md border px-2.5 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:ring-indigo-400 ${active ? 'border-indigo-500/30 bg-indigo-500/10' : 'border-transparent hover:border-slate-800 hover:bg-slate-900'}`}>
      <div className="flex items-start gap-2">
        <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${active ? 'text-indigo-300' : 'text-slate-600'}`} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-slate-200" title={title}>{title}</div>
          {subtitle && <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500" title={subtitle}>{subtitle}</div>}
          {meta && <div className="mt-1 truncate text-[10px] text-slate-600">{meta}</div>}
          {badges && <div className="mt-1.5 flex flex-wrap gap-1">{badges}</div>}
        </div>
      </div>
    </button>
  )
}

export function InlineNotice({ tone = 'slate', children, role }) {
  const classes = {
    slate: 'border-slate-800 bg-slate-950 text-slate-400',
    emerald: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300',
    amber: 'border-amber-500/20 bg-amber-500/10 text-amber-300',
    red: 'border-red-500/20 bg-red-500/10 text-red-300',
    blue: 'border-blue-500/20 bg-blue-500/10 text-blue-300',
  }
  return <div role={role} className={`rounded-md border px-3 py-2 text-xs leading-5 ${classes[tone] || classes.slate}`}>{children}</div>
}

export function EmptyEditor({ title, description, action }) {
  return (
    <div className="flex min-h-[28rem] items-center justify-center p-6 text-center">
      <div className="max-w-sm">
        <DatabaseZap className="mx-auto h-7 w-7 text-slate-700" aria-hidden="true" />
        <h2 className="mt-3 text-sm font-medium text-slate-300">{title}</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        {action && <div className="mt-4">{action}</div>}
      </div>
    </div>
  )
}

export function Toggle({ id, checked, onChange, label, disabled = false }) {
  return (
    <label htmlFor={id} className={`flex items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <span className="text-xs text-slate-300">{label}</span>
      <input id={id} type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} disabled={disabled} className="h-4 w-4 cursor-pointer accent-indigo-500 disabled:cursor-not-allowed" />
    </label>
  )
}
