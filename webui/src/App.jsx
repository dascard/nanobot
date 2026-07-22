import React, { useState, useEffect, useCallback, useRef } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom'
import axios from 'axios'
import {
  Activity,
  BarChart3,
  Bot,
  Brain,
  Clock3,
  Database,
  FileText,
  Gauge,
  Home,
  Images,
  ListChecks,
  Menu,
  MessageSquare,
  Network,
  Radio,
  RefreshCw,
  Search,
  Settings,
  Shield,
  Tags,
  Users,
  Wrench,
  X,
} from 'lucide-react'

import { api } from './api'
import {
  AuthImage,
  Badge,
  Card,
  IconButton,
  JsonBlock,
  MiniStat,
  Modal,
  Pagination,
  Spinner,
} from './components/ui'
import { AgentRunDetailPage } from './features/agent-runs/AgentRunDetailPage'
import { AgentRunsPage } from './features/agent-runs/AgentRunsPage'
import { ToolCallsPage } from './features/agent-runs/ToolCallsPage'
import { LLMApiLogsPage } from './features/agent-runs/LLMApiLogsPage'
import { ModelRepliesTab } from './features/logs/ModelRepliesTab'
import { ModelsPage } from './features/models/ModelsPage'
import { PromptV2TemplatesPage, EffectivePromptPreviewPage } from './features/prompt/PromptPages'
import { ReplyEvalPage } from './features/reply-eval/ReplyEvalPage'
import { ToolsPage } from './features/tools/ToolsPage'
import { EvalsPage } from './features/evals/EvalsPage'
import { GeneratedImagesPage } from './features/generated-images/GeneratedImagesPage'
import { RagDebugPage } from './features/rag/RagDebugPage'
import { RagBenchmarkPage } from './features/rag/RagBenchmarkPage'
import { WebSearchPage } from './features/web-search/WebSearchPage'
import { ProactiveOutreachPage } from './features/proactive-outreach/ProactiveOutreachPage'
import { SessionConfigsPage } from './features/session-config/SessionConfigsPage'

