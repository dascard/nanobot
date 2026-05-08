import { useState, useEffect, useCallback } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1/admin' })
api.interceptors.request.use(c => {
  const t = localStorage.getItem('nanobot_token')
  if (t) c.headers.Authorization = `Bearer ${t}`
  return c
})

function useAuth() {
  const [token, setToken] = useState(localStorage.getItem('nanobot_token') || '')
  const login = (t) => { localStorage.setItem('nanobot_token', t); setToken(t) }
  const logout = () => { localStorage.removeItem('nanobot_token'); setToken('') }
  return { token, login, logout, isLoggedIn: !!token }
}

// ── Login ──
function Login({ onLogin }) {
  const [t, setT] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setErr(''); setLoading(true)
    try {
      await axios.get('/api/v1/admin/me', { headers: { Authorization: `Bearer ${t}` } })
      onLogin(t)
    } catch { setErr('令牌验证失败') }
    finally { setLoading(false) }
  }
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(34,197,94,0.08),transparent_70%)]" />
      <form onSubmit={submit} className="relative bg-slate-800/60 backdrop-blur-xl p-8 rounded-2xl w-96 border border-slate-700/50 shadow-2xl">
        <div className="w-12 h-12 bg-emerald-500/20 rounded-xl flex items-center justify-center mx-auto mb-4">
          <span className="text-emerald-400 text-xl font-bold">N</span>
        </div>
        <h1 className="text-xl text-white mb-6 text-center font-semibold tracking-tight">Nanobot Admin</h1>
        {err && <div className="text-red-400 text-sm mb-4 text-center bg-red-500/10 py-2 rounded-lg">{err}</div>}
        <input type="password" value={t} onChange={e => setT(e.target.value)}
          placeholder="API 令牌" className="w-full p-3 rounded-xl bg-slate-900/80 text-white mb-4 border border-slate-600/50 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none transition-all" />
        <button disabled={loading}
          className="w-full p-3 bg-emerald-600 text-white rounded-xl hover:bg-emerald-500 disabled:opacity-50 font-medium transition-all">
          {loading ? '验证中...' : '登录'}
        </button>
      </form>
    </div>
  )
}

// ── Layout ──
const NAV = [
  { to: '/', label: '仪表盘', end: true },
  { to: '/stickers', label: '表情包' },
  { to: '/blocks', label: '屏蔽' },
  { to: '/configs', label: '配置' },
  { to: '/settings', label: '设置' },
  { to: '/db', label: '数据库' },
  { to: '/logs', label: '日志' },
  { to: '/prompt', label: '提示词' },
]

function Layout({ children, onLogout }) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 flex">
      <nav className="w-48 bg-slate-900/80 backdrop-blur-sm border-r border-slate-800 p-4 flex flex-col gap-0.5">
        <div className="flex items-center gap-2 mb-6 px-2">
          <div className="w-7 h-7 bg-emerald-500/20 rounded-lg flex items-center justify-center">
            <span className="text-emerald-400 text-sm font-bold">N</span>
          </div>
          <span className="text-sm font-semibold text-white tracking-wide">Nanobot</span>
        </div>
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end}
            className={({ isActive }) =>
              `flex items-center px-3 py-2 rounded-lg text-sm transition-all duration-200 focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isActive ? 'bg-emerald-500/15 text-emerald-400 font-medium' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'}`}>
            {n.label}
          </NavLink>
        ))}
        <button onClick={onLogout}
          className="mt-auto px-3 py-2 text-sm text-slate-500 hover:text-red-400 transition-colors duration-200 rounded-lg focus-visible:ring-2 focus-visible:ring-red-500/50">
          退出
        </button>
      </nav>
      <main className="flex-1 p-6 overflow-auto">{children}</main>
    </div>
  )
}

// ── Shared ──
function Card({ children, className = '' }) {
  return <div className={`bg-slate-900/60 backdrop-blur-sm border border-slate-800 rounded-xl ${className}`}>{children}</div>
}
function Modal({ children, onClose, wide }) {
  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate-in fade-in" onClick={onClose}>
      <div className={`bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl max-h-[85vh] overflow-auto ${wide ? 'w-[32rem]' : 'w-96'}`} onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}
