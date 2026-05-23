import React, { useState, useEffect, useCallback, useRef } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useLocation, useParams } from 'react-router-dom'
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
  { to: '/prompt-preview', label: 'V2 运行预览' },
  { to: '/prompt-v2-templates', label: 'V2 模板' },
  { to: '/agent-runs', label: '运行追踪' },
  { to: '/llm-api-logs', label: 'LLM API 日志' },
  { to: '/models', label: '模型' },
  { to: '/blocks', label: '屏蔽' },
  { to: '/tools', label: '工具管理' },
  { to: '/logs', label: '日志' },
  { to: '/audit', label: '审计' },
  { to: '/configs', label: '群聊策略' },
  { to: '/settings', label: '设置' },
  { to: '/memory', label: '群体记忆' },
  { to: '/reply-eval', label: 'Reply 测试' },
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
  return <div className={`bg-slate-900/60 backdrop-blur-sm border border-slate-800 rounded-lg ${className}`}>{children}</div>
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
  const title = typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  return (
    <Card className={`p-3 min-h-[72px] transition-colors ${onClick ? 'cursor-pointer hover:bg-slate-800/60' : ''}`} onClick={onClick}>
      <div className="text-[11px] text-slate-500 mb-1 truncate">{label}</div>
      <div className={`text-lg font-semibold leading-tight ${color} truncate`} title={title}>{value ?? '...'}</div>
    </Card>
  )
}

function InfoGrid({ items = [], columns = 'md:grid-cols-4' }) {
  return (
    <div className={`grid grid-cols-1 ${columns} gap-2`}>
      {items.filter(Boolean).map(item => (
        <div key={item.label} className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 min-w-0">
          <div className="text-[11px] text-slate-600 mb-0.5 truncate">{item.label}</div>
          <div className={`text-xs font-medium truncate ${item.className || 'text-slate-300'}`} title={typeof item.value === 'string' || typeof item.value === 'number' ? String(item.value) : ''}>
            {item.value ?? '-'}
          </div>
        </div>
      ))}
    </div>
  )
}

function ActionButton({ children, tone = 'slate', className = '', ...props }) {
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

function formatRate(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(1)}%`
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
          {(() => {
            const dashboardRoutes = Object.values(modelStatus?.routes || modelStatus?.api_routes || {})
            if (!dashboardRoutes.length) return <div className="text-xs text-slate-500 py-2">暂无模型路由状态</div>
            return dashboardRoutes.map(r => (
              <div key={r.route_key || r.stage} className="flex items-center justify-between py-2 border-b border-slate-800/50 text-xs">
                <span className="text-slate-400">{r.label || r.route_key || r.stage}</span>
                <span className="text-slate-300 font-mono truncate max-w-[200px]">{r.model || '未配置'}</span>
              </div>
            ))
          })()}
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
  const [promptSource, setPromptSource] = useState('')
  const [promptOutputPath, setPromptOutputPath] = useState('')
  const [metrics, setMetrics] = useState({})
  const [frags, setFrags] = useState([])
  const [defaultDir, setDefaultDir] = useState('')
  const [runtimeDir, setRuntimeDir] = useState('')
  const [outputPath, setOutputPath] = useState('')
  const [backupDir, setBackupDir] = useState('')
  const [backups, setBackups] = useState([])
  const [editing, setEditing] = useState(null)
  const [editContent, setEditContent] = useState('')
  const [building, setBuilding] = useState(false)
  const [defaultPreview, setDefaultPreview] = useState(null)
  const [diffPreview, setDiffPreview] = useState(null)
  const [tab, setTab] = useState('fragments')
  const [toast, setToast] = useState('')

  const load = useCallback(() => {
    api.get('/prompt').then(r => {
      setPrompt(r.data.content)
      setMetrics(r.data.metrics || {})
      setPromptSource(r.data.source || '')
      setPromptOutputPath(r.data.output_path || '')
    }).catch(() => {})
    api.get('/prompt/fragments').then(r => {
      setFrags(r.data.fragments || [])
      setDefaultDir(r.data.default_dir || '')
      setRuntimeDir(r.data.runtime_dir || '')
      setOutputPath(r.data.output_path || '')
      setBackupDir(r.data.backup_dir || '')
    }).catch(() => {})
    api.get('/prompt/backups').then(r => setBackups(r.data.backups || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const editingFrag = frags.find(f => f.name === editing)
  const origContent = editingFrag?.content || ''
  const dirty = editing && editContent !== origContent

  const openEditor = (f) => {
    if (dirty && editing !== f.name && !confirm('当前修改未保存，确认切换？')) return
    setEditing(f.name)
    setEditContent(f.content)
    setDefaultPreview(null)
    setDiffPreview(null)
  }
  const closeEditor = (force = false) => {
    if (!force && dirty && !confirm('当前修改未保存，确认关闭？')) return
    setEditing(null)
    setEditContent('')
    setDefaultPreview(null)
    setDiffPreview(null)
  }
  const saveFragment = useCallback(() => {
    if (!editing || !dirty) return
    api.put(`/prompt/fragments/${encodeURIComponent(editing)}`, { content: editContent }).then(r => {
      setToast(`已保存到运行时片段 · ${r.data.after_hash}`)
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
        alert('构建失败\n' + (r.data.error || r.data.stderr || ''))
      }
    }).finally(() => setBuilding(false))
  }
  const initRuntime = () => {
    api.post('/prompt/init-runtime').then(r => {
      setToast(r.data.copied?.length ? `已初始化 ${r.data.copied.length} 个缺失片段` : '所有片段已存在')
      load()
    }).catch(e => alert(e.response?.data?.detail || '初始化失败'))
  }
  const viewDefault = (name) => {
    api.get(`/prompt/fragments/${encodeURIComponent(name)}/default`).then(r => setDefaultPreview(r.data))
      .catch(e => alert(e.response?.data?.detail || '获取默认片段失败'))
  }
  const viewDiff = (name) => {
    api.get(`/prompt/fragments/${encodeURIComponent(name)}/diff-default`).then(r => setDiffPreview(r.data))
      .catch(e => alert(e.response?.data?.detail || '对比失败'))
  }
  const resetToDefault = (name) => {
    if (!confirm(`确认用默认版本覆盖运行时片段 ${name}？当前运行时内容将先备份。`)) return
    api.post(`/prompt/fragments/${encodeURIComponent(name)}/reset-to-default`).then(() => {
      setToast(`已恢复 ${name} 到默认版本`)
      load()
    }).catch(e => alert(e.response?.data?.detail || '恢复失败'))
  }
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 3000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div>
      {toast && <div className="mb-3 px-4 py-2 bg-emerald-500/15 border border-emerald-500/30 rounded-xl text-sm text-emerald-400">{toast}</div>}
      <Card className="p-4 mb-4 border-amber-500/20 bg-amber-500/5">
        <div className="flex gap-3">
          <span className="text-xs text-amber-400 mt-0.5">⚠</span>
          <div>
            <p className="text-sm text-amber-300 mb-1">Legacy prompt.md 回滚入口。</p>
            <p className="text-xs text-slate-500">此页面只用于 v1 紧急回滚和迁移对比；V2 真实请求与有效预览请使用 Prompt Runtime V2。默认片段目录由 Git 管理，WebUI 保存只写入运行时片段目录。</p>
            {(defaultDir || runtimeDir || outputPath) && <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-2 text-[10px] text-slate-600">
              {defaultDir && <span>默认片段: <span className="text-slate-500 font-mono">{defaultDir}</span></span>}
              {runtimeDir && <span>运行时片段: <span className="text-slate-500 font-mono">{runtimeDir}</span></span>}
              {outputPath && <span>构建输出: <span className="text-slate-500 font-mono">{outputPath}</span></span>}
              {backupDir && <span>备份: <span className="text-slate-500 font-mono">{backupDir}</span></span>}
            </div>}
          </div>
        </div>
      </Card>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">Legacy Prompt 回滚</h1>
          <Badge tone="amber">v1 rollback only</Badge>
          <div className="flex gap-1 bg-slate-900 rounded-lg p-0.5">
            <button onClick={() => setTab('fragments')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'fragments' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>编辑片段</button>
            <button onClick={() => setTab('preview')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'preview' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>完整 prompt.md</button>
            <button onClick={() => setTab('backups')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'backups' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>备份回滚</button>
          </div>
        </div>
        <div className="flex gap-2">
          <NavLink to="/prompt-preview" className="px-3 py-2 bg-emerald-700/70 hover:bg-emerald-700 rounded-xl text-sm">查看 V2 预览</NavLink>
          <button onClick={initRuntime} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">初始化缺失片段</button>
          <button onClick={rebuild} disabled={building}
            className="px-4 py-2 bg-amber-700/70 hover:bg-amber-700 disabled:opacity-50 rounded-xl text-sm font-medium transition-colors">
            {building ? '构建中...' : '重新构建运行时 prompt.md'}
          </button>
        </div>
      </div>

      {tab === 'preview' ? (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
          <Card className="p-4 xl:col-span-3">
            <pre className="text-xs leading-relaxed whitespace-pre-wrap max-h-[calc(100vh-200px)] overflow-auto text-slate-300">{prompt}</pre>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-400 mb-3">校验</h2>
            <div className="space-y-3 text-sm">
              <div><span className="text-slate-500 text-xs">来源</span><div className="text-slate-300"><Badge tone={promptSource === 'runtime' ? 'emerald' : 'slate'}>{promptSource}</Badge></div></div>
              {promptOutputPath && <div><span className="text-slate-500 text-xs">输出路径</span><div className="text-slate-400 text-[10px] font-mono break-all">{promptOutputPath}</div></div>}
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
                    <button onClick={() => { if (confirm(`确认回滚 ${b.fragment} 到该备份?`)) api.post(`/prompt/backups/${encodeURIComponent(b.name)}/rollback`).then(() => { setToast('已回滚运行时片段，请重新构建 prompt.md'); load() }) }}
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
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors block ${editing === f.name ? 'bg-emerald-500/15 text-emerald-400 font-medium' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}>
                <div className="truncate">{f.name}</div>
                <div className="text-[10px] mt-0.5 truncate">
                  {!f.has_default ? <span className="text-amber-500">无默认版本</span>
                   : f.is_modified ? <span className="text-emerald-400">已修改</span>
                   : <span className="text-slate-600">未修改</span>}
                </div>
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
                    {editingFrag && (
                      <>
                        {!editingFrag.has_default ? <Badge tone="amber">无默认</Badge>
                         : editingFrag.is_modified ? <Badge tone="emerald">已修改</Badge>
                         : <Badge tone="slate">未修改</Badge>}
                        {editingFrag.runtime_hash && <span className="text-[10px] text-slate-600 font-mono">{editingFrag.runtime_hash}</span>}
                      </>
                    )}
                    {dirty && <span className="text-xs text-amber-400">● 未保存</span>}
                  </div>
                  <div className="flex gap-2">
                    {editingFrag?.has_default && <button onClick={() => viewDefault(editing)} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">查看默认</button>}
                    {editingFrag?.has_default && <button onClick={() => viewDiff(editing)} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">对比默认</button>}
                    {editingFrag?.has_default && <button onClick={() => resetToDefault(editing)} className="px-3 py-1.5 bg-amber-700/50 hover:bg-amber-700 text-amber-300 rounded-lg text-xs transition-colors">恢复默认</button>}
                    <button onClick={closeEditor}
                      className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">取消</button>
                    <button onClick={saveFragment}
                      className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium transition-colors">保存到运行时片段</button>
                  </div>
                </div>
                <textarea value={editContent}
                  onChange={e => setEditContent(e.target.value)}
                  className="flex-1 w-full p-4 rounded-xl bg-slate-900 border border-slate-700 text-sm text-slate-300 font-mono leading-relaxed resize-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none" />
                <div className="text-xs text-slate-600 mt-1">
                  Ctrl+S 或 Cmd+S 保存 · 保存后需点"重新构建运行时 prompt.md"生效 · {editContent.length} 字符
                </div>
                {defaultPreview && (
                  <Card className="mt-3 p-4">
                    <h3 className="text-sm font-medium text-slate-400 mb-2">默认版本: {defaultPreview.name} · {defaultPreview.hash}</h3>
                    <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-60 overflow-auto">{defaultPreview.content}</pre>
                  </Card>
                )}
                {diffPreview && (
                  <Card className="mt-3 p-4">
                    <h3 className="text-sm font-medium text-slate-400 mb-2">差异对比 · runtime(default) → runtime(edited)</h3>
                    <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-60 overflow-auto">{diffPreview.diff}</pre>
                  </Card>
                )}
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

// ── Managed Prompts ──
function defaultVarsForPrompt(item) {
  const vars = {}
  const keys = [...(item?.required_vars || []), ...(item?.optional_vars || [])]
  keys.forEach(k => { vars[k] = '' })
  if ('user_input' in vars) vars.user_input = '你好'
  if ('history_context' in vars) vars.history_context = '上一轮上下文'
  if ('pending_text' in vars) vars.pending_text = '有人在群里问机器人问题'
  if ('question' in vars) vars.question = '最近有哪些异常?'
  if ('conversation' in vars) vars.conversation = '用户: 我喜欢简洁的中文回复'
  if ('group_id' in vars) vars.group_id = 'group_1001'
  if ('logs' in vars) vars.logs = '张三: 今天部署完成\n李四: 发现一个错误'
  return JSON.stringify(vars, null, 2)
}

function ManagedPromptsPage() {
  const [items, setItems] = useState([])
  const [mode, setMode] = useState('')
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState(null)
  const [content, setContent] = useState('')
  const [varsText, setVarsText] = useState('{}')
  const [preview, setPreview] = useState(null)
  const [history, setHistory] = useState([])
  const [toast, setToast] = useState('')
  const [loading, setLoading] = useState(false)
  const [promptDir, setPromptDir] = useState('')
  const [defaultDir, setDefaultDir] = useState('')

  const loadList = useCallback(() => {
    setLoading(true)
    api.get('/prompts').then(r => {
      const list = r.data.items || []
      setItems(list)
      setMode(r.data.mode || '')
      setPromptDir(r.data.prompt_dir || '')
      setDefaultDir(r.data.default_dir || '')
      if (!selected && list.length) setSelected(list[0].prompt_key)
    }).finally(() => setLoading(false))
  }, [selected])

  useEffect(() => {
    const id = setTimeout(loadList, 0)
    return () => clearTimeout(id)
  }, [loadList])
  useEffect(() => {
    if (!selected) return
    api.get(`/prompts/${encodeURIComponent(selected)}`).then(r => {
      setDetail(r.data)
      setContent(r.data.content || '')
      setVarsText(defaultVarsForPrompt(r.data))
      setPreview(null)
    }).catch(e => alert(e.response?.data?.detail || '加载模板失败'))
    api.get(`/prompts/${encodeURIComponent(selected)}/history`).then(r => setHistory(r.data.items || [])).catch(() => setHistory([]))
  }, [selected])

  const save = () => {
    api.put(`/prompts/${encodeURIComponent(selected)}`, { content }).then(r => {
      setToast(`已保存 ${r.data.after_hash}`)
      loadList()
      api.get(`/prompts/${encodeURIComponent(selected)}/history`).then(x => setHistory(x.data.items || []))
    }).catch(e => alert(e.response?.data?.detail || '保存失败'))
  }
  const runPreview = () => {
    let variables
    try { variables = JSON.parse(varsText || '{}') }
    catch { alert('变量 JSON 格式错误'); return }
    api.post(`/prompts/${encodeURIComponent(selected)}/preview`, { variables, mode: 'preview' }).then(r => setPreview(r.data))
      .catch(e => alert(e.response?.data?.detail || '预览失败'))
  }
  const reload = () => api.post('/prompts/reload').then(() => { setToast('已重新加载模板缓存'); loadList() })
  const rollback = (name) => {
    if (!confirm(`确认回滚 ${selected} 到该版本?`)) return
    api.post(`/prompts/${encodeURIComponent(selected)}/rollback`, { backup_name: name }).then(() => {
      setToast('已回滚')
      api.get(`/prompts/${encodeURIComponent(selected)}`).then(r => { setDetail(r.data); setContent(r.data.content || '') })
      api.get(`/prompts/${encodeURIComponent(selected)}/history`).then(r => setHistory(r.data.items || []))
    }).catch(e => alert(e.response?.data?.detail || '回滚失败'))
  }
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 2500)
    return () => clearTimeout(t)
  }, [toast])

  if (loading && !items.length) return <Spinner />
  return (
    <div>
      {toast && <div className="mb-3 px-4 py-2 bg-emerald-500/15 border border-emerald-500/30 rounded-xl text-sm text-emerald-400">{toast}</div>}
      <Card className="p-4 mb-4 border-emerald-500/20 bg-emerald-500/5">
        <div className="flex gap-3">
          <span className="text-xs text-emerald-400 mt-0.5">ℹ</span>
          <div>
            <p className="text-sm text-emerald-300 mb-1">V1 PromptManager 模板和对比工具。</p>
            <p className="text-xs text-slate-500">V2 主链路使用独立 `core/prompt_v2` compiler；这里保留用于 v1 回滚、迁移整理和离线对比。默认模板目录：<span className="text-slate-400 font-mono">{defaultDir || 'prompts.default'}</span> · 运行时模板目录：<span className="text-slate-400 font-mono">{promptDir || 'data/prompts'}</span>。</p>
          </div>
        </div>
      </Card>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">V1 模板 / 对比</h1>
          <p className="text-slate-500 text-sm">PromptManager Markdown 模板、变量预览、备份与回滚；不作为 V2 主回复编排入口</p>
          {(promptDir || defaultDir) && <div className="flex gap-4 mt-1 text-[10px] text-slate-600">
            {defaultDir && <span>默认模板: <span className="text-slate-500 font-mono">{defaultDir}</span></span>}
            {promptDir && <span>运行目录: <span className="text-slate-500 font-mono">{promptDir}</span></span>}
          </div>}
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={mode === 'managed' ? 'emerald' : mode === 'shadow' ? 'blue' : 'slate'}>{mode || 'unknown'}</Badge>
          <button onClick={reload} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">Reload</button>
          <button onClick={save} disabled={!selected} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 rounded-xl text-sm font-medium">保存到运行时模板</button>
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4" style={{ minHeight: 'calc(100vh - 150px)' }}>
        <Card className="p-3 xl:col-span-2 overflow-auto">
          <div className="text-xs text-slate-500 mb-2">{items.length} 个模板</div>
          {items.map(item => (
            <button key={item.prompt_key} onClick={() => setSelected(item.prompt_key)}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs mb-1 ${selected === item.prompt_key ? 'bg-emerald-500/15 text-emerald-400' : 'text-slate-400 hover:bg-slate-800 hover:text-white'}`}>
              <div className="font-medium truncate">{item.prompt_key}</div>
              {item.parse_error ? <div className="text-red-400 truncate">{item.parse_error}</div> : <div className="text-slate-600 truncate">{item.name}</div>}
            </button>
          ))}
        </Card>
        <div className="xl:col-span-6 flex flex-col min-w-0">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h2 className="text-sm font-medium text-emerald-400">{selected || '未选择'}</h2>
              <div className="text-xs text-slate-600">{detail?.description || ''}</div>
            </div>
            <div className="text-xs text-slate-500">{content.length} 字符</div>
          </div>
          <textarea value={content} onChange={e => setContent(e.target.value)}
            className="flex-1 min-h-[520px] w-full p-4 rounded-xl bg-slate-900 border border-slate-700 text-sm text-slate-300 font-mono leading-relaxed resize-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none" />
        </div>
        <div className="xl:col-span-4 space-y-4 min-w-0">
          <Card className="p-4">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-medium text-slate-300">预览变量</h2>
              <button onClick={runPreview} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs">预览</button>
            </div>
            <textarea value={varsText} onChange={e => setVarsText(e.target.value)}
              className="w-full h-44 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 resize-none" />
            {preview && (
              <div className="mt-3">
                <div className="flex gap-2 mb-2">
                  <Badge tone="blue">{preview.token_estimate || 0} tokens</Badge>
                  {(preview.warnings || []).length > 0 && <Badge tone="amber">{preview.warnings.length} warnings</Badge>}
                </div>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-xs bg-slate-950 border border-slate-800 rounded-xl p-3 text-slate-300">{preview.content}</pre>
              </div>
            )}
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">备份历史</h2>
            <div className="space-y-2 max-h-72 overflow-auto">
              {history.map(h => (
                <div key={h.name} className="flex items-center justify-between gap-2 text-xs border-b border-slate-800 pb-2">
                  <div className="min-w-0">
                    <div className="font-mono text-slate-400 truncate">{h.created_at}</div>
                    <div className="text-slate-600">{h.hash} · {h.size} bytes</div>
                  </div>
                  <button onClick={() => rollback(h.name)} className="px-2 py-1 bg-amber-700/50 hover:bg-amber-700 text-amber-300 rounded-lg">回滚</button>
                </div>
              ))}
              {!history.length && <div className="py-8 text-center text-sm text-slate-600">暂无备份</div>}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

