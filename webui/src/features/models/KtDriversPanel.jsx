import { useEffect, useState } from 'react'
import {
  CheckCircle2,
  Clipboard,
  ExternalLink,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  TimerReset,
  Trash2,
  Wrench,
} from 'lucide-react'

import { api } from '../../api'
import { ActionButton } from '../../components/ui'
import {
  DriverBadge,
  InlineNotice,
  StatePill,
  formatApiError,
  formatTime,
} from './modelConsoleUi'

function formatEpoch(value) {
  if (!value) return '未知'
  return new Date(Number(value) * 1000).toLocaleString('zh-CN', { hour12: false })
}

const ACCOUNT_STATUS_LABELS = {
  ready: '可用',
  refresh_required: '待自动刷新',
  expired: '需重新登录',
  login_required: '未登录',
  unavailable: '凭据不可用',
  disabled: '已停用',
}

function CodexAccountRow({ account, busy, onLogin, onSave, onDelete }) {
  const [name, setName] = useState(account.name || '')
  const [weight, setWeight] = useState(Number(account.weight || 1))

  const changed = name.trim() !== account.name || Number(weight) !== Number(account.weight)
  const statusReady = account.enabled && ['ready', 'refresh_required'].includes(account.status)

  return (
    <article className="rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="grid gap-3 lg:grid-cols-[minmax(12rem,1fr)_7rem_10rem_auto] lg:items-end">
        <label className="text-[10px] text-slate-500">
          账号名称
          <input value={name} onChange={event => setName(event.target.value)} maxLength={100} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200 outline-none focus:border-indigo-500" />
        </label>
        <label className="text-[10px] text-slate-500">
          轮询权重
          <input type="number" min="1" max="100" value={weight} onChange={event => setWeight(event.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200 outline-none focus:border-indigo-500" />
        </label>
        <div>
          <div className="text-[10px] text-slate-500">账号状态</div>
          <div className="mt-1 flex h-8 items-center gap-2"><StatePill ok={statusReady}>{ACCOUNT_STATUS_LABELS[account.status] || account.status}</StatePill><span className="text-[10px] text-slate-600">{formatEpoch(account.expires_at)}</span></div>
        </div>
        <div className="flex flex-wrap justify-start gap-2 lg:justify-end">
          {changed && <ActionButton tone="emerald" disabled={busy || !name.trim() || Number(weight) < 1 || Number(weight) > 100} onClick={() => onSave(account.id, { name: name.trim(), weight: Number(weight) })}>保存</ActionButton>}
          <ActionButton disabled={busy} onClick={() => onSave(account.id, { enabled: !account.enabled })}>{account.enabled ? '停用' : '启用'}</ActionButton>
          <ActionButton tone="blue" disabled={busy} onClick={() => onLogin(account.id)}>{account.credential_configured ? '重新登录' : '登录'}</ActionButton>
          <ActionButton tone="red" disabled={busy} onClick={() => onDelete(account)} className="gap-1"><Trash2 className="h-3 w-3" />删除</ActionButton>
        </div>
      </div>
    </article>
  )
}

export function KtDriversPanel({ driverSchemas, nativeTools, codexStatus, onChanged }) {
  const [login, setLogin] = useState(null)
  const [loginError, setLoginError] = useState('')
  const [usage, setUsage] = useState(null)
  const [usageLoading, setUsageLoading] = useState(false)
  const [newAccountName, setNewAccountName] = useState('')
  const [accountBusy, setAccountBusy] = useState('')

  useEffect(() => {
    if (!login?.login_id || login.status !== 'pending') return undefined
    const timer = window.setInterval(async () => {
      try {
        const response = await api.get(`/models/codex/device-login/${encodeURIComponent(login.login_id)}`)
        setLogin(response.data)
        if (response.data.status === 'authenticated') await onChanged?.()
      } catch (error) {
        setLoginError(formatApiError(error, 'Codex 登录状态查询失败'))
        window.clearInterval(timer)
      }
    }, Math.max(2000, Number(login.poll_after_seconds || 3) * 1000))
    return () => window.clearInterval(timer)
  }, [login?.login_id, login?.status, login?.poll_after_seconds, onChanged])

  const startLogin = async (accountId = '') => {
    setLoginError('')
    setLogin({ status: 'starting' })
    setAccountBusy(accountId || 'new')
    try {
      const response = await api.post('/models/codex/device-login', {
        account_id: accountId,
        name: accountId ? '' : newAccountName.trim(),
      })
      setLogin(response.data)
      if (!accountId) setNewAccountName('')
      await onChanged?.()
      window.open(response.data.verification_url, '_blank', 'noopener,noreferrer')
    } catch (error) {
      setLogin(null)
      setLoginError(formatApiError(error, 'Codex Device OAuth 启动失败'))
    } finally {
      setAccountBusy('')
    }
  }

  const saveAccount = async (accountId, changes) => {
    setLoginError('')
    setAccountBusy(accountId)
    try {
      await api.patch(`/models/codex/accounts/${encodeURIComponent(accountId)}`, changes)
      await onChanged?.()
    } catch (error) {
      setLoginError(formatApiError(error, 'Codex 账号保存失败'))
    } finally { setAccountBusy('') }
  }

  const deleteAccount = async account => {
    if (!window.confirm(`确定删除 Codex 账号“${account.name}”？此操作会同时删除加密凭据。`)) return
    setLoginError('')
    setAccountBusy(account.id)
    try {
      await api.delete(`/models/codex/accounts/${encodeURIComponent(account.id)}`)
      if (login?.account_id === account.id) setLogin(null)
      await onChanged?.()
    } catch (error) {
      setLoginError(formatApiError(error, 'Codex 账号删除失败'))
    } finally { setAccountBusy('') }
  }

  const loadUsage = async () => {
    setUsageLoading(true)
    try {
      const response = await api.get('/models/codex/usage')
      setUsage(response.data)
    } catch (error) {
      setUsage({ error: formatApiError(error, 'Codex Usage 获取失败') })
    } finally { setUsageLoading(false) }
  }

  const copyCode = () => navigator.clipboard?.writeText(login?.user_code || '').catch(() => {})

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900">
        <header className="border-b border-slate-800 px-4 py-3 sm:px-5"><h2 className="text-sm font-semibold text-slate-100">KT Provider Drivers</h2><p className="mt-1 text-[11px] leading-4 text-slate-500">这里展示 Nanobot 实际接入的 KT Driver 能力，不再用一个“可用”布尔标签代替操作入口。</p></header>
        <div className="overflow-x-auto">
          <table className="min-w-[760px] w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950/50 text-[10px] uppercase tracking-wide text-slate-600"><tr><th className="px-4 py-2.5">Driver</th><th className="px-4 py-2.5">协议</th><th className="px-4 py-2.5">KT Agent</th><th className="px-4 py-2.5">同步 Completion Route</th><th className="px-4 py-2.5">关键参数</th></tr></thead>
            <tbody>{driverSchemas.map(schema => <tr key={schema.id} className="border-b border-slate-800/70 last:border-b-0"><td className="px-4 py-3"><div className="flex items-center gap-2"><DriverBadge driver={schema.id} /><span className="text-slate-300">{schema.label}</span></div></td><td className="px-4 py-3 text-slate-400">{schema.transport}</td><td className="px-4 py-3"><StatePill ok={schema.route_support?.kt_agent && schema.runtime_available}>{!schema.route_support?.kt_agent ? '未接入' : schema.runtime_available ? '运行时就绪' : '依赖缺失'}</StatePill>{schema.runtime_unavailable_reason && <div className="mt-1 text-[10px] text-amber-300">{schema.runtime_unavailable_reason}</div>}</td><td className="px-4 py-3"><StatePill ok={schema.route_support?.sync_completion}>{schema.route_support?.sync_completion ? '已接入' : '仅 KT Agent'}</StatePill></td><td className="max-w-md px-4 py-3 text-[10px] leading-4 text-slate-500">{schema.fields?.join(' · ')}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(20rem,0.75fr)]">
        <section className="rounded-lg border border-slate-800 bg-slate-900">
          <header className="flex flex-col gap-3 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5"><div><div className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-violet-300" aria-hidden="true" /><h2 className="text-sm font-semibold text-slate-100">Codex OAuth 账号池</h2>{codexStatus?.authenticated ? <StatePill ok>{codexStatus.enabled_account_count || 0} 个可用</StatePill> : <StatePill ok={false}>未就绪</StatePill>}</div><p className="mt-1 text-[11px] text-slate-500">多账号凭据独立加密；新会话按权重轮询，单个会话保持粘性，失败后切到下一账号。</p></div><ActionButton onClick={loadUsage} disabled={usageLoading || !codexStatus?.authenticated}>{usageLoading ? '读取中...' : '刷新最近用量'}</ActionButton></header>
          <div className="p-4 sm:p-5">
            <div className="grid gap-3 sm:grid-cols-3"><div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">账号池状态</div><div className="mt-1 flex items-center gap-2 text-xs text-slate-300">{codexStatus?.authenticated ? <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" /> : <TimerReset className="h-3.5 w-3.5 text-amber-300" />}{codexStatus?.account_count || 0} 个账号 / {codexStatus?.enabled_account_count || 0} 个可用</div></div><div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">首账号选择</div><div className="mt-1 text-xs text-slate-300">加权轮询 + 会话粘性</div></div><div className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="text-[10px] text-slate-600">失败策略</div><div className="mt-1 text-xs text-slate-300">下一账号 → 下一模型</div></div></div>
            <div className="mt-3"><InlineNotice>仅添加你有权使用且允许自动化调用的账号；账号池不得用于绕过账号或工作区限额。</InlineNotice></div>

            <div className="mt-4 flex flex-col gap-2 rounded-md border border-slate-800 bg-slate-950 p-3 sm:flex-row sm:items-end">
              <label className="min-w-0 flex-1 text-[10px] text-slate-500">新账号名称（可选）<input value={newAccountName} onChange={event => setNewAccountName(event.target.value)} maxLength={100} placeholder={`Codex 账号 ${(codexStatus?.account_count || 0) + 1}`} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200 outline-none focus:border-indigo-500" /></label>
              <ActionButton tone="blue" onClick={() => startLogin('')} disabled={Boolean(accountBusy) || login?.status === 'starting' || login?.status === 'pending'}>添加账号并登录</ActionButton>
            </div>

            <div className="mt-4 space-y-2">
              {(codexStatus?.accounts || []).length === 0 ? <InlineNotice>账号池为空。添加账号后，Codex Route 才会进入运行时候选。</InlineNotice> : (codexStatus?.accounts || []).map(account => <CodexAccountRow key={`${account.id}:${account.name}:${account.weight}`} account={account} busy={accountBusy === account.id} onLogin={startLogin} onSave={saveAccount} onDelete={deleteAccount} />)}
            </div>

            {login?.status === 'starting' && <div className="mt-4"><InlineNotice tone="blue"><RefreshCw className="mr-1 inline h-3.5 w-3.5 animate-spin" />正在向 OpenAI 申请 Device Code...</InlineNotice></div>}
            {login?.user_code && login.status === 'pending' && <div className="mt-4 rounded-lg border border-violet-500/20 bg-violet-500/5 p-4"><div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div><div className="text-[10px] uppercase tracking-wide text-violet-300">Device Code</div><div className="mt-1 font-mono text-2xl font-semibold tracking-[0.18em] text-white">{login.user_code}</div><div className="mt-2 text-[11px] text-slate-500">有效至 {formatEpoch(login.expires_at)}，页面会自动轮询登录结果。</div></div><div className="flex flex-wrap gap-2"><ActionButton onClick={copyCode} className="gap-1.5"><Clipboard className="h-3.5 w-3.5" />复制代码</ActionButton><a href={login.verification_url} target="_blank" rel="noreferrer" className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-violet-500">打开验证页<ExternalLink className="h-3.5 w-3.5" /></a></div></div></div>}
            {login?.status === 'authenticated' && <div className="mt-4"><InlineNotice tone="emerald"><CheckCircle2 className="mr-1 inline h-3.5 w-3.5" />Codex OAuth 登录完成，凭据已加密写入账号池。</InlineNotice></div>}
            {login && ['failed', 'expired', 'denied', 'cancelled'].includes(login.status) && <div className="mt-4"><InlineNotice tone="red">{login.error || `登录状态：${login.status}`}</InlineNotice></div>}
            {loginError && <div className="mt-4"><InlineNotice tone="red" role="alert">{loginError}</InlineNotice></div>}

            {usage && <div className="mt-4"><h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Rate Limit / Credits Snapshot</h3>{usage.error ? <InlineNotice tone="red">{usage.error}</InlineNotice> : usage.status !== 'ok' ? <InlineNotice>{usage.status === 'no_data_yet' ? '尚无用量快照。完成一次 Codex 请求后，KT 会从响应 Header 捕获配额。' : usage.status === 'not_logged_in' ? 'Codex 尚未登录。' : usage.status}</InlineNotice> : <div className="space-y-2"><div className="text-[10px] text-slate-600">捕获时间：{formatTime(usage.captured_at)}</div>{usage.snapshots?.map((snapshot, index) => <pre key={snapshot.name || index} className="overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-[10px] leading-5 text-slate-300">{JSON.stringify(snapshot, null, 2)}</pre>)}</div>}</div>}
          </div>
        </section>

        <section className="rounded-lg border border-slate-800 bg-slate-900">
          <header className="border-b border-slate-800 px-4 py-3"><div className="flex items-center gap-2"><Wrench className="h-4 w-4 text-indigo-300" aria-hidden="true" /><h2 className="text-sm font-semibold text-slate-100">Provider Native Tools</h2></div><p className="mt-1 text-[11px] text-slate-500">由 KT 工具目录实时返回；在 Provider Connection 中选择后，运行时按 Provider 身份注入。</p></header>
          <div className="space-y-3 p-4">{nativeTools.length === 0 ? <InlineNotice>KT 当前没有登记 Provider Native Tool。</InlineNotice> : nativeTools.map(tool => <article key={tool.name} className="rounded-md border border-slate-800 bg-slate-950 p-3"><div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-slate-200">{tool.name}</span><div className="flex gap-1">{tool.provider_support?.map(driver => <DriverBadge key={driver} driver={driver} />)}</div></div><p className="mt-2 text-[10px] leading-4 text-slate-500">{tool.description}</p>{tool.option_schema && <div className="mt-3 space-y-1.5">{Object.entries(tool.option_schema).map(([name, option]) => <div key={name} className="grid grid-cols-[6rem_minmax(0,1fr)] gap-2 border-t border-slate-800 pt-1.5 text-[10px]"><span className="font-mono text-slate-400">{name}</span><span className="text-slate-600">{option.label || option.type} · 默认 {String(option.default ?? '-')}</span></div>)}</div>}</article>)}</div>
        </section>
      </div>
    </div>
  )
}
