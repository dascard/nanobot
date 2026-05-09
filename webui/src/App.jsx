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
  { to: '/configs', label: '配置' },
  { to: '/settings', label: '设置' },
  { to: '/memory', label: '群体记忆' },
  { to: '/db', label: '数据库' },
]

function Layout({ children, onLogout }) {
  const [version, setVersion] = useState(null)
  useEffect(() => {
    api.get('/version').then(r => setVersion(r.data)).catch(() => setVersion(null))
  }, [])
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 flex">
      <nav className="w-48 bg-slate-900/80 backdrop-blur-sm border-r border-slate-800 p-4 flex flex-col gap-0.5">
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

function MiniStat({ label, value, tone = 'slate' }) {
  const color = {
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
    red: 'text-red-300',
    blue: 'text-blue-300',
    slate: 'text-white',
  }[tone] || 'text-white'
  return (
    <Card className="p-4 min-h-[92px]">
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
  useEffect(() => {
    api.get('/overview').then(r => setData(r.data)).catch(() => setData(null))
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
        <MiniStat label="最近错误数" value={c.recent_errors} tone={c.recent_errors ? 'red' : 'slate'} />
        <MiniStat label="TimingGate parse_error" value={c.timing_parse_errors} tone={c.timing_parse_errors ? 'red' : 'slate'} />
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
          <div className="space-y-3 text-sm">
            <div className="flex justify-between gap-3"><span className="text-slate-500">主模型</span><span className="truncate">{data.models?.main || '-'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-slate-500">快模型</span><span className="truncate">{data.models?.fast || '-'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-slate-500">智能模型</span><span className="truncate">{data.models?.smart || '-'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-slate-500">TimingGate</span><span className="truncate">{data.models?.timing_gate || '-'}</span></div>
          </div>
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
      <Card className="p-2 mb-4 flex gap-1 flex-wrap">
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
  const [data, setData] = useState({ items: [], stats: {} })
  const [groupId, setGroupId] = useState('')
  const [context, setContext] = useState('<timing_context>\n群: 测试群\n触发原因: ambient\n[用户名]用户A\n[发言内容]刚才这个报错怎么回事\n</timing_context>')
  const [repeats, setRepeats] = useState(1)
  const [testResult, setTestResult] = useState(null)
  const [running, setRunning] = useState(false)

  const load = useCallback(() => {
    api.get('/timing-gate/events', { params: { group_id: groupId, limit: 80 } }).then(r => setData(r.data))
  }, [groupId])
  useEffect(() => { load() }, [load])

  const stats = data.stats || {}
  const runTest = () => {
    setRunning(true)
    api.post('/timing-gate/test', { context, repeats: Number(repeats) })
      .then(r => setTestResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }

  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">TimingGate 调试</h1>
          <p className="text-slate-500 text-sm">查看 raw 输出、解析结果、fallback 和延迟统计</p>
        </div>
        <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-6 gap-3 mb-4">
        <MiniStat label="总记录" value={stats.total || 0} />
        <MiniStat label="continue" value={stats.actions?.continue || 0} tone="emerald" />
        <MiniStat label="wait" value={stats.actions?.wait || 0} tone="amber" />
        <MiniStat label="no_reply" value={stats.actions?.no_reply || 0} />
        <MiniStat label="parse_error" value={stats.parse_error || 0} tone={stats.parse_error ? 'red' : 'slate'} />
        <MiniStat label="parse_error% " value={`${(stats.parse_error_ratio != null ? (stats.parse_error_ratio * 100).toFixed(1) : '0')}%`} />
        <MiniStat label="continue% " value={`${(stats.continue_ratio != null ? (stats.continue_ratio * 100).toFixed(1) : '0')}%`} tone="emerald" />
        <MiniStat label="wait% " value={`${(stats.wait_ratio != null ? (stats.wait_ratio * 100).toFixed(1) : '0')}%`} tone="amber" />
        <MiniStat label="no_reply% " value={`${(stats.no_reply_ratio != null ? (stats.no_reply_ratio * 100).toFixed(1) : '0')}%`} />
        <MiniStat label="avg 延迟" value={`${stats.avg_latency_ms || 0}ms`} />
        <MiniStat label="p95 延迟" value={`${stats.p95_latency_ms || 0}ms`} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <Card className="p-4 xl:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-slate-400">最近 TimingGate 记录</h2>
            <input value={groupId} onChange={e => setGroupId(e.target.value)} placeholder="按群号过滤"
              className="w-40 p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs" />
          </div>
          <TimingEventsTable rows={data.items || []} />
        </Card>

        <Card className="p-4">
          <h2 className="text-sm font-medium text-slate-400 mb-3">手动测试</h2>
          <textarea value={context} onChange={e => setContext(e.target.value)} rows={9}
            className="w-full p-3 rounded-xl bg-slate-950 border border-slate-700 font-mono text-xs mb-3" />
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-slate-500">次数</span>
            <input type="number" min="1" max="20" value={repeats} onChange={e => setRepeats(e.target.value)}
              className="w-20 p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs" />
            <button onClick={() => setRepeats(20)} className="px-2 py-1 bg-slate-800 rounded text-xs">20 次稳定性</button>
          </div>
          <button onClick={runTest} disabled={running}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">
            {running ? '运行中...' : '运行 TimingGate'}
          </button>
          {testResult && <JsonBlock value={testResult} className="mt-3 max-h-80" />}
        </Card>
      </div>
    </div>
  )
}

function TimingEventsTable({ rows = [] }) {
  const [expanded, setExpanded] = useState(null)
  if (!rows.length) return <div className="text-sm text-slate-600 py-10 text-center">暂无 TimingGate 记录</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-2">时间</th><th className="py-2 px-2">群</th><th className="py-2 px-2">触发消息</th><th className="py-2 px-2">action</th><th className="py-2 px-2">mode</th><th className="py-2 px-2">pending</th><th className="py-2 px-2">ctx_ch</th><th className="py-2 px-2">talk</th><th className="py-2 px-2">msg1/5m</th><th className="py-2 px-2">delay</th><th className="py-2 px-2">gen</th><th className="py-2 px-2">latency</th><th className="py-2 px-2">parse</th><th className="py-2 px-2">trigger</th><th className="py-2 px-2">fallback</th><th className="py-2 px-2">reason</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id} onClick={() => setExpanded(expanded === r.id ? null : r.id)}
              className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer align-top">
              <td className="py-2 px-2 whitespace-nowrap text-slate-500">{r.time}</td>
              <td className="py-2 px-2">{r.group_id}</td>
              <td className="py-2 px-2 max-w-[160px] truncate">{r.trigger_message}</td>
              <td className="py-2 px-2"><Badge tone={actionTone(r.action)}>{r.action || '-'}</Badge></td>
              <td className="py-2 px-2 text-slate-500">{r.mode || '-'}</td>
              <td className="py-2 px-2">{r.pending_count ?? '-'}</td>
              <td className="py-2 px-2 text-slate-500">{r.context_chars ?? '-'}</td>
              <td className="py-2 px-2">{r.talk_value != null ? Number(r.talk_value).toFixed(2) : '-'}</td>
              <td className="py-2 px-2 text-slate-500">{r.msg_1m ?? '-'}/{r.msg_5m ?? '-'}</td>
              <td className="py-2 px-2">{r.delay_seconds ?? '-'}</td>
              <td className="py-2 px-2">{r.generation ?? '-'}</td>
              <td className="py-2 px-2">{r.latency_ms ? `${r.latency_ms}ms` : '-'}</td>
              <td className="py-2 px-2">{r.parse_error ? <Badge tone="red">parse_error</Badge> : <span className="text-slate-600">ok</span>}</td>
              <td className="py-2 px-2 max-w-[120px] truncate text-slate-500">{r.trigger_reason || '-'}</td>
              <td className="py-2 px-2 max-w-[100px] truncate">{r.fallback_action || '-'}</td>
              <td className="py-2 px-2 max-w-[200px] truncate text-slate-400">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {expanded && <JsonBlock value={rows.find(r => r.id === expanded)} className="mt-3 max-h-96" />}
    </div>
  )
}

// ── Sticker Dedup ──
function StickerDedupPage() {
  const [data, setData] = useState({ groups: [] })
  const load = () => api.get('/stickers/duplicate-groups?limit=100').then(r => setData(r.data))
  useEffect(() => { load() }, [])

  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">去重工作台</h1>
          <p className="text-slate-500 text-sm">按 content_hash 分组，展示重复表情包</p>
        </div>
        <button onClick={load} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>
      </div>
      {(data.groups || []).map(g => (
        <Card key={g.content_hash} className="p-4 mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-slate-400">hash:</span>
            <code className="text-xs bg-slate-950 px-2 py-0.5 rounded">{g.content_hash}</code>
            <Badge tone="amber">{g.count} 个重复</Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="py-2 px-2">id</th><th className="py-2 px-2">预览</th><th className="py-2 px-2">名称</th><th className="py-2 px-2">描述</th><th className="py-2 px-2">状态</th><th className="py-2 px-2">dedupe</th><th className="py-2 px-2">使用次数</th><th className="py-2 px-2">preview</th><th className="py-2 px-2">describe</th>
              </tr></thead>
              <tbody>
                {(g.items || []).map(s => (
                  <tr key={s.id} className="border-b border-slate-800/50">
                    <td className="py-2 px-2">{s.id}</td>
                    <td className="py-2 px-2">{s.local_path ? <img src={`${API_BASE}/stickers/${s.id}/preview`} className="w-8 h-8 object-cover rounded" alt="" /> : '-'}</td>
                    <td className="py-2 px-2 max-w-[120px] truncate">{s.name || '-'}</td>
                    <td className="py-2 px-2 max-w-[200px] truncate">{s.description || '-'}</td>
                    <td className="py-2 px-2"><Badge tone={s.status === 'active' ? 'emerald' : s.status === 'disabled' ? 'amber' : 'slate'}>{s.status}</Badge></td>
                    <td className="py-2 px-2">{s.dedupe_status !== 'unique' ? <Badge tone="purple">{s.dedupe_status}</Badge> : <span className="text-slate-600">unique</span>}</td>
                    <td className="py-2 px-2">{s.usage_count}</td>
                    <td className="py-2 px-2"><Badge tone={s.preview_status === 'ok' ? 'emerald' : 'amber'}>{s.preview_status}</Badge></td>
                    <td className="py-2 px-2"><Badge tone={s.describe_status === 'ok' ? 'emerald' : s.describe_status === 'failed' ? 'red' : 'slate'}>{s.describe_status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  )
}

// ── Stickers ──
function StickersPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [search, setSearch] = useState('')
  const [sf, setSf] = useState('')
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
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-2 px-2 font-medium">流 ID</th><th className="py-2 px-2 font-medium">发言</th><th className="py-2 px-2 font-medium w-10">@</th><th className="py-2 px-2 font-medium w-10">E</th><th className="py-2 px-2 font-medium w-10">L</th><th className="py-2 px-2 font-medium w-10">J</th><th className="py-2 px-2 font-medium w-10">P</th><th className="py-2 px-2 font-medium">平滑</th><th className="py-2 px-2 font-medium"></th></tr></thead>
          <tbody>
            {data.items.map(c => (
              <tr key={c.chat_stream_id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                <td className="py-2 px-2 truncate max-w-[300px] text-xs text-slate-400">{c.chat_stream_id}</td>
                <td className="py-2 px-2">{c.talk_value}</td>
                <td className="py-2 px-2">{c.mentioned_bot_reply ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.use_expression ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.enable_expression_learning ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.enable_jargon_learning ? '✓' : '—'}</td>
                <td className="py-2 px-2">{c.enable_group_profile ? <Badge tone="emerald">✓</Badge> : '—'}</td>
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
    enable_jargon_learning: config.enable_jargon_learning,
    enable_group_profile: config.enable_group_profile || false,
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
        <label className="flex items-center gap-2 mb-2 text-sm text-slate-400">
          <input type="checkbox" checked={f.enable_group_profile}
            onChange={e => setF({ ...f, enable_group_profile: e.target.checked })} className="accent-emerald-500" />enable_group_profile（群画像注入）
        </label>
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

  const refreshFiles = () => api.get('/logs').then(r => setFiles(r.data.files))
  useEffect(() => { refreshFiles() }, [])

  const loadLog = (name, n = lines) => {
    setSel(name)
    api.get(`/logs/${encodeURIComponent(name)}?lines=${n}`).then(r => setContent(r.data.content))
  }

  const formatSize = (s) => s < 1024 ? `${s}B` : s < 1048576 ? `${(s/1024).toFixed(1)}KB` : `${(s/1048576).toFixed(1)}MB`

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">日志</h1>
        <button onClick={refreshFiles} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors">刷新列表</button>
      </div>
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
            <select value={lines} onChange={e => { const n = Number(e.target.value); setLines(n); if (sel) loadLog(sel, n) }}
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
  const [data, setData] = useState(null)
  const [prompt, setPrompt] = useState('用一句话回复：Nanobot 模型连通性测试')
  const [model, setModel] = useState('')
  const [jsonMode, setJsonMode] = useState(true)
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)

  const load = () => api.get('/models/status').then(r => setData(r.data))
  useEffect(() => { load() }, [])
  const run = () => {
    setRunning(true)
    api.post('/models/chat-test', { model, prompt, json_mode: jsonMode })
      .then(r => setResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }
  if (!data) return <Spinner />
  return (
    <div>
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">模型路由与测试</h1>
          <p className="text-slate-500 text-sm">主模型、快模型、TimingGate 和图片打标模型状态</p>
        </div>
        <Badge tone={data.api_key_configured ? 'emerald' : 'red'}>{data.api_key_configured ? 'NEW_API_KEY 已配置' : 'NEW_API_KEY 未配置'}</Badge>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <Card className="p-4">
          <h2 className="text-sm font-medium text-slate-400 mb-3">当前配置</h2>
          <div className="space-y-2">
            {(data.configured || []).map(item => (
              <div key={item.role} className="flex justify-between gap-3 text-sm">
                <span className="text-slate-500">{item.role}</span>
                <span className="truncate">{item.name || item.base_url || '-'}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-4 xl:col-span-2">
          <h2 className="text-sm font-medium text-slate-400 mb-3">连通性测试</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-3">
            <select value={model} onChange={e => setModel(e.target.value)} className="p-2 rounded-xl bg-slate-950 border border-slate-700 text-sm">
              <option value="">自动路由</option>
              {(data.models || []).map(m => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
            <label className="flex items-center gap-2 text-sm text-slate-400 px-2">
              <input type="checkbox" checked={jsonMode} onChange={e => setJsonMode(e.target.checked)} className="accent-emerald-500" />
              JSON 输出测试
            </label>
            <button onClick={run} disabled={running}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">
              {running ? '测试中...' : '测试普通聊天/JSON'}
            </button>
          </div>
          <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={3}
            className="w-full p-3 rounded-xl bg-slate-950 border border-slate-700 text-sm mb-3" />
          {result && <JsonBlock value={result} className="max-h-80" />}
        </Card>
      </div>

      <Card className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="px-3 py-2">模型</th><th className="px-3 py-2">tier</th><th className="px-3 py-2">base_url</th><th className="px-3 py-2">可用</th><th className="px-3 py-2">intel</th><th className="px-3 py-2">cost</th><th className="px-3 py-2">timeout</th><th className="px-3 py-2">最近错误</th></tr></thead>
          <tbody>{(data.models || []).map(m => (
            <tr key={m.name} className="border-b border-slate-800/50">
              <td className="px-3 py-2 font-mono max-w-[260px] truncate">{m.name}</td>
              <td className="px-3 py-2">{m.tier}</td>
              <td className="px-3 py-2 max-w-[260px] truncate text-slate-500">{m.base_url}</td>
              <td className="px-3 py-2"><Badge tone={m.available ? 'emerald' : 'red'}>{m.available ? '可用' : '不可用'}</Badge></td>
              <td className="px-3 py-2">{m.intelligence}</td>
              <td className="px-3 py-2">{m.cost_input_1m}</td>
              <td className="px-3 py-2">{m.timeout}s</td>
              <td className="px-3 py-2 text-red-300">{m.recent_error || '-'}</td>
            </tr>
          ))}</tbody>
        </table>
      </Card>
    </div>
  )
}

// ── Memory ──
function MemoryPage() {
  const [groupId, setGroupId] = useState('')
  const [memType, setMemType] = useState('')
  const [memories, setMemories] = useState([])
  const [loading, setLoading] = useState(false)

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
              <th className="py-2 px-3">id</th><th className="py-2 px-3">类型</th><th className="py-2 px-3">内容</th><th className="py-2 px-3">confidence</th><th className="py-2 px-3">证据数</th><th className="py-2 px-3">decay</th><th className="py-2 px-3">来源</th><th className="py-2 px-3">状态</th><th className="py-2 px-3">更新</th>
            </tr></thead>
            <tbody>
              {memories.map(m => (
                <tr key={m.id} className="border-b border-slate-800/50">
                  <td className="py-2 px-3 text-slate-500">{m.id}</td>
                  <td className="py-2 px-3"><Badge>{m.memory_type}</Badge></td>
                  <td className="py-2 px-3 max-w-[400px] truncate">{m.content}</td>
                  <td className="py-2 px-3">{Number(m.confidence).toFixed(2)}</td>
                  <td className="py-2 px-3">{m.evidence_count}</td>
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
    </div>
  )
}

// ── Audit ──
function AuditPage() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [page, setPage] = useState(1)
  const load = useCallback(() => {
    api.get('/audit-logs', { params: { page, limit: 50 } }).then(r => setData(r.data))
  }, [page])
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