const PROMPT_V2_RUNTIME_NODES = [
  { key: 'runtime_context', label: 'system: runtime_context' },
  { key: 'persona_reference', label: 'system: persona_reference' },
  { key: 'conversation_context_header', label: 'system: conversation_context_header' },
  { key: 'history_messages', label: 'history: messages' },
  { key: 'group_context', label: 'system: group profile / expression / jargon' },
  { key: 'effort_constraint', label: 'system: effort_constraint' },
  { key: 'runtime_tool_prompt', label: 'system: runtime_tool_prompt' },
  { key: 'current_user_event', label: 'user: current_user_input' },
]

function flowAppliesToChat(item, chatType) {
  const types = item?.chat_types
  if (!Array.isArray(types) || !types.length) return true
  return types.includes(chatType)
}

function orderedFlowNodes(flow, chatType) {
  const nodes = (flow?.nodes || []).filter(node => flowAppliesToChat(node, chatType))
  const ids = new Set(nodes.map(node => node.id))
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const edges = (flow?.edges || []).filter(edge =>
    flowAppliesToChat(edge, chatType) && ids.has(edge.from) && ids.has(edge.to)
  )
  const incoming = new Map(nodes.map(node => [node.id, new Set()]))
  const outgoing = new Map(nodes.map(node => [node.id, new Set()]))
  edges.forEach(edge => {
    incoming.get(edge.to)?.add(edge.from)
    outgoing.get(edge.from)?.add(edge.to)
  })
  const ready = [...incoming.entries()]
    .filter(([, from]) => from.size === 0)
    .map(([id]) => id)
    .sort((a, b) => (index.get(a) || 0) - (index.get(b) || 0))
  const result = []
  while (ready.length) {
    const id = ready.shift()
    if (result.includes(id)) continue
    result.push(id)
    ;[...(outgoing.get(id) || [])]
      .sort((a, b) => (index.get(a) || 0) - (index.get(b) || 0))
      .forEach(target => {
        incoming.get(target)?.delete(id)
        if ((incoming.get(target)?.size || 0) === 0) ready.push(target)
      })
    ready.sort((a, b) => (index.get(a) || 0) - (index.get(b) || 0))
  }
  nodes.forEach(node => {
    if (!result.includes(node.id)) result.push(node.id)
  })
  const byId = new Map(nodes.map(node => [node.id, node]))
  return result.map(id => byId.get(id)).filter(Boolean)
}

const FLOW_NODE_WIDTH = 220
const FLOW_NODE_HEIGHT = 96
const PROMPT_V2_DEFAULT_NODE_POSITIONS = {
  base_contract: { x: 80, y: 220 },
  group_policy: { x: 360, y: 80 },
  private_policy: { x: 360, y: 360 },
  runtime_context: { x: 660, y: 220 },
  identity_context: { x: 940, y: 220 },
  persona_reference: { x: 1220, y: 220 },
  conversation_context_header: { x: 1500, y: 220 },
  history_messages: { x: 1780, y: 220 },
  group_context: { x: 2060, y: 80 },
  effort_constraint: { x: 2340, y: 220 },
  runtime_tool_prompt: { x: 2620, y: 220 },
  current_user_event: { x: 2900, y: 220 },
}

function nodeCanvasPosition(node, index = 0) {
  const raw = node?.position || PROMPT_V2_DEFAULT_NODE_POSITIONS[node?.id] || {}
  const fallbackX = 80 + (index % 5) * 280
  const fallbackY = 120 + Math.floor(index / 5) * 180
  const x = Number(raw.x)
  const y = Number(raw.y)
  return {
    x: Number.isFinite(x) ? x : fallbackX,
    y: Number.isFinite(y) ? y : fallbackY,
  }
}

function PromptFlowCanvas({
  flow,
  chatType,
  selectedNodeId,
  connectingFrom,
  onSelectNode,
  onMoveNode,
  onDeleteNode,
  onStartConnect,
  onConnectNode,
  onCancelConnect,
}) {
  const canvasRef = useRef(null)
  const dragRef = useRef(null)
  const nodes = flow?.nodes || []
  const nodeById = new Map(nodes.map((node, idx) => [node.id, { node, idx, pos: nodeCanvasPosition(node, idx) }]))
  const activeNodeIds = new Set(nodes.filter(node => flowAppliesToChat(node, chatType)).map(node => node.id))
  const edges = (flow?.edges || []).filter(edge => nodeById.has(edge.from) && nodeById.has(edge.to))

  const startDragNode = (event, node) => {
    if (event.button !== 0) return
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const idx = nodes.findIndex(item => item.id === node.id)
    const pos = nodeCanvasPosition(node, idx)
    dragRef.current = {
      nodeId: node.id,
      offsetX: event.clientX - rect.left + canvasRef.current.scrollLeft - pos.x,
      offsetY: event.clientY - rect.top + canvasRef.current.scrollTop - pos.y,
    }
  }

  const handleMouseMove = event => {
    if (!dragRef.current || !canvasRef.current) return
    const rect = canvasRef.current.getBoundingClientRect()
    const x = event.clientX - rect.left + canvasRef.current.scrollLeft - dragRef.current.offsetX
    const y = event.clientY - rect.top + canvasRef.current.scrollTop - dragRef.current.offsetY
    onMoveNode(dragRef.current.nodeId, {
      x: Math.max(20, Math.round(x)),
      y: Math.max(20, Math.round(y)),
    })
  }

  const stopDragNode = () => {
    dragRef.current = null
  }

  return (
    <div
      ref={canvasRef}
      data-testid="prompt-flow-canvas"
      onMouseMove={handleMouseMove}
      onMouseUp={stopDragNode}
      onMouseLeave={stopDragNode}
      className="relative h-[680px] overflow-auto rounded-lg border border-slate-800 bg-slate-950"
      style={{
        backgroundImage: 'radial-gradient(circle, rgba(148, 163, 184, 0.16) 1px, transparent 1px)',
        backgroundSize: '28px 28px',
      }}
    >
      <div className="relative min-w-[3220px] min-h-[760px]">
        <svg data-testid="prompt-flow-edge-layer" className="absolute inset-0 w-full h-full pointer-events-none">
          <defs>
            <marker id="prompt-flow-arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#34d399" />
            </marker>
            <marker id="prompt-flow-arrow-muted" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
            </marker>
          </defs>
          {edges.map((edge, idx) => {
            const from = nodeById.get(edge.from)
            const to = nodeById.get(edge.to)
            const active = flowAppliesToChat(edge, chatType) && activeNodeIds.has(edge.from) && activeNodeIds.has(edge.to)
            const x1 = from.pos.x + FLOW_NODE_WIDTH
            const y1 = from.pos.y + FLOW_NODE_HEIGHT / 2
            const x2 = to.pos.x
            const y2 = to.pos.y + FLOW_NODE_HEIGHT / 2
            const mid = Math.max(70, Math.abs(x2 - x1) / 2)
            const path = `M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}`
            return (
              <path
                key={`${edge.from}-${edge.to}-${idx}`}
                d={path}
                fill="none"
                stroke={active ? '#34d399' : '#475569'}
                strokeWidth={active ? 2.5 : 1.5}
                strokeDasharray={active ? '0' : '5 6'}
                markerEnd={active ? 'url(#prompt-flow-arrow-active)' : 'url(#prompt-flow-arrow-muted)'}
                opacity={active ? 0.95 : 0.45}
              />
            )
          })}
        </svg>

        {nodes.map((node, idx) => {
          const pos = nodeCanvasPosition(node, idx)
          const active = activeNodeIds.has(node.id)
          const selected = selectedNodeId === node.id
          const connectTarget = connectingFrom && connectingFrom !== node.id
          return (
            <div
              key={node.id}
              className={`absolute rounded-lg border bg-slate-900/95 ${selected ? 'border-emerald-400 ring-1 ring-emerald-500/40' : active ? 'border-slate-700' : 'border-slate-800 opacity-50'} cursor-default`}
              style={{ left: pos.x, top: pos.y, width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT }}
              onMouseDown={e => startDragNode(e, node)}
              onClick={() => onSelectNode(node)}
            >
              <div className="flex h-full flex-col p-3">
                <div className="flex items-start gap-2 min-w-0">
                  <span className={`mt-0.5 h-2.5 w-2.5 rounded-full ${node.type === 'template' ? 'bg-emerald-400' : 'bg-blue-400'}`} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-100">{node.label || node.id}</div>
                    <div className="mt-1 truncate text-[10px] text-slate-500">{node.type === 'template' ? node.template_key : node.runtime_key}</div>
                  </div>
                  <Badge tone={node.type === 'template' ? 'emerald' : 'blue'}>{node.chat_types?.[0] || 'all'}</Badge>
                </div>
                <div className="mt-auto flex items-center gap-1">
                  <button onMouseDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); onStartConnect(node.id) }}
                    className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-[10px] text-slate-300">
                    开始连线
                  </button>
                  {connectTarget && (
                    <button onMouseDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); onConnectNode(connectingFrom, node.id) }}
                      className="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-[10px] text-white">
                      连到这里
                    </button>
                  )}
                  <button onMouseDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); onDeleteNode(node.id) }}
                    className="ml-auto px-2 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-[10px] text-red-300">
                    删除节点
                  </button>
                </div>
              </div>
            </div>
          )
        })}
        {connectingFrom && (
          <button onClick={onCancelConnect} className="absolute left-4 top-4 rounded bg-amber-500/15 border border-amber-500/30 px-3 py-1.5 text-xs text-amber-200">
            正在从 {connectingFrom} 连线，点击目标节点的"连到这里"
          </button>
        )}
      </div>
    </div>
  )
}

