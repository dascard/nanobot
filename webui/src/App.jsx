import React, { useState, useEffect, useCallback, useRef } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom'
import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1/admin' })
api.interceptors.request.use(c => {
  const t = localStorage.getItem('nanobot_token')
  if (t) c.headers.Authorization = `Bearer ${t}`
  return c
})

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null }; }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  componentDidCatch(error, info) {
    console.error('WebUI ErrorBoundary:', error, info)
    api.post('/logs/frontend-error', {
      message: error?.message || '',
      stack: `${error?.stack || ''}\n${info?.componentStack || ''}`,
      url: window.location.href,
    }).catch(() => {})
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-64 flex items-center justify-center p-8">
          <div className="bg-slate-900 border border-red-800 rounded-xl p-8 max-w-lg text-center">
            <h2 className="text-xl font-bold text-red-400 mb-2">页面出错</h2>
            <p className="text-slate-400 text-sm mb-4">{this.state.error?.message || '未知错误'}</p>
            <button onClick={() => { this.setState({ hasError: false }); window.location.href = '/'; }}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-white transition-colors">
              回到首页
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

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
  { to: '/', label: '首页总览', end: true },
  { to: '/groups', label: '群聊运行' },
  { to: '/timing-gate', label: 'TimingGate' },
  { to: '/stickers', label: '表情包' },
  { to: '/stickers/duplicates', label: '去重工作台' },
  { to: '/prompt', label: 'Prompt' },
  { to: '/models', label: '模型' },
  { to: '/blocks', label: '屏蔽' },
  { to: '/logs', label: '日志' },
  { to: '/audit', label: '审计' },
  { to: '/configs', label: '群聊策略' },
  { to: '/settings', label: '设置' },
  { to: '/memory', label: '群体记忆' },
  { to: '/evals', label: 'Eval 评测' },
  { to: '/db', label: '数据库' },
]