function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (!item || typeof item !== 'object') return String(item)
      const loc = Array.isArray(item.loc) ? item.loc.join('.') : ''
      return [loc, item.msg || item.message || JSON.stringify(item)].filter(Boolean).join(': ')
    }).join('\n')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.error || JSON.stringify(detail)
  }
  return error?.message || fallback
}

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
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <div className="mx-auto flex min-h-screen w-full max-w-md items-center px-4">
        <form onSubmit={submit} className="w-full rounded-lg border border-slate-800 bg-slate-900 p-6 shadow-2xl shadow-black/30">
          <div className="mb-5 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-500/15 text-sm font-bold text-indigo-200">N</div>
            <div>
              <h1 className="text-lg font-semibold text-white">Nanobot Admin</h1>
              <p className="text-xs text-slate-500">管理后台访问令牌</p>
            </div>
          </div>
          {err && <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</div>}
          <label htmlFor="admin-token" className="mb-4 block text-xs font-medium text-slate-400">
            API 令牌
            <input
              id="admin-token"
              type="password"
              value={t}
              onChange={e => setT(e.target.value)}
              autoComplete="current-password"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none transition-colors focus:border-emerald-500"
            />
          </label>
          <button disabled={loading}
            className="w-full rounded-lg bg-emerald-600 p-2.5 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50">
            {loading ? '验证中...' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Layout ──
const NAV_SECTIONS = [
  {
    title: '总览',
    items: [
      { to: '/', label: '首页总览', end: true, icon: Home },
    ],
  },
  {
    title: '运行链路',
    items: [
      { to: '/agent-runs', label: '运行追踪', icon: Activity },
      { to: '/llm-api-logs', label: 'LLM API 日志', icon: Network },
      { to: '/rag-debug', label: 'RAG Debug', icon: Search },
      { to: '/rag-benchmark', label: 'RAG Benchmark', icon: BarChart3 },
      { to: '/reply-eval', label: 'Reply 测试', icon: ListChecks },
      { to: '/timing-gate', label: 'TimingGate', icon: Clock3 },
      { to: '/proactive-outreach', label: '主动外呼', icon: Radio },
      { to: '/logs', label: '日志', icon: FileText },
    ],
  },
  {
    title: 'Prompt',
    items: [
      { to: '/prompt-preview', label: '运行预览' },
      { to: '/prompt-templates', label: '模板' },
    ],
  },
  {
    title: '模型与工具',
    items: [
      { to: '/models', label: '模型', icon: Bot },
      { to: '/web-search', label: '搜索 API', icon: Search },
      { to: '/tools', label: '工具管理', icon: Wrench },
      { to: '/evals', label: 'Eval 评测', icon: BarChart3 },
    ],
  },
  {
    title: '数据治理',
    items: [
      { to: '/groups', label: '群聊运行', icon: Users },
      { to: '/memory', label: '群体记忆', icon: Brain },
      { to: '/persona', label: '用户画像', icon: Users },
      { to: '/generated-images', label: '生成图片', icon: Images },
      { to: '/stickers', label: '表情包', icon: Tags },
      { to: '/stickers/duplicates', label: '去重工作台', icon: Search },
      { to: '/db', label: '数据库', icon: Database },
    ],
  },
  {
    title: '系统',
    items: [
      { to: '/configs', label: '会话策略', icon: Gauge },
      { to: '/blocks', label: '屏蔽', icon: Shield },
      { to: '/settings', label: '设置', icon: Settings },
      { to: '/audit', label: '审计', icon: FileText },
    ],
  },
]
const NAV = NAV_SECTIONS.flatMap(section => section.items)

function NavContent({ version, onLogout, onNavigate }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-2 pb-4">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-500/15 text-sm font-bold text-indigo-200">N</div>
          <span className="text-sm font-semibold tracking-wide text-white">Nanobot</span>
        </div>
        <div className="mt-2 truncate font-mono text-[11px] text-slate-500" title={version?.full_commit || ''}>
          {version?.display ? `版本 ${version.display}` : '版本 unknown'}
        </div>
      </div>
      <div className="prompt-flow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {NAV_SECTIONS.map(section => (
          <div key={section.title}>
            <div className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">{section.title}</div>
            <div className="space-y-0.5">
              {section.items.map(n => {
                const Icon = n.icon || MessageSquare
                return (
                  <NavLink key={n.to} to={n.to} end={n.end} onClick={onNavigate}
                    className={({ isActive }) =>
                      `flex min-h-9 items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isActive ? 'bg-indigo-500/15 text-indigo-100 font-medium' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'}`}>
                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span className="truncate">{n.label}</span>
                  </NavLink>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      <button onClick={onLogout}
        className="mt-4 shrink-0 rounded-lg px-3 py-2 text-left text-sm text-slate-500 transition-colors hover:bg-red-500/10 hover:text-red-300">
        退出
      </button>
    </div>
  )
}

function Layout({ children, onLogout }) {
  const [version, setVersion] = useState(null)
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()
  useEffect(() => {
    api.get('/version').then(r => setVersion(r.data)).catch(() => setVersion(null))
  }, [])
  return (
    <div className="app-shell h-[100dvh] overflow-hidden bg-slate-950 text-slate-200 md:flex md:h-screen md:overflow-hidden">
      <aside className="app-sidebar hidden md:flex md:h-screen md:w-64 md:shrink-0 md:flex-col md:overflow-hidden md:border-r md:border-slate-800 md:bg-slate-900 md:p-4">
        <NavContent version={version} onLogout={onLogout} />
      </aside>

      <div className="app-mobile-header sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-slate-800 bg-slate-950/95 px-4 backdrop-blur md:hidden">
        <button type="button" aria-label="打开导航" onClick={() => setNavOpen(true)}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-slate-200">
          <Menu className="h-4 w-4" aria-hidden="true" />
        </button>
        <div className="min-w-0 text-sm font-semibold text-slate-100">Nanobot Admin</div>
        <div className="h-9 w-9" />
      </div>

      {navOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" aria-label="关闭导航遮罩" className="absolute inset-0 bg-black/60" onClick={() => setNavOpen(false)} />
          <nav aria-label={`主导航，共 ${NAV.length} 项`} className="relative flex h-full w-[min(82vw,20rem)] flex-col overflow-hidden border-r border-slate-800 bg-slate-900 p-4 shadow-2xl shadow-black">
            <div className="mb-4 flex items-center justify-between">
              <span className="text-sm font-semibold text-white">导航</span>
              <IconButton label="关闭导航" icon={X} onClick={() => setNavOpen(false)} />
            </div>
            <NavContent version={version} onLogout={onLogout} onNavigate={() => setNavOpen(false)} />
          </nav>
        </div>
      )}

      <div className="app-content min-h-0 min-w-0 flex-1 overflow-hidden">
        <main id="main-content" className="app-main-scroll h-[calc(100dvh-3.5rem)] min-w-0 overflow-y-auto overflow-x-hidden px-4 py-4 md:h-screen md:overflow-y-auto md:px-6 md:py-6">
          <ErrorBoundary key={location.pathname}>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  )
}

// ── Shared helpers ──
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
              <input type="number" min="1" max="5" value={repeats} onChange={e => setRepeats(e.target.value)}
                className="w-16 p-1.5 rounded bg-slate-950 border border-slate-700 text-xs" />
              <button onClick={() => setRepeats(5)} className="px-2 py-1 bg-slate-800 rounded text-xs">5次</button>
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
  const scoring = event.scoring || {}
  const signals = scoring.signals || {}
  const subSignals = signals.sub_signals || {}
  const hasScoring = Object.keys(scoring).length > 0
  const scoreValue = (value) => {
    if (value === null || value === undefined || value === '') return '-'
    const n = Number(value)
    return Number.isFinite(n) ? n.toFixed(3) : String(value)
  }
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
      <div className="rounded-lg border border-slate-800 bg-slate-950 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="text-xs font-medium text-slate-300">规则评分</div>
          <Badge tone={hasScoring ? 'blue' : 'slate'}>{scoring.stage || '暂无 scoring'}</Badge>
        </div>
        {hasScoring ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">E_rule</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.participation_score)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">E_final</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.final_score)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">theta</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.theta)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">band</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.low_threshold)} / {scoreValue(scoring.high_threshold)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">conflict</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.conflict_score)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">soft_cap</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.soft_reject_cap)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">delay</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.delay_seconds)}</div>
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs text-slate-500">信号分解</div>
              <div className="grid grid-cols-3 gap-1.5 text-[11px] text-slate-400">
                <div>d0: <span className="font-mono text-slate-200">{scoreValue(signals.explicit_direct_score)}</span></div>
                <div>linger: <span className="font-mono text-slate-200">{scoreValue(signals.linger_score)}</span></div>
                <div>linger_active: <span className="font-mono text-slate-200">{signals.linger_active === true ? 'yes' : signals.linger_active === false ? 'no' : '-'}</span></div>
                <div>linger_reply_count: <span className="font-mono text-slate-200">{scoreValue(signals.linger_reply_count)}</span></div>
                <div>linger_time_remaining: <span className="font-mono text-slate-200">{scoreValue(signals.linger_time_remaining)}</span></div>
                <div>d: <span className="font-mono text-slate-200">{scoreValue(signals.direct_score)}</span></div>
                <div>w: <span className="font-mono text-slate-200">{scoreValue(signals.wait_signal)}</span></div>
                <div>s: <span className="font-mono text-slate-200">{scoreValue(signals.suppress_score)}</span></div>
                <div>s_ack: <span className="font-mono text-slate-200">{scoreValue(subSignals.s_ack)}</span></div>
                <div>s_transport: <span className="font-mono text-slate-200">{scoreValue(subSignals.s_transport)}</span></div>
                <div>s_transport_tier: <span className="font-mono text-slate-200">{subSignals.s_transport_tier || '-'}</span></div>
                <div>s_other: <span className="font-mono text-slate-200">{scoreValue(subSignals.s_other)}</span></div>
                <div>s_bot: <span className="font-mono text-slate-200">{scoreValue(subSignals.s_bot)}</span></div>
                <div>w_marker: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_marker)}</span></div>
                <div>w_file: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_file)}</span></div>
                <div>w_incomplete: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_incomplete)}</span></div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
              <div>模型参与: <span className="text-slate-200">{scoring.model_used ? 'yes' : 'no'}</span></div>
              <div>model_action: <span className="text-slate-200">{scoring.model_action || '-'}</span></div>
              <div>model_confidence: <span className="font-mono text-slate-200">{scoreValue(scoring.model_confidence)}</span></div>
              <div>model_weight: <span className="font-mono text-slate-200">{scoreValue(scoring.model_weight)}</span></div>
            </div>
            {scoring.reason && <div className="text-[11px] leading-4 text-slate-500">{scoring.reason}</div>}
          </div>
        ) : (
          <div className="text-xs text-slate-600">旧记录或非 shadow 路径没有 scoring 字段。</div>
        )}
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <div className="text-xs text-slate-500">模型输入</div>
          <button onClick={onUseAsTest} className="px-2 py-0.5 bg-emerald-700/50 hover:bg-emerald-700 rounded text-[10px]">用此复测</button>
        </div>
        <pre className="rounded bg-slate-950 border border-slate-800 p-2 text-[10px] whitespace-pre-wrap max-h-48 overflow-auto">{contextText || '(无)'}</pre>
      </div>
      <div><div className="text-xs text-slate-500 mb-1">raw</div><pre className="rounded bg-slate-950 border border-slate-800 p-2 text-[10px] whitespace-pre-wrap max-h-32 overflow-auto">{event.raw || '-'}</pre></div>
      {hasScoring && <JsonBlock value={scoring} className="max-h-40" />}
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
  const [nearError, setNearError] = useState('')
  const [scanLoading, setScanLoading] = useState(false)
  const navigate = useNavigate()

  const loadNear = () => api.get('/stickers/near-duplicate-candidates?limit=100')
    .then(r => { setNearDuplicates(r.data.items || []); setNearError('') })
    .catch(e => setNearError(formatApiError(e)))

  const load = useCallback(() => api.get('/stickers/duplicate-groups?limit=100')
    .then(r => {
      const groups = r.data?.groups || []
      setData(r.data || {})
      setError('')
      setSelectedGroup(current => current || groups[0] || null)
    })
    .catch(e => { setError(formatApiError(e, '加载失败')) }), [])
  useEffect(() => {
    const timer = window.setTimeout(() => { load() }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const doAction = (stickerId, action, body = {}) => {
    api.post(`/stickers/${stickerId}/${action}`, body)
      .then(() => load())
      .catch(e => alert(formatApiError(e)))
  }

  const runBackfill = () => {
    if (!confirm('将对全库 content_hash 重复分组执行精确去重，确定？')) return
    api.post('/stickers/dedupe/exact/backfill')
      .then(r => { alert(`完成：${r.data.total_groups} 组, ${r.data.total_duplicates} 个标记`); load() })
      .catch(e => alert(formatApiError(e)))
  }

  const runPhashBackfill = () => {
    api.post('/stickers/phash/backfill?limit=200')
      .then(r => alert(`phash 补建: ${r.data.ok} OK / ${r.data.skipped} skip`))
      .catch(e => alert(formatApiError(e)))
  }

  const runNearScan = () => {
    if (scanLoading) return
    setScanLoading(true)
    setNearError('')
    api.post('/stickers/near-duplicate/scan?limit=100')
      .then(r => { alert(`扫描完成: ${r.data.candidates_created} 个候选`); loadNear() })
      .catch(e => { const msg = formatApiError(e); setNearError(msg); alert(msg) })
      .finally(() => setScanLoading(false))
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
          <button onClick={runPhashBackfill}
            className="px-3 py-1.5 bg-slate-700/50 hover:bg-slate-700 rounded-lg text-xs">phash 补建</button>
          <button onClick={runNearScan} disabled={scanLoading}
            className="px-3 py-1.5 bg-purple-700/50 hover:bg-purple-700 disabled:opacity-50 rounded-lg text-xs">{scanLoading ? '扫描中...' : '扫描疑似重复'}</button>
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
                              <IconButton label="重试预览" icon={RefreshCw} size="xs" onClick={() => doAction(s.id, 'preview/retry')} />
                              <IconButton label="重试打标" icon={Tags} size="xs" onClick={() => doAction(s.id, 'redescribe')} />
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
          {nearError && <div className="mb-3 rounded-lg border border-red-800 bg-red-900/20 px-3 py-2 text-xs text-red-300 whitespace-pre-wrap">{nearError}</div>}
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
                      <button onClick={() => api.post(`/stickers/near-duplicate-candidates/${r.id}/confirm`).then(loadNear).catch(e => alert(formatApiError(e)))}
                        className="px-1.5 py-0.5 bg-emerald-700/40 rounded text-[10px]">确认</button>
                      <button onClick={() => api.post(`/stickers/near-duplicate-candidates/${r.id}/ignore`).then(loadNear).catch(e => alert(formatApiError(e)))}
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
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
        <table className="min-w-[520px] w-full text-sm">
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
        </div>
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
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
        <table className="min-w-[520px] w-full text-sm">
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
        </div>
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
                    className={`${s.value_type === 'str' ? 'w-64 text-left' : 'w-28 text-center'} p-2 rounded-xl bg-slate-900 border border-slate-700 text-sm ${s.readonly ? 'opacity-50 cursor-not-allowed' : ''}`}
                    onBlur={e => {
                      const v = e.target.value.trim()
                      if (v === String(s.value ?? '')) return
                      if (s.value_type === 'str') { update(s.key, v); return }
                      if (!v) return
                      const p = s.value_type === 'float' ? parseFloat(v) : parseInt(v)
                      if (Number.isNaN(p)) { e.target.value = s.value; return }
                      update(s.key, p)
                    }} />
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
function DbCell({ value, meta, tableName, column, onExpand }) {
  const display = value === null || value === undefined ? '' : String(value)
  const kind = meta?.kind || 'value'
  const canExpand = Boolean(meta?.truncated)
  const tone = kind === 'redacted' ? 'text-red-300' : kind === 'binary' ? 'text-amber-300' : 'text-slate-300'
  if (canExpand) {
    return (
      <button
        type="button"
        title="展开预览"
        onClick={() => onExpand({ tableName, column, value: display, meta })}
        className={`block w-full max-w-[280px] truncate text-left underline decoration-dotted underline-offset-2 ${tone}`}
      >
        {display}
      </button>
    )
  }
  return <span title={display} className={`block max-w-[280px] truncate ${tone}`}>{display || '-'}</span>
}

function DbResultTable({ data, tableName, onExpand }) {
  const columns = data?.columns || []
  const rows = data?.rows || []
  const cellMeta = data?.cell_meta || []
  if (!columns.length) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-xs">
        <thead>
          <tr>
            {columns.map(c => <th key={c} className="w-[180px] px-3 py-2 text-left font-medium text-slate-500">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-800/50 hover:bg-slate-800/30">
              {columns.map(c => (
                <td key={c} className="px-3 py-1.5 align-top">
                  <DbCell value={r[c]} meta={cellMeta[i]?.[c]} tableName={tableName} column={c} onExpand={onExpand} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DbPage() {
  const [tables, setTables] = useState([])
  const [groups, setGroups] = useState([])
  const [tableMeta, setTableMeta] = useState({})
  const [tableSearch, setTableSearch] = useState('')
  const [sel, setSel] = useState('')
  const [rows, setRows] = useState({ columns: [], rows: [], total: 0, page: 1, limit: 50, has_next: false })
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(50)
  const [loadingTable, setLoadingTable] = useState(false)
  const [expandedCell, setExpandedCell] = useState(null)
  const [sql, setSql] = useState('')
  const [sqlResult, setSqlResult] = useState(null)
  useEffect(() => {
    api.get('/db/tables').then(r => {
      const nextTables = r.data.tables || []
      setTables(nextTables)
      setGroups(r.data.groups || [{ key: 'all', label: '全部表', tables: nextTables }])
      setTableMeta(r.data.table_meta || {})
    })
  }, [])
  const queryTable = (t, nextPage = 1, nextLimit = limit) => {
    setSel(t)
    setPage(nextPage)
    setLimit(nextLimit)
    setLoadingTable(true)
    api.get(`/db/tables/${t}`, { params: { page: nextPage, limit: nextLimit } })
      .then(r => setRows(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setLoadingTable(false))
  }
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
  const normalizedSearch = tableSearch.trim().toLowerCase()
  const filteredGroups = groups.map(g => ({
    ...g,
    tables: (g.tables || []).filter(t => {
      const meta = tableMeta[t] || {}
      return !normalizedSearch || t.toLowerCase().includes(normalizedSearch) || String(meta.description || '').toLowerCase().includes(normalizedSearch)
    }),
  })).filter(g => g.tables.length)
  const selectedMeta = tableMeta[sel] || rows.table_meta || {}
  const runSql = () => {
    api.post('/db/query', { query: sql })
      .then(r => setSqlResult(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }
  return (
    <div>
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold">数据库浏览</h1>
          <p className="text-sm text-slate-500">只读白名单表浏览，敏感列按后端策略预览或脱敏。</p>
        </div>
        <button onClick={backupDb} className="inline-flex items-center justify-center rounded-lg bg-slate-700 px-3 py-1.5 text-xs hover:bg-slate-600">下载备份</button>
      </div>
      <Card className="mb-4 p-3">
        <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="relative min-w-0 md:w-80">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-500" />
            <input
              value={tableSearch}
              onChange={e => setTableSearch(e.target.value)}
              placeholder="搜索表"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-8 pr-3 text-sm outline-none focus:border-emerald-500"
            />
          </div>
          <div className="text-xs text-slate-500">{tables.length} 张白名单表</div>
        </div>
        <div className="space-y-3">
          {filteredGroups.map(group => (
            <div key={group.key}>
              <div className="mb-1 text-[11px] font-medium text-slate-500">{group.label}</div>
              <div className="flex flex-wrap gap-1.5">
                {group.tables.map(t => <button key={t} onClick={() => queryTable(t, 1, limit)}
                  title={tableMeta[t]?.description || t}
                  className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${sel === t ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>{t}</button>)}
              </div>
            </div>
          ))}
        </div>
      </Card>
      {sel && (
        <Card className="mb-4">
          <div className="flex flex-col gap-3 border-b border-slate-800 p-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-medium text-slate-200">{sel}</div>
              <div className="mt-0.5 text-xs text-slate-500">{selectedMeta.description || '只读数据表'} · {rows.total || 0} rows</div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <select value={limit} onChange={e => queryTable(sel, 1, Number(e.target.value))}
                className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-300">
                {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}/页</option>)}
              </select>
              <button onClick={() => queryTable(sel, Math.max(1, page - 1), limit)} disabled={page <= 1 || loadingTable}
                className="rounded-lg bg-slate-800 px-3 py-1.5 text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-40">上一页</button>
              <span className="min-w-14 text-center text-slate-500">第 {page} 页</span>
              <button onClick={() => queryTable(sel, page + 1, limit)} disabled={!rows.has_next || loadingTable}
                className="rounded-lg bg-slate-800 px-3 py-1.5 text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-40">下一页</button>
            </div>
          </div>
          {loadingTable ? <Spinner /> : <DbResultTable data={rows} tableName={sel} onExpand={setExpandedCell} />}
        </Card>
      )}
      <Card className="p-4">
        <h2 className="mb-1 text-sm font-medium text-slate-300">SQL 查询 (只读)</h2>
        <p className="mb-2 text-xs text-slate-500">仅允许查询后端白名单表；结果会应用同一套脱敏、BLOB 占位和截断预览策略。</p>
        <textarea value={sql} onChange={e => setSql(e.target.value)} rows={3} placeholder="SELECT ..."
          className="mb-2 w-full rounded-xl border border-slate-700 bg-slate-950 p-3 font-mono text-sm outline-none focus:border-emerald-500" />
        <button onClick={runSql}
          className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500">运行</button>
        {sqlResult && (
          <div className="mt-3">
            <div className="mb-1 text-xs text-slate-500">{sqlResult.row_count} rows</div>
            <DbResultTable data={sqlResult} tableName="sql_query" onExpand={setExpandedCell} />
          </div>
        )}
      </Card>
      {expandedCell && (
        <Modal wide onClose={() => setExpandedCell(null)}>
          <div className="flex items-start justify-between border-b border-slate-800 p-4">
            <div>
              <div className="text-sm font-medium text-slate-200">展开预览</div>
              <div className="mt-1 text-xs text-slate-500">{expandedCell.tableName}.{expandedCell.column} · {expandedCell.meta?.full_length || 0} 字符</div>
            </div>
            <IconButton label="关闭" icon={X} size="xs" onClick={() => setExpandedCell(null)} />
          </div>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words p-4 text-xs leading-relaxed text-slate-300">{expandedCell.value}</pre>
        </Modal>
      )}
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
  const [errorContext, setErrorContext] = useState(false)
  const [logEvents, setLogEvents] = useState([])
  const [follow, setFollow] = useState(false)
  const [fileSize, setFileSize] = useState(0)
  const preRef = useRef(null)

  const refreshFiles = () => api.get('/logs').then(r => setFiles(r.data.files))
  useEffect(() => { refreshFiles() }, [])

  const loadLog = (name, n = lines, lv = logLevel, q = searchQ, grouped = errorContext) => {
    setSel(name)
    setFollow(false)
    setFileSize(0)
    const params = { lines: n }
    if (grouped) {
      params.level = 'ERROR'
      params.group_errors = true
      params.context_before = 5
      params.context_after = 8
    } else if (lv) params.level = lv
    if (q) params.q = q
    api.get(`/logs/${encodeURIComponent(name)}`, { params }).then(r => {
      setContent(r.data.content)
      setLogEvents(r.data.events || [])
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
              <select value={lines} onChange={e => { const n = e.target.value === 'all' ? 'all' : Number(e.target.value); setLines(n); if (sel) loadLog(sel, n) }}
                className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
                <option value="100">100</option><option value="200">200</option><option value="500">500</option><option value="1000">1000</option><option value="all">所有</option>
              </select>
              <select value={logLevel} onChange={e => { setLogLevel(e.target.value); if (sel) loadLog(sel, lines, e.target.value, searchQ) }}
                className="p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs">
                <option value="">全部级别</option>
                <option value="ERROR">ERROR</option><option value="WARNING">WARNING</option><option value="INFO">INFO</option><option value="DEBUG">DEBUG</option>
              </select>
              <input value={searchQ} onChange={e => setSearchQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && sel && loadLog(sel, lines, logLevel, searchQ)}
                placeholder="搜索..." className="w-40 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
              {sel && <button onClick={() => loadLog(sel)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">刷新</button>}
              {sel && <button onClick={() => { const next = !errorContext; setErrorContext(next); loadLog(sel, lines, logLevel, searchQ, next) }}
                className={`px-3 py-1 rounded-lg text-xs ${errorContext ? 'bg-red-600 text-white hover:bg-red-500' : 'bg-slate-700 hover:bg-slate-600'}`}>ERROR 上下文</button>}
              {sel && (
                <button
                  onClick={() => follow ? setFollow(false) : startFollow(sel)}
                  className={`px-3 py-1 rounded-lg text-xs transition-colors ${follow ? 'bg-emerald-600 hover:bg-emerald-700 text-white' : 'bg-slate-700 hover:bg-slate-600'}`}>
                  {follow ? '⏸ 停止跟随' : '▶ 跟随'}
                </button>
              )}
              {follow && <span className="text-xs text-emerald-400">实时 {formatSize(fileSize)}</span>}
            </div>
            {logEvents.length > 0 ? (
              <div ref={preRef} className="flex-1 space-y-3 overflow-auto rounded-xl border border-slate-800 bg-slate-950 p-3">
                {logEvents.map((event, idx) => (
                  <details key={`${event.line_start}-${idx}`} open className="rounded-lg border border-red-500/20 bg-red-500/5">
                    <summary className="cursor-pointer px-3 py-2 text-xs text-red-300">ERROR #{idx + 1} · lines {event.line_start}-{event.line_end}</summary>
                    <pre className="whitespace-pre-wrap border-t border-red-500/10 p-3 text-xs leading-relaxed text-slate-300">{[...(event.before_lines || []), ...(event.event_lines || []), ...(event.after_lines || [])].join('\n')}</pre>
                  </details>
                ))}
              </div>
            ) : (
              <pre ref={preRef} className="flex-1 p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs leading-relaxed overflow-auto text-slate-300 font-mono whitespace-pre-wrap">{content || '点击左侧文件查看'}</pre>
            )}
          </div>
        </div>
      ) : (
        <ModelRepliesTab />
      )}
    </div>
  )
}


function SessionSummaryBrowser({ mode }) {
  const [sessions, setSessions] = useState([])
  const [selectedSession, setSelectedSession] = useState('')
  const [items, setItems] = useState([])
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(false)
  const [operationLoading, setOperationLoading] = useState('')
  const [operationError, setOperationError] = useState('')
  const [includeContent, setIncludeContent] = useState(false)
  const [includeArchived, setIncludeArchived] = useState(false)
  const [query, setQuery] = useState('')
  const isRecent = mode === 'recent'

  const loadSessions = useCallback(() => {
    return api.get('/session-memory/sessions', { params: { session_limit: 100, kind: isRecent ? 'recent' : 'long' } })
      .then(r => {
        const next = r.data.items || []
        setSessions(next)
        setSelectedSession(current => (
          current && next.some(item => item.session_id === current)
            ? current
            : (next[0]?.session_id || '')
        ))
      })
      .catch(() => setSessions([]))
  }, [isRecent])

  const loadDetail = useCallback((
    sessionId = selectedSession,
    full = includeContent,
    archived = includeArchived,
  ) => {
    if (!sessionId) return
    setLoading(true)
    setOperationError('')
    const endpoint = isRecent
      ? `/session-memory/sessions/${encodeURIComponent(sessionId)}/summaries`
      : `/session-memory/sessions/${encodeURIComponent(sessionId)}/digests`
    const params = isRecent
      ? { summary_limit_per_session: 50, include_content: full, include_archived: archived }
      : { digest_limit_per_session: 80, include_content: full, include_archived: archived }
    const detailRequest = api.get(endpoint, { params })
      .then(r => setItems(r.data.items || []))
      .catch(e => { setItems([]); setOperationError(formatApiError(e)) })
    const jobsRequest = isRecent
      ? api.get(`/session-memory/${encodeURIComponent(sessionId)}/rolling-summary`)
        .then(r => setJobs(r.data.jobs || []))
        .catch(() => setJobs([]))
      : Promise.resolve(setJobs([]))
    Promise.allSettled([detailRequest, jobsRequest])
      .finally(() => setLoading(false))
  }, [includeArchived, includeContent, isRecent, selectedSession])

  useEffect(() => { loadSessions() }, [loadSessions])
  useEffect(() => {
    const timer = window.setTimeout(() => { loadDetail() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadDetail])

  const filtered = sessions.filter(s => {
    const needle = query.trim().toLowerCase()
    if (!needle) return true
    return String(s.session_id || '').toLowerCase().includes(needle) ||
      String(s.user_id || '').toLowerCase().includes(needle)
  })
  const selectedSessionInfo = sessions.find(item => item.session_id === selectedSession) || null

  const refreshAfterOperation = useCallback(() => {
    return Promise.allSettled([
      loadSessions(),
      selectedSession ? loadDetail(selectedSession, includeContent) : Promise.resolve(),
    ])
  }, [includeContent, loadDetail, loadSessions, selectedSession])

  const regenerateRecentSummary = useCallback(() => {
    if (!selectedSession) return
    const hasSummary = Number(selectedSessionInfo?.summary_count || 0) > 0
    setOperationLoading('recent')
    setOperationError('')
    const chatType = selectedSessionInfo?.chat_type || (selectedSession.startsWith('group_') ? 'group' : 'private')
    const userId = selectedSessionInfo?.user_id || selectedSession
    const request = hasSummary
      ? api.post(`/session-memory/${encodeURIComponent(selectedSession)}/rolling-summary/enqueue-llm`, { force: true, chat_type: chatType, user_id: userId })
      : api.post(`/session-memory/${encodeURIComponent(selectedSession)}/rolling-summary/run`, { force: true, dry_run: false, chat_type: chatType, user_id: userId })
    request
      .then(() => refreshAfterOperation())
      .catch(e => setOperationError(formatApiError(e)))
      .finally(() => setOperationLoading(''))
  }, [refreshAfterOperation, selectedSession, selectedSessionInfo])

  const regenerateLongDigest = useCallback(() => {
    if (!selectedSession) return
    setOperationLoading('long')
    setOperationError('')
    api.post(`/session-memory/${encodeURIComponent(selectedSession)}/digests/run`, {
      force: true,
      target_date: selectedSessionInfo?.latest_digest_date || '',
    })
      .then(() => refreshAfterOperation())
      .catch(e => setOperationError(formatApiError(e)))
      .finally(() => setOperationLoading(''))
  }, [refreshAfterOperation, selectedSession, selectedSessionInfo])

  const retrySummaryJob = useCallback((jobId) => {
    setOperationLoading(`retry-${jobId}`)
    setOperationError('')
    api.post(`/session-memory/jobs/${jobId}/retry`)
      .then(() => loadDetail(selectedSession, includeContent))
      .catch(e => setOperationError(formatApiError(e)))
      .finally(() => setOperationLoading(''))
  }, [includeContent, loadDetail, selectedSession])

  return (
    <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="min-h-[560px] overflow-hidden">
        <div className="border-b border-slate-800 p-3">
          <label className="block text-[11px] font-medium text-slate-400">
            session_id
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索 session_id / user_id"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
          </label>
        </div>
        <div className="max-h-[620px] overflow-y-auto">
          {filtered.map(s => {
            const count = isRecent ? s.summary_count : s.digest_count
            const preview = isRecent ? s.active_summary_preview : s.latest_digest_preview
            const emptyPreview = isRecent ? '无近期摘要预览' : '无长期摘要预览'
            const rangeText = isRecent
              ? `turn_start ${s.oldest_turn_index || 0} · turn_end ${s.latest_turn_index || 0}`
              : `digest_date ${s.latest_digest_date || '-'} · latest ${s.latest_digest_created_at || '-'}`
            return (
            <button key={s.session_id} onClick={() => { setSelectedSession(s.session_id); loadDetail(s.session_id) }}
              className={`w-full border-b border-slate-800/70 px-3 py-3 text-left transition-colors ${selectedSession === s.session_id ? 'bg-emerald-500/10' : 'hover:bg-slate-800/50'}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-mono text-sm text-slate-100">{s.session_id}</div>
                  <div className="mt-0.5 text-[11px] text-slate-500">{s.chat_type || '-'} · {s.user_id || '-'}</div>
                </div>
                <Badge tone={isRecent ? 'blue' : 'emerald'}>{count}</Badge>
              </div>
              <div className="mt-2 text-[11px] text-slate-500">
                {rangeText}
              </div>
              <div className="mt-1 truncate text-[11px] text-slate-500">{preview || emptyPreview}</div>
            </button>
            )
          })}
          {filtered.length === 0 && <div className="px-4 py-10 text-center text-xs text-slate-600">没有摘要 session</div>}
        </div>
      </Card>

      <div className="min-w-0 space-y-3">
        <Card className="p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="font-mono text-sm text-slate-200">{selectedSession || '未选择 session'}</div>
              <div className="mt-1 text-xs text-slate-500">{isRecent ? '近期摘要 rolling_session_summaries' : '长期摘要 memory_digests'}</div>
            </div>
            <div className="flex flex-wrap gap-2">
              {isRecent ? (
                <button onClick={regenerateRecentSummary} disabled={!selectedSession || operationLoading === 'recent'}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white hover:bg-indigo-500 disabled:opacity-50">
                  <RefreshCw className={`h-3.5 w-3.5 ${operationLoading === 'recent' ? 'animate-spin' : ''}`} />
                  {Number(selectedSessionInfo?.summary_count || 0) > 0 ? '重新生成 LLM 摘要' : '生成近期摘要'}
                </button>
              ) : (
                <button onClick={regenerateLongDigest} disabled={!selectedSession || operationLoading === 'long'}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white hover:bg-indigo-500 disabled:opacity-50">
                  <RefreshCw className={`h-3.5 w-3.5 ${operationLoading === 'long' ? 'animate-spin' : ''}`} />
                  重新生成长期摘要
                </button>
              )}
              <button onClick={() => { const next = !includeContent; setIncludeContent(next); loadDetail(selectedSession, next) }}
                className={`rounded-lg px-3 py-2 text-xs ${includeContent ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-200 hover:bg-slate-700'}`}>
                {includeContent ? '隐藏全文' : '展开全文'}
              </button>
              <button onClick={() => setIncludeArchived(value => !value)}
                className={`rounded-lg px-3 py-2 text-xs ${includeArchived ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-200 hover:bg-slate-700'}`}>
                {includeArchived ? '隐藏归档' : '显示归档'}
              </button>
              <button onClick={() => loadDetail()} className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700">刷新</button>
            </div>
          </div>
          {operationError && (
            <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{operationError}</div>
          )}
          {isRecent && jobs.length > 0 && (
            <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/70 p-2">
              <div className="mb-2 text-[11px] font-medium text-slate-500">LLM 摘要任务</div>
              <div className="space-y-1">
                {jobs.slice(0, 5).map(job => (
                  <div key={job.id} className="flex flex-wrap items-center justify-between gap-2 rounded bg-slate-900 px-2 py-1.5 text-xs text-slate-400">
                    <div className="min-w-0">
                      <span className="font-mono text-slate-300">job {job.id}</span>
                      <span className="ml-2">{job.status}</span>
                      <span className="ml-2">turn {job.covered_from_turn_id}-{job.covered_until_turn_id}</span>
                      {job.error && <span className="ml-2 text-red-300">{job.error}</span>}
                    </div>
                    {job.status === 'failed' && (
                      <button onClick={() => retrySummaryJob(job.id)} disabled={operationLoading === `retry-${job.id}`}
                        className="inline-flex items-center gap-1 rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700 disabled:opacity-50">
                        <RefreshCw className={`h-3 w-3 ${operationLoading === `retry-${job.id}` ? 'animate-spin' : ''}`} />
                        重试失败摘要任务
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
        {loading ? <Spinner /> : items.length === 0 ? (
          <div className="rounded-lg border border-slate-800 py-16 text-center text-sm text-slate-600">当前 session 没有摘要</div>
        ) : items.map(item => (
          <Card key={isRecent ? item.summary_id : (item.source_id || item.digest_id)} className="p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <Badge tone={isRecent ? 'blue' : 'emerald'}>{isRecent ? `summary ${item.summary_id}` : `摘要 ${item.digest_id}`}</Badge>
                {isRecent ? <Badge tone={item.summary_kind === 'deterministic_fallback' ? 'amber' : 'emerald'}>{item.summary_kind === 'deterministic_fallback' ? '代码兜底' : 'LLM 摘要'}</Badge> : <Badge>层级 {(item.levels || [item.level]).join('/')}</Badge>}
                {isRecent && <Badge>{item.summary_kind}</Badge>}
                {isRecent && <Badge tone={item.is_active ? 'emerald' : item.is_archived ? 'slate' : 'amber'}>{item.is_active ? 'active' : item.is_archived ? 'archived' : 'inactive'}</Badge>}
                {!isRecent && <Badge>{item.status}</Badge>}
                {!isRecent && <Badge>{item.layer_count || 1} 行合并</Badge>}
              </div>
              <div className="text-[11px] text-slate-500">{item.updated_at || item.created_at || '-'}</div>
            </div>
            {isRecent ? (
              <div className="mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">turn_start {item.turn_start}</div>
                <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">turn_end {item.turn_end}</div>
                <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">quality {Number(item.quality_score || 0).toFixed(2)}</div>
                <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">llm {item.llm_status || '-'}</div>
              </div>
            ) : (
              <>
                <div className="mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">digest_date {item.digest_date || '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">summary_type {item.summary_type || '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">generator {item.generator || '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">quality_score {Number(item.quality_score || 0).toFixed(2)}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">recall_card_count {item.recall_card_count ?? '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">message_count {item.message_count ?? '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">source_start_log_id {item.source_start_log_id || '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">source_end_log_id {item.source_end_log_id || '-'}</div>
                  <div className="rounded bg-slate-950 px-2 py-1 text-slate-400">parent {item.parent_id || '-'}</div>
                </div>
                <div className="mb-2 grid gap-2 text-xs md:grid-cols-2">
                  <div className="min-w-0 rounded bg-slate-950 px-2 py-1 text-slate-400">
                    <span className="text-slate-500">source_id </span>
                    <span className="break-all font-mono text-slate-300">{item.source_id || '-'}</span>
                  </div>
                  <div className="min-w-0 rounded bg-slate-950 px-2 py-1 text-slate-400">
                    <span className="text-slate-500">source_range </span>
                    <span className="break-all font-mono text-slate-300">{item.source_range || '-'}</span>
                  </div>
                  <div className="min-w-0 rounded bg-slate-950 px-2 py-1 text-slate-400">
                    <span className="text-slate-500">prompt_template </span>
                    <span className="break-all font-mono text-slate-300">{item.prompt_template || '-'}</span>
                  </div>
                  <div className="min-w-0 rounded bg-slate-950 px-2 py-1 text-slate-400">
                    <span className="text-slate-500">fallback_reason </span>
                    <span className={item.fallback_reason ? 'break-all text-amber-300' : 'text-slate-300'}>{item.fallback_reason || '-'}</span>
                  </div>
                </div>
                <details className="mb-2">
                  <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">prompt_version</summary>
                  <JsonBlock value={item.prompt_version || {}} className="mt-2 max-h-40" />
                </details>
              </>
            )}
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-300">{item.content || item.preview || '-'}</pre>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">raw_json</summary>
              <JsonBlock value={item.raw_json} className="mt-2 max-h-64" />
            </details>
            {!isRecent && Array.isArray(item.layers) && item.layers.length > 0 && (
              <details className="mt-3" open>
                <summary className="cursor-pointer text-xs font-medium text-slate-400 hover:text-slate-200">
                  L0 / L1 / L2 子层级（{item.layers.length}）
                </summary>
                <div className="mt-2 space-y-2">
                  {item.layers.map(layer => (
                    <div key={layer.digest_id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <Badge>digest {layer.digest_id}</Badge>
                        <Badge tone={layer.level === 2 ? 'blue' : layer.level === 1 ? 'emerald' : 'slate'}>L{layer.level}</Badge>
                        <span>{layer.summary_type || '-'}</span>
                        {layer.parent_id && <span>parent {layer.parent_id}</span>}
                      </div>
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-300">{layer.content || layer.preview || '-'}</pre>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}

// ── Memory ──
function MemoryPage() {
  const [memoryTab, setMemoryTab] = useState('group')
  const [groupId, setGroupId] = useState('')
  const [memType, setMemType] = useState('')
  const [overview, setOverview] = useState([])
  const [memories, setMemories] = useState([])
  const [overviewLoading, setOverviewLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [windowHours, setWindowHours] = useState(24)
  const [instructions, setInstructions] = useState('')
  const [lastExtractResult, setLastExtractResult] = useState(null)
  const [expandedEvidence, setExpandedEvidence] = useState(null)
  const [injectionPreview, setInjectionPreview] = useState(null)
  const [injectionLoading, setInjectionLoading] = useState(false)
  const [memoryUpdatingId, setMemoryUpdatingId] = useState(null)
  const memoryLoadKeyRef = useRef('')

  const loadOverview = useCallback(() => {
    setOverviewLoading(true)
    return api.get('/group-memories/overview')
      .then(r => setOverview(r.data.items || []))
      .finally(() => setOverviewLoading(false))
  }, [])

  const load = useCallback((targetGroupId = groupId) => {
    const target = String(targetGroupId || '').trim()
    if (!target) return Promise.resolve()
    setLoading(true)
    const params = memType ? { memory_type: memType } : {}
    const loadKey = `${target}|${memType || ''}`
    memoryLoadKeyRef.current = loadKey
    return api.get(`/group-memories/${encodeURIComponent(target)}/items`, { params })
      .then(r => setMemories(r.data.memories || []))
      .catch(e => {
        if (memoryLoadKeyRef.current === loadKey) memoryLoadKeyRef.current = ''
        throw e
      })
      .finally(() => setLoading(false))
  }, [groupId, memType])

  useEffect(() => {
    const timer = window.setTimeout(() => { loadOverview() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadOverview])

  const exactOverviewGroup = overview.find(item => {
    const q = groupId.trim()
    return q && (item.group_id === q || item.raw_group_id === q || item.stream_id === q)
  })

  useEffect(() => {
    if (!exactOverviewGroup || extracting) return
    const loadKey = `${exactOverviewGroup.group_id}|${memType || ''}`
    if (memoryLoadKeyRef.current === loadKey) return
    load(exactOverviewGroup.group_id)
  }, [exactOverviewGroup, extracting, load, memType])

  const handleGroupIdChange = value => {
    setGroupId(value)
    setExpandedEvidence(null)
    setLastExtractResult(null)
    setInjectionPreview(null)
    setMemories([])
    memoryLoadKeyRef.current = ''
  }

  const selectGroup = item => {
    setGroupId(item.group_id)
    setExpandedEvidence(null)
    setLastExtractResult(null)
    setInjectionPreview(null)
    load(item.group_id)
  }

  const enableInjection = async () => {
    if (!groupId || injectionLoading) return
    setInjectionLoading(true)
    try {
      const r = await api.put(`/group-memories/${encodeURIComponent(groupId)}/injection-config`, {
        group_profile_mode: 'on',
      })
      setInjectionPreview({ ...(injectionPreview || {}), group_profile_mode: r.data.group_profile_mode, chat_stream_id: r.data.chat_stream_id })
      await loadOverview()
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setInjectionLoading(false)
    }
  }

  const previewInjection = async () => {
    if (!groupId || injectionLoading) return
    setInjectionLoading(true)
    try {
      const r = await api.post(`/group-memories/${encodeURIComponent(groupId)}/injection-preview`, {
        user_input: instructions || '当前群聊消息',
      })
      setInjectionPreview(r.data)
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setInjectionLoading(false)
    }
  }

  const updateMemory = async (memoryId, patch) => {
    if (!memoryId || memoryUpdatingId) return
    setMemoryUpdatingId(memoryId)
    try {
      const r = await api.patch(`/group-memories/items/${memoryId}`, patch)
      const updated = r.data.memory
      setMemories(items => items.map(item => item.id === memoryId ? updated : item))
      setInjectionPreview(null)
      await loadOverview()
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setMemoryUpdatingId(null)
    }
  }

  const editMemoryContent = memory => {
    const content = prompt('编辑群体记忆内容', memory.content || '')
    if (content == null) return
    updateMemory(memory.id, { content })
  }

  const runExtract = async () => {
    if (!groupId || extracting) return
    setExtracting(true)
    setLastExtractResult(null)
    try {
      const r = await api.post(`/group-memories/${encodeURIComponent(groupId)}/extract`, {
        window_hours: Number(windowHours),
        instructions,
      })
      setLastExtractResult(r.data)
      if (Array.isArray(r.data.memories)) {
        setMemories(r.data.memories)
      }
      const resultGroupId = r.data.group_id || groupId
      setGroupId(resultGroupId)
      await Promise.all([loadOverview(), load(resultGroupId)])
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setExtracting(false)
    }
  }

  const stats = {
    groups: overview.length,
    withMemory: overview.filter(x => Number(x.memory_count || 0) > 0).length,
    injectable: overview.reduce((sum, x) => sum + Number(x.injectable_count || 0), 0),
    empty: overview.filter(x => Number(x.memory_count || 0) === 0).length,
  }
  const filteredOverview = overview.filter(item => {
    const q = groupId.trim().toLowerCase()
    if (!q) return true
    return String(item.group_id || '').toLowerCase().includes(q) ||
      String(item.session_name || '').toLowerCase().includes(q) ||
      String(item.raw_group_id || '').toLowerCase().includes(q)
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">群体记忆</h1>
          <p className="mt-1 text-xs text-slate-500">查看群聊记忆覆盖，并按 session_id 浏览近期摘要与长期摘要。</p>
        </div>
        <button onClick={loadOverview}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
          disabled={overviewLoading}>
          <RefreshCw className={`h-3.5 w-3.5 ${overviewLoading ? 'animate-spin' : ''}`} />
          刷新概览
        </button>
      </div>

      <div className="flex rounded-lg border border-slate-800 bg-slate-950 p-1">
        {[
          ['group', '群体记忆'],
          ['recent', '近期摘要'],
          ['long', '长期摘要'],
        ].map(([key, label]) => (
          <button key={key} onClick={() => setMemoryTab(key)}
            className={`rounded-md px-3 py-1.5 text-xs transition-colors ${memoryTab === key ? 'bg-emerald-500/15 text-emerald-300' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'}`}>
            {label}
          </button>
        ))}
      </div>

      {memoryTab === 'group' ? (
        <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MiniStat label="已发现群" value={stats.groups} />
        <MiniStat label="已有记忆" value={stats.withMemory} tone="blue" />
        <MiniStat label="可注入项" value={stats.injectable} tone="emerald" />
        <MiniStat label="待提取群" value={stats.empty} tone="amber" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="min-h-[520px] overflow-hidden">
          <div className="border-b border-slate-800 p-3">
            <label className="block text-[11px] font-medium text-slate-400">
              搜索或输入 group_id
              <input value={groupId} onChange={e => handleGroupIdChange(e.target.value)} placeholder="group_123456 / 群名"
                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
            </label>
          </div>
          <div className="max-h-[620px] overflow-y-auto">
            {overviewLoading ? <Spinner /> : filteredOverview.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs text-slate-600">没有匹配的群</div>
            ) : filteredOverview.map(item => {
              const selected = item.group_id === groupId || item === exactOverviewGroup
              return (
                <button key={item.group_id} onClick={() => selectGroup(item)}
                  className={`w-full border-b border-slate-800/70 px-3 py-3 text-left transition-colors ${selected ? 'bg-emerald-500/10' : 'hover:bg-slate-800/50'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-100">{item.session_name || item.group_id}</div>
                      <div className="mt-0.5 text-[11px] text-slate-500">{item.group_id}</div>
                    </div>
                    <Badge tone={Number(item.injectable_count || 0) > 0 ? 'emerald' : Number(item.memory_count || 0) > 0 ? 'blue' : 'amber'}>
                      {item.memory_count || 0}
                    </Badge>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] text-slate-500">
                    <span>日志 {item.log_count || 0}</span>
                    <span>注入 {item.injectable_count || 0}</span>
                    <span>{item.group_profile_mode || 'off'}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </Card>

        <div className="min-w-0 space-y-4">
          <Card className="p-3">
            <div className="grid gap-3 lg:grid-cols-[1fr_140px_140px_auto]">
              <label className="block text-[11px] font-medium text-slate-400">
                当前群
                <input value={groupId} onChange={e => handleGroupIdChange(e.target.value)} placeholder="group_id"
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
              </label>
              <label className="block text-[11px] font-medium text-slate-400">
                类型
                <select value={memType} onChange={e => setMemType(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500">
                  <option value="">全部类型</option>
                  {['topic', 'slang', 'style', 'relationship', 'event', 'preference'].map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="block text-[11px] font-medium text-slate-400">
                提取窗口
                <select value={windowHours} onChange={e => setWindowHours(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500">
                  <option value={24}>24 小时</option>
                  <option value={168}>7 天</option>
                  <option value={720}>30 天</option>
                  <option value={0}>全部历史</option>
                </select>
              </label>
              <div className="flex items-end gap-2">
                <button onClick={() => load()} disabled={!groupId || loading}
                  className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50">查询</button>
                <button onClick={enableInjection} disabled={!groupId || injectionLoading}
                  className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50">一键开启注入</button>
                <button onClick={previewInjection} disabled={!groupId || injectionLoading}
                  className="rounded-lg bg-blue-700 px-3 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50">模拟注入</button>
                <button onClick={runExtract} disabled={!groupId || extracting}
                  className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50">
                  {extracting ? '提取中...' : '提取记忆'}
                </button>
              </div>
            </div>
            <label className="mt-3 block text-[11px] font-medium text-slate-400">
              提取指引
              <input value={instructions} onChange={e => setInstructions(e.target.value)}
                placeholder="可选，例如：只提取稳定事实，忽略临时玩笑"
                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
            </label>
            {lastExtractResult && (
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-5">
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-400">原始 {lastExtractResult.raw_count}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-400">清洗 {lastExtractResult.deduped_count}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-emerald-300">新增 {lastExtractResult.stats?.new || 0}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-blue-300">更新 {lastExtractResult.stats?.updated || 0}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-300">可注入 {lastExtractResult.injectable_count}</div>
              </div>
            )}
            {injectionPreview && (
              <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={injectionPreview.group_profile_mode === 'on' ? 'emerald' : injectionPreview.group_profile_mode === 'preview' ? 'blue' : 'slate'}>
                    注入 {injectionPreview.group_profile_mode || 'off'}
                  </Badge>
                  <span className="text-slate-400">selected {(injectionPreview.group_memory_ids || []).length}</span>
                  <span className="text-slate-500">chars {injectionPreview.group_memory_context_chars || 0}</span>
                  {injectionPreview.chat_stream_id && <span className="text-slate-500">{injectionPreview.chat_stream_id}</span>}
                </div>
                {Array.isArray(injectionPreview.group_memory_ids) && injectionPreview.group_memory_ids.length > 0 && (
                  <div className="mt-2 text-slate-400">注入 ID: {injectionPreview.group_memory_ids.join(', ')}</div>
                )}
                {injectionPreview.group_profile_mode === 'preview' && (
                  <div className="mt-2 text-blue-300">preview 模式只展示预览结果，不会真实注入 prompt。</div>
                )}
                {Array.isArray(injectionPreview.group_memory_skipped) && injectionPreview.group_memory_skipped.length > 0 && (
                  <div className="mt-2 text-slate-500">
                    跳过: {injectionPreview.group_memory_skipped.slice(0, 5).map(x => `${x.id}:${x.reason}`).join(' / ')}
                  </div>
                )}
                {injectionPreview.group_memory_context && (
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-900 p-2 text-[11px] leading-4 text-slate-300">
                    {injectionPreview.group_memory_context}
                  </pre>
                )}
              </div>
            )}
          </Card>

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-medium text-slate-200">记忆列表</h2>
              <p className="mt-1 text-[11px] text-slate-500">当前筛选结果 {memories.length} 条</p>
            </div>
            {lastExtractResult && <Badge tone="emerald">本次可注入 {lastExtractResult.injectable_count}</Badge>}
          </div>

          {loading ? <Spinner /> : memories.length === 0 ? <div className="rounded-lg border border-slate-800 py-16 text-center text-sm text-slate-600">{groupId ? '暂无记忆；提取后会显示在这里，也可以点查询刷新' : '从左侧选择群或输入 group_id'}</div> : (
            <Card className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-800 text-left text-slate-500">
                  <th className="px-3 py-2">id</th><th className="px-3 py-2">类型</th><th className="px-3 py-2">内容</th><th className="px-3 py-2">confidence</th><th className="px-3 py-2">证据</th><th className="px-3 py-2">decay</th><th className="px-3 py-2">来源</th><th className="px-3 py-2">策略</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">更新</th><th className="px-3 py-2">操作</th>
                </tr></thead>
                <tbody>
                  {memories.map(m => (
                    <tr key={m.id} className="border-b border-slate-800/50 align-top">
                      <td className="px-3 py-2 text-slate-500">{m.id}</td>
                      <td className="px-3 py-2"><Badge>{m.memory_type}</Badge></td>
                      <td className="max-w-[520px] px-3 py-2 text-slate-200">{m.content}</td>
                      <td className="px-3 py-2">{Number(m.confidence).toFixed(2)}</td>
                      <td className="px-3 py-2"><button onClick={() => setExpandedEvidence(expandedEvidence === m.id ? null : m.id)} className="text-xs text-slate-500 underline hover:text-emerald-400">{m.evidence_count}</button></td>
                      <td className="px-3 py-2">{Number(m.decay_score).toFixed(2)}</td>
                      <td className="px-3 py-2 text-slate-500">{m.source}</td>
                      <td className="px-3 py-2"><Badge tone={m.inject_policy === 'auto' ? 'emerald' : m.inject_policy === 'manual_only' ? 'blue' : 'slate'}>{m.inject_policy || 'auto'}</Badge></td>
                      <td className="px-3 py-2">{m.status === 'active' ? <Badge tone="emerald">active</Badge> : m.status === 'archived' ? <Badge tone="slate">archived</Badge> : <Badge tone="amber">{m.status}</Badge>}</td>
                      <td className="px-3 py-2 text-xs text-slate-500">{m.updated_at}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          <button onClick={() => editMemoryContent(m)} disabled={memoryUpdatingId === m.id}
                            className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-50">编辑</button>
                          {m.status === 'disabled' ? (
                            <button onClick={() => updateMemory(m.id, { status: 'active', inject_policy: 'auto', disabled_reason: '' })} disabled={memoryUpdatingId === m.id}
                              className="rounded border border-emerald-700 px-2 py-1 text-[11px] text-emerald-300 hover:bg-emerald-950 disabled:opacity-50">恢复</button>
                          ) : (
                            <button onClick={() => updateMemory(m.id, { status: 'disabled', inject_policy: 'never', disabled_reason: 'web_admin_disabled' })} disabled={memoryUpdatingId === m.id}
                              className="rounded border border-red-800 px-2 py-1 text-[11px] text-red-300 hover:bg-red-950 disabled:opacity-50">禁用</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
          {expandedEvidence && (() => {
            const m = memories.find(x => x.id === expandedEvidence)
            return m ? (
              <Card className="p-3">
                <div className="mb-2 text-xs text-slate-500">证据日志 ID 列表</div>
                <JsonBlock value={m.evidence_log_ids_json} className="max-h-48" />
              </Card>
            ) : null
          })()}
        </div>
      </div>
        </>
      ) : (
        <SessionSummaryBrowser mode={memoryTab} />
      )}
    </div>
  )
}

// ── Persona ──
function PersonaPage() {
  const [users, setUsers] = useState([])
  const [userId, setUserId] = useState('')
  const [facts, setFacts] = useState([])
  const [status, setStatus] = useState('')
  const [memoryType, setMemoryType] = useState('')
  const [userInput, setUserInput] = useState('请按我的偏好回答')
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [extractResult, setExtractResult] = useState(null)

  const loadUsers = useCallback(async () => {
    const r = await api.get('/persona/users', { params: { q: userId, limit: 120 } })
    setUsers(r.data.items || [])
  }, [userId])

  const loadFacts = useCallback(async (target = userId) => {
    if (!target) return
    setLoading(true)
    try {
      const r = await api.get(`/persona/users/${encodeURIComponent(target)}/facts`, {
        params: { status, memory_type: memoryType },
      })
      setFacts(r.data.items || [])
      setUserId(target)
    } finally {
      setLoading(false)
    }
  }, [userId, status, memoryType])

  useEffect(() => {
    const timer = window.setTimeout(() => { loadUsers().catch(() => {}) }, 0)
    return () => window.clearTimeout(timer)
  }, [loadUsers])

  const updateFact = async (factId, patch) => {
    try {
      const r = await api.patch(`/persona/facts/${factId}`, patch)
      const updated = r.data.fact
      setFacts(items => items.map(item => item.id === factId ? updated : item))
      setPreview(null)
      await loadUsers()
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    }
  }

  const editFactContent = fact => {
    const content = prompt('编辑用户画像内容', fact.content || '')
    if (content == null) return
    updateFact(fact.id, { content })
  }

  const previewInjection = async () => {
    if (!userId) return
    const r = await api.post(`/persona/users/${encodeURIComponent(userId)}/injection-preview`, {
      user_input: userInput,
      max_items: 6,
      max_chars: 900,
    })
    setPreview(r.data)
  }

  const extractPersona = async () => {
    if (!userId || extracting) return
    setExtracting(true)
    setExtractResult(null)
    try {
      const r = await api.post(`/persona/users/${encodeURIComponent(userId)}/extract`, {
        window_hours: 168,
        limit: 80,
      })
      setExtractResult(r.data)
      await Promise.all([loadUsers(), loadFacts(userId)])
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setExtracting(false)
    }
  }

  const stats = {
    users: users.length,
    facts: facts.length,
    injectable: facts.filter(x => x.status === 'active' && x.inject_policy === 'auto').length,
    review: facts.filter(x => x.status === 'review').length,
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">用户画像</h1>
          <p className="mt-1 text-xs text-slate-500">治理长期用户偏好，预览本轮会注入哪些画像。</p>
        </div>
        <button onClick={loadUsers}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700">
          <RefreshCw className="h-3.5 w-3.5" />
          刷新用户
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MiniStat label="用户数" value={stats.users} />
        <MiniStat label="画像项" value={stats.facts} tone="blue" />
        <MiniStat label="可注入" value={stats.injectable} tone="emerald" />
        <MiniStat label="待审核" value={stats.review} tone="amber" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="min-h-[520px] overflow-hidden">
          <div className="border-b border-slate-800 p-3">
            <label className="block text-[11px] font-medium text-slate-400">
              搜索或输入 user_id
              <input value={userId} onChange={e => setUserId(e.target.value)} placeholder="用户 ID"
                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
            </label>
          </div>
          <div className="max-h-[620px] overflow-y-auto">
            {users.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs text-slate-600">暂无用户画像</div>
            ) : users.map(item => (
              <button key={item.user_id} onClick={() => loadFacts(item.user_id)}
                className={`w-full border-b border-slate-800/70 px-3 py-3 text-left transition-colors ${item.user_id === userId ? 'bg-emerald-500/10' : 'hover:bg-slate-800/50'}`}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-slate-100">{item.name || item.user_id}</div>
                    <div className="mt-0.5 text-[11px] text-slate-500">{item.user_id}</div>
                  </div>
                  <Badge tone={Number(item.injectable_count || 0) > 0 ? 'emerald' : Number(item.fact_count || 0) > 0 ? 'blue' : 'slate'}>
                    {item.fact_count || 0}
                  </Badge>
                </div>
                <div className="mt-2 text-[11px] text-slate-500">可注入 {item.injectable_count || 0}</div>
              </button>
            ))}
          </div>
        </Card>

        <div className="min-w-0 space-y-4">
          <Card className="p-3">
            <div className="grid gap-3 lg:grid-cols-[1fr_140px_170px_auto]">
              <label className="block text-[11px] font-medium text-slate-400">
                当前用户
                <input value={userId} onChange={e => setUserId(e.target.value)} placeholder="user_id"
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
              </label>
              <label className="block text-[11px] font-medium text-slate-400">
                状态
                <select value={status} onChange={e => setStatus(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500">
                  <option value="">全部状态</option>
                  {['review', 'active', 'disabled', 'archived', 'rejected'].map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              </label>
              <label className="block text-[11px] font-medium text-slate-400">
                类型
                <select value={memoryType} onChange={e => setMemoryType(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500">
                  <option value="">全部类型</option>
                  {['stable_preference', 'interaction_style', 'stable_background', 'long_term_project'].map(x => <option key={x} value={x}>{x}</option>)}
                </select>
              </label>
              <div className="flex items-end gap-2">
                <button onClick={() => loadFacts()} disabled={!userId || loading}
                  className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50">查询</button>
                <button onClick={extractPersona} disabled={!userId || extracting}
                  className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50">{extracting ? '提取中...' : '提取画像'}</button>
                <button onClick={previewInjection} disabled={!userId}
                  className="rounded-lg bg-blue-700 px-3 py-2 text-xs font-medium text-white hover:bg-blue-600 disabled:opacity-50">模拟注入</button>
              </div>
            </div>
            <label className="mt-3 block text-[11px] font-medium text-slate-400">
              模拟输入
              <input value={userInput} onChange={e => setUserInput(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500" />
            </label>
            {extractResult && (
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-400">原始 {extractResult.raw_count}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-400">候选 {extractResult.candidate_count}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-emerald-300">新增 {extractResult.stats?.created || 0}</div>
                <div className="rounded-lg bg-slate-950 px-3 py-2 text-amber-300">拒收 {extractResult.stats?.rejected || 0}</div>
              </div>
            )}
            {preview && (
              <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={preview.persona_fact_ids?.length ? 'emerald' : 'slate'}>selected {(preview.persona_fact_ids || []).length}</Badge>
                  <span className="text-slate-500">chars {preview.persona_context_chars || 0}</span>
                </div>
                {Array.isArray(preview.persona_skipped) && preview.persona_skipped.length > 0 && (
                  <div className="mt-2 text-slate-500">
                    跳过: {preview.persona_skipped.slice(0, 5).map(x => `${x.id}:${x.reason}`).join(' / ')}
                  </div>
                )}
                {preview.persona_context && (
                  <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-900 p-2 text-[11px] leading-4 text-slate-300">
                    {preview.persona_context}
                  </pre>
                )}
              </div>
            )}
          </Card>

          <div>
            <h2 className="text-sm font-medium text-slate-200">画像列表</h2>
            <p className="mt-1 text-[11px] text-slate-500">当前筛选结果 {facts.length} 条</p>
          </div>
          {loading ? <Spinner /> : facts.length === 0 ? (
            <div className="rounded-lg border border-slate-800 py-16 text-center text-sm text-slate-600">{userId ? '暂无画像；可先提取或调整筛选' : '从左侧选择用户或输入 user_id'}</div>
          ) : (
            <Card className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-800 text-left text-slate-500">
                  <th className="px-3 py-2">id</th><th className="px-3 py-2">类型</th><th className="px-3 py-2">内容</th><th className="px-3 py-2">证据</th><th className="px-3 py-2">inject_policy</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">注入</th><th className="px-3 py-2">操作</th>
                </tr></thead>
                <tbody>
                  {facts.map(f => (
                    <tr key={f.id} className="border-b border-slate-800/50 align-top">
                      <td className="px-3 py-2 text-slate-500">{f.id}</td>
                      <td className="px-3 py-2"><Badge>{f.memory_type}</Badge></td>
                      <td className="max-w-[560px] px-3 py-2 text-slate-200">{f.content}</td>
                      <td className="px-3 py-2 text-slate-400">{f.evidence_count}</td>
                      <td className="px-3 py-2"><Badge tone={f.inject_policy === 'auto' ? 'emerald' : f.inject_policy === 'manual_only' ? 'blue' : 'slate'}>{f.inject_policy}</Badge></td>
                      <td className="px-3 py-2"><Badge tone={f.status === 'active' ? 'emerald' : f.status === 'review' ? 'amber' : 'slate'}>{f.status}</Badge></td>
                      <td className="px-3 py-2 text-xs text-slate-500">{f.injected_count || 0}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          <button onClick={() => editFactContent(f)} className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800">编辑</button>
                          <button onClick={() => updateFact(f.id, { status: 'active', inject_policy: 'auto' })} className="rounded border border-emerald-700 px-2 py-1 text-[11px] text-emerald-300 hover:bg-emerald-950">启用</button>
                          <button onClick={() => updateFact(f.id, { status: 'disabled', inject_policy: 'never', disabled_reason: 'web_admin_disabled' })} className="rounded border border-red-800 px-2 py-1 text-[11px] text-red-300 hover:bg-red-950">禁用</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>
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
          <Route path="/proactive-outreach" element={<ProactiveOutreachPage />} />
          <Route path="/stickers" element={<StickersPage />} />
          <Route path="/stickers/duplicates" element={<StickerDedupPage />} />
          <Route path="/blocks" element={<BlocksPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/configs" element={<SessionConfigsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/persona" element={<PersonaPage />} />
          <Route path="/generated-images" element={<GeneratedImagesPage />} />
          <Route path="/reply-eval" element={<ReplyEvalPage />} />
          <Route path="/rag-debug" element={<RagDebugPage />} />
          <Route path="/rag-benchmark" element={<RagBenchmarkPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/db" element={<DbPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />
          <Route path="/prompt-preview" element={<EffectivePromptPreviewPage />} />
          <Route path="/prompt-v2-templates" element={<Navigate to="/prompt-templates" replace />} />
          <Route path="/prompt-templates" element={<PromptV2TemplatesPage />} />
          <Route path="/agent-runs/:runId" element={<AgentRunDetailPage />} />
          <Route path="/agent-runs" element={<AgentRunsPage />} />
          <Route path="/llm-api-logs" element={<LLMApiLogsPage />} />
          <Route path="/tool-calls" element={<ToolCallsPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/web-search" element={<WebSearchPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