function Pagination({ page, total, limit, onChange }) {
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
function Spinner() {
  return <div className="flex items-center justify-center py-20"><div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" /></div>
}

function AuthImage({ url, alt, className, onClick }) {
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

// ── Dashboard ──
function Dashboard() {
  const [stats, setStats] = useState({})
  useEffect(() => {
    Promise.all([
      api.get('/stickers?limit=1'), api.get('/block-rules?limit=1'),
      api.get('/configs?limit=1'),
    ]).then(([s, b, c]) => setStats({ stickers: s.data.total, blocks: b.data.total, configs: c.data.total }))
      .catch(() => {})
  }, [])
  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">仪表盘</h1>
      <p className="text-slate-500 text-sm mb-6">系统概览与快捷操作</p>
      <div className="grid grid-cols-3 gap-4 mb-6">
        <DashCard title="表情包" value={stats.stickers} to="/stickers" color="emerald" />
        <DashCard title="屏蔽规则" value={stats.blocks} to="/blocks" color="blue" />
        <DashCard title="流配置" value={stats.configs} to="/configs" color="amber" />
      </div>
      <Card className="p-5">
        <h3 className="text-sm font-medium text-slate-400 mb-3">快捷操作</h3>
        <div className="flex gap-3">
          <a href="/api/v1/admin/db/backup"
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm transition-colors flex items-center gap-2">
            <svg className="w-4 h-4 inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m0 0l-6-6m6 6l6-6"/></svg> 下载数据库备份
          </a>
        </div>
      </Card>
    </div>
  )
}
function DashCard({ title, value, to, color }) {
  const c = { emerald: 'border-emerald-500/30 hover:border-emerald-400', blue: 'border-blue-500/30 hover:border-blue-400', amber: 'border-amber-500/30 hover:border-amber-400' }[color]
  return (
    <NavLink to={to} className={`block bg-slate-900/60 backdrop-blur-sm border ${c} rounded-xl p-5 hover:bg-slate-800/60 transition-all`}>
      <div className="text-slate-400 text-xs mb-1">{title}</div>
      <div className="text-3xl font-bold">{value ?? '...'}</div>
    </NavLink>
  )
}

// ── Stickers ──
function StickersPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [search, setSearch] = useState('')
  const [sf, setSf] = useState('')
  const [page, setPage] = useState(1)
  const [edit, setEdit] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [preview, setPreview] = useState(null)
  const [selected, setSelected] = useState(new Set())

  const load = useCallback(() => {
    api.get('/stickers', { params: { search, page, limit: 20, status: sf } }).then(r => setData(r.data))
  }, [search, page, sf])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">表情包管理</h1>
          <p className="text-slate-500 text-sm">{data.total} 个表情包</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium transition-colors">+ 新建</button>
      </div>
      <div className="flex gap-2 mb-4">
        <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} placeholder="搜索名称/描述..."
          className="flex-1 p-2.5 rounded-xl bg-slate-900 border border-slate-700 focus:border-emerald-500 outline-none text-sm transition-colors" />
        <select value={sf} onChange={e => { setSf(e.target.value); setPage(1) }}
          className="p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
          <option value="">全部</option><option value="active">启用</option>
          <option value="disabled">禁用</option><option value="deleted">已删除</option>
        </select>
        <button onClick={load} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm transition-colors">搜索</button>
      </div>
      {selected.size > 0 && (
        <div className="mb-3 px-4 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl flex items-center gap-3">
          <span className="text-sm text-emerald-400">已选 {selected.size} 个</span>
          <button onClick={() => { if (confirm(`确认软删除 ${selected.size} 个表情包?`)) api.post('/stickers/batch-delete', { ids: [...selected] }).then(r => { setSelected(new Set()); load(); alert(`已删除 ${r.data.deleted} 个`) }) }}
            className="px-3 py-1 bg-red-600 hover:bg-red-500 rounded-lg text-xs font-medium transition-colors">批量删除</button>
          <button onClick={() => setSelected(new Set())} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">取消选择</button>
        </div>
      )}
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-2 font-medium"><input type="checkbox" checked={selected.size === data.items.length && data.items.length > 0}
              onChange={e => setSelected(e.target.checked ? new Set(data.items.map(s => s.id)) : new Set())} className="accent-emerald-500" /></th>
            <th className="py-2 px-2 font-medium">预览</th><th className="py-2 px-2 font-medium">名称</th><th className="py-2 px-2 font-medium">描述</th><th className="py-2 px-2 font-medium">状态</th><th className="py-2 px-2 font-medium">使用</th><th className="py-2 px-2 font-medium">操作</th></tr></thead>
          <tbody>
            {data.items.map(s => (
              <tr key={s.id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-1.5 px-2"><input type="checkbox" checked={selected.has(s.id)}
                  onChange={e => { const ns = new Set(selected); e.target.checked ? ns.add(s.id) : ns.delete(s.id); setSelected(ns) }} className="accent-emerald-500" /></td>
                <td className="py-1.5 px-2">
                  <div className="w-10 h-10 rounded-lg bg-slate-800 overflow-hidden cursor-pointer hover:ring-2 ring-emerald-500/50 transition-all"
                    onClick={() => setPreview(`/api/v1/admin/stickers/${s.id}/preview`)}>
                    <AuthImage url={`/api/v1/admin/stickers/${s.id}/preview`} alt={s.name} className="w-full h-full object-cover" />
                  </div>
                </td>
                <td className="py-1.5 px-2 truncate max-w-[120px]">{s.name || '-'}</td>
                <td className="py-1.5 px-2 truncate max-w-[200px] text-slate-400">{s.description || '-'}</td>
                <td className="py-1.5 px-2">
                  <div className="flex items-center gap-1.5">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${s.status === 'active' ? 'bg-emerald-500/15 text-emerald-400' : s.status === 'disabled' ? 'bg-amber-500/15 text-amber-400' : 'bg-red-500/15 text-red-400'}`}>{s.status}</span>
                    {s.preview_status && s.preview_status !== 'ok' && s.preview_status !== 'pending' && (
                      <span className={`px-1.5 py-0.5 rounded text-xs ${
                        s.preview_status === 'expired' ? 'bg-red-500/15 text-red-400' :
                        s.preview_status === 'fetch_failed' ? 'bg-amber-500/15 text-amber-400' :
                        s.preview_status === 'invalid_image' ? 'bg-orange-500/15 text-orange-400' :
                        'bg-slate-700 text-slate-300'}`}>{s.preview_status}</span>
                    )}
                    {s.preview_status && s.preview_status !== 'ok' && (
                      <button onClick={() => api.post(`/stickers/${s.id}/preview/retry`).then(load)}
                        className="px-1.5 py-0.5 bg-amber-700/50 hover:bg-amber-700 text-amber-300 rounded text-xs" title="重试缓存">↻</button>
                    )}
                  </div>
                </td>
                <td className="py-1.5 px-2 text-slate-400">{s.usage_count}</td>
                <td className="py-1.5 px-2">
                  <div className="flex gap-1">
                    <button onClick={() => setEdit(s)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">编辑</button>
                    {s.status !== 'deleted' ? (
                      <>
                        <button onClick={() => api.post(`/stickers/${s.id}/${s.status === 'active' ? 'disable' : 'enable'}`).then(load)}
                          className={`px-2 py-1 rounded-lg text-xs transition-colors ${s.status === 'active' ? 'bg-amber-700/50 hover:bg-amber-700 text-amber-300' : 'bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300'}`}>
                          {s.status === 'active' ? '禁用' : '启用'}
                        </button>
                        <button onClick={() => { if (confirm('确认删除?')) api.delete(`/stickers/${s.id}`).then(load) }}
                          className="px-2 py-1 bg-red-700/50 hover:bg-red-700 text-red-300 rounded-lg text-xs transition-colors">删除</button>
                      </>
                    ) : (
                      <button onClick={() => api.put(`/stickers/${s.id}`, { status: 'disabled' }).then(load)}
                        className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300 rounded-lg text-xs transition-colors">恢复</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Pagination page={page} total={data.total} limit={20} onChange={setPage} />
      {showCreate && <StickerCreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {edit && <StickerEditModal sticker={edit} onClose={() => setEdit(null)} onSaved={load} />}
      {preview && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 cursor-pointer" onClick={() => setPreview(null)}>
          <AuthImage url={preview} alt="preview" className="max-h-[80vh] max-w-[80vw] object-contain rounded-xl shadow-2xl" />
        </div>
      )}
    </div>
  )
}

function StickerCreateModal({ onClose, onCreated }) {
  const [f, setF] = useState({ file_ref: '', name: '', description: '', group_id: '', status: 'active', tags: '', emotions: '' })
  return (
    <Modal onClose={onClose}>
      <div className="p-6">
        <h2 className="text-lg font-bold mb-4">新建表情包</h2>
        <label className="text-xs text-slate-400 mb-1 block">文件引用 (URL/CQ码)</label>
        <input value={f.file_ref} onChange={e => setF({ ...f, file_ref: e.target.value })}
          placeholder="https://... 或 CQ 码" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        {f.file_ref && <div className="text-xs text-slate-500 mb-3 p-2 bg-slate-900 rounded-lg">创建后将通过后端代理预览</div>}
        <input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="名称" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <textarea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="描述" rows={2} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <input value={f.group_id} onChange={e => setF({ ...f, group_id: e.target.value })} placeholder="群号 (留空=全局)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm">
          <option value="active">启用</option><option value="disabled">禁用</option></select>
        <input value={f.tags} onChange={e => setF({ ...f, tags: e.target.value })} placeholder="标签 (逗号分隔)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <input value={f.emotions} onChange={e => setF({ ...f, emotions: e.target.value })} placeholder="情感 (逗号分隔)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-4 text-sm" />
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={() => {
            if (!f.file_ref.trim()) { alert('file_ref 不能为空'); return }
            api.post('/stickers', {
              file_ref: f.file_ref, name: f.name, description: f.description,
              group_id: f.group_id, status: f.status,
              tags: f.tags.split(',').map(s => s.trim()).filter(Boolean),
              emotions: f.emotions.split(',').map(s => s.trim()).filter(Boolean),
            }).then(() => { onCreated(); onClose() }).catch(e => alert(e.response?.data?.detail || '创建失败'))
          }} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">创建</button>
        </div>
      </div>
    </Modal>
  )
}

function StickerEditModal({ sticker, onClose, onSaved }) {
  const [f, setF] = useState({
    name: sticker.name || '', description: sticker.description || '',
    tags: (sticker.tags || []).join(','), emotions: (sticker.emotions || []).join(','),
    status: sticker.status || 'active',
  })
  return (
    <Modal onClose={onClose}>
      <div className="p-6">
        <h2 className="text-lg font-bold mb-1">编辑 #{sticker.id}</h2>
        <p className="text-xs text-slate-500 mb-4">{sticker.file_ref?.substring(0, 80)}</p>
        <AuthImage url={`/api/v1/admin/stickers/${sticker.id}/preview`} alt="preview"
          className="w-full h-32 object-contain rounded-lg mb-4 border border-slate-700 bg-slate-950" />
        <input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="名称" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <textarea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="描述" rows={2} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm">
          <option value="active">启用</option><option value="disabled">禁用</option><option value="deleted">已删除</option></select>
        <input value={f.tags} onChange={e => setF({ ...f, tags: e.target.value })} placeholder="标签 (逗号分隔)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <input value={f.emotions} onChange={e => setF({ ...f, emotions: e.target.value })} placeholder="情感 (逗号分隔)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-4 text-sm" />
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={() => {
            api.put(`/stickers/${sticker.id}`, {
              name: f.name, description: f.description, status: f.status,
              tags: f.tags.split(',').map(s => s.trim()).filter(Boolean),
              emotions: f.emotions.split(',').map(s => s.trim()).filter(Boolean),
            }).then(() => { onSaved(); onClose() })
          }} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Block Rules ──
function BlocksPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [showCreate, setShowCreate] = useState(false)
  const load = useCallback(() => { api.get('/block-rules', { params: { limit: 50 } }).then(r => setData(r.data)) }, [])
  useEffect(() => { load() }, [load])
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">屏蔽规则</h1>
          <p className="text-slate-500 text-sm">{data.total} 条规则</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium transition-colors">+ 新建</button>
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3 font-medium">用户</th><th className="py-2 px-3 font-medium">类型</th><th className="py-2 px-3 font-medium">模式</th><th className="py-2 px-3 font-medium">原因</th><th className="py-2 px-3 font-medium">状态</th><th className="py-2 px-3 font-medium">操作</th></tr></thead>
          <tbody>
            {data.items.map(r => (
              <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-2 px-3">{r.user_id}</td>
                <td className="py-2 px-3 text-slate-400">{r.target_type}</td>
                <td className="py-2 px-3 text-slate-400">{r.rule_mode}</td>
                <td className="py-2 px-3 truncate max-w-[200px] text-slate-400">{r.reason || '-'}</td>
                <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded-full text-xs ${r.enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'}`}>{r.enabled ? 'ON' : 'OFF'}</span></td>
                <td className="py-2 px-3">
                  <div className="flex gap-1">
                    <button onClick={() => api.put(`/block-rules/${r.id}`, { enabled: r.enabled ? 0 : 1 }).then(load)}
                      className={`px-2 py-1 rounded-lg text-xs transition-colors ${r.enabled ? 'bg-amber-700/50 hover:bg-amber-700 text-amber-300' : 'bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300'}`}>{r.enabled ? '禁用' : '启用'}</button>
                    <button onClick={() => { if (confirm('确认删除?')) api.delete(`/block-rules/${r.id}`).then(load) }}
                      className="px-2 py-1 bg-red-700/50 hover:bg-red-700 text-red-300 rounded-lg text-xs transition-colors">删除</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {showCreate && <BlockCreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
    </div>
  )
}
function BlockCreateModal({ onClose, onCreated }) {
  const [f, setF] = useState({ user_id: '', target_type: 'private', group_id: '', rule_mode: 'log_only', reason: '' })
  return (
    <Modal onClose={onClose}>
      <div className="p-6">
        <h2 className="text-lg font-bold mb-4">新建屏蔽规则</h2>
        <input value={f.user_id} onChange={e => setF({ ...f, user_id: e.target.value })} placeholder="用户 ID" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        <select value={f.target_type} onChange={e => setF({ ...f, target_type: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm">
          <option value="private">私聊</option><option value="group">群聊</option><option value="all">全部</option></select>
        {f.target_type === 'group' && <input value={f.group_id} onChange={e => setF({ ...f, group_id: e.target.value })} placeholder="群号" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />}
        <input value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} placeholder="原因" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-4 text-sm" />
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={() => api.post('/block-rules', f).then(() => { onCreated(); onClose() })}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">创建</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Configs ──
function ConfigsPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [edit, setEdit] = useState(null)
  const load = useCallback(() => { api.get('/configs', { params: { limit: 50 } }).then(r => setData(r.data)) }, [])
  useEffect(() => { load() }, [load])
  return (
    <div>
      <div className="mb-4"><h1 className="text-2xl font-bold">流配置</h1><p className="text-slate-500 text-sm">{data.total} 个配置</p></div>
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-2 font-medium">流 ID</th><th className="py-2 px-2 font-medium">发言</th><th className="py-2 px-2 font-medium w-10">@</th><th className="py-2 px-2 font-medium w-10">E</th><th className="py-2 px-2 font-medium w-10">L</th><th className="py-2 px-2 font-medium w-10">J</th><th className="py-2 px-2 font-medium">平滑</th><th className="py-2 px-2 font-medium"></th></tr></thead>
          <tbody>
            {data.items.map(c => (
              <tr key={c.chat_stream_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-2 px-2 truncate max-w-[300px] text-xs text-slate-400">{c.chat_stream_id}</td>
                <td className="py-2 px-2">{c.talk_value}</td>
                <td className="py-2 px-2">{c.mentioned_bot_reply ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.use_expression ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.enable_expression_learning ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.enable_jargon_learning ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.planner_smooth}</td>
                <td className="py-2 px-2"><button onClick={() => setEdit(c)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">编辑</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {edit && <ConfigEditModal config={edit} onClose={() => setEdit(null)} onSaved={load} />}
    </div>
  )
}
function ConfigEditModal({ config, onClose, onSaved }) {
  const [f, setF] = useState({
    talk_value: config.talk_value, mentioned_bot_reply: config.mentioned_bot_reply,
    use_expression: config.use_expression, enable_expression_learning: config.enable_expression_learning,
    enable_jargon_learning: config.enable_jargon_learning, planner_smooth: config.planner_smooth,
  })
  return (
    <Modal onClose={onClose}>
      <div className="p-6">
        <h2 className="text-lg font-bold mb-1">编辑配置</h2>
        <p className="text-xs text-slate-500 mb-4">{config.chat_stream_id}</p>
        <label className="text-xs text-slate-400">发言 Value</label>
        <input type="number" step="0.05" min="0.05" max="1" value={f.talk_value}
          onChange={e => setF({ ...f, talk_value: parseFloat(e.target.value) })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm" />
        {['mentioned_bot_reply', 'use_expression', 'enable_expression_learning', 'enable_jargon_learning'].map(k => (
          <label key={k} className="flex items-center gap-2 mb-2 text-sm text-slate-400">
            <input type="checkbox" checked={f[k]} onChange={e => setF({ ...f, [k]: e.target.checked })} className="accent-emerald-500" />{k}
          </label>
        ))}
        <label className="text-xs text-slate-400">Planner 平滑</label>
        <input type="number" value={f.planner_smooth} onChange={e => setF({ ...f, planner_smooth: parseInt(e.target.value) })}
          className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-4 text-sm" />
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={() => {
            api.put(`/configs/${encodeURIComponent(config.chat_stream_id)}`, {
              ...f, mentioned_bot_reply: f.mentioned_bot_reply ? 1 : 0, use_expression: f.use_expression ? 1 : 0,
              enable_expression_learning: f.enable_expression_learning ? 1 : 0, enable_jargon_learning: f.enable_jargon_learning ? 1 : 0,
            }).then(() => { onSaved(); onClose() })
          }} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Settings ──
function SettingsPage() {
  const [data, setData] = useState(null)
  const load = () => api.get('/settings').then(r => setData(r.data))
  useEffect(() => { load() }, [])
  const update = (key, value) => { api.put(`/settings/${encodeURIComponent(key)}`, { value }).then(load) }
  const categories = [...new Set((data?.settings || []).map(s => s.category))]
  return (
    <div>
      <div className="mb-4"><h1 className="text-2xl font-bold">系统设置</h1><p className="text-slate-500 text-sm">热重载配置，修改即时生效</p></div>
      {categories.map(cat => (
        <div key={cat} className="mb-6">
          <h2 className="text-sm font-semibold text-emerald-400 mb-3 uppercase tracking-wider">{cat}</h2>
          <div className="space-y-2">
            {(data?.settings || []).filter(s => s.category === cat).map(s => (
              <Card key={s.key} className="p-4 flex items-center gap-4">
                <div className="flex-1 min-w-0">
                  <div className="text-sm">{s.key}</div>
                  <div className="text-xs text-slate-500">{s.description}</div>
                </div>
                {s.value_type === 'bool' ? (
                  <button onClick={() => !s.readonly && update(s.key, !s.value)} disabled={s.readonly}
                    className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-colors ${s.readonly ? 'bg-slate-800 text-slate-600 cursor-not-allowed' : s.value ? 'bg-emerald-600 text-white hover:bg-emerald-500' : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}>
                    {s.value ? 'ON' : 'OFF'}</button>
                ) : (
                  <input type={s.value_type === 'int' || s.value_type === 'float' ? 'number' : 'text'}
                    defaultValue={s.value} step={s.value_type === 'float' ? '0.1' : '1'} min={s.min_value} max={s.max_value} disabled={s.readonly}
                    className={`w-28 p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm text-center ${s.readonly ? 'opacity-50 cursor-not-allowed' : ''}`}
                    onBlur={e => { const v = e.target.value.trim(); if (!v || v === String(s.value)) return; const p = s.value_type === 'float' ? parseFloat(v) : parseInt(v); if (Number.isNaN(p)) { e.target.value = s.value; return } update(s.key, p) }} />
                )}
                {s.restart_required && <span className="text-amber-500 text-xs">需重启</span>}
                {s.readonly && <span className="text-slate-600 text-xs">只读</span>}
              </Card>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── 数据库浏览 ──
function DbPage() {
  const [tables, setTables] = useState([])
  const [sel, setSel] = useState('')
  const [rows, setRows] = useState({ columns: [], rows: [], total: 0 })
  const [sql, setSql] = useState('')
  const [sqlResult, setSqlResult] = useState(null)
  useEffect(() => { api.get('/db/tables').then(r => setTables(r.data.tables)) }, [])
  const queryTable = (t) => { setSel(t); api.get(`/db/tables/${t}`, { params: { limit: 50 } }).then(r => setRows(r.data)) }
  return (
    <div>
      <div className="mb-4"><h1 className="text-2xl font-bold">数据库浏览</h1><p className="text-slate-500 text-sm">只读数据浏览</p></div>
      <Card className="p-3 mb-4">
        <div className="flex gap-1.5 flex-wrap">
          {tables.map(t => <button key={t} onClick={() => queryTable(t)}
            className={`px-3 py-1.5 rounded-lg text-xs transition-colors ${sel === t ? 'bg-emerald-600 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-400'}`}>{t}</button>)}
        </div>
      </Card>
      {sel && rows.columns.length > 0 && (
        <Card className="mb-4 overflow-x-auto">
          <div className="p-3 border-b border-slate-800 text-sm text-slate-400">{sel} ({rows.total} rows)</div>
          <table className="w-full text-xs">
            <thead><tr>{rows.columns.map(c => <th key={c} className="px-3 py-2 text-left text-slate-500 font-medium whitespace-nowrap">{c}</th>)}</tr></thead>
            <tbody>{rows.rows.map((r, i) => <tr key={i} className="border-t border-slate-800/50 hover:bg-slate-800/30">{rows.columns.map(c => <td key={c} className="px-3 py-1.5 max-w-[200px] truncate whitespace-nowrap">{r[c]}</td>)}</tr>)}</tbody>
          </table>
        </Card>
      )}
      <Card className="p-4">
        <h2 className="text-sm font-medium text-slate-400 mb-2">SQL 查询 (只读)</h2>
        <textarea value={sql} onChange={e => setSql(e.target.value)} rows={3} placeholder="SELECT ..."
          className="w-full p-3 rounded-xl bg-slate-900 border border-slate-700 font-mono text-sm mb-2" />
        <button onClick={() => api.post('/db/query', { query: sql }).then(r => setSqlResult(r.data)).catch(e => alert(e.response?.data?.detail || e.message))}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">运行</button>
        {sqlResult && (
          <div className="mt-3 overflow-x-auto">
            <div className="text-xs text-slate-500 mb-1">{sqlResult.row_count} rows</div>
            <table className="w-full text-xs"><thead><tr>{sqlResult.columns.map(c => <th key={c} className="px-2 py-1 text-left text-slate-500">{c}</th>)}</tr></thead>
              <tbody>{sqlResult.rows.map((r, i) => <tr key={i} className="border-t border-slate-800/50">{sqlResult.columns.map(c => <td key={c} className="px-2 py-0.5">{r[c]}</td>)}</tr>)}</tbody></table>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Logs ──
function LogsPage() {
  const [files, setFiles] = useState([])
  const [sel, setSel] = useState('')
  const [content, setContent] = useState('')
  const [lines, setLines] = useState(200)
  useEffect(() => { api.get('/logs').then(r => setFiles(r.data.files)) }, [])

  const loadLog = (name) => {
    setSel(name)
    api.get(`/logs/${encodeURIComponent(name)}?lines=${lines}`).then(r => setContent(r.data.content))
  }

  const formatSize = (s) => s < 1024 ? `${s}B` : s < 1048576 ? `${(s/1024).toFixed(1)}KB` : `${(s/1048576).toFixed(1)}MB`

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">日志</h1>
      <div className="flex gap-4" style={{ height: 'calc(100vh - 140px)' }}>
        <div className="w-56 flex-shrink-0 space-y-1 overflow-auto">
          {files.map(f => (
            <button key={f.name} onClick={() => loadLog(f.name)}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${sel === f.name ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}>
              <div className="truncate">{f.name}</div>
              <div className="text-slate-600">{formatSize(f.size)}</div>
            </button>
          ))}
        </div>
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-sm text-slate-400">行数:</span>
            <select value={lines} onChange={e => { setLines(Number(e.target.value)); if (sel) loadLog(sel) }}
              className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
              <option value="100">100</option><option value="200">200</option><option value="500">500</option><option value="1000">1000</option>
            </select>
            {sel && <button onClick={() => loadLog(sel)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>}
          </div>
          <pre className="flex-1 p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs leading-relaxed overflow-auto text-slate-300 font-mono whitespace-pre-wrap">{content || '点击左侧文件查看'}</pre>
        </div>
      </div>
    </div>
  )
}

// ── Prompt ──
function PromptPage() {
  const [prompt, setPrompt] = useState('')
  const [frags, setFrags] = useState([])
  const [editing, setEditing] = useState(null)
  const [editContent, setEditContent] = useState('')
  const [building, setBuilding] = useState(false)
  const [tab, setTab] = useState('fragments')
  const [toast, setToast] = useState('')

  const load = () => {
    api.get('/prompt').then(r => setPrompt(r.data.content)).catch(() => {})
    api.get('/prompt/fragments').then(r => setFrags(r.data.fragments)).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const origContent = frags.find(f => f.name === editing)?.content || ''
  const dirty = editing && editContent !== origContent

  const openEditor = (f) => {
    if (dirty && editing !== f.name && !confirm('当前修改未保存，确认切换？')) return
    setEditing(f.name)
    setEditContent(f.content)
  }
  const closeEditor = (force = false) => {
    if (!force && dirty && !confirm('当前修改未保存，确认关闭？')) return
    setEditing(null)
    setEditContent('')
  }
  const saveFragment = () => {
    if (!editing || !dirty) return
    api.put(`/prompt/fragments/${encodeURIComponent(editing)}`, { content: editContent }).then(() => {
      setToast('已保存，记得重新构建 prompt.md 才能生效')
      setEditing(null)
      setEditContent('')
      load()
    }).catch(e => alert(e.response?.data?.detail || '保存失败'))
  }
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && editing) {
        e.preventDefault()
        saveFragment()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [editing, editContent])

  const rebuild = () => {
    setBuilding(true)
    api.post('/prompt/build').then(r => {
      if (r.data.ok) {
        setToast('构建成功: ' + r.data.output)
        load()
      } else {
        alert('构建失败\n' + (r.data.stderr || r.data.error || ''))
      }
    }).finally(() => setBuilding(false))
  }
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 3000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div>
      {toast && <div className="mb-3 px-4 py-2 bg-emerald-500/15 border border-emerald-500/30 rounded-xl text-sm text-emerald-400">{toast}</div>}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">提示词</h1>
          <div className="flex gap-1 bg-slate-900 rounded-lg p-0.5">
            <button onClick={() => setTab('fragments')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'fragments' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>编辑片段</button>
            <button onClick={() => setTab('preview')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'preview' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>完整 prompt.md</button>
          </div>
        </div>
        <button onClick={rebuild} disabled={building}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium transition-colors">
          {building ? '构建中...' : '重新构建 prompt.md'}
        </button>
      </div>

      {tab === 'preview' ? (
        <Card className="p-4">
          <pre className="text-xs leading-relaxed whitespace-pre-wrap max-h-[calc(100vh-200px)] overflow-auto text-slate-300">{prompt}</pre>
        </Card>
      ) : (
        <div className="flex gap-4" style={{ height: 'calc(100vh - 160px)' }}>
          {/* Fragment list */}
          <div className="w-56 flex-shrink-0 space-y-1 overflow-auto">
            <div className="text-xs text-slate-500 px-1 mb-2">{frags.length} 个片段</div>
            {frags.map(f => (
              <button key={f.name}
                onClick={() => openEditor(f)}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors truncate block ${editing === f.name ? 'bg-emerald-500/15 text-emerald-400 font-medium' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}>
                {f.name}
              </button>
            ))}
          </div>
          {/* Editor panel */}
          <div className="flex-1 flex flex-col min-w-0">
            {editing ? (
              <>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-medium text-emerald-400">{editing}</h2>
                    {dirty && <span className="text-xs text-amber-400">● 未保存</span>}
                  </div>
                  <div className="flex gap-2">
                    <button onClick={closeEditor}
                      className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">取消</button>
                    <button onClick={saveFragment}
                      className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium transition-colors">保存</button>
                  </div>
                </div>
                <textarea value={editContent}
                  onChange={e => setEditContent(e.target.value)}
                  className="flex-1 w-full p-4 rounded-xl bg-slate-900 border border-slate-700 text-sm text-slate-300 font-mono leading-relaxed resize-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none" />
                <div className="text-xs text-slate-600 mt-1">
                  Ctrl+S 或 Cmd+S 保存 · 保存后需点"重新构建 prompt.md"生效 · {editContent.length} 字符
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-slate-600 text-sm">
                点击左侧片段开始编辑
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── App ──
export default function App() {
  const auth = useAuth()
  if (!auth.isLoggedIn) return <Login onLogin={auth.login} />
  return (
    <BrowserRouter>
      <Layout onLogout={auth.logout}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/stickers" element={<StickersPage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/configs" element={<ConfigsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/db" element={<DbPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/prompt" element={<PromptPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
