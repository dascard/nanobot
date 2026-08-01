import { useEffect, useState } from 'react'

import { api } from '../api'

export function Card({ children, className = '', ...props }) {
  return <div {...props} className={`rounded-lg border border-slate-800 bg-slate-900 ${className}`}>{children}</div>
}

export function ViewportPage({ children, className = '' }) {
  return (
    <div className={`flex h-full min-h-0 flex-col ${className}`}>
      {children}
    </div>
  )
}

export function Modal({ children, onClose, wide }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm animate-in fade-in" onClick={onClose}>
      <div className={`max-h-[88vh] overflow-auto rounded-lg border border-slate-700 bg-slate-900 shadow-2xl ${wide ? 'w-[min(96vw,42rem)]' : 'w-[min(96vw,28rem)]'}`} onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

export function PageHeader({ title, description, actions, meta, className = '' }) {
  return (
    <div className={`mb-4 flex shrink-0 flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-start md:justify-between ${className}`}>
      <div className="min-w-0">
        <h1 className="text-lg font-semibold leading-7 text-slate-50 md:text-xl">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Toolbar({ children, className = '' }) {
  return <div className={`mb-4 flex flex-wrap items-end gap-2 rounded-lg border border-slate-800 bg-slate-900 p-3 ${className}`}>{children}</div>
}

export function SectionHeader({ title, description, actions }) {
  return (
    <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0">
        <h2 className="text-sm font-medium text-slate-200">{title}</h2>
        {description && <p className="mt-1 text-[11px] leading-4 text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Field({ id, label, children, className = '', hint }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <label htmlFor={id} className="block text-[11px] font-medium text-slate-400">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {hint && <div className="mt-1 text-[11px] font-normal text-slate-500">{hint}</div>}
    </div>
  )
}

export function IconButton({ label, icon: Icon, className = '', size = 'sm', ...props }) {
  const sizes = size === 'xs' ? 'h-7 w-7' : 'h-8 w-8'
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className={`inline-flex ${sizes} shrink-0 cursor-pointer items-center justify-center rounded-lg border border-slate-700 bg-slate-800 text-slate-300 transition-colors hover:border-slate-600 hover:bg-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {Icon ? <Icon className={size === 'xs' ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" /> : null}
    </button>
  )
}

export function Pagination({ page, total, limit, onChange }) {
  const maxPage = Math.ceil(total / limit)
  if (maxPage <= 1) return null
  return (
    <div className="flex items-center gap-3 mt-4 text-sm">
      <button onClick={() => onChange(p => p - 1)} disabled={page <= 1}
        className="px-3 py-1.5 bg-slate-800 rounded-lg disabled:opacity-30 hover:bg-slate-700 transition-colors">← 上一页</button>
      <span className="text-slate-400">{page}/{maxPage} 页 ({total})</span>
      <button onClick={() => onChange(p => p + 1)} disabled={page >= maxPage}
        className="px-3 py-1.5 bg-slate-800 rounded-lg disabled:opacity-30 hover:bg-slate-700 transition-colors">下一页 →</button>
    </div>
  )
}

export function Spinner() {
  return <div className="flex items-center justify-center py-20"><div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" /></div>
}

export function Badge({ children, tone = 'slate', className = '' }) {
  const tones = {
    emerald: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
    amber: 'bg-amber-500/15 text-amber-300 border-amber-500/20',
    red: 'bg-red-500/15 text-red-400 border-red-500/20',
    blue: 'bg-blue-500/15 text-blue-300 border-blue-500/20',
    purple: 'bg-purple-500/15 text-purple-300 border-purple-500/20',
    slate: 'bg-slate-800 text-slate-300 border-slate-700',
  }
  return <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs ${tones[tone] || tones.slate} ${className}`}>{children}</span>
}

export function MiniStat({ label, value, tone = 'slate', onClick }) {
  const color = {
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
    red: 'text-red-300',
    blue: 'text-blue-300',
    slate: 'text-white',
  }[tone] || 'text-white'
  const title = typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  return (
    <Card className={`p-3 min-h-[72px] transition-colors ${onClick ? 'cursor-pointer hover:bg-slate-800/60' : ''}`} onClick={onClick}>
      <div className="text-[11px] text-slate-400 mb-1 truncate">{label}</div>
      <div className={`text-lg font-semibold leading-tight ${color} truncate`} title={title}>{value ?? '...'}</div>
    </Card>
  )
}

export function InfoGrid({ items = [], columns = 'md:grid-cols-4' }) {
  return (
    <div className={`grid grid-cols-1 ${columns} gap-2`}>
      {items.filter(Boolean).map(item => (
        <div key={item.label} className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 min-w-0">
          <div className="text-[11px] text-slate-500 mb-0.5 truncate">{item.label}</div>
          <div className={`text-xs font-medium truncate ${item.className || 'text-slate-300'}`} title={typeof item.value === 'string' || typeof item.value === 'number' ? String(item.value) : ''}>
            {item.value ?? '-'}
          </div>
        </div>
      ))}
    </div>
  )
}

export function ActionButton({ children, tone = 'slate', className = '', ...props }) {
  const tones = {
    emerald: 'bg-emerald-600 hover:bg-emerald-500 text-white disabled:bg-emerald-900/40',
    red: 'bg-red-700/70 hover:bg-red-700 text-red-50 disabled:bg-red-950/40',
    amber: 'bg-amber-700/50 hover:bg-amber-700 text-amber-100 disabled:bg-amber-950/40',
    blue: 'bg-blue-700/70 hover:bg-blue-700 text-blue-50 disabled:bg-blue-950/40',
    slate: 'bg-slate-800 hover:bg-slate-700 text-slate-200 disabled:bg-slate-900',
  }
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone] || tones.slate} ${className}`}
    >
      {children}
    </button>
  )
}

export function JsonBlock({ value, className = '' }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value || {}, null, 2)
  return <pre className={`rounded-lg bg-slate-950 border border-slate-800 p-3 text-xs leading-relaxed text-slate-300 overflow-auto whitespace-pre-wrap ${className}`}>{text || '-'}</pre>
}

export function AuthImage({ url, alt, className, onClick }) {
  const [src, setSrc] = useState('')
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let objUrl = ''
    const path = url.replace('/api/v1/admin', '')
    api.get(path, { responseType: 'blob' })
      .then(r => { objUrl = URL.createObjectURL(r.data); setSrc(objUrl) })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false))
    return () => { if (objUrl) URL.revokeObjectURL(objUrl) }
  }, [url])

  if (loading) return <div className={`flex items-center justify-center bg-slate-800 ${className}`}>...</div>
  if (failed) return <div className={`flex items-center justify-center bg-slate-800 text-slate-600 text-xs ${className}`}>img</div>
  return <img src={src} alt={alt} className={className} loading="lazy" onClick={onClick} />
}