function Layout({ children, onLogout }) {
  const [version, setVersion] = useState(null)
  const location = useLocation()
  useEffect(() => {
    api.get('/version').then(r => setVersion(r.data)).catch(() => setVersion(null))
  }, [])
  return (
    <div className="h-screen bg-slate-950 text-slate-200 flex overflow-hidden">
      <nav className="w-48 h-screen overflow-y-auto bg-slate-900/80 backdrop-blur-sm border-r border-slate-800 p-4 flex flex-col gap-0.5">
        <div className="mb-6 px-2">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 bg-emerald-500/20 rounded-lg flex items-center justify-center">
              <span className="text-emerald-400 text-sm font-bold">N</span>
            </div>
            <span className="text-sm font-semibold text-white tracking-wide">Nanobot</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-500 font-mono truncate" title={version?.full_commit || ''}>
            {version?.display ? `版本 ${version.display}` : '版本 unknown'}
          </div>
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
      <main className="flex-1 h-screen overflow-y-auto p-6"><ErrorBoundary key={location.pathname}>{children}</ErrorBoundary></main>
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

function Badge({ children, tone = 'slate' }) {
  const tones = {
    emerald: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
    amber: 'bg-amber-500/15 text-amber-300 border-amber-500/20',
    red: 'bg-red-500/15 text-red-400 border-red-500/20',
    blue: 'bg-blue-500/15 text-blue-300 border-blue-500/20',
    purple: 'bg-purple-500/15 text-purple-300 border-purple-500/20',
    slate: 'bg-slate-800 text-slate-300 border-slate-700',
  }
  return <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs ${tones[tone] || tones.slate}`}>{children}</span>
}

function actionTone(action) {
  if (action === 'continue') return 'emerald'
  if (action === 'wait') return 'amber'
  if (action === 'no_reply') return 'slate'
  return 'blue'
}

function formatAgo(seconds) {
  if (seconds === null || seconds === undefined || seconds === '') return '-'
  const n = Number(seconds)
  if (Number.isNaN(n)) return '-'
  if (n < 60) return `${Math.max(0, Math.round(n))}s`
  if (n < 3600) return `${Math.round(n / 60)}m`
  return `${Math.round(n / 3600)}h`
}

function MiniStat({ label, value, tone = 'slate', onClick }) {
  const color = {
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
    red: 'text-red-300',
    blue: 'text-blue-300',
    slate: 'text-white',
  }[tone] || 'text-white'
  return (
    <Card className={`p-4 min-h-[92px] ${onClick ? 'cursor-pointer hover:bg-slate-800/60' : ''}`} onClick={onClick}>
      <div className="text-xs text-slate-500 mb-2">{label}</div>
      <div className={`text-2xl font-semibold tracking-tight ${color}`}>{value ?? '...'}</div>
    </Card>
  )
}

function JsonBlock({ value, className = '' }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value || {}, null, 2)
  return <pre className={`rounded-xl bg-slate-950 border border-slate-800 p-3 text-xs leading-relaxed text-slate-300 overflow-auto whitespace-pre-wrap ${className}`}>{text || '-'}</pre>
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
  const [data, setData] = useState(null)
  const [modelStatus, setModelStatus] = useState(null)
  const navigate = useNavigate()
  useEffect(() => {
    api.get('/overview').then(r => setData(r.data)).catch(() => setData(null))
  }, [])
  useEffect(() => {
    api.get('/models/status').then(r => setModelStatus(r.data)).catch(() => {})
  }, [])
  if (!data) return <Spinner />
  const c = data.counters || {}
  const timing = data.timing || {}
  return (
    <div>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">Nanobot 运行总览</h1>
          <p className="text-slate-500 text-sm">服务状态、模型路由、最近 1h 流量与失败信号</p>
        </div>
        <Badge tone={data.service?.ok ? 'emerald' : 'red'}>{data.service?.ok ? '服务在线' : '服务异常'}</Badge>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <MiniStat label="最近 1h 请求数" value={c.requests_1h} tone="blue" />
        <MiniStat label="最近 1h 群消息数" value={c.group_messages_1h} />
        <MiniStat label="最近 1h 回复数" value={c.replies_1h} tone="emerald" />
        <MiniStat label="TimingGate 错误数" value={c.recent_errors} tone={c.recent_errors ? 'red' : 'slate'} onClick={() => navigate('/timing-gate?error_only=1')} />
        <MiniStat label="TimingGate parse_error" value={c.timing_parse_errors} tone={c.timing_parse_errors ? 'red' : 'slate'} onClick={() => navigate('/timing-gate?parse_error_only=1')} />
        <MiniStat label="Sticker 缓存失败" value={c.sticker_cache_failures} tone={c.sticker_cache_failures ? 'amber' : 'slate'} />
        <MiniStat label="打标失败" value={c.tagging_failures} tone={c.tagging_failures ? 'amber' : 'slate'} />
        <MiniStat label="打标描述失败" value={c.sticker_describe_failures || 0} tone={c.sticker_describe_failures ? 'amber' : 'slate'} />
        <MiniStat label="TimingGate 1h 总数" value={timing.total || 0} />
        <MiniStat label="TimingGate p95" value={`${timing.p95_latency_ms || 0}ms`} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="p-5 xl:col-span-2">
          <h2 className="text-sm font-medium text-slate-400 mb-4">健康检查</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(data.health || []).map(item => (
              <div key={item.name} className="flex items-start gap-3 rounded-lg bg-slate-950/60 border border-slate-800 p-3">
                <Badge tone={item.ok ? 'emerald' : 'red'}>{item.ok ? 'OK' : 'FAIL'}</Badge>
                <div className="min-w-0">
                  <div className="text-sm text-slate-200">{item.name}</div>
                  <div className="text-xs text-slate-500 truncate">{item.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="text-sm font-medium text-slate-400 mb-4">模型路由状态</h2>
          {modelStatus && Object.values(modelStatus.api_routes).map(r => (
            <div key={r.stage} className="flex items-center justify-between py-2 border-b border-slate-800/50 text-xs">
              <span className="text-slate-400">{r.label}</span>
              <span className="text-slate-300 font-mono truncate max-w-[200px]">{r.model || '未配置'}</span>
            </div>
          ))}
          <div className="mt-5 flex gap-2">
            <NavLink to="/models" className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs">模型测试</NavLink>
            <NavLink to="/timing-gate" className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs">TimingGate 调试</NavLink>
          </div>
        </Card>

        <Card className="p-5 xl:col-span-3">
          <h2 className="text-sm font-medium text-slate-400 mb-4">TimingGate 分布</h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <MiniStat label="continue" value={timing.actions?.continue || 0} tone="emerald" />
            <MiniStat label="wait" value={timing.actions?.wait || 0} tone="amber" />
            <MiniStat label="no_reply" value={timing.actions?.no_reply || 0} />
            <MiniStat label="parse_error 比例" value={`${Math.round((timing.parse_error_ratio || 0) * 100)}%`} tone={timing.parse_error ? 'red' : 'slate'} />
            <MiniStat label="平均延迟" value={`${timing.avg_latency_ms || 0}ms`} />
          </div>
        </Card>
      </div>

      <div className="mt-4">
        <a href="/api/v1/admin/db/backup"
          className="inline-flex px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm transition-colors">
          下载数据库备份
        </a>
      </div>
    </div>
  )
}
// ── Groups ──
function GroupsPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true)
    api.get('/groups').then(r => setData(r.data)).finally(() => setLoading(false))
  }
  useEffect(() => {
    api.get('/groups').then(r => setData(r.data)).finally(() => setLoading(false))
  }, [])
  if (loading) return <Spinner />
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">群聊运行状态</h1>
          <p className="text-slate-500 text-sm">{data.total} 个群，按最近状态排查为什么回/不回</p>
        </div>
        <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3 font-medium">群</th><th className="py-2 px-3 font-medium">talk</th><th className="py-2 px-3 font-medium">消息</th><th className="py-2 px-3 font-medium">pending</th><th className="py-2 px-3 font-medium">回复</th><th className="py-2 px-3 font-medium">gen</th><th className="py-2 px-3 font-medium">timer</th><th className="py-2 px-3 font-medium">最近 action</th><th className="py-2 px-3 font-medium">reason</th><th className="py-2 px-3 font-medium">latency</th></tr></thead>
          <tbody>
            {data.items.map(g => (
              <tr key={g.session_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 px-3">
                  <NavLink to={`/groups/${g.group_id}`} className="text-emerald-300 hover:text-emerald-200">{g.session_name || g.group_id}</NavLink>
                  <div className="text-xs text-slate-600">{g.group_id}</div>
                </td>
                <td className="py-2 px-3">{g.talk_value}</td>
                <td className="py-2 px-3 text-slate-400">{g.msg_1m}/{g.msg_5m}</td>
                <td className="py-2 px-3">{g.pending_count ?? '-'}</td>
                <td className="py-2 px-3 text-slate-400">{formatAgo(g.since_last_reply)}</td>
                <td className="py-2 px-3">{g.generation || 0}</td>
                <td className="py-2 px-3">{g.has_pending_timer ? <Badge tone="amber">pending</Badge> : <span className="text-slate-600">-</span>}</td>
                <td className="py-2 px-3">{g.recent_action ? <Badge tone={actionTone(g.recent_action)}>{g.recent_action}</Badge> : '-'}</td>
                <td className="py-2 px-3 max-w-[320px] truncate text-slate-400">{g.recent_reason || '-'}</td>
                <td className="py-2 px-3 text-slate-400">{g.recent_latency_ms ? `${g.recent_latency_ms}ms` : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

function GroupDetailPage() {
  const groupId = location.pathname.split('/').pop()
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('ambient')
  useEffect(() => { api.get(`/groups/${encodeURIComponent(groupId)}`).then(r => setData(r.data)) }, [groupId])
  if (!data) return <Spinner />
  const g = data.group || {}
  return (
    <div>
      <div className="mb-4">
        <NavLink to="/groups" className="text-xs text-slate-500 hover:text-slate-300">← 群聊运行</NavLink>
        <h1 className="text-2xl font-bold mt-2">{g.session_name || g.group_id}</h1>
        <p className="text-slate-500 text-sm">group_{g.group_id} · talk_value {g.talk_value}</p>
      </div>
      <div className="grid grid-cols-2 xl:grid-cols-6 gap-3 mb-4">
        <MiniStat label="generation" value={g.generation || 0} />
        <MiniStat label="pending timer" value={g.has_pending_timer ? 'YES' : 'NO'} tone={g.has_pending_timer ? 'amber' : 'slate'} />
        <MiniStat label="running" value={data.runtime?.running ? 'YES' : 'NO'} tone={data.runtime?.running ? 'emerald' : 'slate'} />
        <MiniStat label="msg_1m" value={g.msg_1m || 0} />
        <MiniStat label="msg_5m" value={g.msg_5m || 0} />
        <MiniStat label="since reply" value={formatAgo(g.since_last_reply)} />
        <MiniStat label="pending" value={data.runtime?.pending_count || 0} />
        <MiniStat label="wait_count" value={data.runtime?.wait_count ?? '-'} />
        <MiniStat label="since bot reply" value={data.runtime?.last_bot_reply_ago != null ? formatAgo(data.runtime.last_bot_reply_ago) : '-'} />
        <MiniStat label="last active" value={data.runtime?.last_active_ago != null ? formatAgo(data.runtime.last_active_ago) : '-'} />
        <MiniStat label="last trigger" value={data.runtime?.last_trigger_reason || '-'} />
        <MiniStat label="total_wait_s" value={data.runtime?.total_wait_s != null ? `${Number(data.runtime.total_wait_s).toFixed(1)}s` : '-'} />
      </div>
      <Card className="sticky top-0 z-10 p-2 mb-4 flex gap-1 flex-wrap bg-slate-950/95 backdrop-blur border border-slate-800">
        {[
          ['ambient', '最近 ambient'],
          ['replies', 'bot 回复'],
          ['timing', 'TimingGate 判定'],
          ['blocks', '屏蔽记录'],
          ['stickers', '表情包入库'],
          ['runtime', '运行时'],
        ].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3 py-1.5 rounded-lg text-xs ${tab === id ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>{label}</button>
        ))}
      </Card>
      {tab === 'ambient' && <LogList rows={data.ambient_messages} />}
      {tab === 'replies' && <LogList rows={data.bot_replies} />}
      {tab === 'timing' && <TimingEventsTable rows={data.timing_events || []} />}
      {tab === 'blocks' && <JsonBlock value={data.blocked_rules} />}
      {tab === 'stickers' && <StickerMiniTable rows={data.sticker_records || []} />}
      {tab === 'runtime' && <JsonBlock value={data.runtime} />}
    </div>
  )
}

function LogList({ rows = [] }) {
  return (
    <div className="space-y-2">
      {rows.map(r => (
        <Card key={r.id} className="p-3">
          <div className="flex items-center gap-2 mb-2 text-xs text-slate-500">
            <span>#{r.id}</span><span>{r.time}</span><span>{r.sender_name || r.role}</span><span>{r.message_id}</span>
          </div>
          <div className="text-sm whitespace-pre-wrap">{r.content}</div>
        </Card>
      ))}
      {!rows.length && <div className="text-sm text-slate-600 py-10 text-center">暂无记录</div>}
    </div>
  )
}

function StickerMiniTable({ rows }) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="px-3 py-2">id</th><th className="px-3 py-2">description</th><th className="px-3 py-2">preview</th><th className="px-3 py-2">describe</th><th className="px-3 py-2">hash</th><th className="px-3 py-2">last_seen</th></tr></thead>
        <tbody>{rows.map(s => <tr key={s.id} className="border-b border-slate-800/50"><td className="px-3 py-2">{s.id}</td><td className="px-3 py-2 max-w-[320px] truncate">{s.description || '-'}</td><td className="px-3 py-2">{s.preview_status}</td><td className="px-3 py-2">{s.describe_status}</td><td className="px-3 py-2 max-w-[160px] truncate">{s.content_hash}</td><td className="px-3 py-2">{s.last_seen}</td></tr>)}</tbody>
      </table>
    </Card>
  )
}

// ── TimingGate ──
function TimingGatePage() {
  const [data, setData] = useState({ items: [], stats: {}, total: 0 })
  const [groupId, setGroupId] = useState('')
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(30)
  const [selected, setSelected] = useState(null)
  const [context, setContext] = useState('<timing_context>\n群: 测试群\n触发原因: ambient\n[用户名]用户A\n[发言内容]刚才这个报错怎么回事\n</timing_context>')
  const [repeats, setRepeats] = useState(1)
  const [testResult, setTestResult] = useState(null)
  const [running, setRunning] = useState(false)
  const queryParams = new URLSearchParams(window.location.search)
  const errorOnly = queryParams.get('error_only') === '1'
  const parseErrorOnly = queryParams.get('parse_error_only') === '1'

  const load = useCallback(() => {
    const params = { group_id: groupId, page, limit }
    if (errorOnly) params.error_only = 1
    if (parseErrorOnly) params.parse_error_only = 1
    api.get('/timing-gate/events', { params }).then(r => {
      setData(r.data)
      const items = r.data.items || []
      setSelected(prev => items.some(x => x.id === prev?.id) ? prev : items[0] || null)
    }).catch(() => setData({ items: [], stats: {}, total: 0 }))
  }, [groupId, page, limit, errorOnly, parseErrorOnly])
  useEffect(() => { load() }, [load])

  const stats = data.stats || {}
  const runTest = () => {
    setRunning(true)
    api.post('/timing-gate/test', { context, repeats: Number(repeats) })
      .then(r => setTestResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }

  const handleUseAsTest = () => {
    const ctx = selected?.context || selected?.input_summary || selected?.pending_text || ''
    setContext(ctx)
    if (!selected?.context) alert('该记录没有完整模型输入，仅使用摘要复测')
  }

  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">TimingGate 调试</h1>
          <p className="text-slate-500 text-sm">左右布局：列表 + 详情/复测</p>
        </div>
        <div className="flex gap-2">
          <input value={groupId} onChange={e => { setGroupId(e.target.value); setPage(1) }} placeholder="群号过滤"
            className="w-36 p-2 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
          <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
        </div>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-6 gap-3 mb-4">
        <MiniStat label="continue" value={stats.actions?.continue || 0} tone="emerald" />
        <MiniStat label="wait" value={stats.actions?.wait || 0} tone="amber" />
        <MiniStat label="no_reply" value={stats.actions?.no_reply || 0} />
        <MiniStat label="parse_error" value={stats.parse_error || 0} tone={stats.parse_error ? 'red' : 'slate'} />
        <MiniStat label="error%" value={`${(stats.parse_error_ratio != null ? (stats.parse_error_ratio * 100).toFixed(1) : '0')}%`} />
        <MiniStat label="p95" value={`${stats.p95_latency_ms || 0}ms`} />
      </div>

      {(errorOnly || parseErrorOnly) && (
        <div className="flex items-center gap-2 mb-2">
          {errorOnly && <Badge tone="red">仅显示错误</Badge>}
          {parseErrorOnly && <Badge tone="red">仅显示 parse_error</Badge>}
          <button onClick={() => { window.history.replaceState({}, '', window.location.pathname); window.location.reload() }}
            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-xs">清除过滤</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_460px] gap-4">
        <Card className="overflow-hidden">
          <div className="p-3 border-b border-slate-800 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-500">limit:</span>
            <select value={limit} onChange={e => { setLimit(Number(e.target.value)); setPage(1) }}
              className="p-1.5 rounded bg-slate-900 border border-slate-700 text-xs">
              <option value="20">20</option><option value="30">30</option><option value="50">50</option>
            </select>
          </div>
          <div className="max-h-[calc(100vh-320px)] overflow-auto">
            <TimingEventsTable rows={data.items || []} selectedId={selected?.id} onSelect={setSelected} />
          </div>
          <div className="p-3 border-t border-slate-800">
            <Pagination page={page} total={data.total || 0} limit={limit} onChange={(p) => { setPage(p); setSelected(null) }} />
          </div>
        </Card>

        <Card className="p-4 sticky top-4 self-start max-h-[calc(100vh-120px)] overflow-auto">
          <TimingEventDetail event={selected} onUseAsTest={handleUseAsTest} />
          <div className="mt-4 pt-4 border-t border-slate-800">
            <h3 className="text-sm font-medium text-slate-400 mb-2">手动测试</h3>
            <textarea value={context} onChange={e => setContext(e.target.value)} rows={6}
              className="w-full p-2 rounded-lg bg-slate-950 border border-slate-700 font-mono text-xs mb-2" />
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs text-slate-500">次数</span>
              <input type="number" min="1" max="20" value={repeats} onChange={e => setRepeats(e.target.value)}
                className="w-16 p-1.5 rounded bg-slate-950 border border-slate-700 text-xs" />
              <button onClick={() => setRepeats(20)} className="px-2 py-1 bg-slate-800 rounded text-xs">20次</button>
            </div>
            <button onClick={runTest} disabled={running}
              className="px-3 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-xs font-medium w-full">
              {running ? '运行中...' : '运行 TimingGate'}
            </button>
            {testResult && <JsonBlock value={testResult} className="mt-2 max-h-60" />}
          </div>
        </Card>
      </div>
    </div>
  )
}

function TimingEventsTable({ rows = [], selectedId, onSelect }) {
  if (!rows.length) return <div className="text-sm text-slate-600 py-10 text-center">暂无记录</div>
  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-slate-900 z-10"><tr className="text-left text-slate-500 border-b border-slate-800">
        <th className="py-2 px-2">时间</th><th className="py-2 px-2">群</th><th className="py-2 px-2">消息</th><th className="py-2 px-2">action</th><th className="py-2 px-2">delay</th><th className="py-2 px-2">latency</th><th className="py-2 px-2">reason</th>
      </tr></thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id} onClick={() => onSelect(r)}
            className={`border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer ${selectedId === r.id ? 'bg-emerald-500/10' : ''}`}>
            <td className="py-2 px-2 whitespace-nowrap text-slate-500">{r.time}</td>
            <td className="py-2 px-2">{r.group_id}</td>
            <td className="py-2 px-2 max-w-[120px] truncate">{r.trigger_message}</td>
            <td className="py-2 px-2"><Badge tone={actionTone(r.action)}>{r.action || '-'}</Badge></td>
            <td className="py-2 px-2">{r.delay_seconds ?? '-'}</td>
            <td className="py-2 px-2 text-slate-500">{r.latency_ms ? `${r.latency_ms}ms` : '-'}</td>
            <td className="py-2 px-2 max-w-[120px] truncate text-slate-400 text-[10px]">{r.reason || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function TimingEventDetail({ event, onUseAsTest }) {
  if (!event) return <div className="text-slate-500 text-sm py-8 text-center">点击左侧记录查看详情</div>
  const contextText = event.context || event.input_summary || event.pending_text || ''
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-300">#{event.id}</h2>
        <Badge tone={actionTone(event.action)}>{event.action || '-'}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs text-slate-400">
        <div>group: {event.group_id}</div><div>latency: {event.latency_ms || '-'}ms</div>
        <div>gen: {event.generation ?? '-'}</div><div>pending: {event.pending_count ?? '-'}</div>
        <div>talk: {event.talk_value != null ? Number(event.talk_value).toFixed(2) : '-'}</div><div>mode: {event.mode || '-'}</div>
        <div>hard: {event.hard_rule || '-'}</div><div>dir: {event.directed_to_other ? 'yes' : '-'}</div>
      </div>
      <div><div className="text-xs text-slate-500 mb-1">触发消息</div><div className="text-xs bg-slate-950 rounded p-2 max-h-24 overflow-auto">{event.trigger_message || '-'}</div></div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <div className="text-xs text-slate-500">模型输入</div>
          <button onClick={onUseAsTest} className="px-2 py-0.5 bg-emerald-700/50 hover:bg-emerald-700 rounded text-[10px]">用此复测</button>
        </div>
        <pre className="rounded bg-slate-950 border border-slate-800 p-2 text-[10px] whitespace-pre-wrap max-h-48 overflow-auto">{contextText || '(无)'}</pre>
      </div>
      <div><div className="text-xs text-slate-500 mb-1">raw</div><pre className="rounded bg-slate-950 border border-slate-800 p-2 text-[10px] whitespace-pre-wrap max-h-32 overflow-auto">{event.raw || '-'}</pre></div>
      <JsonBlock value={{ action: event.action, delay: event.delay_seconds, reason: event.reason, error_type: event.error_type, parse_error: event.parse_error, fallback: event.fallback_action }} />
    </div>
  )
}

// ── Sticker Dedup ──
function StickerDedupPage() {
  const [data, setData] = useState({ groups: [] })
  const [error, setError] = useState('')
  const [selectedGroup, setSelectedGroup] = useState(null)
  const [showDisabled, setShowDisabled] = useState(false)
  const [dedupTab, setDedupTab] = useState('exact')
  const [nearDuplicates, setNearDuplicates] = useState([])
  const navigate = useNavigate()

  const loadNear = () => api.get('/stickers/near-duplicate-candidates?limit=100')
    .then(r => setNearDuplicates(r.data.items || []))
    .catch(e => alert(e?.response?.data?.detail || e.message))

  const load = () => api.get('/stickers/duplicate-groups?limit=100')
    .then(r => { setData(r.data || {}); setError(''); if (!selectedGroup && (r.data?.groups || []).length) setSelectedGroup(r.data.groups[0]) })
    .catch(e => { setError(e?.response?.data?.detail || e.message || '加载失败') })
  useEffect(() => { load() }, [])

  const doAction = (stickerId, action, body = {}) => {
    api.post(`/stickers/${stickerId}/${action}`, body)
      .then(() => load())
      .catch(e => alert(e?.response?.data?.detail || e.message))
  }

  const runBackfill = () => {
    if (!confirm('将对全库 content_hash 重复分组执行精确去重，确定？')) return
    api.post('/stickers/dedupe/exact/backfill')
      .then(r => { alert(`完成：${r.data.total_groups} 组, ${r.data.total_duplicates} 个标记`); load() })
      .catch(e => alert(e?.response?.data?.detail || e.message))
  }

  const groups = data.groups || []
  const selGroup = selectedGroup || {}
  const selItems = (selGroup.items || []).filter(s => showDisabled || s.status !== 'disabled')
  const canonicalId = selGroup.canonical_id
  const canonical = selItems.find(s => s.id === canonicalId) || selItems.find(s => s.status === 'active' && !s.duplicate_of_id)

  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">去重工作台</h1>
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate('/stickers')} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">返回列表</button>
          <button onClick={runBackfill} className="px-3 py-1.5 bg-amber-700/50 hover:bg-amber-700 rounded-lg text-xs">一键历史去重</button>
          <button onClick={() => api.post('/stickers/phash/backfill?limit=200').then(r => alert(`phash 补建: ${r.data.ok} OK / ${r.data.skipped} skip`))}
            className="px-3 py-1.5 bg-slate-700/50 hover:bg-slate-700 rounded-lg text-xs">phash 补建</button>
          <button onClick={() => api.post('/stickers/near-duplicate/scan?limit=100').then(r => { alert(`扫描完成: ${r.data.candidates_created} 个候选`); loadNear() })}
            className="px-3 py-1.5 bg-purple-700/50 hover:bg-purple-700 rounded-lg text-xs">扫描疑似重复</button>
          <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
        </div>
      </div>
      <Card className="sticky top-0 z-10 p-2 mb-4 flex gap-1 flex-wrap bg-slate-950/95 backdrop-blur border border-slate-800">
        <button onClick={() => setDedupTab('exact')} className={`px-3 py-1 rounded text-xs ${dedupTab === 'exact' ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400'}`}>精确重复</button>
        <button onClick={() => { setDedupTab('near'); loadNear() }} className={`px-3 py-1 rounded text-xs ${dedupTab === 'near' ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400'}`}>疑似重复</button>
      </Card>
      {error && <Card className="p-4 mb-4 border border-red-800 bg-red-900/20"><p className="text-sm text-red-400">{error}</p></Card>}

      {dedupTab === 'exact' && (
        <>
          {groups.length === 0 && !error && <p className="text-slate-500 text-sm py-8 text-center">暂无重复表情包</p>}

          {groups.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
              <Card className="p-2 max-h-[calc(100vh-160px)] overflow-auto">
                {groups.map(g => (
                  <button key={g.content_hash || '-'} onClick={() => setSelectedGroup(g)}
                    className={`w-full text-left p-2 rounded-lg text-xs transition-colors mb-1 ${selectedGroup?.content_hash === g.content_hash ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:bg-slate-800/50'}`}>
                    <div className="truncate font-mono">{g.content_hash?.substring(0, 16) || '-'}</div>
                    <span className="text-slate-600">{g.count || 0} 个重复</span>
                  </button>
                ))}
              </Card>

              {selectedGroup && (
                <Card className="p-4 max-h-[calc(100vh-160px)] overflow-auto">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <code className="text-xs bg-slate-950 px-2 py-0.5 rounded break-all">{selectedGroup.content_hash || '-'}</code>
                      <span className="text-xs text-slate-500 ml-2">{selectedGroup.count || 0} 个</span>
                    </div>
                    <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer">
                      <input type="checkbox" checked={showDisabled} onChange={e => setShowDisabled(e.target.checked)} className="rounded" />
                      显示 disabled
                    </label>
                  </div>

                  {canonical && (
                    <div className="mb-3 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                      <div className="text-xs text-emerald-400 mb-1">当前 canonical</div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">{canonical.name || '-'} <span className="text-slate-500">#{canonical.id}</span></span>
                        <span className="text-xs text-slate-500">使用 {canonical.usage_count ?? 0} 次</span>
                      </div>
                    </div>
                  )}

                  <table className="w-full text-xs">
                    <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                      <th className="py-2 px-1 w-10">预览</th><th className="py-2 px-1">id</th><th className="py-2 px-1">名称</th><th className="py-2 px-1">状态</th><th className="py-2 px-1">dedupe</th><th className="py-2 px-1">使用</th><th className="py-2 px-1">操作</th>
                    </tr></thead>
                    <tbody>
                      {selItems.map(s => (
                        <tr key={s.id} className="border-b border-slate-800/50">
                          <td className="py-2 px-1"><AuthImage url={`/api/v1/admin/stickers/${s.id}/preview`} alt="" className="w-8 h-8 object-cover rounded" /></td>
                          <td className="py-2 px-1">{s.id}</td>
                          <td className="py-2 px-1 max-w-[80px] truncate">{s.name || '-'}</td>
                          <td className="py-2 px-1"><Badge tone={s.status === 'active' ? 'emerald' : 'amber'}>{s.status || '-'}</Badge></td>
                          <td className="py-2 px-1">{s.dedupe_status || 'unique'}</td>
                          <td className="py-2 px-1 text-slate-500">{s.usage_count ?? 0}</td>
                          <td className="py-2 px-1">
                            <div className="flex gap-1 flex-wrap">
                              {s.dedupe_status !== 'canonical' && s.status === 'active' && (
                                <button onClick={() => doAction(s.id, 'set-canonical')} className="px-1.5 py-0.5 bg-emerald-700/40 hover:bg-emerald-700 rounded text-[10px] text-emerald-300">canonical</button>
                              )}
                              {s.id !== canonical?.id && s.dedupe_status !== 'duplicate' && canonical && (
                                <button onClick={() => doAction(s.id, 'mark-duplicate', {canonical_id: canonical.id})} className="px-1.5 py-0.5 bg-purple-700/40 hover:bg-purple-700 rounded text-[10px] text-purple-300">重复</button>
                              )}
                              {s.dedupe_status === 'duplicate' ? (
                                <button onClick={() => doAction(s.id, 'set-canonical')} className="px-1.5 py-0.5 bg-emerald-700/40 hover:bg-emerald-700 rounded text-[10px] text-emerald-300">恢复</button>
                              ) : s.status === 'active' ? (
                                <button onClick={() => doAction(s.id, 'disable')} className="px-1.5 py-0.5 bg-amber-700/40 hover:bg-amber-700 rounded text-[10px] text-amber-300">禁用</button>
                              ) : (
                                <button onClick={() => doAction(s.id, 'enable')} className="px-1.5 py-0.5 bg-slate-700/40 hover:bg-slate-700 rounded text-[10px]">启用</button>
                              )}
                              {/* 预览/打标工具 */}
                              <button onClick={() => doAction(s.id, 'preview/retry')} className="px-1.5 py-0.5 bg-slate-700/30 hover:bg-slate-700 rounded text-[10px]" title="重试预览">🔄</button>
                              <button onClick={() => doAction(s.id, 'redescribe')} className="px-1.5 py-0.5 bg-slate-700/30 hover:bg-slate-700 rounded text-[10px]" title="重试打标">🏷</button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          )}
        </>
      )}

      {dedupTab === 'near' && (
        <Card className="p-4 max-h-[calc(100vh-160px)] overflow-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-1 w-10">A</th><th className="py-2 px-1">名称</th><th className="py-2 px-1 w-10">B</th><th className="py-2 px-1">名称</th>
              <th className="py-2 px-1">pH</th><th className="py-2 px-1">dH</th><th className="py-2 px-1">操作</th>
            </tr></thead>
            <tbody>
              {nearDuplicates.map(r => (
                <tr key={r.id} className="border-b border-slate-800/50">
                  <td className="py-2 px-1"><AuthImage url={`/api/v1/admin/stickers/${r.sticker_a.id}/preview`} alt="" className="w-8 h-8 object-cover rounded" /></td>
                  <td className="py-2 px-1 max-w-[100px] truncate">{r.sticker_a.name || '-'} <span className="text-slate-600">#{r.sticker_a.id}</span></td>
                  <td className="py-2 px-1"><AuthImage url={`/api/v1/admin/stickers/${r.sticker_b.id}/preview`} alt="" className="w-8 h-8 object-cover rounded" /></td>
                  <td className="py-2 px-1 max-w-[100px] truncate">{r.sticker_b.name || '-'} <span className="text-slate-600">#{r.sticker_b.id}</span></td>
                  <td className="py-2 px-1"><Badge tone={r.phash_dist <= 4 ? 'red' : 'amber'}>{r.phash_dist}</Badge></td>
                  <td className="py-2 px-1"><Badge tone={r.dhash_dist <= 4 ? 'red' : 'amber'}>{r.dhash_dist}</Badge></td>
                  <td className="py-2 px-1">
                    <div className="flex gap-1">
                      <button onClick={() => api.post(`/stickers/near-duplicate-candidates/${r.id}/confirm`).then(loadNear)}
                        className="px-1.5 py-0.5 bg-emerald-700/40 rounded text-[10px]">确认</button>
                      <button onClick={() => api.post(`/stickers/near-duplicate-candidates/${r.id}/ignore`).then(loadNear)}
                        className="px-1.5 py-0.5 bg-slate-700/40 rounded text-[10px]">忽略</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}

// ── Stickers ──
function StickersPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [search, setSearch] = useState('')
  const [sf, setSf] = useState('active')
  const [failure, setFailure] = useState('')
  const [page, setPage] = useState(1)
  const [edit, setEdit] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [preview, setPreview] = useState(null)
  const [selected, setSelected] = useState(new Set())

  const load = useCallback(() => {
    api.get('/stickers', { params: { search, page, limit: 20, status: sf, failure } }).then(r => setData(r.data))
  }, [search, page, sf, failure])
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
        <input value={search} onChange={e => { setSearch(e.target.value); setPage(1); setSelected(new Set()) }} placeholder="搜索名称/描述..."
          className="flex-1 p-2.5 rounded-xl bg-slate-900 border border-slate-700 focus:border-emerald-500 outline-none text-sm transition-colors" />
        <select value={sf} onChange={e => { setSf(e.target.value); setPage(1); setSelected(new Set()) }}
          className="p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
          <option value="">全部</option><option value="active">启用</option>
          <option value="disabled">禁用</option><option value="deleted">已删除</option><option value="duplicate">重复项</option>
        </select>
        <select value={failure} onChange={e => { setFailure(e.target.value); setPage(1); setSelected(new Set()) }}
          className="p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
          <option value="">全部质量状态</option>
          <option value="preview_failed">缓存失败</option>
          <option value="describe_failed">打标失败</option>
          <option value="unlabeled">未打标/失败</option>
          <option value="duplicate">重复项</option>
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
            <th className="py-2 px-2 font-medium"><input type="checkbox"
              checked={data.items.length > 0 && data.items.every(s => selected.has(s.id))}
              onChange={e => { const ids = data.items.map(s => s.id); setSelected(e.target.checked ? new Set(ids) : new Set()) }}
              className="accent-emerald-500" /></th>
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
                    <span className={`px-2 py-0.5 rounded-full text-xs ${s.status === 'active' ? 'bg-emerald-500/15 text-emerald-400' : s.status === 'disabled' ? 'bg-amber-500/15 text-amber-400' : s.status === 'duplicate' ? 'bg-purple-500/15 text-purple-400' : 'bg-red-500/15 text-red-400'}`}>{s.status}</span>
                    {s.dedupe_status === 'duplicate' && s.duplicate_of_id && (
                      <span className="px-1.5 py-0.5 rounded text-xs bg-purple-500/15 text-purple-400" title={`重复于 #${s.duplicate_of_id}`}>dup</span>
                    )}
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
                    {s.describe_status && s.describe_status !== 'ok' && (
                      <span className={`px-1.5 py-0.5 rounded text-xs ${
                        s.describe_status === 'failed' ? 'bg-red-500/15 text-red-400' : 'bg-slate-700 text-slate-300'
                      }`} title={s.describe_last_error || ''}>{s.describe_status}</span>
                    )}
                  </div>
                </td>
                <td className="py-1.5 px-2 text-slate-400">{s.usage_count}</td>
                <td className="py-1.5 px-2">
                  <div className="flex gap-1">
                    <button onClick={() => setEdit(s)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">编辑</button>
                    {s.describe_status !== 'ok' && s.status === 'active' && (
                      <button onClick={() => api.post(`/stickers/${s.id}/redescribe`).then(r => { alert(r.data.ok ? '打标成功' : '打标失败: ' + r.data.error); load() })}
                        className="px-2 py-1 bg-indigo-700/50 hover:bg-indigo-700 text-indigo-300 rounded-lg text-xs transition-colors" title="重新描述">AI</button>
                    )}
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
      <Pagination page={page} total={data.total} limit={20} onChange={(next) => { setSelected(new Set()); setPage(next) }} />
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
  const tabs = [
    { key: 'global', label: '全局内容规则' },
    { key: 'session', label: '局部内容规则' },
    { key: 'user', label: '用户屏蔽' },
    { key: 'test', label: '命中测试' },
  ]
  const [tab, setTab] = useState('global')
  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">内容规则 / 屏蔽规则</h1>
      <p className="text-slate-500 text-sm mb-4">全局/局部内容规则 + 用户屏蔽 + 命中测试</p>
      <div className="flex gap-2 mb-6 border-b border-slate-800 pb-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'global' && <ContentRuleTab scopeType="global" />}
      {tab === 'session' && <ContentRuleTab scopeType="session" />}
      {tab === 'user' && <UserBlockTab />}
      {tab === 'test' && <BlockRuleTestTab />}
    </div>
  )
}

// ── 内容规则 Tab ──
function ContentRuleTab({ scopeType }) {
  const [data, setData] = useState({ items: [], total: 0 })
  const [showModal, setShowModal] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [streams, setStreams] = useState([])
  const isSession = scopeType === 'session'
  const load = useCallback(() => {
    api.get('/content-block-rules', { params: { scope_type: scopeType, limit: 200 } }).then(r => setData(r.data))
  }, [scopeType])
  useEffect(() => { load(); if (isSession) api.get('/chat-streams').then(r => setStreams(r.data.items || [])) }, [load, isSession])

  const toggle = (r) => api.post(`/content-block-rules/${r.id}/toggle`).then(load)
  const del = (r) => { if (confirm('确认删除？')) api.delete(`/content-block-rules/${r.id}`).then(load) }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-slate-500 text-sm">{data.total} 条{scopeType === 'global' ? '全局' : '局部'}规则</p>
        <button onClick={() => { setEditRow(null); setShowModal(true) }}
          className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors">+ 新建</button>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-1.5 px-2 font-medium">pattern</th>
            <th className="py-1.5 px-2 font-medium">类型</th>
            {isSession && <th className="py-1.5 px-2 font-medium">stream</th>}
            <th className="py-1.5 px-2 font-medium w-10">禁回</th>
            <th className="py-1.5 px-2 font-medium w-10">禁学</th>
            <th className="py-1.5 px-2 font-medium w-10">禁上下</th>
            <th className="py-1.5 px-2 font-medium">分类</th>
            <th className="py-1.5 px-2 font-medium">状态</th>
            <th className="py-1.5 px-2 font-medium">操作</th>
          </tr></thead>
          <tbody>
            {data.items.map(r => (
              <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-1.5 px-2 font-mono max-w-[150px] truncate" title={r.pattern}>{r.pattern}</td>
                <td className="py-1.5 px-2 text-slate-400">{r.match_type}</td>
                {isSession && <td className="py-1.5 px-2 text-slate-500 text-xs max-w-[120px] truncate" title={r.chat_stream_id}>{r.chat_stream_id || '(空)'}</td>}
                <td className="py-1.5 px-2 text-center">{r.no_reply ? <span className="text-red-400">🚫</span> : '-'}</td>
                <td className="py-1.5 px-2 text-center">{r.no_learn ? <span className="text-amber-400">🧠</span> : '-'}</td>
                <td className="py-1.5 px-2 text-center">{r.no_context ? <span className="text-blue-400">📝</span> : '-'}</td>
                <td className="py-1.5 px-2 text-slate-400">{r.category}</td>
                <td className="py-1.5 px-2">
                  <button onClick={() => toggle(r)} className={`px-2 py-0.5 rounded-full text-xs font-medium ${r.enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-600/30 text-slate-500'}`}>{r.enabled ? 'ON' : 'OFF'}</button>
                </td>
                <td className="py-1.5 px-2">
                  <div className="flex gap-1">
                    <button onClick={() => { setEditRow(r); setShowModal(true) }} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">编辑</button>
                    <button onClick={() => del(r)} className="px-2 py-1 bg-red-700/50 hover:bg-red-700 text-red-300 rounded text-xs">删除</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {showModal && (
        <ContentRuleModal
          scopeType={scopeType}
          editRow={editRow}
          streams={streams}
          onClose={() => setShowModal(false)}
          onCreated={() => { setShowModal(false); load() }}
        />
      )}
    </div>
  )
}

// ── 内容规则创建/编辑 Modal ──
function ContentRuleModal({ scopeType, editRow, streams, onClose, onCreated }) {
  const def = {
    pattern: '', match_type: 'contains', scope_type: scopeType, chat_stream_id: '',
    no_reply: 0, no_learn: 1, no_context: 0, category: 'no_learn', reason: '',
  }
  const [f, setF] = useState(editRow || def)
  const save = () => {
    if (!f.pattern.trim()) return alert('pattern 不能为空')
    const payload = { ...f }
    if (scopeType === 'global') payload.chat_stream_id = ''
    const req = editRow
      ? api.put(`/content-block-rules/${editRow.id}`, payload)
      : api.post('/content-block-rules', payload)
    req.then(onCreated)
  }
  return (
    <Modal onClose={onClose}>
      <div className="p-6 max-w-lg">
        <h2 className="text-lg font-bold mb-4">{editRow ? '编辑' : '新建'}内容规则 ({scopeType === 'global' ? '全局' : '局部'})</h2>
        <div className="space-y-3">
          <input value={f.pattern} onChange={e => setF({ ...f, pattern: e.target.value })} placeholder="匹配模式 (pattern)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
          <div className="grid grid-cols-2 gap-3">
            <select value={f.match_type} onChange={e => setF({ ...f, match_type: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
              <option value="contains">contains</option><option value="exact">exact</option><option value="regex">regex</option></select>
            <input value={f.category} onChange={e => setF({ ...f, category: e.target.value })} placeholder="分类" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
          </div>
          {scopeType === 'session' && (
            <select value={f.chat_stream_id} onChange={e => setF({ ...f, chat_stream_id: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
              <option value="">全部流 (空 = 所有局部)</option>
              {streams.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-1 text-slate-400"><input type="checkbox" checked={!!f.no_reply} onChange={e => setF({ ...f, no_reply: e.target.checked ? 1 : 0 })} className="rounded" /> 禁止回复</label>
            <label className="flex items-center gap-1 text-slate-400"><input type="checkbox" checked={!!f.no_learn} onChange={e => setF({ ...f, no_learn: e.target.checked ? 1 : 0 })} className="rounded" /> 禁止学习</label>
            <label className="flex items-center gap-1 text-slate-400"><input type="checkbox" checked={!!f.no_context} onChange={e => setF({ ...f, no_context: e.target.checked ? 1 : 0 })} className="rounded" /> 禁止入上下文</label>
          </div>
          <input value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} placeholder="原因备注" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
        </div>
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={save} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">{editRow ? '保存' : '创建'}</button>
        </div>
      </div>
    </Modal>
  )
}

// ── 用户屏蔽 Tab ──
function UserBlockTab() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [showCreate, setShowCreate] = useState(false)
  const load = useCallback(() => { api.get('/block-rules', { params: { limit: 50 } }).then(r => setData(r.data)) }, [])
  useEffect(() => { load() }, [load])
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-slate-500 text-sm">{data.total} 条规则</p>
        <button onClick={() => setShowCreate(true)} className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium">+ 新建</button>
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3 font-medium">用户</th><th className="py-2 px-3 font-medium">类型</th><th className="py-2 px-3 font-medium">模式</th><th className="py-2 px-3 font-medium">原因</th><th className="py-2 px-3 font-medium">状态</th><th className="py-2 px-3 font-medium">操作</th></tr></thead>
          <tbody>
            {data.items.map(r => (
              <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 px-3">{r.user_id}</td>
                <td className="py-2 px-3 text-slate-400">{r.target_type}</td>
                <td className="py-2 px-3 text-slate-400">{r.rule_mode}</td>
                <td className="py-2 px-3 truncate max-w-[200px] text-slate-400">{r.reason || '-'}</td>
                <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded-full text-xs ${r.enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'}`}>{r.enabled ? 'ON' : 'OFF'}</span></td>
                <td className="py-2 px-3">
                  <div className="flex gap-1">
                    <button onClick={() => api.put(`/block-rules/${r.id}`, { enabled: r.enabled ? 0 : 1 }).then(load)}
                      className={`px-2 py-1 rounded-lg text-xs ${r.enabled ? 'bg-amber-700/50 hover:bg-amber-700 text-amber-300' : 'bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300'}`}>{r.enabled ? '禁用' : '启用'}</button>
                    <button onClick={() => { if (confirm('确认删除?')) api.delete(`/block-rules/${r.id}`).then(load) }}
                      className="px-2 py-1 bg-red-700/50 hover:bg-red-700 text-red-300 rounded-lg text-xs">删除</button>
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

// ── 命中测试 Tab ──
function BlockRuleTestTab() {
  const [msg, setMsg] = useState('')
  const [streamId, setStreamId] = useState('')
  const [result, setResult] = useState(null)
  const [streams, setStreams] = useState([])
  useEffect(() => { api.get('/chat-streams').then(r => setStreams(r.data.items || [])) }, [])
  const test = () => {
    if (!msg.trim()) return
    api.post('/block-rules/test', { message: msg.trim(), chat_stream_id: streamId }).then(r => setResult(r.data))
  }
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div>
        <h2 className="text-lg font-medium mb-3">测试消息匹配</h2>
        <textarea value={msg} onChange={e => setMsg(e.target.value)} rows={4}
          placeholder="输入要测试的消息内容..."
          className="w-full p-3 rounded-xl bg-slate-900 border border-slate-700 text-sm mb-3 resize-none" />
        <select value={streamId} onChange={e => setStreamId(e.target.value)}
          className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mb-3">
          <option value="">无指定流 (全局规则)</option>
          {streams.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={test} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">测试匹配</button>
      </div>
      <div>
        <h2 className="text-lg font-medium mb-3">结果</h2>
        {result === null ? (
          <p className="text-slate-500 text-sm">输入消息后点击测试</p>
        ) : result.matched ? (
          <div className="space-y-3">
            <Badge tone="red">已命中</Badge>
            <div className="text-sm text-slate-300">
              <div className="text-xs text-slate-500 mb-1">命中规则:</div>
              {result.rules.map((r, i) => (
                <div key={i} className="p-2 bg-slate-800 rounded-lg text-xs mb-2">
                  <div className="font-mono text-slate-200">{r.pattern}</div>
                  <div className="text-slate-500 mt-1">
                    match_type: {r.match_type} | scope: {r.scope_type} | category: {r.category}
                    {r.chat_stream_id && <span> | stream: {r.chat_stream_id}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <Badge tone="emerald">未命中</Badge>
        )}
      </div>
    </div>
  )
}

// ── Configs ──
function ConfigsPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [edit, setEdit] = useState(null)
  const [viewMode, setViewMode] = useState('effective')
  const load = useCallback(() => {
    const params = { limit: 50 }
    if (viewMode === 'effective') params.effective = 1
    api.get('/configs', { params }).then(r => setData(r.data))
  }, [viewMode])
  useEffect(() => { load() }, [load])
  const deleteConfig = (sid) => {
    if (!confirm(`确认删除 ${sid} 的覆写配置？将恢复为系统默认值。`)) return
    api.delete(`/configs/${encodeURIComponent(sid)}`).then(load).catch(e => alert(e.response?.data?.detail || e.message))
  }
  return (
    <div>
      <div className="mb-4">
        <h1 className="text-2xl font-bold">群聊策略配置</h1>
        <p className="text-slate-500 text-sm mt-1">按群聊/私聊流覆写 talk_value、表达学习、群画像等策略。未覆写的流使用系统默认值。</p>
      </div>
      <div className="flex items-center gap-2 mb-4">
        <div className="flex gap-1 bg-slate-900 rounded-lg p-0.5">
          <button onClick={() => setViewMode('effective')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${viewMode === 'effective' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>有效配置</button>
          <button onClick={() => setViewMode('override')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${viewMode === 'override' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>仅覆写</button>
        </div>
        <span className="text-xs text-slate-500">{data.total} 条</span>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3 font-medium">流 ID</th>
            <th className="py-2 px-3 font-medium">发言值</th>
            <th className="py-2 px-3 font-medium">@回复</th>
            <th className="py-2 px-3 font-medium">表达注入</th>
            <th className="py-2 px-3 font-medium">表达学习</th>
            <th className="py-2 px-3 font-medium">黑话学习</th>
            <th className="py-2 px-3 font-medium">群画像</th>
            <th className="py-2 px-3 font-medium">平滑轮数</th>
            {viewMode === 'effective' && <th className="py-2 px-3 font-medium">来源</th>}
            <th className="py-2 px-3 font-medium"></th>
          </tr></thead>
          <tbody>
            {data.items.map(c => (
              <tr key={c.chat_stream_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-2 px-3 truncate max-w-[220px] text-xs text-slate-400">{c.chat_stream_id}</td>
                <td className="py-2 px-3">{c.talk_value}</td>
                <td className="py-2 px-3">{c.mentioned_bot_reply ? '✓' : '—'}</td>
                <td className="py-2 px-3">{c.use_expression ? '✓' : '—'}</td>
                <td className="py-2 px-3">{c.enable_expression_learning ? '✓' : '—'}</td>
                <td className="py-2 px-3">{c.enable_jargon_learning ? '✓' : '—'}</td>
                <td className="py-2 px-3"><Badge tone={c.group_profile_mode === 'on' ? 'emerald' : c.group_profile_mode === 'preview' ? 'amber' : 'slate'}>{c.group_profile_mode || 'off'}</Badge></td>
                <td className="py-2 px-3">{c.planner_smooth}</td>
                {viewMode === 'effective' && (
                  <td className="py-2 px-3">
                    <Badge tone={c.source === 'db' ? 'blue' : 'slate'}>{c.source === 'db' ? 'DB 覆写' : '默认'}</Badge>
                  </td>
                )}
                <td className="py-2 px-3">
                  <div className="flex gap-1">
                    <button onClick={() => setEdit(c)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">编辑</button>
                    {(viewMode === 'effective' && c.has_override) || viewMode === 'override' ? (
                      <button onClick={() => deleteConfig(c.chat_stream_id)} className="px-2 py-1 bg-red-700/50 hover:bg-red-700 text-red-300 rounded-lg text-xs transition-colors">删除覆写</button>
                    ) : null}
                    {viewMode === 'effective' && !c.has_override && (
                      <button onClick={() => { setEdit(c); }} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300 rounded-lg text-xs transition-colors">创建覆写</button>
                    )}
                  </div>
                </td>
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
    enable_jargon_learning: config.enable_jargon_learning,
    group_profile_mode: config.group_profile_mode || 'off',
    planner_smooth: config.planner_smooth,
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
        <label className="text-xs text-slate-400">群画像注入 mode（待 ContextBuilder 接入后生效）</label>
        <select value={f.group_profile_mode} onChange={e => setF({ ...f, group_profile_mode: e.target.value })}
          className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 mb-3 text-sm">
          <option value="off">off — 不生成不注入</option>
          <option value="preview">preview — 生成并记录 debug，不注入 prompt</option>
          <option value="on">on — 生成并注入 GroupProfileContext</option>
        </select>
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
  const [search, setSearch] = useState('')
  const reloadFromDB = () => api.post('/settings/reload').then(load).catch(e => alert(e.response?.data?.detail || e.message))
  const resetKey = (key) => {
    if (!confirm(`确认将 ${key} 重置为默认值？`)) return
    api.post(`/settings/${encodeURIComponent(key)}/reset`).then(load).catch(e => alert(e.response?.data?.detail || e.message))
  }
  const searchLower = search.trim().toLowerCase()
  const matchesSearch = (s) => !searchLower || String(s.key || '').toLowerCase().includes(searchLower) || String(s.description || '').toLowerCase().includes(searchLower)
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">系统设置</h1>
          <p className="text-slate-500 text-sm">热重载配置，修改即时生效</p>
        </div>
        <div className="flex gap-2">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索配置..."
            className="w-40 p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs" />
          <button onClick={reloadFromDB} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">从 DB 重载</button>
        </div>
      </div>
      {categories.map(cat => (
        <div key={cat} className="mb-6">
          <h2 className="text-sm font-semibold text-emerald-400 mb-3 uppercase tracking-wider">{cat}</h2>
          <div className="space-y-2">
            {(data?.settings || []).filter(matchesSearch).filter(s => s.category === cat).map(s => (
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
                {s.dangerous && <span className="text-red-500 text-xs">危险</span>}
                {s.restart_required && <span className="text-amber-500 text-xs">需重启</span>}
                {s.readonly && <span className="text-slate-600 text-xs">只读</span>}
                {!s.readonly && <button onClick={() => resetKey(s.key)} className="px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded text-xs text-slate-500">默认</button>}
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
  const backupDb = async () => {
    try {
      const res = await api.get('/db/backup', { responseType: 'blob' })
      const blob = new Blob([res.data], { type: 'application/octet-stream' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `nanobot-backup-${new Date().toISOString().slice(0, 19).replaceAll(':', '-')}.db`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) { alert(e.response?.data?.detail || e.message) }
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div><h1 className="text-2xl font-bold">数据库浏览</h1><p className="text-slate-500 text-sm">只读数据浏览</p></div>
        <button onClick={backupDb} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">下载备份</button>
      </div>
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
  const [tab, setTab] = useState('files')
  const [files, setFiles] = useState([])
  const [sel, setSel] = useState('')
  const [content, setContent] = useState('')
  const [lines, setLines] = useState(500)
  const [logLevel, setLogLevel] = useState('')
  const [searchQ, setSearchQ] = useState('')
  const [follow, setFollow] = useState(false)
  const [fileSize, setFileSize] = useState(0)
  const preRef = useRef(null)

  const refreshFiles = () => api.get('/logs').then(r => setFiles(r.data.files))
  useEffect(() => { refreshFiles() }, [])

  const loadLog = (name, n = lines, lv = logLevel, q = searchQ) => {
    setSel(name)
    setFollow(false)
    setFileSize(0)
    const params = { lines: n }
    if (lv) params.level = lv
    if (q) params.q = q
    api.get(`/logs/${encodeURIComponent(name)}`, { params }).then(r => {
      setContent(r.data.content)
      if (r.data.file_size) setFileSize(r.data.file_size)
    })
  }

  const pollTail = useCallback(() => {
    if (!sel || !follow) return
    const params = { since_bytes: fileSize }
    if (logLevel) params.level = logLevel
    if (searchQ) params.q = searchQ
    api.get(`/logs/${encodeURIComponent(sel)}`, { params }).then(r => {
      if (r.data.content) {
        setContent(prev => prev + r.data.content)
        setFileSize(r.data.file_size)
        setTimeout(() => {
          if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight
        }, 0)
      }
    })
  }, [sel, follow, fileSize, logLevel, searchQ])

  useEffect(() => {
    if (!follow) return
    const id = setInterval(pollTail, 2000)
    return () => clearInterval(id)
  }, [pollTail, follow])

  const startFollow = (name) => {
    setSel(name)
    setFollow(true)
    const params = { lines: 200, since_bytes: 0 }
    if (logLevel) params.level = logLevel
    if (searchQ) params.q = searchQ
    api.get(`/logs/${encodeURIComponent(name)}`, { params }).then(r => {
      setContent(r.data.content)
      if (r.data.file_size) setFileSize(r.data.file_size)
      setTimeout(() => {
        if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight
      }, 0)
    })
  }

  const formatSize = (s) => s < 1024 ? `${s}B` : s < 1048576 ? `${(s/1024).toFixed(1)}KB` : `${(s/1048576).toFixed(1)}MB`

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">日志</h1>
          <div className="flex rounded-lg bg-slate-900 border border-slate-700 p-0.5">
            <button onClick={() => setTab('files')} className={`px-3 py-1 rounded-md text-xs transition-colors ${tab === 'files' ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:text-white'}`}>日志文件</button>
            <button onClick={() => { setTab('replies'); setFollow(false) }} className={`px-3 py-1 rounded-md text-xs transition-colors ${tab === 'replies' ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:text-white'}`}>模型回复</button>
          </div>
        </div>
        {tab === 'files' && <button onClick={refreshFiles} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">刷新列表</button>}
      </div>
      {tab === 'files' ? (
        <div className="flex gap-4" style={{ height: 'calc(100vh - 140px)' }}>
          <div className="w-56 flex-shrink-0 space-y-1 overflow-auto">
            {files.map(f => (
              <button key={f.name} onClick={() => { setFollow(false); loadLog(f.name) }}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${sel === f.name ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}>
                <div className="truncate">{f.name}</div>
                <div className="text-slate-600">{formatSize(f.size)}</div>
              </button>
            ))}
          </div>
          <div className="flex-1 flex flex-col min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-sm text-slate-400">行数:</span>
              <select value={lines} onChange={e => { const n = Number(e.target.value); setLines(n); if (sel) loadLog(sel, n) }}
                className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
                <option value="100">100</option><option value="200">200</option><option value="500">500</option><option value="1000">1000</option>
              </select>
              <select value={logLevel} onChange={e => { setLogLevel(e.target.value); if (sel) loadLog(sel, lines, e.target.value, searchQ) }}
                className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
                <option value="">全部级别</option>
                <option value="ERROR">ERROR</option><option value="WARNING">WARNING</option><option value="INFO">INFO</option><option value="DEBUG">DEBUG</option>
              </select>
              <input value={searchQ} onChange={e => setSearchQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && sel && loadLog(sel, lines, logLevel, searchQ)}
                placeholder="搜索..." className="w-40 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
              {sel && <button onClick={() => loadLog(sel)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>}
              {sel && (
                <button
                  onClick={() => follow ? setFollow(false) : startFollow(sel)}
                  className={`px-3 py-1 rounded-lg text-xs transition-colors ${follow ? 'bg-emerald-600 hover:bg-emerald-700 text-white' : 'bg-slate-700 hover:bg-slate-600'}`}>
                  {follow ? '⏸ 停止跟随' : '▶ 跟随'}
                </button>
              )}
              {follow && <span className="text-xs text-emerald-400">实时 {formatSize(fileSize)}</span>}
            </div>
            <pre ref={preRef} className="flex-1 p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs leading-relaxed overflow-auto text-slate-300 font-mono whitespace-pre-wrap">{content || '点击左侧文件查看'}</pre>
          </div>
        </div>
      ) : (
        <ModelRepliesTab />
      )}
    </div>
  )
}


function ModelRepliesTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [groupId, setGroupId] = useState('')
  const [cursorStack, setCursorStack] = useState([])  // 后退历史：每前进一次 push 当前页起点
  const [currentBeforeId, setCurrentBeforeId] = useState(0)
  const [nextBeforeId, setNextBeforeId] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const limit = 30

  const load = (beforeId = 0, pushStack = false) => {
    const params = { limit, kind: 'group_reply' }
    if (groupId) params.group_id = groupId
    if (beforeId) params.before_id = beforeId
    api.get('/model-replies', { params }).then(r => {
      const data = r.data
      if (pushStack) {
        setCursorStack(prev => [...prev, currentBeforeId])
      }
      setItems(data.items || [])
      setTotal(data.count || 0)
      setCurrentBeforeId(beforeId)
      setNextBeforeId(data.page_info?.next_before_id || 0)
      setHasMore(data.page_info?.has_more || false)
    })
  }
  useEffect(() => { load(0) }, [])

  const goBack = () => {
    if (cursorStack.length === 0) return
    const prev = cursorStack[cursorStack.length - 1]
    setCursorStack(s => s.slice(0, -1))
    load(prev, false)
  }

  const formatTime = (ts) => ts ? new Date(ts).toLocaleString('zh-CN', { hour12: false }) : ''

  return (
    <div style={{ height: 'calc(100vh - 140px)' }} className="flex flex-col">
      <div className="flex items-center gap-2 mb-3">
        <input value={groupId} onChange={e => setGroupId(e.target.value)} onKeyDown={e => e.key === 'Enter' && load(0)}
          placeholder="群号筛选..." className="w-32 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <button onClick={() => load(0)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">查询</button>
        <span className="text-xs text-slate-500 ml-2">约 {total} 条</span>
        <div className="flex-1" />
        <button disabled={cursorStack.length === 0} onClick={goBack}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded-lg text-xs">‹ 后退</button>
        <button disabled={!hasMore} onClick={() => load(nextBeforeId, true)}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded-lg text-xs">更早 ›</button>
      </div>
      <div className="flex-1 overflow-auto rounded-xl bg-slate-950 border border-slate-800">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-900 text-slate-400">
            <tr>
              <th className="py-2 px-3 text-left w-36">时间</th>
              <th className="py-2 px-3 text-left w-16">群号</th>
              <th className="py-2 px-3 text-left">回复内容</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {items.map((m, i) => (
              <tr key={m.id || i} className="hover:bg-slate-900/50 transition-colors">
                <td className="py-2 px-3 text-slate-500 whitespace-nowrap align-top">{formatTime(m.created_at)}</td>
                <td className="py-2 px-3 text-slate-400 font-mono align-top">{m.group_id || '-'}</td>
                <td className="py-2 px-3 text-slate-300 whitespace-pre-wrap break-all">
                  <div className="max-h-20 overflow-y-auto">{m.content}</div>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={3} className="py-8 text-center text-slate-600">暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Prompt ──
function PromptPage() {
  const [prompt, setPrompt] = useState('')
  const [metrics, setMetrics] = useState({})
  const [frags, setFrags] = useState([])
  const [backups, setBackups] = useState([])
  const [editing, setEditing] = useState(null)
  const [editContent, setEditContent] = useState('')
  const [building, setBuilding] = useState(false)
  const [tab, setTab] = useState('fragments')
  const [toast, setToast] = useState('')

  const load = useCallback(() => {
    api.get('/prompt').then(r => { setPrompt(r.data.content); setMetrics(r.data.metrics || {}) }).catch(() => {})
    api.get('/prompt/fragments').then(r => setFrags(r.data.fragments)).catch(() => {})
    api.get('/prompt/backups').then(r => setBackups(r.data.backups || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

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
  const saveFragment = useCallback(() => {
    if (!editing || !dirty) return
    api.put(`/prompt/fragments/${encodeURIComponent(editing)}`, { content: editContent }).then(() => {
      setToast('已保存，记得重新构建 prompt.md 才能生效')
      setEditing(null)
      setEditContent('')
      load()
    }).catch(e => alert(e.response?.data?.detail || '保存失败'))
  }, [dirty, editContent, editing, load])
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && editing) {
        e.preventDefault()
        saveFragment()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [editing, saveFragment])

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
            <button onClick={() => setTab('backups')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'backups' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>备份回滚</button>
          </div>
        </div>
        <button onClick={rebuild} disabled={building}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium transition-colors">
          {building ? '构建中...' : '重新构建 prompt.md'}
        </button>
      </div>

      {tab === 'preview' ? (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
          <Card className="p-4 xl:col-span-3">
            <pre className="text-xs leading-relaxed whitespace-pre-wrap max-h-[calc(100vh-200px)] overflow-auto text-slate-300">{prompt}</pre>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-400 mb-3">校验</h2>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between"><span className="text-slate-500">字符数</span><span>{metrics.chars || 0}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">估算 token</span><span>{metrics.estimated_tokens || 0}</span></div>
              <div><span className="text-slate-500 text-xs">重复片段</span><JsonBlock value={metrics.duplicate_fragments || []} className="mt-1 max-h-32" /></div>
              <div><span className="text-slate-500 text-xs">危险标记</span><JsonBlock value={metrics.danger_markers || []} className="mt-1 max-h-32" /></div>
            </div>
          </Card>
        </div>
      ) : tab === 'backups' ? (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3">备份</th><th className="py-2 px-3">fragment</th><th className="py-2 px-3">hash</th><th className="py-2 px-3">大小</th><th className="py-2 px-3">操作</th></tr></thead>
            <tbody>
              {backups.map(b => (
                <tr key={b.name} className="border-b border-slate-800/50">
                  <td className="py-2 px-3 font-mono text-xs">{b.created_at}</td>
                  <td className="py-2 px-3">{b.fragment}</td>
                  <td className="py-2 px-3 font-mono text-xs text-slate-400">{b.hash}</td>
                  <td className="py-2 px-3 text-slate-400">{b.size}</td>
                  <td className="py-2 px-3">
                    <button onClick={() => { if (confirm(`确认回滚 ${b.fragment} 到该备份?`)) api.post(`/prompt/backups/${encodeURIComponent(b.name)}/rollback`).then(() => { setToast('已回滚片段，请重新构建 prompt.md'); load() }) }}
                      className="px-2 py-1 bg-amber-700/50 hover:bg-amber-700 text-amber-300 rounded-lg text-xs">回滚</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!backups.length && <div className="py-10 text-center text-sm text-slate-600">暂无备份</div>}
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

// ── Models ──
function ModelsPage() {
  const [status, setStatus] = useState(null)
  const [editRoute, setEditRoute] = useState(null)
  const [testResult, setTestResult] = useState({})
  const [localResult, setLocalResult] = useState({})
  const load = () => api.get('/models/status').then(r => setStatus(r.data))
  useEffect(() => { load() }, [])

  const handleTest = async (key) => {
    setTestResult(p => ({ ...p, [key]: { loading: true } }))
    try { const r = await api.post(`/models/routes/${key}/test`); setTestResult(p => ({ ...p, [key]: r.data })) }
    catch (e) { setTestResult(p => ({ ...p, [key]: { ok: false, error: e.message } })) }
  }
  const handleLocal = async (comp, action) => {
    setLocalResult(p => ({ ...p, [comp]: { loading: true } }))
    try { const r = await api.post(`/models/local/${comp}/${action}`); setLocalResult(p => ({ ...p, [comp]: r.data })) }
    catch (e) { setLocalResult(p => ({ ...p, [comp]: { ok: false, error: e.message } })) }
  }

  if (!status) return <Spinner />

  const routeList = Object.entries(status.api_routes || {})

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">模型路由</h1>
      <p className="text-slate-500 text-sm mb-6">
        API 模型路由可编辑/测试；本地语义组件为按需懒加载，不属于 API 路由。
      </p>

      <h2 className="text-lg font-medium mb-3">API 模型路由</h2>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-8">
        {routeList.map(([key, r]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{r.label} <span className="text-xs text-slate-500 font-mono ml-1">{key}</span></h3>
                {r.inherited_from && (
                  <span className="text-xs text-amber-400">继承自 {r.inherited_from}{r.overridden_fields && Object.keys(r.overridden_fields).length > 0 ? ` (覆盖: ${Object.keys(r.overridden_fields).join(', ')})` : ''}</span>
                )}
              </div>
              <div className="flex gap-1">
                {r.editable !== false && (
                  <button onClick={() => setEditRoute({ key, ...r })} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">编辑</button>
                )}
                <button onClick={() => handleTest(key)} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
              </div>
            </div>
            <div className="space-y-0.5 text-xs text-slate-400">
              <div>base_url: <span className="text-slate-500 font-mono break-all">{r.base_url || r.api_url || ''}</span></div>
              {r.model && <div>model: <span className="text-slate-200 font-mono">{r.model}</span></div>}
              {r.api_key_configured !== undefined && <div>API key: {r.api_key_configured ? '✅' : '❌'}</div>}
              {r.max_tokens !== undefined && <div>max_tokens: {r.max_tokens} | timeout: {r.timeout}s | temp: {r.temperature}</div>}
              {r.source && <div className="text-slate-600">source: {r.source}</div>}
            </div>
            {testResult[key] && !testResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${testResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {testResult[key].ok ? `✅ ${testResult[key].latency_ms}ms` : `❌ ${testResult[key].error}`}
              </div>
            )}
            {testResult[key] && testResult[key].loading && <div className="mt-2 text-xs text-slate-500">测试中...</div>}
          </Card>
        ))}
      </div>

      <h2 className="text-lg font-medium mb-3">本地语义组件</h2>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {Object.entries(status.local_components || {}).map(([key, c]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{key}</h3>
                <span className="text-xs text-slate-500">配置: {c.configured ? '已配置' : '未配置'} | 加载: {c.load_state === 'loaded' ? '已加载' : c.load_state === 'fallback' ? '降级' : c.load_state === 'unavailable' ? '不可用' : '未加载'}</span>
                {c.fallback && <span className="text-xs text-amber-400 ml-1">(降级: {c.fallback})</span>}
              </div>
              <div className="flex gap-1">
                <button onClick={() => handleLocal(key, 'warmup')} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">预热</button>
                <button onClick={() => handleLocal(key, 'test')} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
              </div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>模型: <span className="text-slate-200">{c.model}</span></div>
              <div>加载器: {c.loader}</div>
              <div>用途: {c.role}</div>
              <div className="text-slate-600">触发: {c.trigger}</div>
              {c.note && <div className="text-slate-600 italic">{c.note}</div>}
              {c.error && <div className="text-red-400 truncate">{c.error}</div>}
            </div>
            {localResult[key] && !localResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${localResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {localResult[key].ok
                  ? `✅ ${localResult[key].latency_ms}ms${localResult[key].dim ? ' | dim=' + localResult[key].dim : ''}`
                  : `❌ ${localResult[key].error || ''}${localResult[key].hint ? ' | ' + localResult[key].hint : ''}`}
              </div>
            )}
            {localResult[key] && localResult[key].loading && <div className="mt-2 text-xs text-slate-500">执行中...</div>}
          </Card>
        ))}
      </div>

      {editRoute && (
        <RouteEditModal route={editRoute} onClose={() => setEditRoute(null)} onSaved={() => { setEditRoute(null); load() }} />
      )}
    </div>
  )
}

function RouteEditModal({ route, onClose, onSaved }) {
  const [f, setF] = useState({
    base_url: route.base_url || '', model: route.model || '',
    api_key: '', timeout: route.timeout || 15, temperature: route.temperature || 0,
    max_tokens: route.max_tokens || 30,
  })
  const [models, setModels] = useState([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const loadModels = () => {
    setModelsLoading(true)
    api.get('/models/available', { params: { route_key: route.key } })
      .then(r => setModels(r.data.models || [])).catch(() => {}).finally(() => setModelsLoading(false))
  }
  const save = () => {
    const payload = {}
    for (const k of ['base_url', 'model', 'timeout', 'temperature', 'max_tokens']) {
      if (f[k] !== '' && f[k] !== undefined) payload[k] = String(f[k])
    }
    if (f.api_key && f.api_key.trim()) payload.api_key = f.api_key.trim()
    api.put(`/models/routes/${route.key}`, payload).then(onSaved)
  }
  return (
    <Modal onClose={onClose}>
      <div className="p-6 max-w-lg">
        <h2 className="text-lg font-bold mb-4">编辑 {route.label} ({route.key})</h2>
        {route.inherited_from && (
          <p className="text-xs text-amber-400 mb-3">继承自 {route.inherited_from}，仅需覆盖差异字段</p>
        )}
        <div className="space-y-3">
          <input value={f.base_url} onChange={e => setF({ ...f, base_url: e.target.value })} placeholder="base_url" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
          <div className="flex gap-2">
            <input value={f.model} onChange={e => setF({ ...f, model: e.target.value })} placeholder="model" className="flex-1 p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
            <button onClick={loadModels} disabled={modelsLoading} className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-xs">{modelsLoading ? '...' : '可选'}</button>
          </div>
          {models.length > 0 && (
            <select onChange={e => setF({ ...f, model: e.target.value })} value={f.model} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm">
              <option value="">手动输入</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          <input type="password" value={f.api_key} onChange={e => setF({ ...f, api_key: e.target.value })} placeholder="API key (留空不修改)" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm" />
          <div className="grid grid-cols-3 gap-3">
            <div><label className="text-xs text-slate-500">timeout</label><input type="number" value={f.timeout} onChange={e => setF({ ...f, timeout: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">temp</label><input type="number" step="0.1" value={f.temperature} onChange={e => setF({ ...f, temperature: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">max_tokens</label><input type="number" value={f.max_tokens} onChange={e => setF({ ...f, max_tokens: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={save} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Memory ──
function MemoryPage() {
  const [groupId, setGroupId] = useState('')
  const [memType, setMemType] = useState('')
  const [memories, setMemories] = useState([])
  const [loading, setLoading] = useState(false)
  const [expandedEvidence, setExpandedEvidence] = useState(null)

  const load = () => {
    if (!groupId) return
    setLoading(true)
    api.get(`/admin/groups/${encodeURIComponent(groupId)}/memories${memType ? `?memory_type=${memType}` : ''}`)
      .then(r => setMemories(r.data.memories || [])).finally(() => setLoading(false))
  }

  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">群体记忆</h1>
          <p className="text-slate-500 text-sm">按群查看 GroupMemory：话题/黑话/风格/关系/事件/偏好</p>
        </div>
      </div>
      <div className="flex items-center gap-3 mb-4">
        <input value={groupId} onChange={e => setGroupId(e.target.value)} placeholder="group_id"
          className="w-48 p-2 rounded-lg bg-slate-950 border border-slate-700 text-sm" />
        <select value={memType} onChange={e => setMemType(e.target.value)}
          className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-sm">
          <option value="">全部类型</option>
          {['topic', 'slang', 'style', 'relationship', 'event', 'preference'].map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm">查询</button>
      </div>
      {loading ? <Spinner /> : memories.length === 0 ? <div className="text-sm text-slate-600 py-10 text-center">{groupId ? '暂无记忆' : '输入群号后查询'}</div> : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-3">id</th><th className="py-2 px-3">类型</th><th className="py-2 px-3">内容</th><th className="py-2 px-3">confidence</th><th className="py-2 px-3">证据</th><th className="py-2 px-3">decay</th><th className="py-2 px-3">来源</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">更新</th>
            </tr></thead>
            <tbody>
              {memories.map(m => (
                <tr key={m.id} className="border-b border-slate-800/50">
                  <td className="py-2 px-3 text-slate-500">{m.id}</td>
                  <td className="py-2 px-3"><Badge>{m.memory_type}</Badge></td>
                  <td className="py-2 px-3 max-w-[400px] truncate">{m.content}</td>
                  <td className="py-2 px-3">{Number(m.confidence).toFixed(2)}</td>
                  <td className="py-2 px-3"><button onClick={() => setExpandedEvidence(expandedEvidence === m.id ? null : m.id)} className="text-xs underline text-slate-500 hover:text-emerald-400">{m.evidence_count}</button></td>
                  <td className="py-2 px-3">{Number(m.decay_score).toFixed(2)}</td>
                  <td className="py-2 px-3 text-slate-500">{m.source}</td>
                  <td className="py-2 px-3">{m.status === 'active' ? <Badge tone="emerald">active</Badge> : m.status === 'archived' ? <Badge tone="slate">archived</Badge> : <Badge tone="amber">{m.status}</Badge>}</td>
                  <td className="py-2 px-3 text-slate-500 text-xs">{m.updated_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {expandedEvidence && (() => {
        const m = memories.find(x => x.id === expandedEvidence)
        return m ? (
          <Card className="p-3 mt-3">
            <div className="text-xs text-slate-500 mb-2">证据日志 ID: {m.id}</div>
            <JsonBlock value={m.evidence_log_ids_json} className="max-h-48" />
          </Card>
        ) : null
      })()}
    </div>
  )
}

// ── Audit ──
function AuditPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const load = useCallback(() => {
    api.get('/audit-logs', { params: { page, limit: 50, action: actionFilter, target_type: typeFilter } }).then(r => setData(r.data))
  }, [page, actionFilter, typeFilter])
  useEffect(() => { load() }, [load])
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Admin 审计日志</h1>
          <p className="text-slate-500 text-sm">Prompt、表情包、屏蔽规则、配置等高风险操作</p>
        </div>
        <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
      </div>
      <div className="flex items-center gap-3 mb-4">
        <select value={actionFilter} onChange={e => { setActionFilter(e.target.value); setPage(1) }}
          className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
          <option value="">全部操作</option>
          {['update_setting','reset_setting','update_prompt_fragment','rebuild_prompt','rollback_prompt_fragment',
            'create_sticker','update_sticker','enable_sticker','disable_sticker','delete_sticker','redescribe_sticker','batch_delete_stickers',
            'create_block_rule','update_block_rule','delete_block_rule','update_config'].map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={typeFilter} onChange={e => { setTypeFilter(e.target.value); setPage(1) }}
          className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
          <option value="">全部类型</option>
          <option value="setting">setting</option><option value="prompt">prompt</option>
          <option value="sticker">sticker</option><option value="block_rule">block_rule</option>
          <option value="config">config</option>
        </select>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="px-3 py-2">time</th><th className="px-3 py-2">actor</th><th className="px-3 py-2">action</th><th className="px-3 py-2">target</th><th className="px-3 py-2">detail</th><th className="px-3 py-2">ip</th></tr></thead>
          <tbody>{data.items.map(r => (
            <tr key={r.id} className="border-b border-slate-800/50 align-top">
              <td className="px-3 py-2 whitespace-nowrap text-slate-500">{r.created_at}</td>
              <td className="px-3 py-2">{r.admin_user}</td>
              <td className="px-3 py-2"><Badge tone="blue">{r.action}</Badge></td>
              <td className="px-3 py-2">{r.target_type}:{r.target_id}</td>
              <td className="px-3 py-2 max-w-[520px]"><JsonBlock value={r.detail_json} className="max-h-36" /></td>
              <td className="px-3 py-2 text-slate-500">{r.ip_address || '-'}</td>
            </tr>
          ))}</tbody>
        </table>
      </Card>
      <Pagination page={page} total={data.total} limit={50} onChange={setPage} />
    </div>
  )
}

// ── Eval ──
function EvalsPage() {
  const [tab, setTab] = useState('candidates')
  const [candidates, setCandidates] = useState({ items: [], total: 0 })
  const [candPage, setCandPage] = useState(1)
  const [suiteFilter, setSuiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [detail, setDetail] = useState(null)
  const [showLabel, setShowLabel] = useState(null)
  const [labelSuite, setLabelSuite] = useState('')
  const [labelFields, setLabelFields] = useState({})
  const [labelShowJson, setLabelShowJson] = useState(false)
  const [runs, setRuns] = useState([])
  const [runDetail, setRunDetail] = useState(null)
  const [running, setRunning] = useState(false)
  const [sampleInfo, setSampleInfo] = useState(null)

  const loadCandidates = useCallback(() => {
    const params = { page: candPage, limit: 20 }
    if (suiteFilter) params.suite = suiteFilter
    if (statusFilter) params.status = statusFilter
    if (sourceFilter) params.source = sourceFilter
    api.get('/evals/candidates', { params }).then(r => setCandidates(r.data))
  }, [candPage, suiteFilter, statusFilter, sourceFilter])

  const loadRuns = useCallback(() => {
    api.get('/evals/runs', { params: { limit: 20 } }).then(r => setRuns(r.data.items || []))
  }, [])

  useEffect(() => {
    if (tab === 'candidates') loadCandidates()
    if (tab === 'runs') loadRuns()
  }, [tab, loadCandidates, loadRuns])

  const runEval = () => {
    setRunning(true)
    api.post('/evals/run', { suite: 'regression' })
      .then(r => { alert(`Eval 完成: ${r.data.passed}/${r.data.total} passed`); loadRuns() })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }

  const runSample = () => {
    api.post('/evals/sample/run')
      .then(r => { setSampleInfo(r.data); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const loadDetail = (caseId) => {
    api.get(`/evals/candidates/${encodeURIComponent(caseId)}`)
      .then(r => setDetail(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doLabel = (caseId) => {
    // 从表单构建 expected_json
    let expectedJson = { ...labelFields }
    delete expectedJson._rawJson
    if (labelShowJson && labelFields._rawJson) {
      try { expectedJson = JSON.parse(labelFields._rawJson) } catch { alert('JSON 格式错误'); return }
    }
    if (Object.keys(expectedJson).length === 0 || expectedJson.needs_label) {
      alert('请先选择期望值')
      return
    }
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/label`, { expected_json: expectedJson })
      .then(() => { setShowLabel(null); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doIgnore = (caseId) => {
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/ignore`)
      .then(() => loadCandidates())
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doPromote = (caseId) => {
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`)
      .then(r => { alert(`已提升到 regression: ${r.data.path}`); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const loadRunDetail = (runId) => {
    api.get(`/evals/runs/${runId}`).then(r => setRunDetail(r.data)).catch(e => alert(e.message))
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Eval 评测系统</h1>
          <p className="text-slate-500 text-sm">候选管理、标签、回归测试与运行历史</p>
        </div>
        <div className="flex gap-2">
          <button onClick={runSample} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">采样</button>
          <button onClick={runEval} disabled={running}
            className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-xs font-medium">
            {running ? '运行中...' : '运行 Eval'}
          </button>
        </div>
      </div>
      {sampleInfo && (
        <div className="mb-3 px-4 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-sm text-emerald-400">
          采样完成: 新增 {sampleInfo.created} 个候选
        </div>
      )}
      <Card className="sticky top-0 z-10 p-2 mb-4 flex gap-1 flex-wrap bg-slate-950/95 backdrop-blur border border-slate-800">
        <button onClick={() => setTab('candidates')}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'candidates' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>候选列表</button>
        <button onClick={() => setTab('runs')}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'runs' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>运行历史</button>
      </Card>

      {tab === 'candidates' && (
        <div>
          <div className="flex gap-2 mb-4">
            <input value={suiteFilter} onChange={e => { setSuiteFilter(e.target.value); setCandPage(1) }}
              placeholder="suite 过滤" className="w-32 p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs" />
            <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setCandPage(1) }}
              className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
              <option value="">全部状态</option>
              <option value="candidate">candidate</option>
              <option value="labeled">labeled</option>
              <option value="ignored">ignored</option>
              <option value="promoted">promoted</option>
            </select>
            <select value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); setCandPage(1) }}
              className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
              <option value="">全部来源</option>
              <option value="log">log</option>
              <option value="db">db</option>
            </select>
          </div>
          <Card>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-2">case_id</th>
                <th className="px-3 py-2">suite</th>
                <th className="px-3 py-2">来源</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">描述</th>
                <th className="px-3 py-2">创建时间</th>
                <th className="px-3 py-2">操作</th>
              </tr></thead>
              <tbody>
                {candidates.items.map(c => (
                  <tr key={c.case_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-3 py-2 font-mono max-w-[200px] truncate">{c.case_id}</td>
                    <td className="px-3 py-2"><Badge>{c.suite}</Badge></td>
                    <td className="px-3 py-2 text-slate-400">{c.source}</td>
                    <td className="px-3 py-2">
                      <Badge tone={c.status === 'promoted' ? 'emerald' : c.status === 'labeled' ? 'blue' : c.status === 'ignored' ? 'slate' : 'amber'}>{c.status}</Badge>
                    </td>
                    <td className="px-3 py-2 max-w-[300px] truncate text-slate-400">{c.description}</td>
                    <td className="px-3 py-2 text-slate-500">{c.created_at}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        <button onClick={() => loadDetail(c.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">详情</button>
                        {c.status === 'candidate' && (
                          <>
                            <button onClick={() => { setShowLabel(c.case_id); setLabelSuite(c.suite); setLabelFields({}); setLabelShowJson(false) }}
                              className="px-2 py-1 bg-indigo-700/50 hover:bg-indigo-700 text-indigo-300 rounded text-xs">标记</button>
                            <button onClick={() => doIgnore(c.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">忽略</button>
                          </>
                        )}
                        {c.status === 'labeled' && (
                          <button onClick={() => doPromote(c.case_id)}
                            className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300 rounded text-xs">提升</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Pagination page={candPage} total={candidates.total} limit={20} onChange={setCandPage} />

          {/* Detail modal */}
          {detail && (
            <Modal onClose={() => setDetail(null)} wide>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">{detail.case_id}</h2>
                <div className="text-xs text-slate-400 mb-4">{detail.description}</div>
                <div className="space-y-3">
                  <div><div className="text-xs text-slate-500 mb-1">input</div><JsonBlock value={detail.input} className="max-h-48" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">expected</div><JsonBlock value={detail.expected} className="max-h-32" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">来源</div><span className="text-sm">{detail.source}: {detail.source_ref}</span></div>
                  <div><div className="text-xs text-slate-500 mb-1">指纹</div><code className="text-xs bg-slate-950 px-2 py-0.5 rounded">{detail.fingerprint}</code></div>
                </div>
              </div>
            </Modal>
          )}

          {/* Label modal */}
          {showLabel && (
            <Modal onClose={() => setShowLabel(null)}>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">标记期望值</h2>
                <p className="text-xs text-slate-500 mb-2">{showLabel}</p>
                <Badge className="mb-4">{labelSuite || 'unknown'}</Badge>

                {labelSuite === 'memory_learning' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">是否应该学习</div>
                      <select value={labelFields.should_learn || ''} onChange={e => setLabelFields({...labelFields, should_learn: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">应该学习</option>
                        <option value="false">不应学习</option>
                        <option value="uncertain">不确定</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">分类</div>
                      <input value={labelFields.category || ''} onChange={e => setLabelFields({...labelFields, category: e.target.value})}
                        placeholder="stock_formula, spam_symbol..."
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="× 不应被学成黑话"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">含义（可选）</div>
                      <textarea value={labelFields.meaning || ''} onChange={e => setLabelFields({...labelFields, meaning: e.target.value})}
                        rows={2} className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {labelSuite === 'timing_gate' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">期望动作</div>
                      <select value={labelFields.expected_action || ''} onChange={e => setLabelFields({...labelFields, expected_action: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="continue">continue</option>
                        <option value="wait">wait</option>
                        <option value="no_reply">no_reply</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">延迟（秒）</div>
                      <input type="number" value={labelFields.delay_seconds || ''} onChange={e => setLabelFields({...labelFields, delay_seconds: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="应该继续回复"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {labelSuite === 'group_reply' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">质量评价</div>
                      <select value={labelFields.quality || ''} onChange={e => setLabelFields({...labelFields, quality: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="good">合适</option>
                        <option value="bad">不合适</option>
                        <option value="interrupt">过度插话</option>
                        <option value="tone">语气不对</option>
                        <option value="context_error">上下文错误</option>
                        <option value="permission_error">权限错误</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="描述问题"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {/* 其他 suite：默认表单 + JSON 高级模式 */}
                {!['memory_learning','timing_gate','group_reply'].includes(labelSuite) && (
                  <div className="space-y-3">
                    <p className="text-xs text-slate-500">此 suite 暂无专用表单，请使用高级 JSON 模式或直接在下方编辑。</p>
                    <textarea value={labelFields._rawJson || JSON.stringify({needs_label: true}, null, 2)} onChange={e => setLabelFields({...labelFields, _rawJson: e.target.value})}
                      rows={8} className="w-full p-3 rounded-xl bg-slate-900 border border-slate-700 font-mono text-xs" />
                  </div>
                )}

                {/* 高级 JSON 模式（所有 suite 都有） */}
                <div className="mt-4">
                  <button onClick={() => setLabelShowJson(!labelShowJson)} className="text-xs text-slate-500 hover:text-slate-300">
                    {labelShowJson ? '收起' : '▶'} 高级 JSON 模式
                  </button>
                  {labelShowJson && (
                    <textarea value={labelFields._rawJson || JSON.stringify(labelFields, null, 2)} onChange={e => setLabelFields({...labelFields, _rawJson: e.target.value})}
                      rows={8} className="w-full p-3 mt-2 rounded-xl bg-slate-900 border border-slate-700 font-mono text-xs" />
                  )}
                </div>

                <div className="flex gap-2 justify-end mt-4">
                  <button onClick={() => setShowLabel(null)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
                  <button onClick={() => doLabel(showLabel)}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存标记</button>
                </div>
              </div>
            </Modal>
          )}
        </div>
      )}

      {tab === 'runs' && (
        <div>
          <Card>
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-2">ID</th>
                <th className="px-3 py-2">suite</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">通过率</th>
                <th className="px-3 py-2">通过/总数</th>
                <th className="px-3 py-2">git_sha</th>
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">操作</th>
              </tr></thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-3 py-2">{r.id}</td>
                    <td className="px-3 py-2"><Badge>{r.suite}</Badge></td>
                    <td className="px-3 py-2">
                      <Badge tone={r.status === 'completed' ? 'emerald' : 'amber'}>{r.status}</Badge>
                    </td>
                    <td className="px-3 py-2">
                      <span className={r.pass_rate >= 0.8 ? 'text-emerald-400' : r.pass_rate >= 0.5 ? 'text-amber-400' : 'text-red-400'}>
                        {(r.pass_rate * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="px-3 py-2">{r.passed}/{r.total}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">{r.git_sha || '-'}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{r.created_at}</td>
                    <td className="px-3 py-2">
                      <button onClick={() => loadRunDetail(r.id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">详情</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {runDetail && (
            <Modal onClose={() => setRunDetail(null)} wide>
              <div className="p-6 max-h-[80vh] overflow-auto">
                <h2 className="text-lg font-bold mb-2">Run #{runDetail.run?.id}</h2>
                <div className="grid grid-cols-4 gap-3 mb-4">
                  <MiniStat label="suite" value={runDetail.run?.suite} />
                  <MiniStat label="通过率" value={`${((runDetail.run?.pass_rate || 0) * 100).toFixed(1)}%`}
                    tone={runDetail.run?.pass_rate >= 0.8 ? 'emerald' : runDetail.run?.pass_rate >= 0.5 ? 'amber' : 'red'} />
                  <MiniStat label="通过" value={runDetail.run?.passed} tone="emerald" />
                  <MiniStat label="失败" value={runDetail.run?.failed} tone={runDetail.run?.failed ? 'red' : 'slate'} />
                </div>
                {(runDetail.results || []).filter(r => !r.passed).length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-red-400 mb-2">失败 case</h3>
                    <div className="space-y-2">
                      {(runDetail.results || []).filter(r => !r.passed).map(res => (
                        <Card key={res.id} className="p-3">
                          <div className="text-sm font-medium mb-1">{res.case_id}</div>
                          <div className="text-xs text-slate-400">score: {res.score}</div>
                          <JsonBlock value={res.errors} className="mt-1 max-h-32" />
                        </Card>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Modal>
          )}
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
          <Route path="/groups" element={<GroupsPage />} />
          <Route path="/groups/:groupId" element={<GroupDetailPage />} />
          <Route path="/timing-gate" element={<TimingGatePage />} />
          <Route path="/stickers" element={<StickersPage />} />
          <Route path="/stickers/duplicates" element={<StickerDedupPage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/configs" element={<ConfigsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/db" element={<DbPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/prompt" element={<PromptPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