function PromptV2TemplatesPage() {
  const [chatType, setChatType] = useState('group')
  const [templates, setTemplates] = useState([])
  const [selected, setSelected] = useState('chat_main')
  const [selectedNodeId, setSelectedNodeId] = useState('')
  const [detail, setDetail] = useState(null)
  const [content, setContent] = useState('')
  const [variables, setVariables] = useState([])
  const [flow, setFlow] = useState({ version: 1, nodes: [], edges: [] })
  const [flowSource, setFlowSource] = useState('')
  const [flowPath, setFlowPath] = useState('')
  const [defaultDir, setDefaultDir] = useState('')
  const [runtimeDir, setRuntimeDir] = useState('')
  const [templateToAdd, setTemplateToAdd] = useState('')
  const [runtimeToAdd, setRuntimeToAdd] = useState('runtime_context')
  const [connectingFrom, setConnectingFrom] = useState('')
  const [toast, setToast] = useState('')
  const orderedNodes = orderedFlowNodes(flow, chatType)
  const allNodes = flow?.nodes || []
  const selectedNode = allNodes.find(node => node.id === selectedNodeId) || orderedNodes[0] || allNodes[0] || null
  const selectedTemplateKey = selectedNode?.type === 'template' ? (selectedNode.template_key || selected) : ''

  const loadTemplates = useCallback(() => {
    api.get('/prompt-v2/templates').then(r => {
      const list = r.data.items || []
      setTemplates(list)
      setDefaultDir(r.data.default_dir || '')
      setRuntimeDir(r.data.runtime_dir || '')
      const keys = list.map(item => item.template_key)
      setTemplateToAdd(prev => prev || keys[0] || '')
      setSelected(prev => keys.includes(prev) ? prev : (keys.includes('chat_main') ? 'chat_main' : keys[0] || ''))
    }).catch(e => alert(e.response?.data?.detail || '加载 V2 模板失败'))
  }, [])

  const loadFlow = useCallback(() => {
    api.get('/prompt-v2/flow').then(r => {
      setFlow(r.data.flow || { version: 1, nodes: [], edges: [] })
      setFlowSource(r.data.source || '')
      setFlowPath(r.data.path || '')
    }).catch(e => alert(e.response?.data?.detail || '加载 V2 编排图失败'))
  }, [])

  useEffect(() => {
    loadTemplates()
    loadFlow()
    api.get('/prompt-v2/variables')
      .then(r => setVariables(r.data.items || []))
      .catch(() => setVariables([]))
  }, [loadTemplates, loadFlow])

  useEffect(() => {
    if (!selectedTemplateKey) return
    api.get(`/prompt-v2/templates/${encodeURIComponent(selectedTemplateKey)}`).then(r => {
      setDetail(r.data)
      setContent(r.data.content || '')
    }).catch(e => alert(e.response?.data?.detail || '加载 V2 模板失败'))
  }, [selectedTemplateKey])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(''), 2500)
    return () => clearTimeout(t)
  }, [toast])

  const save = () => {
    if (!selectedTemplateKey) return
    api.put(`/prompt-v2/templates/${encodeURIComponent(selectedTemplateKey)}`, { content }).then(r => {
      setToast(`已保存 ${selectedTemplateKey} · ${r.data.after_hash?.slice(0, 12) || ''}`)
      loadTemplates()
    }).catch(e => alert(e.response?.data?.detail || '保存 V2 模板失败'))
  }

  const saveFlow = () => {
    api.put('/prompt-v2/flow', { flow }).then(r => {
      setToast(`已保存编排图 · ${r.data.runtime_path || ''}`)
      loadFlow()
    }).catch(e => alert(e.response?.data?.detail || '保存 V2 编排图失败'))
  }

  const updateNode = (nodeId, patch) => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).map(node => node.id === nodeId ? { ...node, ...patch } : node),
    }))
    if (patch.template_key) setSelected(patch.template_key)
  }

  const addNodeAfterSelection = node => {
    const anchorId = selectedNode?.id || orderedNodes[orderedNodes.length - 1]?.id || ''
    setFlow(prev => {
      const nextNodes = [...(prev.nodes || []), node]
      const nextEdges = [...(prev.edges || [])]
      if (anchorId && anchorId !== node.id) {
        nextEdges.push({ from: anchorId, to: node.id, chat_types: [chatType] })
      }
      return { ...prev, nodes: nextNodes, edges: nextEdges }
    })
    setSelectedNodeId(node.id)
    if (node.type === 'template') setSelected(node.template_key)
  }

  const selectNode = node => {
    setSelectedNodeId(node.id)
    if (node.type === 'template') setSelected(node.template_key || '')
  }

  const moveNode = (nodeId, position) => {
    updateNode(nodeId, { position })
  }

  const addTemplateNode = () => {
    const key = templateToAdd || templates[0]?.template_key
    if (!key) return
    const id = `${key}_${Date.now().toString(36)}`
    addNodeAfterSelection({
      id,
      type: 'template',
      label: `system: ${key}`,
      template_key: key,
      chat_types: [chatType],
    })
  }

  const addRuntimeNode = () => {
    const option = PROMPT_V2_RUNTIME_NODES.find(item => item.key === runtimeToAdd) || PROMPT_V2_RUNTIME_NODES[0]
    if (!option) return
    const id = `${option.key}_${Date.now().toString(36)}`
    addNodeAfterSelection({
      id,
      type: 'runtime',
      label: option.label,
      runtime_key: option.key,
      chat_types: [chatType],
    })
  }

  const deleteNode = nodeId => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).filter(node => node.id !== nodeId),
      edges: (prev.edges || []).filter(edge => edge.from !== nodeId && edge.to !== nodeId),
    }))
    setSelectedNodeId('')
  }

  const connectNode = (fromId, toId) => {
    setFlow(prev => ({
      ...prev,
      edges: [
        ...(prev.edges || []).filter(edge => !(edge.from === fromId && flowAppliesToChat(edge, chatType))),
        ...(toId ? [{ from: fromId, to: toId, chat_types: [chatType] }] : []),
      ],
    }))
    setConnectingFrom('')
  }

  const autoLayoutFlow = () => {
    setFlow(prev => ({
      ...prev,
      nodes: (prev.nodes || []).map((node, idx) => ({
        ...node,
        position: nodeCanvasPosition({ ...node, position: undefined }, idx),
      })),
    }))
  }

  const updateSelectedScope = value => {
    if (!selectedNode) return
    updateNode(selectedNode.id, { chat_types: value === 'all' ? undefined : [value] })
  }

  const updateSelectedTemplate = value => {
    if (!selectedNode || selectedNode.type !== 'template') return
    setSelected(value)
    updateNode(selectedNode.id, { template_key: value, label: `system: ${value}` })
  }

  const updateSelectedRuntime = value => {
    if (!selectedNode || selectedNode.type !== 'runtime') return
    const option = PROMPT_V2_RUNTIME_NODES.find(item => item.key === value)
    updateNode(selectedNode.id, { runtime_key: value, label: option?.label || `system: ${value}` })
  }

  return (
    <div>
      {toast && <div className="mb-3 px-4 py-2 bg-emerald-500/15 border border-emerald-500/30 rounded-lg text-sm text-emerald-400">{toast}</div>}
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">Prompt V2 模板</h1>
          <p className="text-slate-500 text-sm">模板是节点内容，编排图决定真实 PromptPlan 顺序；变量是全局白名单，当前输入仍只作为 user event 注入一次</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-slate-600">
            <span>默认模板目录: <span className="font-mono text-slate-500">{defaultDir || '-'}</span></span>
            <span>运行时模板目录: <span className="font-mono text-slate-500">{runtimeDir || '-'}</span></span>
            <span>编排图: <span className="font-mono text-slate-500">{flowPath || '-'}</span></span>
          </div>
        </div>
        <div className="flex gap-2">
          <NavLink to="/prompt-preview" className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300">运行预览</NavLink>
          <button onClick={saveFlow} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs font-medium">保存编排图</button>
          <button onClick={save} disabled={!selectedTemplateKey} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-xs font-medium">保存 V2 模板</button>
        </div>
      </div>

      <div className="grid grid-cols-1 2xl:grid-cols-[260px_minmax(0,1fr)_420px] gap-4">
        <div className="space-y-3 min-w-0">
          <Card className="p-3">
            <div className="text-xs font-medium text-slate-300 mb-2">Canvas 编排</div>
            <select value={chatType} onChange={e => setChatType(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200">
              <option value="group">高亮群聊路径</option>
              <option value="private">高亮私聊路径</option>
            </select>
            <div className="mt-2 text-[11px] text-slate-600">source: {flowSource || '-'} · 灰色节点不参与当前路径</div>
            <button onClick={autoLayoutFlow} className="mt-3 w-full px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">自动布局</button>
          </Card>

          <Card className="p-3 space-y-3">
            <div>
              <div className="text-xs font-medium text-slate-300 mb-2">添加模板节点</div>
              <select value={templateToAdd} onChange={e => setTemplateToAdd(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                {templates.map(t => <option key={t.template_key} value={t.template_key}>{t.template_key}</option>)}
              </select>
              <button onClick={addTemplateNode} className="mt-2 w-full px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-200">添加节点</button>
            </div>
            <div>
              <div className="text-xs font-medium text-slate-300 mb-2">添加运行时节点</div>
              <select value={runtimeToAdd} onChange={e => setRuntimeToAdd(e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                {PROMPT_V2_RUNTIME_NODES.map(item => <option key={item.key} value={item.key}>{item.key}</option>)}
              </select>
              <button onClick={addRuntimeNode} className="mt-2 w-full px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-200">添加运行时</button>
            </div>
          </Card>

          <Card className="p-3">
            <div className="text-xs font-medium text-slate-300 mb-2">当前路径顺序</div>
            <div className="space-y-1 max-h-72 overflow-auto">
              {orderedNodes.map((node, idx) => (
                <button key={node.id} onClick={() => selectNode(node)}
                  className={`w-full text-left rounded px-2 py-1.5 text-xs ${selectedNode?.id === node.id ? 'bg-emerald-500/15 text-emerald-300' : 'hover:bg-slate-800 text-slate-400'}`}>
                  <span className="text-slate-600 mr-2">{idx + 1}</span>{node.label || node.id}
                </button>
              ))}
            </div>
          </Card>
        </div>

        <div className="min-w-0">
          <PromptFlowCanvas
            flow={flow}
            chatType={chatType}
            selectedNodeId={selectedNode?.id || ''}
            connectingFrom={connectingFrom}
            onSelectNode={selectNode}
            onMoveNode={moveNode}
            onDeleteNode={deleteNode}
            onStartConnect={setConnectingFrom}
            onConnectNode={connectNode}
            onCancelConnect={() => setConnectingFrom('')}
          />
        </div>

        <div className="min-w-0 space-y-4">
          <Card className="p-4">
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="min-w-0">
                <div className="text-xs text-slate-500 mb-1">当前节点</div>
                <h2 className="text-sm font-medium text-emerald-300 truncate">{selectedNode?.label || '未选择节点'}</h2>
                <div className="text-[11px] text-slate-600 truncate">{selectedNode?.id || '-'}</div>
              </div>
              {selectedNode && <Badge tone={selectedNode.type === 'template' ? 'emerald' : 'blue'}>{selectedNode.type}</Badge>}
            </div>

            {selectedNode ? (
              <div className="space-y-3">
                <label className="block text-xs text-slate-500">节点名称
                  <input value={selectedNode.label || ''} onChange={e => updateNode(selectedNode.id, { label: e.target.value })}
                    className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200" />
                </label>
                <label className="block text-xs text-slate-500">作用范围
                  <select value={selectedNode.chat_types?.[0] || 'all'} onChange={e => updateSelectedScope(e.target.value)}
                    className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                    <option value="all">全局</option>
                    <option value="group">仅群聊</option>
                    <option value="private">仅私聊</option>
                  </select>
                </label>
                {selectedNode.type === 'template' ? (
                  <label className="block text-xs text-slate-500">节点模板
                    <select value={selectedTemplateKey || ''} onChange={e => updateSelectedTemplate(e.target.value)}
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                      {templates.map(t => <option key={t.template_key} value={t.template_key}>{t.template_key}</option>)}
                    </select>
                  </label>
                ) : (
                  <label className="block text-xs text-slate-500">runtime_key
                    <select value={selectedNode.runtime_key || ''} onChange={e => updateSelectedRuntime(e.target.value)}
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs text-slate-200">
                      {PROMPT_V2_RUNTIME_NODES.map(item => <option key={item.key} value={item.key}>{item.key}</option>)}
                    </select>
                  </label>
                )}
              </div>
            ) : (
              <div className="text-sm text-slate-600">点击画布节点开始编辑。</div>
            )}
          </Card>

          <Card className="p-4">
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="min-w-0">
                <div className="text-xs font-medium text-slate-300">模板内容</div>
                <div className="text-[11px] text-slate-600 truncate">{detail?.active_path || ''}</div>
              </div>
              <div className="flex gap-2">
                <Badge tone={detail?.source === 'runtime' ? 'emerald' : 'slate'}>{detail?.source || 'default'}</Badge>
                <Badge tone="blue">{detail?.sha256?.slice(0, 12) || '-'}</Badge>
              </div>
            </div>
            {selectedNode?.type === 'runtime' ? (
              <div className="min-h-[340px] rounded-lg bg-slate-950 border border-slate-800 p-4">
                <div className="text-xs text-slate-500 mb-2">运行时注入节点</div>
                <div className="text-lg text-slate-200 mb-1">{selectedNode.runtime_key}</div>
                <div className="text-sm text-slate-500">这个节点由 compiler 注入真实运行数据，不在模板文件中编辑。</div>
              </div>
            ) : (
              <textarea value={content} onChange={e => setContent(e.target.value)}
                className="w-full min-h-[340px] p-4 rounded-lg bg-slate-950 border border-slate-800 text-sm font-mono text-slate-300 leading-relaxed resize-y focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none" />
            )}
          </Card>

          <Card className="p-4">
            <div className="text-xs font-medium text-slate-300 mb-2">全局可插入变量白名单</div>
            <div className="flex flex-wrap gap-2 max-h-48 overflow-auto">
              {variables.map(v => (
                <span key={v.name} title={`${v.description || ''}${v.example ? ` · 示例: ${v.example}` : ''}`} className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-xs">
                  <code className="text-emerald-300">{`{{ ${v.name} }}`}</code>
                  <span className="text-slate-500">{v.description}</span>
                </span>
              ))}
              {!variables.length && <span className="text-xs text-slate-600">没有开放变量</span>}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

function EffectivePromptPreviewPage() {
  const [form, setForm] = useState({
    engine: 'v2',
    chat_type: 'private',
    session_id: '',
    user_id: '',
    group_id: '',
    sender_name: '',
    prompt_key: '',
    mode: 'shadow',
    user_input: '你好',
  })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const update = (key, value) => setForm(prev => ({ ...prev, [key]: value }))
  const run = () => {
    setLoading(true)
    api.post('/prompt/effective-preview', form)
      .then(r => setResult(r.data))
      .catch(e => alert(e.response?.data?.detail || '预览失败'))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    const id = setTimeout(run, 0)
    return () => clearTimeout(id)
  }, [])
  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-2xl font-bold">Prompt Runtime V2</h1>
            <Badge tone="emerald">primary</Badge>
          </div>
          <p className="text-slate-500 text-sm">按 chat/session 调用真实 compiler，还原本轮实际发给模型的 messages、tools schema、section hash 和审计信息</p>
        </div>
        <NavLink to="/prompt-v2-templates" className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300">
          V2 模板
        </NavLink>
      </div>
      <Card className="p-4 mb-4 border-emerald-500/20 bg-emerald-500/5">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <MiniStat label="线上模式" value="v1 / v2" tone="emerald" />
          <MiniStat label="当前页面默认" value="v2" tone="emerald" />
          <MiniStat label="shadow / managed" value="仅 v1 对比" tone="amber" />
          <MiniStat label="当前输入" value="只作为 user event" />
        </div>
      </Card>
      <Card className="p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <label className="text-xs text-slate-500">engine
            <select value={form.engine} onChange={e => update('engine', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
              <option value="v2">v2 - 当前运行时</option>
              <option value="v1">v1 - 回滚/对比</option>
            </select>
          </label>
          <label className="text-xs text-slate-500">chat_type
            <select value={form.chat_type} onChange={e => update('chat_type', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
              <option value="private">private</option>
              <option value="group">group</option>
            </select>
          </label>
          {form.engine === 'v1' ? (
            <label className="text-xs text-slate-500">v1 mode
              <select value={form.mode} onChange={e => update('mode', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200">
                <option value="shadow">shadow</option>
                <option value="managed">managed</option>
                <option value="legacy">legacy</option>
              </select>
            </label>
          ) : (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
              <div className="text-xs text-slate-500">v2 mode</div>
              <div className="text-sm text-emerald-300 mt-1">无 shadow / managed</div>
            </div>
          )}
          <label className="text-xs text-slate-500">session_id
            <input value={form.session_id} onChange={e => update('session_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">group_id
            <input value={form.group_id} onChange={e => update('group_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">user_id
            <input value={form.user_id} onChange={e => update('user_id', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">sender_name
            <input value={form.sender_name} onChange={e => update('sender_name', e.target.value)} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
          <label className="text-xs text-slate-500">prompt_key
            <input value={form.prompt_key} onChange={e => update('prompt_key', e.target.value)} placeholder={form.chat_type === 'group' ? 'group_chat' : 'private_chat'} className="mt-1 w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200" />
          </label>
        </div>
        <label className="block text-xs text-slate-500 mt-3">user_input
          <textarea value={form.user_input} onChange={e => update('user_input', e.target.value)} className="mt-1 w-full h-24 bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-200 resize-none" />
        </label>
        <div className="mt-3 flex gap-2">
          <button onClick={run} disabled={loading} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">{loading ? '生成中...' : '生成预览'}</button>
          {result?.recent_agent_run_id && <NavLink to={`/agent-runs/${result.recent_agent_run_id}`} className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">最近运行</NavLink>}
        </div>
      </Card>
      <Card className="p-3 mb-4 border-blue-500/20 bg-blue-500/5">
        <div className="flex gap-2">
          <span className="text-xs text-blue-400 mt-0.5">ℹ</span>
          <div className="text-xs text-slate-500">
            这是预览构造结果（根据 session_id 读取历史、画像和运行时工具说明模拟构造），<strong>不是从真实模型调用链路抓取的最终 payload</strong>。
            真实发送的 request 请以 <NavLink to="/llm-api-logs" className="text-blue-400 underline">LLM API 日志</NavLink> 中的 request_json 为准。
          </div>
        </div>
      </Card>
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <MiniStat label="prompt_source" value={result.prompt_source || '-'} />
            <MiniStat label="prompt_mode" value={result.prompt_mode || '-'} />
            <MiniStat label="prompt_key" value={result.prompt_key || '-'} />
            <MiniStat label="prompt_sha" value={(result.prompt_sha256 || '').slice(0, 16) || '-'} />
            <MiniStat label="messages" value={(result.messages || []).length} />
          </div>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Prompt 来源</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <JsonBlock value={result.prompt_runtime_path || '-'} className="max-h-24" />
              <JsonBlock value={result.prompt_default_path || '-'} className="max-h-24" />
            </div>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">Messages</h2>
            <div className="space-y-1">{(result.messages || []).map((msg, i) => <MessageAccordion key={i} message={msg} index={i} />)}</div>
          </Card>
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">上下文与工具</h2>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              <RawJsonAccordion label="runtime_context" text={result.runtime_context || ''} defaultOpen />
              <RawJsonAccordion label="persona_reference" text={result.persona_reference || ''} />
              <RawJsonAccordion label="conversation_context" text={result.history_context || ''} />
              <RawJsonAccordion label="运行时工具说明" text={result.runtime_tool_prompt || ''} />
            </div>
          </Card>
          <ToolSchemaPreview schemas={result.tool_schemas || result.effective_tool_schemas || []} disabledTools={result.disabled_tools || {}} />
          <Card className="p-4">
            <h2 className="text-sm font-medium text-slate-300 mb-3">完整 request_json</h2>
            <JsonBlock value={result.request_json} className="max-h-[700px]" />
          </Card>
          {(result.recent_llm_api_logs || []).length > 0 && <LLMApiRequestLogsBlock logs={result.recent_llm_api_logs} />}
        </div>
      )}
    </div>
  )
}

function ToolSchemaPreview({ schemas = [], disabledTools = {} }) {
  if (!schemas.length && !Object.keys(disabledTools || {}).length) return null
  const enabledNames = schemas.map(s => s.function?.name || s.name).filter(Boolean)
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-sm font-medium text-slate-300">实际 tools schema</h2>
        <div className="text-xs text-slate-500">启用 {enabledNames.length} 个 · request_json.tools 同步使用这里的结构</div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
        {schemas.map((schema, i) => {
          const fn = schema.function || {}
          const required = fn.parameters?.required || []
          const propNames = Object.keys(fn.parameters?.properties || {})
          return (
            <details key={`${fn.name || i}`} className="border border-slate-700/50 rounded-lg">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs flex items-center gap-2 min-w-0">
                <span className="font-mono text-emerald-300 truncate">{fn.name || schema.name}</span>
                {schema.source && <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-slate-500">{schema.source}</span>}
                {schema.risk_level && <span className="text-slate-600">{schema.risk_level}</span>}
                <span className="ml-auto text-slate-600">{propNames.length} params</span>
              </summary>
              <div className="p-3 border-t border-slate-700/50 space-y-2">
                <p className="text-xs leading-relaxed text-slate-400">{fn.description || '-'}</p>
                <div className="flex flex-wrap gap-1">
                  {propNames.map(name => (
                    <span key={name} className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${required.includes(name) ? 'bg-amber-500/15 text-amber-300' : 'bg-slate-800 text-slate-500'}`}>{name}</span>
                  ))}
                </div>
                <JsonBlock value={schema} className="max-h-72" />
              </div>
            </details>
          )
        })}
      </div>
      {Object.keys(disabledTools || {}).length > 0 && (
        <RawJsonAccordion label="禁用工具原因" text={JSON.stringify(disabledTools, null, 2)} />
      )}
    </Card>
  )
}

// ── Helpers ──
function safeJsonParse(value, fallback = null) {
  if (!value) return fallback
  if (typeof value === 'object') return value
  try { return JSON.parse(value) } catch { return fallback }
}

function formatBytes(n) {
  if (!n || n < 1024) return `${n || 0}B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / 1048576).toFixed(1)}MB`
}

function summarizeDataUrl(url = '') {
  if (!url || !url.startsWith('data:')) return null
  const match = url.match(/^data:([^;,]+)?(;base64)?,/)
  const mime = match?.[1] || 'unknown'
  const isBase64 = Boolean(match?.[2])
  const payload = url.slice(url.indexOf(',') + 1)
  const sizeBytes = isBase64 ? Math.floor(payload.length * 0.75) : payload.length
  return { mime, isBase64, sizeBytes, sizeText: formatBytes(sizeBytes) }
}

// ── 通用复制按钮 ──
function CopyButton({ text, label = '复制', className = '' }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={e => { e.stopPropagation(); navigator.clipboard.writeText(text || '').then(() => { setOk(true); setTimeout(() => setOk(false), 1000) }) }}
      className={`px-2 py-0.5 rounded text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 ${ok ? 'text-emerald-400' : ''} ${className}`}>
      {ok ? '已复制' : label}
    </button>
  )
}

// ── Message 折叠框 ──
function MessageAccordion({ message, index, source }) {
  const content = message.content
  const isArray = Array.isArray(content)
  const charCount = isArray ? JSON.stringify(content).length : (typeof content === 'string' ? content.length : 0)
  const tokenEst = Math.round(charCount * (isArray ? 0.4 : 0.35))
  const hasToolCalls = message.tool_calls?.length > 0
  const sourceName = source?.source || ''

  return (
    <details className="border border-slate-700/50 rounded-lg group">
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs flex items-center gap-2">
        <span className="text-slate-400 font-mono w-6">[{index}]</span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${message.role === 'system' ? 'bg-purple-500/15 text-purple-300' : message.role === 'user' ? 'bg-blue-500/15 text-blue-300' : message.role === 'assistant' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-500/15 text-slate-400'}`}>{message.role}</span>
        {sourceName && <span className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] font-mono text-slate-400">{sourceName}</span>}
        <span className="text-slate-500">· {charCount} chars · ~{tokenEst} tokens</span>
        {hasToolCalls && <span className="text-amber-400 text-[10px]">· tool_calls: {message.tool_calls.map(t => t.function?.name || '?').join(', ')}</span>}
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <ContentBlockViewer content={content} />
        {hasToolCalls && (
          <div className="mt-3 space-y-2">
            {message.tool_calls.map((tc, j) => {
              let argsText
              try { argsText = JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2) } catch { argsText = tc.function?.arguments || '{}' }
              return (
                <details key={j} className="border border-amber-500/20 rounded">
                  <summary className="py-1.5 px-3 cursor-pointer hover:bg-amber-500/10 text-xs text-amber-400">tool_call[{j}] {tc.function?.name || '?'} · id: {(tc.id || '').slice(0, 24)}</summary>
                  <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto m-2">{argsText}</pre>
                </details>
              )
            })}
          </div>
        )}
      </div>
    </details>
  )
}

// ── ContentBlock 查看器（处理多模态 content array） ──
function ContentBlockViewer({ content }) {
  if (typeof content === 'string') {
    return (
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">text</span>
          <CopyButton text={content} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{content}</pre>
      </div>
    )
  }
  if (!Array.isArray(content)) return <pre className="text-xs text-slate-400">{JSON.stringify(content, null, 2)}</pre>

  return (
    <div className="space-y-2">
      {content.map((block, i) => {
        if (block.type === 'text') {
          return (
            <div key={i}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-slate-600">text block[{i}] · {String(block.text || '').length} chars</span>
                <CopyButton text={block.text || ''} />
              </div>
              <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{block.text || ''}</pre>
            </div>
          )
        }
        if (block.type === 'image_url') {
          const url = block.image_url?.url || ''
          const info = summarizeDataUrl(url)
          return (
            <details key={i} className="border border-slate-700/50 rounded">
              <summary className="py-1.5 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">
                image_url block[{i}] · {info ? `${info.mime} · ${info.sizeText}` : 'external URL'}
                {info?.isBase64 && <span className="text-amber-400 ml-1">(base64)</span>}
              </summary>
              <div className="p-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-600">完整 image_url</span>
                  <CopyButton text={url} label="复制 URL" />
                </div>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-96 overflow-auto">{url}</pre>
              </div>
            </details>
          )
        }
        return <pre key={i} className="text-xs text-slate-400">{JSON.stringify(block, null, 2)}</pre>
      })}
    </div>
  )
}

// ── Tool Schema 折叠框 ──
function ToolAccordion({ tool, index }) {
  const schema = tool.function || tool
  return (
    <details className="border border-slate-700/50 rounded-lg">
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-300">
        [{index}] {schema.name || tool.type || 'tool'} · {tool.type || 'function'}
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">function schema</span>
          <CopyButton text={JSON.stringify(schema, null, 2)} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{JSON.stringify(schema, null, 2)}</pre>
      </div>
    </details>
  )
}

// ── Raw JSON 折叠框 ──
function RawJsonAccordion({ label, text, defaultOpen = false }) {
  if (!text || text === '{}' || text === '[]') return null
  return (
    <details className="border border-slate-700/50 rounded-lg" open={defaultOpen}>
      <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-500 flex items-center gap-2">
        {label} <span className="text-slate-600">({text.length} chars)</span>
      </summary>
      <div className="p-3 border-t border-slate-700/50">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-slate-600">{label}</span>
          <CopyButton text={text} />
        </div>
        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{text}</pre>
      </div>
    </details>
  )
}

// ── 结构化 LLM API 日志查看器 ──
function LLMApiLogViewer({ log }) {
  if (!log) return <div className="py-8 text-center text-sm text-slate-600">无数据</div>
  const request = safeJsonParse(log.request_json, {})
  const response = safeJsonParse(log.response_json, {})
  const requestLint = safeJsonParse(log.request_lint_json, {})
  const lintIssues = Array.isArray(requestLint.issues) ? requestLint.issues : []
  const lintCounts = requestLint.severity_counts || {}
  const messageSources = safeJsonParse(log.message_sources_json, [])
  const actualSentTools = safeJsonParse(log.actual_sent_tools_json, requestLint.actual_sent_tools || [])
  const runtimeEnabledTools = safeJsonParse(log.runtime_enabled_tools_json, requestLint.runtime_enabled_tools || [])
  const runtimeDisabledTools = safeJsonParse(log.runtime_disabled_tools_json, requestLint.runtime_disabled_tools || [])
  const frameworkInjectedTools = safeJsonParse(log.framework_injected_tools_json, requestLint.framework_injected_tools || [])
  const messageSourceByIndex = new Map(messageSources.map(src => [src.index, src]))
  const isIncomplete = (log.status === 'created') && (log.latency_ms === 0 || !log.latency_ms)
  const statusTone = log.status === 'success' ? 'emerald' : log.status === 'stream_success' ? 'blue' : log.status === 'error' || log.status === 'failed' || log.status === 'stream_error' ? 'red' : log.status === 'stream_created' ? 'blue' : 'slate'
  const issueTone = (severity) => severity === 'P0' ? 'red' : severity === 'P1' ? 'amber' : 'slate'

  return (
    <div className="space-y-4 text-sm">
      {/* 未完成警告 */}
      {isIncomplete && (
        <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
          该请求只有创建记录，没有响应回写。可能是出口未调用 finish_request，或进程中断。
        </div>
      )}

      {/* 基础信息 */}
      <section>
        <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">基础信息</h3>
        <InfoGrid
          columns="md:grid-cols-4 xl:grid-cols-6"
          items={[
            { label: 'id', value: log.id || '-' },
            { label: 'source', value: log.source || '-', className: 'text-emerald-300' },
            { label: 'provider', value: log.provider || '-' },
            { label: 'model', value: log.model || '-' },
            { label: 'status', value: log.status || '-', className: statusTone === 'emerald' ? 'text-emerald-300' : statusTone === 'blue' ? 'text-blue-300' : statusTone === 'red' ? 'text-red-300' : 'text-slate-300' },
            { label: 'response_status', value: log.response_status || 0 },
            { label: 'latency', value: log.latency_ms ? `${log.latency_ms}ms` : '-' },
            { label: 'run_id', value: log.run_id ? log.run_id.slice(0, 16) : '未绑定 run', className: log.run_id ? 'text-slate-300' : 'text-amber-300' },
            { label: 'trace_id', value: log.trace_id ? log.trace_id.slice(0, 16) : '-' },
            { label: 'created_at', value: (log.created_at || '').replace('T', ' ').slice(0, 19) },
            { label: 'finished_at', value: log.finished_at ? log.finished_at.replace('T', ' ').slice(0, 19) : '-' },
            { label: 'URL', value: (log.url || '-').slice(0, 40) },
          ]}
        />
        {!log.run_id && <div className="mt-1 text-[10px] text-slate-600">可能是 classifier / background / direct HTTP 调用</div>}
        {log.error && <div className="mt-2 p-2 bg-red-500/10 border border-red-500/20 rounded text-xs text-red-300">{log.error}</div>}
      </section>

      {/* 请求参数 */}
      {Object.keys(request).length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">请求参数</h3>
          <div className="flex flex-wrap gap-2">
            {request.model && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">model: <span className="text-slate-400">{request.model}</span></span>}
            {request.temperature !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">temperature: <span className="text-slate-400">{request.temperature}</span></span>}
            {request.top_p !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">top_p: <span className="text-slate-400">{request.top_p}</span></span>}
            {request.max_tokens !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">max_tokens: <span className="text-slate-400">{request.max_tokens}</span></span>}
            {request.stream !== undefined && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">stream: <span className="text-slate-400">{String(request.stream)}</span></span>}
            <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">messages: <span className="text-slate-400">{request.messages?.length || 0}</span></span>
            <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">tools: <span className="text-slate-400">{request.tools?.length || 0}</span></span>
            {request.tool_choice && <span className="px-2 py-1 rounded-lg bg-slate-800 text-xs text-slate-300">tool_choice: <span className="text-slate-400">{typeof request.tool_choice === 'string' ? request.tool_choice : JSON.stringify(request.tool_choice)}</span></span>}
          </div>
        </section>
      )}

      {/* Request Lint */}
      {(lintIssues.length > 0 || actualSentTools.length > 0 || messageSources.length > 0) && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Request Lint</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-2">
            <MiniStat label="P0" value={lintCounts.P0 || 0} tone={(lintCounts.P0 || 0) > 0 ? 'red' : 'emerald'} />
            <MiniStat label="P1" value={lintCounts.P1 || 0} tone={(lintCounts.P1 || 0) > 0 ? 'amber' : 'slate'} />
            <MiniStat label="P2" value={lintCounts.P2 || 0} />
            <MiniStat label="actual_tools" value={actualSentTools.length} />
            <MiniStat label="message_sources" value={messageSources.length} />
          </div>
          {lintIssues.length > 0 && (
            <div className="space-y-1 mb-2">
              {lintIssues.slice(0, 20).map((issue, i) => (
                <div key={i} className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950 p-2 text-xs">
                  <Badge tone={issueTone(issue.severity)}>{issue.severity || '-'}</Badge>
                  <div className="min-w-0">
                    <div className="text-slate-300 font-mono">{issue.code || '-'}</div>
                    <div className="text-slate-500 break-words">{issue.message || ''}</div>
                    {issue.details && <pre className="text-[10px] text-slate-600 whitespace-pre-wrap break-all mt-1">{JSON.stringify(issue.details, null, 2)}</pre>}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Actual Sent Tools</div>
              <div className="flex flex-wrap gap-1">{actualSentTools.length ? actualSentTools.map(name => <Badge key={name} tone="blue">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Runtime Enabled</div>
              <div className="flex flex-wrap gap-1">{runtimeEnabledTools.length ? runtimeEnabledTools.map(name => <Badge key={name} tone="emerald">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Runtime Disabled</div>
              <div className="flex flex-wrap gap-1">{runtimeDisabledTools.length ? runtimeDisabledTools.map(name => <Badge key={name} tone="amber">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-2">
              <div className="text-[10px] text-slate-600 mb-1">Framework Docs</div>
              <div className="flex flex-wrap gap-1">{frameworkInjectedTools.length ? frameworkInjectedTools.map(name => <Badge key={name} tone="red">{name}</Badge>) : <span className="text-xs text-slate-600">无</span>}</div>
            </div>
          </div>
          {messageSources.length > 0 && (
            <details className="border border-slate-700/50 rounded-lg mt-2">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">Message Sources ({messageSources.length})</summary>
              <div className="p-2 space-y-1 max-h-[360px] overflow-auto">
                {messageSources.map(src => (
                  <div key={src.index} className="grid grid-cols-[40px_70px_180px_1fr] gap-2 rounded bg-slate-950 px-2 py-1 text-xs">
                    <span className="text-slate-600">#{src.index}</span>
                    <span className="text-slate-500">{src.role || '-'}</span>
                    <span className="text-slate-300 font-mono truncate">{src.source || '-'}</span>
                    <span className="text-slate-600 truncate">{src.chars || 0} chars · {(src.sha256 || '').slice(0, 12)} · {src.preview || ''}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </section>
      )}

      {/* Messages */}
      {request.messages?.length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Messages ({request.messages.length})</h3>
          <div className="space-y-1">
            {request.messages.map((msg, i) => (
              <MessageAccordion key={i} message={msg} index={i} source={messageSourceByIndex.get(i)} />
            ))}
          </div>
        </section>
      )}

      {/* Tools */}
      {request.tools?.length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Tools ({request.tools.length})</h3>
          <div className="space-y-1">
            {request.tools.map((tool, i) => (
              <ToolAccordion key={i} tool={tool} index={i} />
            ))}
          </div>
        </section>
      )}

      {/* Response */}
      {Object.keys(response).length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Response</h3>
          <div className="space-y-2">
            {response.choices?.map((choice, i) => {
              const msg = choice.message || {}
              return (
                <div key={i} className="border border-slate-700/50 rounded-lg p-3">
                  <div className="flex gap-3 mb-2 text-xs">
                    <span className="text-slate-500">finish_reason: <span className="text-slate-300">{choice.finish_reason || '-'}</span></span>
                  </div>
                  {msg.content && (
                    <div className="mb-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-slate-600">message.content · {String(msg.content).length} chars</span>
                        <CopyButton text={msg.content} />
                      </div>
                      <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{msg.content}</pre>
                    </div>
                  )}
                  {msg.tool_calls?.map((tc, j) => {
                    let argsText
                    try { argsText = JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2) } catch { argsText = tc.function?.arguments || '{}' }
                    return (
                      <details key={j} className="border border-amber-500/20 rounded mb-1">
                        <summary className="py-1.5 px-3 cursor-pointer hover:bg-amber-500/10 text-xs text-amber-400">tool_call[{j}] {tc.function?.name || '?'}</summary>
                        <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto m-2">{argsText}</pre>
                      </details>
                    )
                  })}
                </div>
              )
            })}
            {response.content && (
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-slate-600">content · {String(response.content).length} chars</span>
                  <CopyButton text={response.content} />
                </div>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 max-h-[600px] overflow-auto">{response.content}</pre>
              </div>
            )}
            {response.usage && (
              <details className="border border-slate-700/50 rounded-lg">
                <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-xs text-slate-400">usage</summary>
                <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 rounded text-slate-300 m-2">{JSON.stringify(response.usage, null, 2)}</pre>
              </details>
            )}
          </div>
        </section>
      )}

      {/* Raw JSON */}
      <section>
        <h3 className="text-xs font-medium text-slate-500 mb-2 uppercase tracking-wider">Raw JSON</h3>
        <div className="space-y-1">
          <RawJsonAccordion label="原始 request_json" text={log.request_json} />
          <RawJsonAccordion label="原始 response_json" text={log.response_json} />
          <RawJsonAccordion label="headers_json" text={log.headers_json} />
          <RawJsonAccordion label="request_lint_json" text={log.request_lint_json} />
          <RawJsonAccordion label="message_sources_json" text={log.message_sources_json} />
        </div>
      </section>
    </div>
  )
}

// ── LLM API 请求日志块（可复用） ──
function LLMApiRequestLogsBlock({ logs = [] }) {
  if (!logs.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-slate-500 text-sm mb-2">暂无 API 请求日志</p>
        <p className="text-slate-600 text-xs">可能原因：本次调用未绑定 run_id 或该模型出口未接入追踪</p>
      </Card>
    )
  }
  return (
    <Card>
      <div className="space-y-1">
        {logs.map(ll => {
          const isIncomplete = (ll.status === 'created') && (ll.latency_ms === 0 || !ll.latency_ms)
          const statusTone = ll.status === 'success' ? 'emerald' : ll.status === 'stream_success' ? 'blue' : ll.status === 'error' || ll.status === 'failed' || ll.status === 'stream_error' ? 'red' : ll.status === 'stream_created' ? 'blue' : 'slate'
          const requestLint = safeJsonParse(ll.request_lint_json, {})
          const p0Count = requestLint.severity_counts?.P0 || 0
          return (
            <details key={ll.id} className="border-b border-slate-800/50">
              <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-sm flex gap-3 items-center">
                <Badge tone={statusTone}>{ll.status || '-'}</Badge>
                {p0Count > 0 && <Badge tone="red">P0 {p0Count}</Badge>}
                <span className="text-slate-200 w-16">{ll.source || '-'}</span>
                <span className="text-slate-400 w-32 truncate">{ll.model || '-'}</span>
                {isIncomplete ? (
                  <span className="text-amber-500 text-xs">未完成或未回写响应</span>
                ) : (
                  <>
                    <span className="text-slate-400 w-16">{ll.response_status || 0}</span>
                    <span className="text-slate-500 w-20">{ll.latency_ms || 0}ms</span>
                  </>
                )}
                <span className="text-slate-500 text-xs truncate flex-1">{ll.run_id ? ll.run_id.slice(0, 16) : <span className="text-amber-500">未绑定 run</span>}</span>
                <span className="text-xs text-slate-500">{ll.created_at || '-'}</span>
              </summary>
              <div className="p-4 border-t border-slate-800/50">
                <LLMApiLogViewer log={ll} />
              </div>
            </details>
          )
        })}
      </div>
    </Card>
  )
}

// ── Agent Runs ──
function AgentRunsPage() {
  const [runs, setRuns] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState(null)
  const [toolDetail, setToolDetail] = useState(null)
  const [status, setStatus] = useState('')
  const [sessionFilter, setSessionFilter] = useState('')
  const limit = 30

  const loadRuns = useCallback(() => {
    api.get('/agent-runs', { params: { page, limit, status, session_id: sessionFilter } }).then(r => {
      setRuns(r.data.items || [])
      setTotal(r.data.total || 0)
      if (!selected && r.data.items?.length) setSelected(r.data.items[0].run_id)
    }).catch(() => setRuns([]))
  }, [page, status, sessionFilter, selected])
  useEffect(() => { loadRuns() }, [loadRuns])
  useEffect(() => {
    if (!selected) return
    api.get(`/agent-runs/${encodeURIComponent(selected)}`).then(r => { setDetail(r.data); setToolDetail(null) }).catch(() => setDetail(null))
  }, [selected])

  const openTool = (id) => {
    api.get(`/tool-calls/${encodeURIComponent(id)}`).then(r => setToolDetail(r.data)).catch(() => setToolDetail(null))
  }
  const tone = (s) => s === 'success' ? 'emerald' : s === 'error' ? 'red' : s === 'running' ? 'blue' : s === 'no_reply' ? 'slate' : 'amber'

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">运行追踪</h1>
          <p className="text-slate-500 text-sm">Agent run、Prompt render 与 Tool call 的只读审计视图</p>
        </div>
        <button onClick={loadRuns} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">刷新</button>
      </div>
      <div className="flex gap-2 mb-4">
        <select value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}
          className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm">
          <option value="">全部状态</option>
          <option value="success">success</option>
          <option value="error">error</option>
          <option value="no_reply">no_reply</option>
          <option value="suppressed">suppressed</option>
          <option value="empty">empty</option>
        </select>
        <input value={sessionFilter} onChange={e => { setSessionFilter(e.target.value); setPage(1) }}
          placeholder="session_id" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500" />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <Card className="xl:col-span-5 overflow-hidden">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3">开始</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">会话</th><th className="py-2 px-3">模型</th></tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.run_id} onClick={() => setSelected(r.run_id)}
                  className={`border-b border-slate-800/50 cursor-pointer ${selected === r.run_id ? 'bg-emerald-500/10' : 'hover:bg-slate-800/40'}`}>
                  <td className="py-2 px-3 text-xs text-slate-400">{String(r.started_at || '').replace('T', ' ').slice(0, 19)}</td>
                  <td className="py-2 px-3"><Badge tone={tone(r.status)}>{r.status}</Badge></td>
                  <td className="py-2 px-3 text-xs text-slate-400 max-w-32 truncate">{r.session_id || '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-40 truncate">{r.model || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!runs.length && <div className="py-16 text-center text-sm text-slate-600">暂无运行记录</div>}
          <div className="p-3"><Pagination page={page} total={total} limit={limit} onChange={setPage} /></div>
        </Card>
        <div className="xl:col-span-7 space-y-4 min-w-0">
          {detail ? (<>
              <Card className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h2 className="text-sm font-medium text-emerald-400 font-mono">{(detail.run || detail).run_id}</h2>
                    <div className="text-xs text-slate-600 font-mono">{(detail.run || detail).trace_id}</div>
                  </div>
                  <Badge tone={tone((detail.run || detail).status)}>{(detail.run || detail).status}</Badge>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-3">
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt</div><div className="text-white font-medium truncate">{(detail.run || detail).prompt_key || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Mode</div><div className="text-blue-300 font-medium truncate">{(detail.run || detail).prompt_mode || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt Source</div><div className="text-emerald-300 font-medium truncate">{(detail.run || detail).prompt_source || '-'}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Prompt SHA</div><div className="text-slate-300 font-mono text-xs truncate">{((detail.run || detail).prompt_sha256 || '-').slice(0, 16)}</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Latency</div><div className="text-white font-medium">{(detail.run || detail).latency_ms || 0}ms</div></div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-3"><div className="text-xs text-slate-500 mb-1">Tools</div><div className="text-emerald-300 font-medium">{(detail.tool_calls || []).length}</div></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
                  <div><div className="text-xs text-slate-500 mb-1">运行时路径</div><JsonBlock value={(detail.run || detail).prompt_runtime_path || '-'} className="max-h-20" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">默认路径</div><JsonBlock value={(detail.run || detail).prompt_default_path || '-'} className="max-h-20" /></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div><div className="text-xs text-slate-500 mb-1">输入预览</div><JsonBlock value={(detail.run || detail).input_preview} className="max-h-40" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">输出预览</div><JsonBlock value={(detail.run || detail).output_preview || (detail.run || detail).error} className="max-h-40" /></div>
                </div>
              </Card>
              <Card className="p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">工具调用</h2>
                <div className="space-y-2">
                  {(detail.tool_calls || []).map(t => (
                    <button key={t.tool_call_id} onClick={() => openTool(t.tool_call_id)}
                      className="w-full text-left p-3 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-200">{t.tool_name}</span>
                        <Badge tone={tone(t.status)}>{t.status}</Badge>
                      </div>
                      <div className="text-xs text-slate-600 mt-1">{t.latency_ms || 0}ms · {t.tool_call_id}</div>
                    </button>
                  ))}
                  {!(detail.tool_calls || []).length && <div className="py-8 text-center text-sm text-slate-600">本次没有工具调用</div>}
                </div>
              </Card>
              <Card className="p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">Prompt 渲染记录</h2>
                {(detail.prompt_render_logs || []).map(log => (
                  <div key={log.id} className="mb-3">
                    <div className="flex gap-2 mb-2 flex-wrap"><Badge tone="blue">{log.prompt_key}</Badge><Badge>{log.mode}</Badge><Badge>{log.prompt_source || '-'}</Badge><Badge>{log.token_estimate} tokens</Badge><Badge>{(log.prompt_sha256 || '').slice(0, 12) || '-'}</Badge></div>
                    <JsonBlock value={log.rendered_preview || log.error} className="max-h-44" />
                  </div>
                ))}
                {!(detail.prompt_render_logs || []).length && <div className="py-8 text-center text-sm text-slate-600">暂无 prompt 渲染记录</div>}
              </Card>
              <h2 className="text-sm font-medium text-slate-300 mb-3 mt-4">API 请求</h2>
              <LLMApiRequestLogsBlock logs={detail.llm_api_request_logs || []} />
              {toolDetail && (
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-medium text-slate-300">工具详情: {toolDetail.tool_name}</h2>
                    <button onClick={() => setToolDetail(null)} className="text-xs text-slate-500 hover:text-white">关闭</button>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div><div className="text-xs text-slate-500 mb-1">参数</div><JsonBlock value={toolDetail.args_json} className="max-h-72" /></div>
                    <div><div className="text-xs text-slate-500 mb-1">结果/错误</div><JsonBlock value={toolDetail.result_preview || toolDetail.error} className="max-h-72" /></div>
                  </div>
                </Card>
              )}
            </>
          ) : (
            <Card className="p-12 text-center text-slate-600 text-sm">选择一条运行记录查看详情</Card>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Agent Run 详情（深链 /agent-runs/:runId） ──
function AgentRunDetailPage() {
  const { runId } = useParams()
  const [detail, setDetail] = useState(null)
  const [toolDetail, setToolDetail] = useState(null)
  useEffect(() => {
    api.get(`/agent-runs/${encodeURIComponent(runId)}`).then(r => setDetail(r.data)).catch(() => setDetail(null))
  }, [runId])
  if (!detail || !detail.run) return <Card className="p-12 text-center text-slate-500"><Spinner /></Card>
  const r = detail.run
  return (
    <div>
      <NavLink to="/agent-runs" className="text-xs text-slate-500 hover:text-slate-300 mb-4 inline-block">← 运行追踪</NavLink>
      <h1 className="text-xl font-semibold mb-4">运行详情</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <MiniStat label="run_id" value={r.run_id || ''} />
        <MiniStat label="status" value={r.status || ''} tone={r.status === 'success' ? 'emerald' : r.status === 'error' ? 'red' : 'slate'} />
        <MiniStat label="延迟" value={r.latency_ms ? `${r.latency_ms}ms` : '-'} />
        <MiniStat label="trace_id" value={(r.trace_id || '').slice(0, 16)} />
        <MiniStat label="prompt_key" value={r.prompt_key || '-'} />
        <MiniStat label="prompt_mode" value={r.prompt_mode || '-'} />
        <MiniStat label="prompt_source" value={r.prompt_source || '-'} />
        <MiniStat label="prompt_sha" value={(r.prompt_sha256 || '').slice(0, 16) || '-'} />
        <MiniStat label="model" value={r.model || '-'} />
        <MiniStat label="session_id" value={(r.session_id || '').slice(0, 16) || '-'} />
        <MiniStat label="user_id" value={r.user_id || '-'} />
      </div>
      {(r.prompt_runtime_path || r.prompt_default_path) && (
        <Card className="p-4 mb-4">
          <h3 className="text-sm font-medium text-slate-400 mb-2">Prompt 来源</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div><div className="text-xs text-slate-500 mb-1">运行时路径</div><JsonBlock value={r.prompt_runtime_path || '-'} className="max-h-24" /></div>
            <div><div className="text-xs text-slate-500 mb-1">默认路径</div><JsonBlock value={r.prompt_default_path || '-'} className="max-h-24" /></div>
          </div>
        </Card>
      )}
      {r.error && <div className="p-3 bg-red-500/10 text-red-400 rounded-lg mb-4 text-sm">{r.error}</div>}
      {r.input_preview && <Card className="p-4 mb-4"><h3 className="text-sm font-medium text-slate-400 mb-2">输入摘要</h3><pre className="text-xs text-slate-300 whitespace-pre-wrap">{r.input_preview}</pre></Card>}
      {r.output_preview && <Card className="p-4 mb-4"><h3 className="text-sm font-medium text-slate-400 mb-2">输出摘要</h3><pre className="text-xs text-slate-300 whitespace-pre-wrap">{r.output_preview}</pre></Card>}

      {/* Tool Calls */}
      <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">工具调用 ({detail.tool_calls?.length || 0})</h2>
      {detail.tool_calls?.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-3">工具</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">延迟</th><th className="py-2 px-3">时间</th>
            </tr></thead>
            <tbody>
              {detail.tool_calls.map(tc => (
                <tr key={tc.tool_call_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer" onClick={() => setToolDetail(toolDetail?.tool_call_id === tc.tool_call_id ? null : tc)}>
                  <td className="py-2 px-3 text-slate-200">{tc.tool_name}</td>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${tc.status === 'success' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>{tc.status}</span></td>
                  <td className="py-2 px-3 text-slate-400">{tc.latency_ms ? `${tc.latency_ms}ms` : '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500">{tc.started_at || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {toolDetail && (
            <div className="p-3 border-t border-slate-800">
              <h3 className="text-sm font-medium text-slate-400 mb-2">{toolDetail.tool_name} 详情</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div><div className="text-xs text-slate-500 mb-1">参数</div><JsonBlock value={toolDetail.args_json} className="max-h-72" /></div>
                <div><div className="text-xs text-slate-500 mb-1">结果/错误</div><JsonBlock value={toolDetail.result_preview || toolDetail.error} className="max-h-72" /></div>
              </div>
            </div>
          )}
        </Card>
      )}
      {detail.tool_calls?.length === 0 && <p className="text-xs text-slate-500">无工具调用</p>}

      {/* Prompt Render Logs */}
      {detail.prompt_render_logs?.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">Prompt 渲染日志</h2>
          <Card>
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="py-2 px-3">prompt_key</th><th className="py-2 px-3">mode</th><th className="py-2 px-3">source</th><th className="py-2 px-3">sha</th><th className="py-2 px-3">tokens</th><th className="py-2 px-3">警告</th>
              </tr></thead>
              <tbody>
                {detail.prompt_render_logs.map(pr => (
                  <tr key={pr.id} className="border-b border-slate-800/50">
                    <td className="py-2 px-3 text-slate-200">{pr.prompt_key}</td>
                    <td className="py-2 px-3 text-slate-400">{pr.mode || '-'}</td>
                    <td className="py-2 px-3 text-slate-400 max-w-56 truncate">{pr.prompt_source || '-'}</td>
                    <td className="py-2 px-3 text-slate-500 font-mono text-xs">{(pr.prompt_sha256 || '').slice(0, 12) || '-'}</td>
                    <td className="py-2 px-3 text-slate-400">{pr.token_estimate || '-'}</td>
                    <td className="py-2 px-3 text-xs text-amber-400">{pr.warnings_json || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {/* Reply Contract Checks */}
      {detail.reply_contract_check_logs?.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">Reply 调用检查</h2>
          <Card>
            <div className="divide-y divide-slate-800">
              {detail.reply_contract_check_logs.map(log => (
                <details key={log.id} className="group">
                  <summary className="py-2 px-3 cursor-pointer hover:bg-slate-800/30 text-sm flex gap-3">
                    <span className="text-slate-400 w-16">#{log.attempt}</span>
                    <span className="text-slate-200 w-32">{log.result || '-'}</span>
                    <span className="text-slate-500 w-20">reply:{log.has_reply_tool || 0}</span>
                    <span className="text-slate-500 w-24">no_reply:{log.has_no_reply_tool || 0}</span>
                    <span className="text-slate-500 flex-1 truncate">{log.created_at || '-'}</span>
                  </summary>
                  <pre className="text-xs whitespace-pre-wrap break-all bg-slate-950 p-3 m-2 rounded text-slate-300 max-h-72 overflow-auto">{log.raw_output_preview || ''}</pre>
                </details>
              ))}
            </div>
          </Card>
        </>
      )}

      {/* LLM API Requests */}
      <h2 className="text-sm font-medium text-slate-300 mt-6 mb-3">API 请求</h2>
      <LLMApiRequestLogsBlock logs={detail.llm_api_request_logs || []} />
    </div>
  )
}

// ── Tool Calls 独立页面 ──
function ToolCallsPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [runFilter, setRunFilter] = useState('')
  const [toolFilter, setToolFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const limit = 30
  const load = useCallback(() => {
    const params = { page, limit }
    if (runFilter) params.run_id = runFilter
    if (toolFilter) params.tool_name = toolFilter
    if (statusFilter) params.status = statusFilter
    api.get('/tool-calls', { params }).then(r => { setItems(r.data.items || []); setTotal(r.data.total || 0) }).catch(() => {})
  }, [page, runFilter, toolFilter, statusFilter])
  useEffect(() => { load() }, [load])
  const totalPages = Math.max(1, Math.ceil(total / limit))
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">工具调用</h1>
      <div className="flex gap-2 mb-4">
        <input value={runFilter} onChange={e => { setRunFilter(e.target.value); setPage(1) }} placeholder="run_id..." className="w-40 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <input value={toolFilter} onChange={e => { setToolFilter(e.target.value); setPage(1) }} placeholder="tool_name..." className="w-32 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }} className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
          <option value="">全部状态</option>
          <option value="success">success</option>
          <option value="error">error</option>
        </select>
        <button onClick={() => { setRunFilter(''); setToolFilter(''); setStatusFilter(''); setPage(1) }} className="px-2 py-1 bg-slate-700 rounded text-xs">清除</button>
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3">工具</th><th className="py-2 px-3">run_id</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">延迟</th><th className="py-2 px-3">时间</th>
          </tr></thead>
          <tbody>
            {items.map(tc => (
              <tr key={tc.tool_call_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 px-3 font-mono text-slate-200">{tc.tool_name}</td>
                <td className="py-2 px-3 text-slate-400 text-xs">{(tc.run_id || '').slice(0, 12)}</td>
                <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${tc.status === 'success' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>{tc.status}</span></td>
                <td className="py-2 px-3 text-slate-400">{tc.latency_ms ? `${tc.latency_ms}ms` : '-'}</td>
                <td className="py-2 px-3 text-xs text-slate-500">{tc.started_at || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {total > limit && (
          <div className="flex justify-between p-3 text-xs border-t border-slate-800">
            <span className="text-slate-500">共 {total} 条 | 第 {page}/{totalPages} 页</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">上一页</button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">下一页</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── LLM API 日志独立页面 ──
function LLMApiLogsPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState(null)
  const [page, setPage] = useState(1)
  const [runFilter, setRunFilter] = useState('')
  const [traceFilter, setTraceFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [openId, setOpenId] = useState(null)
  const [detailsById, setDetailsById] = useState({})
  const [loadingDetailId, setLoadingDetailId] = useState(null)
  const [detailErrors, setDetailErrors] = useState({})
  const limit = 30
  const load = useCallback(() => {
    const params = { page, limit }
    if (runFilter) params.run_id = runFilter
    if (traceFilter) params.trace_id = traceFilter
    if (sourceFilter) params.source = sourceFilter
    if (modelFilter) params.model = modelFilter
    if (statusFilter) params.status = statusFilter
    api.get('/llm-api-logs', { params }).then(r => { setItems(r.data.items || []); setTotal(r.data.total || 0); setStats(r.data.stats || null) }).catch(() => {})
  }, [page, runFilter, traceFilter, sourceFilter, modelFilter, statusFilter])
  useEffect(() => { load() }, [load])
  const openLog = (logId) => {
    if (openId === logId) {
      setOpenId(null)
      return
    }
    setOpenId(logId)
    if (detailsById[logId]) return
    setLoadingDetailId(logId)
    setDetailErrors(prev => ({ ...prev, [logId]: '' }))
    api.get(`/llm-api-logs/${logId}`).then(r => {
      setDetailsById(prev => ({ ...prev, [logId]: r.data }))
    }).catch(e => {
      setDetailErrors(prev => ({ ...prev, [logId]: e?.response?.data?.detail || e.message || '加载失败' }))
    }).finally(() => {
      setLoadingDetailId(current => current === logId ? null : current)
    })
  }
  const totalPages = Math.max(1, Math.ceil(total / limit))
  const pageStats = stats || items.reduce((acc, item) => {
    const status = item.status || 'created'
    acc.total += 1
    acc[status] = (acc[status] || 0) + 1
    if (status === 'success' || status === 'stream_success') acc.success += 1
    if (status === 'failed' || status === 'error' || status === 'stream_error') acc.failed += 1
    if (status === 'created' || status === 'stream_created') acc.created += 1
    if (!item.run_id) acc.unbound += 1
    const latency = Number(item.latency_ms || 0)
    if (latency > 0) {
      acc.latencyTotal += latency
      acc.latencyCount += 1
    }
    return acc
  }, { total: 0, success: 0, failed: 0, created: 0, unbound: 0, latencyTotal: 0, latencyCount: 0 })
  const avgLatency = stats ? (stats.avg_latency_ms || 0) : (pageStats.latencyCount ? Math.round(pageStats.latencyTotal / pageStats.latencyCount) : 0)
  return (
    <div>
      <h1 className="text-xl font-semibold mb-1">LLM API 日志</h1>
      <p className="text-slate-500 text-xs mb-4">发往模型网关的完整请求记录</p>
      <div className="flex gap-2 mb-4 flex-wrap">
        <input value={runFilter} onChange={e => { setRunFilter(e.target.value); setPage(1) }}
          placeholder="run_id" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 w-40" />
        <input value={traceFilter} onChange={e => { setTraceFilter(e.target.value); setPage(1) }}
          placeholder="trace_id" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 w-40" />
        <input value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); setPage(1) }}
          placeholder="source" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 w-28" />
        <input value={modelFilter} onChange={e => { setModelFilter(e.target.value); setPage(1) }}
          placeholder="model" className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-emerald-500 w-40" />
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm">
          <option value="">全部状态</option>
          <option value="created">created</option>
          <option value="success">success</option>
          <option value="failed">failed</option>
          <option value="error">error</option>
          <option value="stream_created">stream_created</option>
          <option value="stream_success">stream_success</option>
          <option value="stream_error">stream_error</option>
        </select>
        <button onClick={load} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-sm">刷新</button>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
        <MiniStat label={stats ? '筛选总数' : '当前页总数'} value={pageStats.total} />
        <MiniStat label="success" value={pageStats.success} tone="emerald" />
        <MiniStat label="failed/error" value={pageStats.failed_error ?? pageStats.failed} tone={(pageStats.failed_error ?? pageStats.failed) ? 'red' : 'slate'} />
        <MiniStat label="created" value={pageStats.created} tone={pageStats.created ? 'amber' : 'slate'} />
        <MiniStat label="平均延迟" value={avgLatency ? `${avgLatency}ms` : '-'} />
        <MiniStat label="未绑定 run" value={pageStats.unbound_run_count ?? pageStats.unbound} tone={(pageStats.unbound_run_count ?? pageStats.unbound) ? 'amber' : 'slate'} />
      </div>
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3">状态</th><th className="py-2 px-3">source</th><th className="py-2 px-3">model</th><th className="py-2 px-3">run</th><th className="py-2 px-3">耗时</th><th className="py-2 px-3">时间</th><th className="py-2 px-3">摘要</th>
          </tr></thead>
          <tbody>
            {items.map(ll => {
              const isIncomplete = (ll.status === 'created') && (ll.latency_ms === 0 || !ll.latency_ms)
              const statusTone = ll.status === 'success' ? 'emerald' : ll.status === 'stream_success' ? 'blue' : ll.status === 'error' || ll.status === 'failed' || ll.status === 'stream_error' ? 'red' : ll.status === 'stream_created' ? 'blue' : 'slate'
              const detail = detailsById[ll.id]
              const request = detail ? safeJsonParse(detail.request_json, {}) : {}
              const messagesCount = request.messages?.length || 0
              const toolsCount = request.tools?.length || 0
              const previewText = String(ll.request_preview || ll.response_preview || ll.error || '').replace(/\s+/g, ' ').slice(0, 90)
              return (
              <React.Fragment key={ll.id}>
                <tr className="border-b border-slate-800/50 cursor-pointer hover:bg-slate-800/30"
                  onClick={() => openLog(ll.id)}>
                  <td className="py-2 px-3"><span className={`px-1.5 py-0.5 rounded text-xs ${statusTone === 'emerald' ? 'bg-emerald-500/10 text-emerald-300' : statusTone === 'blue' ? 'bg-blue-500/10 text-blue-300' : statusTone === 'red' ? 'bg-red-500/10 text-red-300' : 'bg-slate-500/10 text-slate-400'}`}>{ll.status || '-'}</span></td>
                  <td className="py-2 px-3 text-slate-200">{ll.source || '-'}</td>
                  <td className="py-2 px-3 text-slate-400 max-w-40 truncate">{ll.model || '-'}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-32 truncate font-mono">{ll.run_id ? ll.run_id.slice(0, 16) : <span className="text-amber-500">未绑定</span>}</td>
                  <td className="py-2 px-3 text-slate-400">{isIncomplete ? <span className="text-amber-500 text-xs" title="未完成或未回写响应">-</span> : `${ll.latency_ms || 0}ms`}</td>
                  <td className="py-2 px-3 text-xs text-slate-500">{String(ll.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                  <td className="py-2 px-3 text-xs text-slate-500 max-w-[340px] truncate">
                    {messagesCount > 0 && `${messagesCount} msgs`}{messagesCount > 0 && toolsCount > 0 && ' · '}{toolsCount > 0 && `${toolsCount} tools`}
                    {!messagesCount && previewText}
                    {ll.summary_only && !detail && <span className="text-slate-600 ml-1">· 点开加载详情</span>}
                    {ll.error && <span className="text-red-400 ml-1">· error</span>}
                  </td>
                </tr>
                {openId === ll.id && (
                <tr className="border-b border-slate-800/50 bg-slate-900/50">
                  <td colSpan={7} className="p-4">
                    {loadingDetailId === ll.id && (
                      <div className="py-8 text-center text-sm text-slate-500">
                        正在加载完整 request_json / response_json...
                      </div>
                    )}
                    {detailErrors[ll.id] && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-300">
                        {detailErrors[ll.id]}
                      </div>
                    )}
                    {detail && <LLMApiLogViewer log={detail} />}
                  </td>
                </tr>
                )}
              </React.Fragment>
            )})}
          </tbody>
        </table>
        {!items.length && <div className="py-16 text-center text-sm text-slate-600">暂无 API 请求日志</div>}
        {total > limit && (
          <div className="p-3 flex items-center justify-between text-xs">
            <span className="text-slate-500">共 {total} 条 | 第 {page}/{totalPages} 页</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">上一页</button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">下一页</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Models ──
function ModelsPage() {
  const tabs = [
    { key: 'catalog', label: '模型列表' },
    { key: 'routes', label: '路由配置' },
    { key: 'providers', label: '供应商' },
    { key: 'local', label: '本地组件' },
  ]
  const [tab, setTab] = useState('catalog')
  const [status, setStatus] = useState(null)
  const [testResult, setTestResult] = useState({})
  const [localResult, setLocalResult] = useState({})
  const load = () => api.get('/models/status').then(r => setStatus(r.data))
  useEffect(() => { load() }, [])

  const handleTest = async (key, mode = 'ping') => {
    setTestResult(p => ({ ...p, [key]: { loading: true, mode } }))
    try {
      const config = mode === 'vision' ? { params: { mode } } : undefined
      const r = await api.post(`/models/routes/${key}/test`, null, config)
      setTestResult(p => ({ ...p, [key]: r.data }))
    }
    catch (e) { setTestResult(p => ({ ...p, [key]: { ok: false, error: e.message } })) }
  }
  const handleLocal = async (comp, action) => {
    setLocalResult(p => ({ ...p, [comp]: { loading: true } }))
    try { const r = await api.post(`/models/local/${comp}/${action}`); setLocalResult(p => ({ ...p, [comp]: r.data })) }
    catch (e) { setLocalResult(p => ({ ...p, [comp]: { ok: false, error: e.message } })) }
  }

  if (!status) return <Spinner />

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">模型路由</h1>
      <div className="flex gap-2 mb-6 border-b border-slate-800 pb-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'catalog' && <ModelCatalogTab providers={status.providers || []} />}
      {tab === 'routes' && <RoutesTab routes={status.routes || {}} providers={status.providers || []} testResult={testResult} onTest={handleTest} onSaved={load} />}
      {tab === 'providers' && <ProvidersTab providers={status.providers || []} />}
      {tab === 'local' && <LocalComponentsTab components={status.local_components || {}} localResult={localResult} onAction={handleLocal} />}
    </div>
  )
}

// ── Tab 1: 模型列表 ──
function ModelCatalogTab({ providers }) {
  const [catalog, setCatalog] = useState([])
  const [routeRefs, setRouteRefs] = useState([])
  const [catProvider, setCatProvider] = useState('')
  const [catQ, setCatQ] = useState('')
  const [catLoading, setCatLoading] = useState(false)
  const [refreshResult, setRefreshResult] = useState(null)
  const loadCatalog = () => {
    setCatLoading(true)
    const params = { limit: 200 }
    if (catProvider) params.provider = catProvider
    if (catQ) params.q = catQ
    api.get('/models/catalog', { params }).then(r => setCatalog(r.data.catalog || [])).catch(() => {}).finally(() => setCatLoading(false))
  }
  useEffect(() => {
    const id = setTimeout(loadCatalog, 0)
    return () => clearTimeout(id)
  }, [catProvider, catQ])
  useEffect(() => { api.get('/models/route-references').then(r => setRouteRefs(r.data.route_references || [])).catch(() => {}) }, [])
  const doRefresh = () => {
    setRefreshResult({ loading: true })
    api.post('/models/catalog/refresh').then(r => { setRefreshResult(r.data); loadCatalog() }).catch(() => setRefreshResult(null))
  }
  // 用 route_references 标记哪些 catalog 模型被路由使用
  const usedBy = {}
  routeRefs.forEach(ref => {
    if (ref.verified) {
      const k = ref.id
      if (!usedBy[k]) usedBy[k] = []
      usedBy[k].push(ref.route_key)
    }
  })
  const unverified = routeRefs.filter(ref => !ref.verified)
  return (
    <div>
      {/* 供应商模型列表 */}
      <p className="text-slate-500 text-sm mb-2">从供应商 /v1/models 同步的真实模型列表。</p>
      <div className="flex gap-2 mb-3">
        <input value={catQ} onChange={e => setCatQ(e.target.value)} placeholder="搜索模型..." className="w-48 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <select value={catProvider} onChange={e => setCatProvider(e.target.value)} className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
          <option value="">全部供应商</option>
          {(providers || []).map(p => (
            <option key={p.id} value={p.id}>{p.id}{p.legacy_aliases?.length > 0 ? ` (原 ${p.legacy_aliases.join(',')})` : ''}</option>
          ))}
        </select>
        <button onClick={doRefresh} disabled={refreshResult?.loading} className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium">刷新供应商模型</button>
      </div>
      {refreshResult && !refreshResult.loading && (
        <div className="mb-3 text-xs">
          {refreshResult.results?.map(r => (
            <span key={r.provider} className={`mr-3 ${r.ok ? 'text-emerald-400' : 'text-red-400'}`}>{r.provider}: {r.ok ? `${r.models.length} 个` : r.error}</span>
          ))}
        </div>
      )}
      {catalog.length === 0 && !catLoading && (
        <div className="text-xs text-slate-500 mb-3 p-3 bg-slate-900 rounded-lg">
          模型列表为空，请点击「刷新供应商模型」从 provider /v1/models 同步。
        </div>
      )}
      <Card>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-3">模型</th><th className="py-2 px-3">供应商</th><th className="py-2 px-3">路由使用</th>
          </tr></thead>
          <tbody>
            {catLoading && <tr><td colSpan={3} className="py-4 text-center text-slate-500">加载中...</td></tr>}
            {catalog.map(m => (
              <tr key={m.id || m.model} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 px-3 font-mono text-slate-200">
                  {m.model}
                  {m.stale && <span className="ml-1 px-1 py-0.5 rounded text-[10px] bg-red-900/30 text-red-400">stale</span>}
                </td>
                <td className="py-2 px-3 text-slate-400">{m.provider}</td>
                <td className="py-2 px-3 text-slate-400 text-xs">{(usedBy[m.id] || []).join(', ') || <span className="text-slate-600">-</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* 路由引用异常：未在 provider catalog 中确认的模型 */}
      {unverified.length > 0 && (
        <div className="mt-6">
          <h2 className="text-sm font-medium text-amber-400 mb-2">路由引用异常</h2>
          <p className="text-xs text-slate-500 mb-2">以下模型被路由引用，但未在供应商 /v1/models 中找到——可能不存在于该 provider。</p>
          <Card>
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="py-2 px-3">模型</th><th className="py-2 px-3">供应商</th><th className="py-2 px-3">路由</th><th className="py-2 px-3">类型</th>
              </tr></thead>
              <tbody>
                {unverified.map(ref => (
                  <tr key={ref.id} className="border-b border-slate-800/50 bg-amber-500/5">
                    <td className="py-2 px-3 font-mono text-amber-300">{ref.model}</td>
                    <td className="py-2 px-3 text-slate-400">{ref.provider}</td>
                    <td className="py-2 px-3 text-slate-400">{ref.route_key}</td>
                    <td className="py-2 px-3 text-xs text-slate-500">{ref.route_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </div>
  )
}

// ── Tab 2: 路由配置 ──
function RoutesTab({ routes, providers, testResult, onTest, onSaved }) {
  const [editRoute, setEditRoute] = useState(null)
  const [resolvedData, setResolvedData] = useState({})
  const routeList = Object.entries(routes)
  const thinkingLabel = (v) => v === 'true' ? '启用' : (v === 'false' ? '禁用' : '自动')
  return (
    <div>
      <p className="text-slate-500 text-sm mb-2">每个 route 选择一个模型和供应商。base_url/API key 默认在「供应商」Tab 管理；route API key 只作为高级覆盖。</p>
      <p className="text-amber-400/80 text-xs mb-3">reply 是当前主回复运行路径；fast/smart 目前用于展示、测试、目录和后续统一路由，主流程暂不自动切换 fast/smart。主回复 controller 初始化仍来自 config.yaml/env；桥接层会在每次回复前同步 reply route 的 provider/base_url/model，其他 controller 初始化参数变更仍需重启或重建 bridge。</p>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {routeList.map(([key, r]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{r.label} <span className="text-xs text-slate-500 ml-1">{key}</span>{r.route_type && <span className={`ml-1 px-1 py-0.5 rounded text-[10px] ${r.route_type === 'controller' ? 'bg-blue-900/30 text-blue-400' : r.route_type === 'classifier' ? 'bg-purple-900/30 text-purple-400' : 'bg-cyan-900/30 text-cyan-400'}`}>{r.route_type}</span>}</h3>
                {r.inherited_from && <span className="text-xs text-amber-400">继承自 {r.inherited_from}{r.overridden_fields && Object.keys(r.overridden_fields).length > 0 ? ` (覆盖: ${Object.keys(r.overridden_fields).join(', ')})` : ''}</span>}
                {r.note && <span className="text-xs text-slate-600 block">{r.note}</span>}
              </div>
              <div className="flex gap-1">
                <button onClick={() => setEditRoute({ key, ...r })} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">编辑</button>
                <button onClick={() => onTest(key)} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
                {key === 'sticker_describe' && <button onClick={() => onTest(key, 'vision')} className="px-2 py-1 bg-cyan-700/50 hover:bg-cyan-700 rounded text-xs">视觉测试</button>}
                <button onClick={() => {
                  const k = key
                  setResolvedData(prev => ({ ...prev, [k]: { loading: true } }))
                  api.get(`/models/routes/${encodeURIComponent(k)}/resolved`).then(r => {
                    setResolvedData(prev => ({ ...prev, [k]: r.data }))
                  }).catch(e => {
                    setResolvedData(prev => ({ ...prev, [k]: { error: e.message } }))
                  })
                }} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">诊断</button>
              </div>
            </div>
            <div className="space-y-0.5 text-xs text-slate-400">
              <div>模型: <span className="text-slate-200">{r.model}</span></div>
              <div>供应商: <span className="text-slate-500">{r.provider_id}</span>{r.provider_enabled === false && <span className="text-red-400 ml-1">已禁用</span>}</div>
              <div>API key: {r.route_api_key_configured ? <span className="text-amber-400">route 覆盖</span> : <span className="text-slate-600">继承供应商</span>}</div>
              <div>max_tokens: {r.max_tokens} | timeout: {r.timeout}s | temp: {r.temperature} | thinking: {thinkingLabel(r.enable_thinking)}</div>
              {r.source && <div className="text-slate-600">source: {r.source}</div>}
            </div>
            {testResult[key] && !testResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${testResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {testResult[key].ok ? `✅ ${testResult[key].latency_ms}ms${testResult[key].vision_payload_ok ? ' | vision payload OK' : ''}` : `❌ ${testResult[key].error}`}
                {testResult[key].note && <div className="text-slate-400 mt-1">{testResult[key].note}</div>}
              </div>
            )}
            {resolvedData[key] && !resolvedData[key].loading && !resolvedData[key].error && (
              <div className="mt-2 p-2 rounded-lg bg-slate-800/50 text-xs space-y-0.5">
                <div className="text-slate-400">解析结果 <span className="text-slate-600">(diagnostic)</span></div>
                <div>base_url: <span className="text-slate-200 font-mono break-all">{resolvedData[key].base_url}</span></div>
                <div>registry_provider: <span className="text-slate-200">{resolvedData[key].registry_provider}</span></div>
                <div>source: <span className="text-slate-300">{resolvedData[key].source}</span></div>
                <div>api_key_source: <span className="text-slate-300">{resolvedData[key].api_key_source || '-'}</span></div>
                <div>enable_thinking: <span className="text-slate-300">{thinkingLabel(resolvedData[key].enable_thinking)}</span></div>
                {resolvedData[key].inherited_from && <div>继承自: <span className="text-amber-400">{resolvedData[key].inherited_from}</span></div>}
                {resolvedData[key].overridden_fields && Object.keys(resolvedData[key].overridden_fields || {}).length > 0 && (
                  <div>覆盖字段: <span className="text-amber-400">{Object.entries(resolvedData[key].overridden_fields).map(([k,v]) => `${k}=${v}`).join(', ')}</span></div>
                )}
                <div>provider_enabled: {resolvedData[key].provider_enabled ? <span className="text-emerald-400">是</span> : <span className="text-red-400">否</span>}</div>
              </div>
            )}
          </Card>
        ))}
      </div>
      {editRoute && (
        <RouteEditModalV2 route={editRoute} providers={providers} onClose={() => setEditRoute(null)} onSaved={() => { setEditRoute(null); onSaved() }} />
      )}
    </div>
  )
}

function RouteEditModalV2({ route, providers, onClose, onSaved }) {
  const routeKey = route.route_key || route.key
  const isChatRoute = ['reply', 'fast', 'smart'].includes(routeKey)
  const supportsRouteApiKey = !isChatRoute
  const [f, setF] = useState({
    provider_id: route.provider_id || '', model: route.model || '',
    max_tokens: route.max_tokens ?? 30, temperature: route.temperature ?? 0,
    timeout: route.timeout ?? 15, enable_thinking: route.enable_thinking || 'auto',
    api_key: '',
  })
  const [clearApiKey, setClearApiKey] = useState(false)
  const [catalog, setCatalog] = useState([])
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [modelSearch, setModelSearch] = useState('')
  const [showManual, setShowManual] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [saveError, setSaveError] = useState('')
  useEffect(() => {
    const params = { limit: modelSearch ? 50 : 200 }
    if (f.provider_id) params.provider = f.provider_id
    if (modelSearch) params.q = modelSearch
    let cancelled = false
    const id = setTimeout(() => {
      setCatalogLoading(true)
      api.get('/models/catalog', { params })
        .then(r => { if (!cancelled) setCatalog(r.data.catalog || []) })
        .catch(() => {})
        .finally(() => { if (!cancelled) setCatalogLoading(false) })
    }, 0)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [f.provider_id, modelSearch])
  const providerModels = catalog.slice(0, 100)
  const hasCurrentModel = f.model && providerModels.some(m => m.model === f.model)
  const save = () => {
    setSaveError('')
    const payload = {}
    if (f.model && f.model.trim()) payload.model = f.model.trim()
    if (f.provider_id) payload.provider = f.provider_id
    payload.max_tokens = String(f.max_tokens)
    payload.temperature = String(f.temperature)
    payload.timeout = String(f.timeout)
    payload.enable_thinking = f.enable_thinking || 'auto'
    if (supportsRouteApiKey && showAdvanced && (f.api_key.trim() || clearApiKey)) {
      payload.api_key = clearApiKey ? '' : f.api_key.trim()
    }
    api.put(`/models/routes/${routeKey}`, payload).then(onSaved).catch(e => setSaveError(e.response?.data?.detail || e.message))
  }
  const fromTimingGate = routeKey === 'private_decision' || routeKey === 'classifier_legacy'
  return (
    <Modal onClose={onClose}>
      <div className="p-6 max-w-md">
        <h2 className="text-lg font-bold mb-3">编辑 {route.label} ({routeKey})</h2>
        {fromTimingGate && <p className="text-xs text-amber-400 mb-3">继承自 timing_gate。仅需配置需要覆盖的字段。</p>}
        <div className="space-y-3">
          <div>
            <label className="text-xs text-slate-500">供应商</label>
            <select value={f.provider_id} onChange={e => { setModelSearch(''); setF({ ...f, provider_id: e.target.value, model: '' }) }} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
              <option value="">不指定</option>
              {(providers || []).map(p => <option key={p.id} value={p.id}>{p.id}{p.enabled === false ? ' (已禁用)' : ''}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500">模型</label>
            {catalog.length > 10 && <input value={modelSearch} onChange={e => setModelSearch(e.target.value)} placeholder="搜索过滤模型..." className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-xs mt-1" />}
            {providerModels.length > 0 && (
              <select value={f.model} onChange={e => setF({ ...f, model: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
                <option value="">请选择模型</option>
                {f.model && !hasCurrentModel && (
                  <option value={f.model}>当前配置：{f.model}（未在该 provider /models 中确认）</option>
                )}
                {providerModels.map(m => <option key={m.id} value={m.model}>{m.model}{m.stale ? ' (stale)' : ''}</option>)}
              </select>
            )}
            {catalogLoading && <div className="text-xs text-slate-500 mt-1">加载中...</div>}
            {!catalogLoading && catalog.length === 0 && <div className="text-xs text-slate-500 mt-1">模型目录为空，请先在「模型列表」Tab 刷新目录</div>}
            {!catalogLoading && f.provider_id && providerModels.length === 0 && catalog.length > 0 && <div className="text-xs text-slate-500 mt-1">该供应商下暂无目录模型</div>}
            <button type="button" onClick={() => setShowManual(!showManual)} className="text-xs text-slate-500 hover:text-slate-300 mt-1">
              {showManual ? '收起手动输入' : '手动输入模型 ID...'}
            </button>
            {showManual && <input value={f.model} onChange={e => setF({ ...f, model: e.target.value })} placeholder="手动输入模型 ID" className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" />}
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div><label className="text-xs text-slate-500">max_tokens</label><input type="number" min="0" value={f.max_tokens} onChange={e => setF({ ...f, max_tokens: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">temp</label><input type="number" step="0.1" min="0" max="2" value={f.temperature} onChange={e => setF({ ...f, temperature: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
            <div><label className="text-xs text-slate-500">timeout</label><input type="number" min="1" value={f.timeout} onChange={e => setF({ ...f, timeout: Number(e.target.value) })} className="w-full p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1" /></div>
          </div>
          <div>
            <label className="text-xs text-slate-500">enable_thinking</label>
            <select value={f.enable_thinking} onChange={e => setF({ ...f, enable_thinking: e.target.value })} className="w-full p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm mt-1">
              <option value="auto">自动</option>
              <option value="true">启用</option>
              <option value="false">禁用</option>
            </select>
          </div>
          {isChatRoute && <p className="text-xs text-slate-600">reply 当前会在每次主回复前同步 provider/model/timeout/temperature/max_tokens；fast/smart 仍为预留配置。</p>}
          <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className="text-xs text-slate-500 hover:text-slate-300">
            {showAdvanced ? '收起高级' : '高级覆盖 ▼'}
          </button>
          {showAdvanced && (
            <div className="p-3 bg-slate-900 rounded-lg space-y-2 text-sm">
              {supportsRouteApiKey ? (
                <>
                  <p className="text-xs text-slate-500">Route API key 是高级覆盖；为空时继承供应商 API key。</p>
                  {route.route_api_key_configured && <p className="text-xs text-amber-400">当前已配置 route 级 API key 覆盖。</p>}
                  <input type="password" value={f.api_key} onChange={e => { setClearApiKey(false); setF({ ...f, api_key: e.target.value }) }} placeholder="填写后只覆盖此 route" className="w-full p-2 rounded-lg bg-slate-800 border border-slate-700 text-xs" />
                  {route.route_api_key_configured && (
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                      <input type="checkbox" checked={clearApiKey} onChange={e => setClearApiKey(e.target.checked)} />
                      清除 route 覆盖，恢复继承供应商 key
                    </label>
                  )}
                </>
              ) : (
                <p className="text-xs text-slate-500">reply/fast/smart API key 统一在供应商页管理。</p>
              )}
            </div>
          )}
        </div>
        {saveError && <div className="text-xs text-red-400 mt-2 p-2 bg-red-500/10 rounded-lg">{saveError}</div>}
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
          <button onClick={save} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存</button>
        </div>
      </div>
    </Modal>
  )
}

// ── Tab 3: 供应商 ──
function ProvidersTab({ providers }) {
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const doSync = () => {
    setSyncing(true)
    api.post('/models/catalog/refresh').then(r => setSyncResult(r.data)).catch(() => {}).finally(() => setSyncing(false))
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-slate-500 text-sm">管理 API 供应商的 base_url 和 api_key。路由通过「路由配置」Tab 选择供应商和模型。</p>
        <button onClick={doSync} disabled={syncing} className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium">{syncing ? '同步中...' : '同步所有模型'}</button>
      </div>
      {syncResult && (
        <div className="mb-3 text-xs">
          {syncResult.results?.map(r => (
            <span key={r.provider} className={`mr-3 ${r.ok ? 'text-emerald-400' : 'text-red-400'}`}>{r.provider}: {r.ok ? `${r.models.length} 个` : r.error}</span>
          ))}
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {(providers || []).map(p => (
          <Card key={p.id} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium text-sm">{p.id}</h3>
              <Badge tone={p.enabled !== false ? 'emerald' : 'slate'}>{p.enabled !== false ? '启用' : '禁用'}</Badge>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>base_url: <span className="text-slate-500 font-mono break-all">{p.base_url || '未配置'}</span></div>
              <div>API key: {p.api_key_configured ? '✅ 已配置' : '❌ 未配置'}</div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}

// ── Tab 4: 本地组件 ──
function LocalComponentsTab({ components, localResult, onAction }) {
  return (
    <div>
      <p className="text-slate-500 text-sm mb-3">本地语义组件为按需懒加载，不属于 API 路由。通过预热/测试按钮触发加载。</p>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {Object.entries(components || {}).map(([key, c]) => (
          <Card key={key} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="font-medium text-sm">{key}</h3>
                <span className="text-xs text-slate-500">配置: {c.configured ? '已配置' : '未配置'} | 加载: {c.load_state === 'loaded' ? '已加载' : c.load_state === 'fallback' ? '降级' : c.load_state === 'unavailable' ? '不可用' : '未加载'}</span>
                {c.fallback && <span className="text-xs text-amber-400 ml-1">(降级: {c.fallback})</span>}
              </div>
              <div className="flex gap-1">
                <button onClick={() => onAction(key, 'warmup')} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">预热</button>
                <button onClick={() => onAction(key, 'test')} className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 rounded text-xs">测试</button>
              </div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>模型: <span className="text-slate-200">{c.model}</span></div>
              <div>用途: {c.role}</div>
              <div className="text-slate-600">触发: {c.trigger}</div>
              {c.note && <div className="text-slate-600 italic">{c.note}</div>}
            </div>
            {localResult[key] && !localResult[key].loading && (
              <div className={`mt-2 p-2 rounded-lg text-xs ${localResult[key].ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                {localResult[key].ok ? `✅ ${localResult[key].latency_ms}ms${localResult[key].dim ? ' | dim=' + localResult[key].dim : ''}` : `❌ ${localResult[key].error || ''}`}
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}

// ── Tools ──
function ToolsPage() {
  const tabs = [
    { key: 'defaults', label: '默认模板' },
    { key: 'overrides', label: '指定覆盖' },
    { key: 'audit', label: '修改记录' },
  ]
  const templates = [
    { key: 'private_default', label: '普通私聊', chatType: 'private', help: '普通私聊的基础工具模板' },
    { key: 'private_superuser_default', label: '私聊 superuser', chatType: 'private_superuser', help: 'superuser 私聊的基础工具模板' },
    { key: 'group_default', label: '群聊', chatType: 'group', help: '群聊的基础工具模板' },
    { key: 'lightweight_default', label: '轻量预设', chatType: 'group', help: '运行时自动降档时使用的轻量工具集合' },
  ]
  const [tab, setTab] = useState('defaults')
  const [templateKey, setTemplateKey] = useState('group_default')
  const [tools, setTools] = useState([])
  const [regInfo, setRegInfo] = useState(null)
  const [regAvail, setRegAvail] = useState(false)
  const [regEmpty, setRegEmpty] = useState(false)
  const [bridgeCt, setBridgeCt] = useState(0)
  const [overrideScope, setOverrideScope] = useState('group')
  const [targetId, setTargetId] = useState('')
  const [targetSearch, setTargetSearch] = useState('')
  const [targetOptions, setTargetOptions] = useState([])
  const [targetPickerOpen, setTargetPickerOpen] = useState(false)
  const [auditLogs, setAuditLogs] = useState([])
  const [expandAudit, setExpandAudit] = useState(null)
  const activeTemplate = templates.find(t => t.key === templateKey) || templates[0]
  const load = useCallback(() => {
    const isUserOverride = tab === 'overrides' && overrideScope === 'user'
    api.get('/tools', {
      params: {
        chat_type: tab === 'defaults' ? activeTemplate.chatType : isUserOverride ? 'private' : 'group',
        group_id: tab === 'overrides' && overrideScope === 'group' ? targetId : '',
        user_id: isUserOverride ? targetId : '',
      },
    }).then(r => {
      setTools(r.data.tools || [])
      setRegInfo(r.data.registry_info || null)
      setRegAvail(r.data.registry_available)
      setRegEmpty(r.data.registry_empty)
      setBridgeCt(r.data.bridge_count || 0)
    })
  }, [tab, overrideScope, targetId, activeTemplate.chatType])
  const loadTargets = useCallback(() => {
    if (tab !== 'overrides') return
    api.get('/tools/targets', {
      params: { scope_type: overrideScope, search: targetSearch, limit: 50 },
    }).then(r => setTargetOptions(r.data.items || []))
  }, [tab, overrideScope, targetSearch])
  const loadAudit = useCallback(() => api.get('/audit-logs', { params: { target_type: 'tool', limit: 50 } }).then(r => setAuditLogs(r.data.items || [])), [])
  useEffect(() => { if (tab === 'audit') loadAudit(); else load() }, [tab, load, loadAudit])
  useEffect(() => { loadTargets() }, [loadTargets])

  const toggleDefault = (t, field) => {
    const val = !t[field]
    api.put(`/tools/${t.name}`, { [field]: val }).then(load)
  }

  const scopeForTab = () => {
    if (tab !== 'overrides' || !targetId.trim()) return null
    return {
      scope_type: overrideScope,
      scope_id: targetId.trim(),
    }
  }

  const setOverride = (t, enabled) => {
    const scope = scopeForTab()
    if (!scope) return
    api.put(`/tools/${t.name}/override`, { ...scope, enabled, reason: '' }).then(load)
  }

  const clearOverride = (t) => {
    const scope = scopeForTab()
    if (!scope) return
    api.delete(`/tools/${t.name}/override`, { params: scope }).then(load)
  }
  const selectTarget = (target) => {
    setTargetId(target.id)
    setTargetSearch(target.label)
    setTargetPickerOpen(false)
  }
  const onTargetInput = (value) => {
    setTargetSearch(value)
    const match = targetOptions.find(item => item.id === value || item.label === value)
    setTargetId(match ? match.id : '')
    setTargetPickerOpen(true)
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">工具管理</h1>
      <p className="text-slate-500 text-sm mb-4">管理工具配置：默认模板决定基础权限，轻量预设用于运行时自动降档，指定覆盖用于具体群聊或私聊用户。</p>
      <div className="flex gap-2 mb-6 border-b border-slate-800 pb-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>{t.label}</button>
        ))}
      </div>
      {tab === 'defaults' && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label htmlFor="tool-template-select" className="text-xs text-slate-500">
            当前模板
            <select id="tool-template-select" value={templateKey} onChange={e => setTemplateKey(e.target.value)}
              className="mt-1 block min-w-[160px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
              {templates.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <span className="pb-1.5 text-xs text-slate-500">{activeTemplate.help}</span>
        </div>
      )}
      {tab === 'overrides' && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label htmlFor="tool-override-scope" className="text-xs text-slate-500">
            覆盖对象
            <select id="tool-override-scope" value={overrideScope} onChange={e => { setOverrideScope(e.target.value); setTargetId(''); setTargetSearch('') }}
              className="mt-1 block min-w-[120px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
              <option value="group">指定群聊</option>
              <option value="user">指定私聊</option>
            </select>
          </label>
          <div className="relative">
            <label htmlFor="tool-override-target" className="text-xs text-slate-500">
              {overrideScope === 'group' ? '搜索群聊' : '搜索私聊用户'}
              <input id="tool-override-target" value={targetSearch}
                onFocus={() => setTargetPickerOpen(true)}
                onBlur={() => setTimeout(() => setTargetPickerOpen(false), 120)}
                onChange={e => onTargetInput(e.target.value)}
                placeholder={overrideScope === 'group' ? '输入群名或群号' : '输入昵称或用户 ID'}
                className="mt-1 block w-72 rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200" />
            </label>
            {targetPickerOpen && (
              <div className="absolute z-20 mt-1 w-72 max-h-64 overflow-auto rounded-lg border border-slate-700 bg-slate-950 shadow-xl">
                {targetOptions.length > 0 ? targetOptions.map(item => (
                  <button key={item.id} type="button" onMouseDown={e => e.preventDefault()} onClick={() => selectTarget(item)}
                    className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-800">
                    <div className="text-slate-200">{item.label}</div>
                    <div className="mt-0.5 flex gap-2 text-[11px] text-slate-500">
                      <span>{item.scope_type}</span>
                      <span>{item.source}</span>
                      {item.recent_at && <span>{item.recent_at.slice(5, 16)}</span>}
                    </div>
                  </button>
                )) : (
                  <div className="px-3 py-3 text-xs text-slate-500">没有匹配的真实会话</div>
                )}
              </div>
            )}
          </div>
          <span className="pb-1.5 text-xs text-slate-500">{targetId ? `当前目标：${targetId}` : '请选择一个已记录的真实目标；覆盖不会对手填未知 ID 生效。'}</span>
        </div>
      )}
      {tab !== 'audit' && <div className="mb-4 flex gap-4 text-xs text-slate-400">
        {!regAvail ? (
          bridgeCt === 0 ? (
            <span className="text-slate-500">会话 bridge 尚未创建（{bridgeCt} 个活跃），触发一条消息后可用</span>
          ) : (
            <span className="text-slate-500">运行时注册状态未知（bridge 未就绪，{bridgeCt} 个活跃）</span>
          )
        ) : regEmpty ? (
          <span className="text-amber-400">KT registry 返回空，请检查 bridge/list_tools</span>
        ) : regInfo ? (
          <>
            <span>会话 bridge: <span className="text-slate-200 font-medium">{bridgeCt}</span> 个</span>
            <span>KT 已加载: <span className="text-slate-200 font-medium">{regInfo.kt_loaded?.length || 0}</span> 个</span>
            {regInfo.missing_meta?.length > 0 && <span className="text-amber-400">元数据缺失: {regInfo.missing_meta.length} 个 ({regInfo.missing_meta.join(', ')})</span>}
            {regInfo.missing_kt?.length > 0 && <span className="text-red-400">KT 未加载: {regInfo.missing_kt.length} 个 ({regInfo.missing_kt.join(', ')})</span>}
          </>
        ) : null}
      </div>}
      {tab !== 'audit' && <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-2">工具</th><th className="py-2 px-2">类别</th><th className="py-2 px-2">风险</th>
            {tab === 'defaults' && <th className="py-2 px-2">{activeTemplate.label}</th>}
            {tab === 'overrides' && <th className="py-2 px-2">配置状态</th>}
            <th className="py-2 px-2">说明</th>
          </tr></thead>
          <tbody>
            {tools.map(t => {
              const isForced = overrideScope === 'group' && t.force_disabled_group
              const isLocked = t.force_enabled
              const isSubagent = t.is_subagent
              const configured = t.configured_enabled ?? t.effective
              const overrideValue = t.override_present
                ? (t.override_enabled ? 'enabled' : 'disabled')
                : 'inherit'
              return (
                <tr key={t.name} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="py-2 px-2 font-mono text-slate-200">
                  {t.name}
                  {t.is_subagent && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-purple-900/30 text-purple-400">subagent</span>}
                  {regAvail && t.registered === false && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-red-900/30 text-red-400">未注册</span>}
                  {regAvail && t.registered === null && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-slate-800 text-slate-500">未知</span>}
                  {regAvail && t.registered === true && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-emerald-900/30 text-emerald-400">已注册</span>}
                </td>
                  <td className="py-2 px-2 text-slate-400">{t.category}</td>
                  <td className="py-2 px-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${t.risk_level === 'high' ? 'bg-red-900/30 text-red-400' : t.risk_level === 'medium' ? 'bg-amber-900/30 text-amber-400' : 'bg-slate-700 text-slate-300'}`}>{t.risk_level}</span>
                  </td>
                  {tab === 'defaults' && (
                    <td className="py-2 px-2">
                      <button onClick={() => !t.force_enabled && !(templateKey === 'group_default' && t.force_disabled_group) && toggleDefault(t, templateKey)}
                        disabled={t.force_enabled || (templateKey === 'group_default' && t.force_disabled_group)}
                        title={templateKey === 'lightweight_default' ? '运行时自动降档会使用这套轻量工具预设' : activeTemplate.help}
                        className={`px-2 py-1 rounded text-xs ${t[templateKey] ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-600/30 text-slate-500'} ${(t.force_enabled || (templateKey === 'group_default' && t.force_disabled_group)) ? 'opacity-50 cursor-not-allowed' : ''}`}>
                        {t[templateKey] ? 'ON' : 'OFF'}
                      </button>
                      {templateKey === 'group_default' && t.force_disabled_group && <span className="ml-2 text-xs text-slate-500">群聊强制禁用</span>}
                    </td>
                  )}
                  {tab === 'overrides' && (
                    <td className="py-2 px-2">
                      {isForced && <span className="text-xs text-slate-500">群聊强制禁用</span>}
                      {isLocked && <span className="text-xs text-emerald-400">强制启用</span>}
                      {isSubagent && <span className="text-xs text-purple-400">subagent（运行时禁用有限）</span>}
                      {!isForced && !isLocked && !isSubagent && (
                        <select value={overrideValue}
                          onChange={e => { const v = e.target.value; if (v === 'inherit') clearOverride(t); else setOverride(t, v === 'enabled') }}
                          disabled={!targetId.trim()}
                          className={`p-1 rounded text-xs bg-slate-900 border border-slate-700 ${!targetId.trim() ? 'opacity-40' : ''}`}>
                          <option value="inherit">继承（{configured ? '启用' : '禁用'}）</option>
                          <option value="enabled">启用</option>
                          <option value="disabled">禁用</option>
                        </select>
                      )}
                    </td>
                  )}
                  <td className="py-2 px-2 text-slate-500 text-xs max-w-[200px] truncate" title={t.description}>{t.description}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>}
      {tab === 'audit' && (
        <Card className="mt-4 overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-2">时间</th><th className="py-2 px-2">动作</th><th className="py-2 px-2">工具</th><th className="py-2 px-2">操作者</th><th className="py-2 px-2">IP</th>
            </tr></thead>
            <tbody>
              {auditLogs.map(d => (
                <tr key={d.id} className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer" onClick={() => setExpandAudit(expandAudit === d.id ? null : d.id)}>
                  <td className="py-2 px-2 text-slate-500 whitespace-nowrap">{d.created_at ? d.created_at.slice(5, 19) : ''}</td>
                  <td className="py-2 px-2"><Badge tone={d.action === 'tool_default_update' ? 'emerald' : 'amber'}>{d.action}</Badge></td>
                  <td className="py-2 px-2 text-slate-300 font-mono">{d.target_id}</td>
                  <td className="py-2 px-2 text-slate-400">{d.admin_user}</td>
                  <td className="py-2 px-2 text-slate-500">{d.ip_address || '-'}</td>
                </tr>
              ))}
              {auditLogs.length === 0 && (
                <tr><td colSpan={5} className="py-8 text-center text-slate-600">暂无工具修改记录</td></tr>
              )}
            </tbody>
          </table>
          {expandAudit && (
            <div className="p-3 bg-slate-900 border-t border-slate-800">
              {(() => {
                const d = auditLogs.find(x => x.id === expandAudit)
                if (!d) return null
                return (
                  <pre className="text-xs text-slate-300 whitespace-pre-wrap break-all">{JSON.stringify(d.detail_json || {}, null, 2)}</pre>
                )
              })()}
            </div>
          )}
        </Card>
      )}
    </div>
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

// ── Reply Test / Eval ──
function replyEvalTone(value) {
  if (value === true || value === 1 || value === 'reply' || value === 'ok' || value === 'retry_success') return 'emerald'
  if (value === 'no_reply' || value === false || value === 0) return 'slate'
  if (value === 'error' || value === 'no_tool_call' || value === 'fake_tool_call_claim' || value === 'suppressed') return 'red'
  if (value === 'prompt_only' || value === 'code_retry' || value === 'baseline') return 'blue'
  return 'slate'
}

function splitCsv(text) {
  return String(text || '').split(',').map(x => x.trim()).filter(Boolean)
}

function caseToDraft(c = {}) {
  return {
    case_id: c.case_id || '',
    title: c.title || '',
    chat_type: c.chat_type || 'group',
    input_text: c.input_text || '',
    expected_action: c.expected_action || 'any',
    tagsText: (c.tags || []).join(', '),
    expectedKeywordsText: (c.expected_keywords || []).join(', '),
    forbiddenKeywordsText: (c.forbidden_keywords || []).join(', '),
    contextText: JSON.stringify(c.context || {}, null, 2),
    enabled: c.enabled ?? 1,
  }
}

function draftToPayload(draft) {
  const context = (() => {
    try {
      return JSON.parse(draft.contextText || '{}')
    } catch {
      throw new Error('context JSON 格式错误')
    }
  })()
  return {
    case_id: draft.case_id,
    title: draft.title,
    chat_type: draft.chat_type || 'group',
    input_text: draft.input_text,
    context,
    expected_action: draft.expected_action || 'any',
    expected_keywords: splitCsv(draft.expectedKeywordsText),
    forbidden_keywords: splitCsv(draft.forbiddenKeywordsText),
    tags: splitCsv(draft.tagsText),
    enabled: draft.enabled ? 1 : 0,
  }
}

function ReplyAttemptCard({ title, attempt }) {
  if (!attempt) return null
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-xs font-medium text-slate-300">{title}</div>
        <Badge tone={replyEvalTone(attempt.result)}>{attempt.result || '-'}</Badge>
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        <Badge tone={attempt.called_reply ? 'emerald' : 'slate'}>reply {attempt.called_reply ? 'yes' : 'no'}</Badge>
        <Badge tone={attempt.called_no_reply ? 'emerald' : 'slate'}>no_reply {attempt.called_no_reply ? 'yes' : 'no'}</Badge>
        <Badge tone={attempt.structured_fallback ? 'blue' : 'slate'}>fallback {attempt.structured_fallback ? 'yes' : 'no'}</Badge>
      </div>
      <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-900 p-2 text-[11px] text-slate-400">{attempt.raw_output || '-'}</pre>
    </div>
  )
}

function ReplyEvalMetricsTable({ metrics = {} }) {
  const rows = [
    ['reply_call_rate', 'reply/no_reply 调用率'],
    ['valid_action_rate', '有效动作率'],
    ['expected_action_accuracy', '预期动作准确率'],
    ['retry_used_rate', '重试使用率'],
    ['retry_success_rate', '重试成功率'],
    ['no_tool_call_rate', '无工具调用率'],
    ['fake_tool_claim_rate', '假工具声明率'],
    ['empty_output_rate', '空输出率'],
  ]
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map(([key, label]) => (
          <tr key={key} className="border-b border-slate-800/60 last:border-0">
            <td className="py-1.5 pr-3 text-slate-500">{label}</td>
            <td className="py-1.5 text-right font-mono text-slate-200">{formatRate(metrics[key])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ReplyEvalResultsTable({ results = [] }) {
  if (!results.length) return <div className="py-8 text-center text-xs text-slate-600">暂无逐条结果</div>
  return (
    <div className="max-h-96 overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-900">
          <tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-2">case</th>
            <th className="py-2 px-2">预期</th>
            <th className="py-2 px-2">实际</th>
            <th className="py-2 px-2">合约</th>
            <th className="py-2 px-2">retry</th>
            <th className="py-2 px-2">结果</th>
            <th className="py-2 px-2">追溯</th>
          </tr>
        </thead>
        <tbody>
          {results.map(r => (
            <tr key={r.id || r.case_id} className="border-b border-slate-800/50 align-top">
              <td className="py-2 px-2 font-mono text-slate-400 max-w-44 truncate" title={r.case_id}>{r.case_id}</td>
              <td className="py-2 px-2"><Badge>{r.expected_action || '-'}</Badge></td>
              <td className="py-2 px-2"><Badge tone={replyEvalTone(r.actual_action)}>{r.actual_action || '-'}</Badge></td>
              <td className="py-2 px-2">{r.called_reply_or_no_reply ? <Badge tone="emerald">ok</Badge> : <Badge tone="red">miss</Badge>}</td>
              <td className="py-2 px-2">{r.retry_used ? <Badge tone="amber">used</Badge> : <span className="text-slate-600">-</span>}</td>
              <td className="py-2 px-2">{r.passed ? <Badge tone="emerald">pass</Badge> : <Badge tone="red">fail</Badge>}</td>
              <td className="py-2 px-2">
                {r.agent_run_id ? <NavLink to={`/agent-runs/${r.agent_run_id}`} className="text-blue-300 hover:text-blue-200">AgentRun</NavLink> : <span className="text-slate-600">-</span>}
                {r.trace_id && <div className="font-mono text-[10px] text-slate-600 truncate max-w-36" title={r.trace_id}>{r.trace_id.slice(0, 16)}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ReplyEvalPage() {
  const [form, setForm] = useState({
    chat_type: 'group',
    session_id: 'reply-test',
    sender_id: 'admin',
    sender_name: 'admin',
    message: '你在吗',
    variant: 'code_retry',
    enable_reply_contract_retry: true,
  })
  const [testResult, setTestResult] = useState(null)
  const [cases, setCases] = useState([])
  const [preview, setPreview] = useState([])
  const [previewSelected, setPreviewSelected] = useState(new Set())
  const [selectedCases, setSelectedCases] = useState(new Set())
  const [runs, setRuns] = useState([])
  const [runResult, setRunResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [evalVariant, setEvalVariant] = useState('code_retry')
  const [evalLimit, setEvalLimit] = useState(50)
  const [newCase, setNewCase] = useState(caseToDraft({}))
  const [editingCase, setEditingCase] = useState(null)

  const loadCases = useCallback(() => {
    api.get('/reply-eval/cases').then(r => setCases(r.data.items || [])).catch(() => setCases([]))
  }, [])
  const loadRuns = useCallback(() => {
    api.get('/reply-eval/runs').then(r => setRuns(r.data.items || [])).catch(() => setRuns([]))
  }, [])
  useEffect(() => { loadCases(); loadRuns() }, [loadCases, loadRuns])

  const setField = (key, value) => setForm(v => ({ ...v, [key]: value }))
  const runManualTest = (realSend = false) => {
    if (realSend && !confirm('确认执行真实发送/运行？这不是 dry-run，可能写入真实 session 状态。')) return
    setLoading(true)
    api.post('/reply-test/run', { ...form, dry_run: !realSend })
      .then(r => setTestResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const createCase = () => {
    let payload
    try { payload = draftToPayload(newCase) } catch (e) { alert(e.message); return }
    api.post('/reply-eval/cases', payload)
      .then(() => { setNewCase(caseToDraft({})); loadCases() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
  const generatePreview = () => {
    api.post('/reply-eval/generate-preview', {}).then(r => {
      const items = r.data.items || []
      setPreview(items)
      setPreviewSelected(new Set(items.map(x => x.case_id)))
    })
  }
  const savePreview = () => {
    const items = preview.filter(item => previewSelected.has(item.case_id))
    if (!items.length) {
      alert('请先选择要保存的预览用例')
      return
    }
    api.post('/reply-eval/save-generated', { items }).then(() => { setPreview([]); setPreviewSelected(new Set()); loadCases() })
  }
  const runEval = (variant = evalVariant) => {
    setLoading(true)
    api.post('/reply-eval/run', {
      variant,
      limit: Number(evalLimit) || 50,
      case_ids: Array.from(selectedCases),
    })
      .then(r => { setRunResult(r.data); loadRuns() })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const loadRunDetail = (id) => {
    setLoading(true)
    api.get(`/reply-eval/runs/${encodeURIComponent(id)}`)
      .then(r => setRunResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }
  const delCase = (caseId) => {
    if (!confirm(`确认删除 ${caseId}?`)) return
    api.delete(`/reply-eval/cases/${encodeURIComponent(caseId)}`).then(loadCases)
  }
  const saveCaseEdit = () => {
    let payload
    try { payload = draftToPayload(editingCase) } catch (e) { alert(e.message); return }
    api.put(`/reply-eval/cases/${encodeURIComponent(editingCase.case_id)}`, payload)
      .then(() => { setEditingCase(null); loadCases() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
  const updatePreviewItem = (caseId, patch) => {
    setPreview(prev => prev.map(item => item.case_id === caseId ? { ...item, ...patch } : item))
  }
  const togglePreviewSelected = (caseId, checked) => {
    setPreviewSelected(prev => {
      const next = new Set(prev)
      checked ? next.add(caseId) : next.delete(caseId)
      return next
    })
  }
  const toggleCaseSelected = (caseId, checked) => {
    setSelectedCases(prev => {
      const next = new Set(prev)
      checked ? next.add(caseId) : next.delete(caseId)
      return next
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold mb-1">Reply 测试</h1>
          <p className="text-xs text-slate-500">单条合约检查、测试集管理与 reply/no_reply A/B 评估</p>
        </div>
        <ActionButton onClick={() => { loadCases(); loadRuns() }}>刷新</ActionButton>
      </div>

      <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1.1fr)_minmax(480px,0.9fr)] gap-4">
        <Card className="p-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div>
              <h2 className="text-sm font-medium text-slate-300">单条调用测试</h2>
              <p className="text-[11px] text-slate-600">dry-run 不写真实发送状态；真实发送入口单独确认</p>
            </div>
            <Badge tone={form.enable_reply_contract_retry ? 'emerald' : 'slate'}>retry {form.enable_reply_contract_retry ? 'on' : 'off'}</Badge>
          </div>
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-2 mb-3">
            <label className="text-[11px] text-slate-500">chat_type
              <select value={form.chat_type} onChange={e => setField('chat_type', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="group">group</option>
                <option value="private">private</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">variant
              <select value={form.variant} onChange={e => setField('variant', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="baseline">baseline</option>
                <option value="prompt_only">prompt_only</option>
                <option value="code_retry">code_retry</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">session_id
              <input value={form.session_id} onChange={e => setField('session_id', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">sender_id
              <input value={form.sender_id} onChange={e => setField('sender_id', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">sender_name
              <input value={form.sender_name} onChange={e => setField('sender_name', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="text-[11px] text-slate-500">character_name
              <input value={form.character_name || ''} onChange={e => setField('character_name', e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400 self-end">
              <input type="checkbox" checked={form.enable_reply_contract_retry} onChange={e => setField('enable_reply_contract_retry', e.target.checked)} className="accent-emerald-500" />
              启用合约重试
            </label>
          </div>
          <label className="block text-[11px] text-slate-500 mb-3">message
            <textarea value={form.message} onChange={e => setField('message', e.target.value)} className="mt-1 w-full h-24 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
            <label className="text-[11px] text-slate-500">recent_context
              <textarea value={form.recent_context || ''} onChange={e => setField('recent_context', e.target.value)} className="mt-1 w-full h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
            </label>
            <label className="text-[11px] text-slate-500">persona_text
              <textarea value={form.persona_text || ''} onChange={e => setField('persona_text', e.target.value)} className="mt-1 w-full h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ActionButton tone="emerald" onClick={() => runManualTest(false)} disabled={loading}>运行 dry-run</ActionButton>
            <ActionButton tone="red" onClick={() => runManualTest(true)} disabled={loading}>真实发送</ActionButton>
            <span className="text-[11px] text-slate-600">真实发送会使用非 dry-run metadata；执行前会二次确认。</span>
          </div>
          {testResult && (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
                <MiniStat label="final" value={testResult.final?.action || '-'} tone={testResult.final?.action === 'reply' ? 'emerald' : 'slate'} />
                <MiniStat label="retry" value={testResult.metrics?.retry_used ? 'used' : 'no'} tone={testResult.metrics?.retry_used ? 'amber' : 'slate'} />
                <MiniStat label="contract" value={testResult.metrics?.reply_contract_ok ? 'ok' : 'miss'} tone={testResult.metrics?.reply_contract_ok ? 'emerald' : 'red'} />
                <MiniStat label="LLM logs" value={(testResult.llm_api_request_logs || []).length} tone="blue" />
                <MiniStat label="run_id" value={(testResult.run_id || '').slice(0, 12) || '-'} />
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
                <ReplyAttemptCard title="first_attempt" attempt={testResult.first_attempt} />
                <ReplyAttemptCard title="retry_attempt" attempt={testResult.retry_attempt} />
              </div>
              {testResult.final?.content && <JsonBlock value={testResult.final.content} className="max-h-40" />}
              <details className="rounded-lg border border-slate-800">
                <summary className="cursor-pointer px-3 py-2 text-xs text-slate-400 hover:bg-slate-800/40">完整结果 JSON</summary>
                <JsonBlock value={testResult} className="m-2 max-h-96" />
              </details>
              {(testResult.llm_api_request_logs || []).length > 0 && <LLMApiRequestLogsBlock logs={testResult.llm_api_request_logs} />}
            </div>
          )}
        </Card>

        <Card className="p-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div>
              <h2 className="text-sm font-medium text-slate-300">A/B 评估</h2>
              <p className="text-[11px] text-slate-600">选择用例后只跑选中项；未选择时跑全部启用项</p>
            </div>
            <Badge tone="blue">{selectedCases.size ? `${selectedCases.size} selected` : 'all enabled'}</Badge>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
            <label className="text-[11px] text-slate-500">variant
              <select value={evalVariant} onChange={e => setEvalVariant(e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs">
                <option value="baseline">baseline</option>
                <option value="prompt_only">prompt_only</option>
                <option value="code_retry">code_retry</option>
              </select>
            </label>
            <label className="text-[11px] text-slate-500">limit
              <input type="number" min="1" max="200" value={evalLimit} onChange={e => setEvalLimit(e.target.value)} className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-xs" />
            </label>
            <div className="self-end flex gap-2 md:col-span-2">
              <ActionButton tone="emerald" onClick={() => runEval(evalVariant)} disabled={loading || !cases.length}>运行评估</ActionButton>
              <ActionButton onClick={() => setSelectedCases(new Set())}>清空选择</ActionButton>
            </div>
          </div>
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-2 text-[11px] text-amber-200 mb-3">
            prompt_only 当前作为独立 variant 记录；是否切换实验 Prompt 需以后端 PromptManager 配置为准。
          </div>
          {runResult && (
            <div className="grid grid-cols-1 xl:grid-cols-[220px_1fr] gap-3 mb-4">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Badge tone={replyEvalTone(runResult.variant)}>{runResult.variant}</Badge>
                  <Badge tone={runResult.failed ? 'red' : 'emerald'}>{runResult.passed || 0}/{runResult.total || 0}</Badge>
                </div>
                <ReplyEvalMetricsTable metrics={runResult.metrics || {}} />
              </div>
              <ReplyEvalResultsTable results={runResult.results || []} />
            </div>
          )}
          <h3 className="text-xs text-slate-500 mb-2">最近评估</h3>
          <div className="space-y-1 max-h-64 overflow-auto">
            {runs.map(r => (
              <button key={r.id} onClick={() => loadRunDetail(r.id)} className="w-full flex items-center gap-2 rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-xs text-left hover:bg-slate-800/60 transition-colors">
                <Badge tone="blue">{r.variant}</Badge>
                <span className="text-slate-300">total {r.total}</span>
                <span className="text-emerald-300">acc {formatRate(r.metrics?.expected_action_accuracy)}</span>
                <span className="text-slate-500 text-xs ml-auto">{r.created_at}</span>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300">测试集</h2>
          <div className="flex gap-2">
            <ActionButton onClick={generatePreview}>生成预览</ActionButton>
            {preview.length > 0 && <ActionButton tone="emerald" onClick={savePreview}>保存选中 {previewSelected.size}</ActionButton>}
          </div>
        </div>
        {preview.length > 0 && (
          <div className="mb-4 rounded-lg border border-slate-800 overflow-hidden">
            <div className="flex items-center justify-between bg-slate-950/70 px-3 py-2 text-xs text-slate-400">
              <span>生成预览 {preview.length} 条</span>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={preview.length > 0 && previewSelected.size === preview.length} onChange={e => setPreviewSelected(e.target.checked ? new Set(preview.map(x => x.case_id)) : new Set())} className="accent-emerald-500" />
                全选
              </label>
            </div>
            <div className="max-h-96 overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-900 text-slate-500">
                  <tr className="border-b border-slate-800"><th className="py-2 px-2 w-8"></th><th className="py-2 px-2">标题</th><th className="py-2 px-2">输入</th><th className="py-2 px-2">预期</th><th className="py-2 px-2">tags</th></tr>
                </thead>
                <tbody>
                  {preview.map(item => (
                    <tr key={item.case_id} className="border-b border-slate-800/50 align-top">
                      <td className="py-2 px-2"><input type="checkbox" checked={previewSelected.has(item.case_id)} onChange={e => togglePreviewSelected(item.case_id, e.target.checked)} className="accent-emerald-500" /></td>
                      <td className="py-2 px-2"><input value={item.title || ''} onChange={e => updatePreviewItem(item.case_id, { title: e.target.value })} className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs" /></td>
                      <td className="py-2 px-2"><textarea value={item.input_text || ''} onChange={e => updatePreviewItem(item.case_id, { input_text: e.target.value })} className="w-full h-12 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs resize-none" /></td>
                      <td className="py-2 px-2">
                        <select value={item.expected_action || 'any'} onChange={e => updatePreviewItem(item.case_id, { expected_action: e.target.value })} className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs">
                          <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
                        </select>
                      </td>
                      <td className="py-2 px-2"><input value={(item.tags || []).join(', ')} onChange={e => updatePreviewItem(item.case_id, { tags: splitCsv(e.target.value) })} className="w-full bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-6 gap-2 mb-4">
          <input value={newCase.case_id} onChange={e => setNewCase(v => ({ ...v, case_id: e.target.value }))} placeholder="case_id" className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs" />
          <input value={newCase.title} onChange={e => setNewCase(v => ({ ...v, title: e.target.value }))} placeholder="标题" className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs" />
          <input value={newCase.input_text} onChange={e => setNewCase(v => ({ ...v, input_text: e.target.value }))} placeholder="输入" className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs md:col-span-2" />
          <select value={newCase.expected_action} onChange={e => setNewCase(v => ({ ...v, expected_action: e.target.value }))} className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs">
            <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
          </select>
          <ActionButton tone="emerald" onClick={createCase} disabled={!newCase.input_text}>新增</ActionButton>
        </div>
        {editingCase && (
          <div className="mb-4 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-medium text-blue-200">编辑 {editingCase.case_id}</h3>
              <ActionButton onClick={() => setEditingCase(null)}>关闭</ActionButton>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-6 gap-2">
              <input value={editingCase.title} onChange={e => setEditingCase(v => ({ ...v, title: e.target.value }))} className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs" />
              <select value={editingCase.chat_type} onChange={e => setEditingCase(v => ({ ...v, chat_type: e.target.value }))} className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs">
                <option value="group">group</option><option value="private">private</option>
              </select>
              <select value={editingCase.expected_action} onChange={e => setEditingCase(v => ({ ...v, expected_action: e.target.value }))} className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs">
                <option value="any">any</option><option value="reply">reply</option><option value="no_reply">no_reply</option>
              </select>
              <input value={editingCase.tagsText} onChange={e => setEditingCase(v => ({ ...v, tagsText: e.target.value }))} placeholder="tags" className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs" />
              <label className="flex items-center gap-2 text-xs text-slate-400"><input type="checkbox" checked={Boolean(editingCase.enabled)} onChange={e => setEditingCase(v => ({ ...v, enabled: e.target.checked ? 1 : 0 }))} className="accent-emerald-500" /> enabled</label>
              <ActionButton tone="emerald" onClick={saveCaseEdit}>保存</ActionButton>
              <textarea value={editingCase.input_text} onChange={e => setEditingCase(v => ({ ...v, input_text: e.target.value }))} className="md:col-span-3 h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs resize-none" />
              <textarea value={editingCase.contextText} onChange={e => setEditingCase(v => ({ ...v, contextText: e.target.value }))} className="md:col-span-3 h-20 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono resize-none" />
            </div>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-3 w-8"><input type="checkbox" checked={cases.length > 0 && selectedCases.size === cases.length} onChange={e => setSelectedCases(e.target.checked ? new Set(cases.map(c => c.case_id)) : new Set())} className="accent-emerald-500" /></th><th className="py-2 px-3">case_id</th><th className="py-2 px-3">标题</th><th className="py-2 px-3">输入</th><th className="py-2 px-3">预期</th><th className="py-2 px-3">tags</th><th className="py-2 px-3">操作</th></tr></thead>
            <tbody>{cases.map(c => (
              <tr key={c.case_id} className="border-b border-slate-800/50">
                <td className="py-2 px-3"><input type="checkbox" checked={selectedCases.has(c.case_id)} onChange={e => toggleCaseSelected(c.case_id, e.target.checked)} className="accent-emerald-500" /></td>
                <td className="py-2 px-3 text-xs text-slate-500">{c.case_id}</td>
                <td className="py-2 px-3">{c.title}</td>
                <td className="py-2 px-3 max-w-md truncate">{c.input_text}</td>
                <td className="py-2 px-3"><Badge tone={replyEvalTone(c.expected_action)}>{c.expected_action}</Badge></td>
                <td className="py-2 px-3 text-xs text-slate-500">{(c.tags || []).join(', ')}</td>
                <td className="py-2 px-3 text-right">
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setEditingCase(caseToDraft(c))} className="text-xs text-blue-300 hover:text-blue-200">编辑</button>
                    <button onClick={() => delCase(c.case_id)} className="text-xs text-red-300 hover:text-red-200">删除</button>
                  </div>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
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
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/configs" element={<ConfigsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/reply-eval" element={<ReplyEvalPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/db" element={<DbPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />
          <Route path="/prompt-legacy" element={<PromptPage />} />
          <Route path="/prompts" element={<ManagedPromptsPage />} />
          <Route path="/prompt-preview" element={<EffectivePromptPreviewPage />} />
          <Route path="/prompt-v2-templates" element={<PromptV2TemplatesPage />} />
          <Route path="/agent-runs/:runId" element={<AgentRunDetailPage />} />
          <Route path="/agent-runs" element={<AgentRunsPage />} />
          <Route path="/llm-api-logs" element={<LLMApiLogsPage />} />
          <Route path="/tool-calls" element={<ToolCallsPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
