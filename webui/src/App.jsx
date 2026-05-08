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
    } catch {
      setErr('Token 验证失败')
    } finally { setLoading(false) }
  }
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-900">
      <form onSubmit={submit} className="bg-gray-800 p-8 rounded-xl w-96">
        <h1 className="text-2xl text-white mb-6 text-center">Nanobot Admin</h1>
        {err && <div className="text-red-400 text-sm mb-4 text-center">{err}</div>}
        <input type="password" value={t} onChange={e => setT(e.target.value)}
          placeholder="API Token" className="w-full p-3 rounded bg-gray-700 text-white mb-4 border border-gray-600" />
        <button disabled={loading} className="w-full p-3 bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50">
          {loading ? '验证中...' : '登录'}
        </button>
      </form>
    </div>
  )
}

function Layout({ children, onLogout }) {
  const links = [
    { to: '/', label: 'Dashboard', end: true },
    { to: '/stickers', label: 'Stickers' },
    { to: '/blocks', label: 'Block Rules' },
    { to: '/configs', label: 'Configs' },
    { to: '/db', label: 'DB Browser' },
    { to: '/prompt', label: 'Prompt' },
  ]
  return (
    <div className="min-h-screen bg-gray-900 text-gray-100 flex">
      <nav className="w-48 bg-gray-800 p-4 flex flex-col gap-1">
        <h2 className="text-lg font-bold mb-4 text-blue-400">Nanobot</h2>
        {links.map(l => (
          <NavLink key={l.to} to={l.to} end={l.end}
            className={({ isActive }) => `px-3 py-2 rounded text-sm ${isActive ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}>
            {l.label}
          </NavLink>
        ))}
        <button onClick={onLogout} className="mt-auto px-3 py-2 text-sm text-red-400 hover:text-red-300">退出</button>
      </nav>
      <main className="flex-1 p-6 overflow-auto">{children}</main>
    </div>
  )
}

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
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>
      <div className="grid grid-cols-3 gap-4">
        <Card title="Stickers" value={stats.stickers} to="/stickers" />
        <Card title="Block Rules" value={stats.blocks} to="/blocks" />
        <Card title="Configs" value={stats.configs} to="/configs" />
      </div>
      <div className="mt-6">
        <a href="/api/v1/admin/db/backup" className="inline-block px-4 py-3 bg-green-700 rounded hover:bg-green-600">下载数据库备份</a>
      </div>
    </div>
  )
}
function Card({ title, value, to }) {
  return (
    <NavLink to={to} className="bg-gray-800 p-6 rounded-xl hover:bg-gray-700">
      <div className="text-gray-400 text-sm">{title}</div>
      <div className="text-3xl font-bold mt-2">{value ?? '...'}</div>
    </NavLink>
  )
}

// ── Stickers ──
function StickersPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [edit, setEdit] = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(() => {
    api.get('/stickers', { params: { search, page, limit: 20, status: statusFilter } }).then(r => setData(r.data))
  }, [search, page, statusFilter])

  useEffect(() => { load() }, [load])

  const restoreSticker = (s, toStatus) => {
    api.put(`/stickers/${s.id}`, { status: toStatus }).then(load)
  }

  const statusColor = (s) => s === 'active' ? 'text-green-400' : s === 'disabled' ? 'text-yellow-400' : 'text-red-400'

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Sticker 管理</h1>
      <div className="flex gap-2 mb-4">
        <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} placeholder="搜索..."
          className="p-2 rounded bg-gray-700 border border-gray-600 flex-1" />
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="p-2 rounded bg-gray-700 border border-gray-600">
          <option value="">全部</option><option value="active">Active</option>
          <option value="disabled">Disabled</option><option value="deleted">Deleted</option>
        </select>
        <button onClick={() => { setPage(1); load() }} className="px-4 py-2 bg-blue-600 rounded">搜索</button>
        <button onClick={() => setShowCreate(true)} className="px-4 py-2 bg-green-600 rounded">新建</button>
      </div>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-gray-400"><th>ID</th><th>Name</th><th>Desc</th><th>Status</th><th>Usage</th><th>Actions</th></tr></thead>
        <tbody>
          {data.items.map(s => (
            <tr key={s.id} className="border-t border-gray-700">
              <td className="py-1">{s.id}</td>
              <td>{s.name || '-'}</td>
              <td className="max-w-xs truncate">{s.description || '-'}</td>
              <td><span className={statusColor(s.status)}>{s.status}</span></td>
              <td>{s.usage_count}</td>
              <td className="flex gap-1 flex-wrap">
                <button onClick={() => setEdit(s)} className="px-2 py-0.5 bg-gray-600 rounded text-xs">编辑</button>
                {s.status !== 'deleted' && (
                  <button onClick={() => api.post(`/stickers/${s.id}/${s.status === 'active' ? 'disable' : 'enable'}`).then(load)}
                    className={`px-2 py-0.5 rounded text-xs ${s.status === 'active' ? 'bg-yellow-700' : 'bg-green-700'}`}>
                    {s.status === 'active' ? '禁用' : '启用'}</button>
                )}
                {s.status !== 'deleted' ? (
                  <button onClick={() => { if (confirm('确认删除?')) api.delete(`/stickers/${s.id}`).then(load) }}
                    className="px-2 py-0.5 bg-red-700 rounded text-xs">删除</button>
                ) : (
                  <button onClick={() => restoreSticker(s, 'disabled')}
                    className="px-2 py-0.5 bg-green-700 rounded text-xs">恢复</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pagination page={page} total={data.total} limit={20} onChange={setPage} />
      {showCreate && <StickerCreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {edit && <StickerEditModal sticker={edit} onClose={() => setEdit(null)} onSaved={load} />}
    </div>
  )
}

function StickerCreateModal({ onClose, onCreated }) {
  const [f, setF] = useState({ file_ref: '', name: '', description: '', group_id: '', status: 'active', tags: '', emotions: '' })
  return (
    <Modal onClose={onClose}>
      <h2 className="text-lg font-bold mb-4">新建 Sticker</h2>
      <input value={f.file_ref} onChange={e => setF({ ...f, file_ref: e.target.value })} placeholder="file_ref (URL/CQ码)" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Name" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <textarea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="Description" className="w-full p-2 rounded bg-gray-700 mb-2" rows={2} />
      <input value={f.group_id} onChange={e => setF({ ...f, group_id: e.target.value })} placeholder="Group ID (留空=全局)" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} className="w-full p-2 rounded bg-gray-700 mb-2">
        <option value="active">Active</option><option value="disabled">Disabled</option>
      </select>
      <input value={f.tags} onChange={e => setF({ ...f, tags: e.target.value })} placeholder="Tags (逗号分隔)" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <input value={f.emotions} onChange={e => setF({ ...f, emotions: e.target.value })} placeholder="Emotions (逗号分隔)" className="w-full p-2 rounded bg-gray-700 mb-4" />
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 bg-gray-600 rounded">取消</button>
        <button onClick={() => {
          if (!f.file_ref.trim()) { alert('file_ref 不能为空'); return }
          api.post('/stickers', {
            file_ref: f.file_ref, name: f.name, description: f.description,
            group_id: f.group_id, status: f.status,
            tags: f.tags.split(',').map(s => s.trim()).filter(Boolean),
            emotions: f.emotions.split(',').map(s => s.trim()).filter(Boolean),
          }).then(() => { onCreated(); onClose() }).catch(e => alert(e.response?.data?.detail || '创建失败'))
        }} className="px-4 py-2 bg-blue-600 rounded">创建</button>
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
      <h2 className="text-lg font-bold mb-4">编辑 Sticker #{sticker.id}</h2>
      <input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Name" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <textarea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="Description" className="w-full p-2 rounded bg-gray-700 mb-2" rows={3} />
      <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} className="w-full p-2 rounded bg-gray-700 mb-2">
        <option value="active">Active</option><option value="disabled">Disabled</option><option value="deleted">Deleted</option>
      </select>
      <input value={f.tags} onChange={e => setF({ ...f, tags: e.target.value })} placeholder="Tags (逗号分隔)" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <input value={f.emotions} onChange={e => setF({ ...f, emotions: e.target.value })} placeholder="Emotions (逗号分隔)" className="w-full p-2 rounded bg-gray-700 mb-4" />
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 bg-gray-600 rounded">取消</button>
        <button onClick={() => {
          api.put(`/stickers/${sticker.id}`, {
            name: f.name, description: f.description, status: f.status,
            tags: f.tags.split(',').map(s => s.trim()).filter(Boolean),
            emotions: f.emotions.split(',').map(s => s.trim()).filter(Boolean),
          }).then(() => { onSaved(); onClose() })
        }} className="px-4 py-2 bg-blue-600 rounded">保存</button>
      </div>
    </Modal>
  )
}

// ── Block Rules ──
function BlocksPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [showCreate, setShowCreate] = useState(false)
  const load = useCallback(() => {
    api.get('/block-rules', { params: { limit: 50 } }).then(r => setData(r.data))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <div className="flex justify-between mb-4">
        <h1 className="text-2xl font-bold">Block Rules</h1>
        <button onClick={() => setShowCreate(true)} className="px-4 py-2 bg-blue-600 rounded">新建</button>
      </div>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-gray-400"><th>ID</th><th>User ID</th><th>Type</th><th>Mode</th><th>Reason</th><th>Enabled</th><th>Actions</th></tr></thead>
        <tbody>
          {data.items.map(r => (
            <tr key={r.id} className="border-t border-gray-700">
              <td className="py-1">{r.id}</td><td>{r.user_id}</td><td>{r.target_type}</td><td>{r.rule_mode}</td>
              <td className="max-w-xs truncate">{r.reason || '-'}</td>
              <td><span className={r.enabled ? 'text-green-400' : 'text-red-400'}>{r.enabled ? 'ON' : 'OFF'}</span></td>
              <td className="flex gap-1">
                <button onClick={() => api.put(`/block-rules/${r.id}`, { enabled: r.enabled ? 0 : 1 }).then(load)}
                  className={`px-2 py-0.5 rounded text-xs ${r.enabled ? 'bg-yellow-700' : 'bg-green-700'}`}>{r.enabled ? '禁用' : '启用'}</button>
                <button onClick={() => { if (confirm('确认删除?')) api.delete(`/block-rules/${r.id}`).then(load) }}
                  className="px-2 py-0.5 bg-red-700 rounded text-xs">删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {showCreate && <BlockCreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
    </div>
  )
}

function BlockCreateModal({ onClose, onCreated }) {
  const [f, setF] = useState({ user_id: '', target_type: 'private', group_id: '', rule_mode: 'log_only', reason: '' })
  return (
    <Modal onClose={onClose}>
      <h2 className="text-lg font-bold mb-4">新建 Block Rule</h2>
      <input value={f.user_id} onChange={e => setF({ ...f, user_id: e.target.value })} placeholder="User ID" className="w-full p-2 rounded bg-gray-700 mb-2" />
      <select value={f.target_type} onChange={e => setF({ ...f, target_type: e.target.value })} className="w-full p-2 rounded bg-gray-700 mb-2">
        <option value="private">Private</option><option value="group">Group</option><option value="all">All</option>
      </select>
      {f.target_type === 'group' && <input value={f.group_id} onChange={e => setF({ ...f, group_id: e.target.value })} placeholder="Group ID" className="w-full p-2 rounded bg-gray-700 mb-2" />}
      <input value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} placeholder="Reason" className="w-full p-2 rounded bg-gray-700 mb-4" />
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 bg-gray-600 rounded">取消</button>
        <button onClick={() => api.post('/block-rules', f).then(() => { onCreated(); onClose() })} className="px-4 py-2 bg-blue-600 rounded">创建</button>
      </div>
    </Modal>
  )
}

// ── Configs ──
function ConfigsPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [edit, setEdit] = useState(null)
  const load = useCallback(() => {
    api.get('/configs', { params: { limit: 50 } }).then(r => setData(r.data))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Stream Configs</h1>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-gray-400"><th>Stream ID</th><th>Talk</th><th>@Reply</th><th>Expr</th><th>Learn</th><th>Jargon</th><th>Smooth</th><th></th></tr></thead>
        <tbody>
          {data.items.map(c => (
            <tr key={c.chat_stream_id} className="border-t border-gray-700">
              <td className="py-1 max-w-xs truncate">{c.chat_stream_id}</td>
              <td>{c.talk_value}</td>
              <td>{c.mentioned_bot_reply ? '✓' : '✗'}</td>
              <td>{c.use_expression ? '✓' : '✗'}</td>
              <td>{c.enable_expression_learning ? '✓' : '✗'}</td>
              <td>{c.enable_jargon_learning ? '✓' : '✗'}</td>
              <td>{c.planner_smooth}</td>
              <td><button onClick={() => setEdit(c)} className="px-2 py-0.5 bg-gray-600 rounded text-xs">编辑</button></td>
            </tr>
          ))}
        </tbody>
      </table>
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
      <h2 className="text-lg font-bold mb-4">编辑 {config.chat_stream_id}</h2>
      <label className="text-sm text-gray-400">Talk Value</label>
      <input type="number" step="0.05" min="0.05" max="1" value={f.talk_value}
        onChange={e => setF({ ...f, talk_value: parseFloat(e.target.value) })} className="w-full p-2 rounded bg-gray-700 mb-2" />
      {['mentioned_bot_reply', 'use_expression', 'enable_expression_learning', 'enable_jargon_learning'].map(k => (
        <label key={k} className="flex items-center gap-2 mb-2 text-sm text-gray-400">
          <input type="checkbox" checked={f[k]} onChange={e => setF({ ...f, [k]: e.target.checked })} /> {k}
        </label>
      ))}
      <label className="text-sm text-gray-400">Planner Smooth</label>
      <input type="number" value={f.planner_smooth} onChange={e => setF({ ...f, planner_smooth: parseInt(e.target.value) })}
        className="w-full p-2 rounded bg-gray-700 mb-4" />
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 bg-gray-600 rounded">取消</button>
        <button onClick={() => {
          api.put(`/configs/${encodeURIComponent(config.chat_stream_id)}`, {
            ...f, mentioned_bot_reply: f.mentioned_bot_reply ? 1 : 0,
            use_expression: f.use_expression ? 1 : 0, enable_expression_learning: f.enable_expression_learning ? 1 : 0,
            enable_jargon_learning: f.enable_jargon_learning ? 1 : 0,
          }).then(() => { onSaved(); onClose() })
        }} className="px-4 py-2 bg-blue-600 rounded">保存</button>
      </div>
    </Modal>
  )
}

// ── DB Browser ──
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
      <h1 className="text-2xl font-bold mb-4">DB Browser</h1>
      <div className="flex gap-2 mb-4 flex-wrap">
        {tables.map(t => <button key={t} onClick={() => queryTable(t)}
          className={`px-3 py-1 rounded text-sm ${sel === t ? 'bg-blue-600' : 'bg-gray-700'}`}>{t}</button>)}
      </div>
      {sel && rows.columns.length > 0 && (
        <div className="mb-6 overflow-x-auto">
          <h2 className="text-lg mb-2">{sel} ({rows.total} rows)</h2>
          <table className="text-xs"><thead><tr>{rows.columns.map(c => <th key={c} className="px-2 py-1 text-left text-gray-400 whitespace-nowrap">{c}</th>)}</tr></thead>
            <tbody>{rows.rows.map((r, i) => <tr key={i} className="border-t border-gray-700">{rows.columns.map(c => <td key={c} className="px-2 py-1 max-w-xs truncate whitespace-nowrap">{r[c]}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      <h2 className="text-lg font-bold mb-2">SQL Query (read-only)</h2>
      <textarea value={sql} onChange={e => setSql(e.target.value)} rows={4} placeholder="SELECT ..."
        className="w-full p-2 rounded bg-gray-700 mb-2 font-mono text-sm" />
      <button onClick={() => api.post('/db/query', { query: sql }).then(r => setSqlResult(r.data)).catch(e => alert(e.response?.data?.detail || e.message))}
        className="px-4 py-2 bg-blue-600 rounded mb-4">运行</button>
      {sqlResult && (
        <div className="overflow-x-auto">
          <table className="text-xs"><thead><tr>{sqlResult.columns.map(c => <th key={c} className="px-2 py-1 text-left text-gray-400">{c}</th>)}</tr></thead>
            <tbody>{sqlResult.rows.map((r, i) => <tr key={i} className="border-t border-gray-700">{sqlResult.columns.map(c => <td key={c} className="px-2 py-1">{r[c]}</td>)}</tr>)}</tbody>
          </table>
          <div className="text-sm text-gray-400 mt-1">{sqlResult.row_count} rows</div>
        </div>
      )}
    </div>
  )
}

// ── Prompt ──
function PromptPage() {
  const [prompt, setPrompt] = useState('')
  const [frags, setFrags] = useState([])
  useEffect(() => {
    api.get('/prompt').then(r => setPrompt(r.data.content))
    api.get('/prompt/fragments').then(r => setFrags(r.data.fragments))
  }, [])
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Prompt</h1>
      <div className="flex gap-4">
        <div className="flex-1">
          <h2 className="text-lg mb-2">Full Prompt</h2>
          <pre className="bg-gray-800 p-4 rounded text-xs max-h-screen overflow-auto whitespace-pre-wrap">{prompt}</pre>
        </div>
        <div className="w-64">
          <h2 className="text-lg mb-2">Fragments</h2>
          {frags.map(f => (
            <details key={f.name} className="mb-2 bg-gray-800 rounded">
              <summary className="p-2 text-sm cursor-pointer text-blue-400">{f.name}</summary>
              <pre className="p-2 text-xs whitespace-pre-wrap border-t border-gray-700">{f.content}</pre>
            </details>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Shared ──
function Modal({ children, onClose }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 p-6 rounded-xl w-96 max-h-96 overflow-auto" onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

function Pagination({ page, total, limit, onChange }) {
  const maxPage = Math.ceil(total / limit)
  if (maxPage <= 1) return null
  return (
    <div className="flex gap-2 mt-4">
      {page > 1 && <button onClick={() => onChange(p => p - 1)} className="px-3 py-1 bg-gray-700 rounded">上一页</button>}
      <span className="py-1 text-gray-400">第 {page}/{maxPage} 页 ({total})</span>
      {page < maxPage && <button onClick={() => onChange(p => p + 1)} className="px-3 py-1 bg-gray-700 rounded">下一页</button>}
    </div>
  )
}

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
          <Route path="/db" element={<DbPage />} />
          <Route path="/prompt" element={<PromptPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
