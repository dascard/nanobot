import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Eye,
  FilePenLine,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  Field,
  JsonBlock,
  Modal,
  PageHeader,
  Pagination,
  Spinner,
  Toolbar,
} from '../../components/ui'

const PAGE_LIMIT = 20
const GUIDANCE_MAX_CHARS = 4000
const INPUT_CLASS = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition-colors focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-60'

function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => item?.msg || item?.message || '请求参数不合法')
      .join('\n')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.error || fallback
  }
  return error?.message || fallback
}

function formatTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

function normalizeExternalSessionId(chatType, value) {
  const normalized = String(value || '').trim()
  const prefix = `${chatType}_`
  return normalized.startsWith(prefix) ? normalized.slice(prefix.length) : normalized
}

function canEditIdentity(config) {
  return Boolean(
    config
    && config.identity_status === 'canonical'
    && !config.identity_conflict
    && config.platform
    && config.chat_type
    && config.session_id,
  )
}

function identityBadge(config) {
  if (config.identity_conflict) return <Badge tone="red">身份冲突</Badge>
  if (config.identity_status === 'canonical') return <Badge tone="blue">canonical</Badge>
  if (config.identity_status === 'legacy_alias') return <Badge tone="amber">legacy alias</Badge>
  return <Badge tone="red">无法规范化</Badge>
}

function ToggleField({ id, label, checked, disabled, onChange }) {
  return (
    <div className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
      <label htmlFor={id} className="text-xs text-slate-300">{label}</label>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={event => onChange(event.target.checked)}
        className="h-4 w-4 cursor-pointer accent-emerald-500 disabled:cursor-not-allowed"
      />
    </div>
  )
}

function SessionConfigResults({ items, viewMode, deletingId, onEdit, onDelete }) {
  if (!items.length) {
    return (
      <Card className="px-4 py-12 text-center text-sm text-slate-500">
        当前筛选条件下没有会话
      </Card>
    )
  }

  return (
    <>
      <Card className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[980px] text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-[11px] text-slate-500">
              <th className="px-3 py-2.5 font-medium">会话</th>
              <th className="px-3 py-2.5 font-medium">平台 / 类型</th>
              <th className="px-3 py-2.5 font-medium">身份</th>
              <th className="px-3 py-2.5 font-medium">专属指导</th>
              <th className="px-3 py-2.5 font-medium">发言值</th>
              <th className="px-3 py-2.5 font-medium">群画像</th>
              <th className="px-3 py-2.5 font-medium">来源</th>
              <th className="px-3 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map(config => {
              const editable = canEditIdentity(config)
              const aliases = config.legacy_aliases || []
              const guidanceConfigured = Boolean(config.session_guidance_configured)
              const guidanceChars = config.session_guidance_chars || 0
              const hasOverride = config.has_override ?? (viewMode === 'override')
              return (
                <tr key={config.chat_stream_id} className="border-b border-slate-800/70 align-top transition-colors last:border-b-0 hover:bg-slate-800/30">
                  <td className="max-w-[19rem] px-3 py-3">
                    <div className="truncate text-xs font-medium text-slate-200" title={config.session_name || config.chat_stream_id}>
                      {config.session_name || config.session_id || config.chat_stream_id}
                    </div>
                    <div className="mt-1 truncate font-mono text-[11px] text-slate-500" title={config.chat_stream_id}>
                      {config.chat_stream_id}
                    </div>
                    {aliases.length > 0 && (
                      <div className="mt-1 truncate text-[11px] text-amber-300" title={aliases.join(', ')}>
                        legacy: {aliases.join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-xs text-slate-300">
                    <div>{config.platform || '-'}</div>
                    <div className="mt-1 text-slate-500">{config.chat_type || '-'}</div>
                  </td>
                  <td className="px-3 py-3">
                    {identityBadge(config)}
                    {config.identity_conflict && (
                      <div className="mt-1 max-w-44 text-[11px] leading-4 text-red-300">
                        canonical 与历史别名同时存在
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <Badge tone={guidanceConfigured ? 'emerald' : 'slate'}>
                      {guidanceConfigured ? `已配置 · ${guidanceChars} 字` : '未配置'}
                    </Badge>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {formatTime(config.session_guidance_updated_at)}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-xs text-slate-300">{config.talk_value ?? '-'}</td>
                  <td className="px-3 py-3">
                    <Badge tone={config.group_profile_mode === 'on' ? 'emerald' : config.group_profile_mode === 'preview' ? 'amber' : 'slate'}>
                      {config.group_profile_mode || 'off'}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    <Badge tone={hasOverride ? 'blue' : 'slate'}>
                      {hasOverride ? 'DB 覆写' : '系统默认'}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-1.5">
                      <ActionButton type="button" onClick={() => onEdit({ ...config, has_override: hasOverride })} disabled={!editable} className="gap-1">
                        <FilePenLine className="h-3.5 w-3.5" />
                        {hasOverride ? '编辑' : '创建覆写'}
                      </ActionButton>
                      {hasOverride && (
                        <ActionButton
                          type="button"
                          tone="red"
                          onClick={() => onDelete(config)}
                          disabled={!editable || deletingId === config.chat_stream_id}
                          className="gap-1"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          删除整条覆写
                        </ActionButton>
                      )}
                    </div>
                    {!editable && (
                      <div className="mt-1 text-right text-[11px] text-red-300">
                        无法规范化的会话只读展示
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>

      <div className="grid gap-3 md:hidden">
        {items.map(config => {
          const editable = canEditIdentity(config)
          const guidanceConfigured = Boolean(config.session_guidance_configured)
          const guidanceChars = config.session_guidance_chars || 0
          const hasOverride = config.has_override ?? (viewMode === 'override')
          return (
            <Card key={config.chat_stream_id} className="p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-slate-100">
                    {config.session_name || config.session_id || config.chat_stream_id}
                  </div>
                  <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
                    {config.chat_stream_id}
                  </div>
                </div>
                {identityBadge(config)}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-[11px] text-slate-500">平台 / 类型</div>
                  <div className="mt-1 text-slate-300">{config.platform || '-'} / {config.chat_type || '-'}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">专属指导</div>
                  <div className="mt-1">
                    <Badge tone={guidanceConfigured ? 'emerald' : 'slate'}>
                      {guidanceConfigured ? `已配置 · ${guidanceChars} 字` : '未配置'}
                    </Badge>
                  </div>
                </div>
              </div>
              {(config.legacy_aliases || []).length > 0 && (
                <div className="mt-2 break-all text-[11px] text-amber-300">
                  legacy: {config.legacy_aliases.join(', ')}
                </div>
              )}
              <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-800 pt-3">
                <ActionButton type="button" onClick={() => onEdit({ ...config, has_override: hasOverride })} disabled={!editable} className="gap-1">
                  <FilePenLine className="h-3.5 w-3.5" />
                  {hasOverride ? '编辑' : '创建覆写'}
                </ActionButton>
                {hasOverride && (
                  <ActionButton
                    type="button"
                    tone="red"
                    onClick={() => onDelete(config)}
                    disabled={!editable || deletingId === config.chat_stream_id}
                    className="gap-1"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    删除整条覆写
                  </ActionButton>
                )}
              </div>
              {!editable && (
                <div className="mt-2 text-[11px] text-red-300">无法规范化的会话只读展示</div>
              )}
            </Card>
          )
        })}
      </div>
    </>
  )
}

function SessionConfigEditor({ config, onClose, onSaved }) {
  const creating = Boolean(config?.isNew)
  const [form, setForm] = useState(() => ({
    platform: config?.platform || 'qq',
    chat_type: config?.chat_type || 'group',
    session_id: config?.session_id || '',
    talk_value: config?.talk_value ?? 0.5,
    mentioned_bot_reply: config?.mentioned_bot_reply ?? true,
    group_profile_mode: config?.group_profile_mode || 'off',
    planner_smooth: config?.planner_smooth ?? 3,
    session_guidance: '',
  }))
  const [detailState, setDetailState] = useState(creating ? 'ready' : 'loading')
  const [detailLoadAttempt, setDetailLoadAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    if (creating) return undefined
    let active = true
    const timer = window.setTimeout(async () => {
      setDetailState('loading')
      setError('')
      try {
        const response = await api.get(`/configs/${encodeURIComponent(config.chat_stream_id)}`)
        if (!active) return
        const detail = response.data || {}
        setForm(current => ({
          ...current,
          platform: detail.platform || config.platform || 'qq',
          chat_type: detail.chat_type || config.chat_type || 'group',
          session_id: detail.session_id || config.session_id || '',
          talk_value: detail.talk_value ?? current.talk_value,
          mentioned_bot_reply: detail.mentioned_bot_reply ?? current.mentioned_bot_reply,
          group_profile_mode: detail.group_profile_mode || current.group_profile_mode,
          planner_smooth: detail.planner_smooth ?? current.planner_smooth,
          session_guidance: detail.session_guidance || '',
        }))
        setDetailState('ready')
      } catch (requestError) {
        if (active) {
          setDetailState('failed')
          setError(formatApiError(requestError, '加载会话配置失败'))
        }
      }
    }, 0)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [config, creating, detailLoadAttempt])

  const setField = (key, value) => {
    setForm(current => ({ ...current, [key]: value }))
  }
  const guidance = form.session_guidance
  const guidanceChars = Array.from(guidance).length
  const guidanceTooLong = guidanceChars > 4000
  const externalSessionId = normalizeExternalSessionId(form.chat_type, form.session_id)
  const runtimeSessionId = externalSessionId
    ? (
        config.runtime_session_id
        || (
          form.platform === 'qq'
            ? `${form.chat_type}_${externalSessionId}`
            : externalSessionId
        )
      )
    : ''
  const identityReady = Boolean(
    form.platform.trim()
    && form.chat_type
    && externalSessionId,
  )
  const isEditableIdentity = creating || canEditIdentity(config)
  const detailLoaded = creating || detailState === 'ready'
  const detailLoadFailed = detailState === 'failed'
  const loading = detailState === 'loading'
  const busy = loading || saving || previewing
  const retryDetailLoad = () => setDetailLoadAttempt(current => current + 1)

  const validateNumbers = () => {
    const talkValue = Number.parseFloat(String(form.talk_value))
    const plannerSmooth = Number.parseInt(String(form.planner_smooth), 10)
    if (!Number.isFinite(talkValue) || talkValue < 0.05 || talkValue > 1) {
      setError('发言值必须在 0.05 到 1 之间')
      return null
    }
    if (!Number.isInteger(plannerSmooth) || plannerSmooth < 1) {
      setError('Planner 平滑轮数必须是大于 0 的整数')
      return null
    }
    return { talkValue, plannerSmooth }
  }

  const save = async () => {
    if (!detailLoaded || !identityReady || !isEditableIdentity || guidanceTooLong) return
    const numbers = validateNumbers()
    if (!numbers) return
    setSaving(true)
    setError('')
    try {
      await api.put('/configs', {
        platform: form.platform.trim(),
        chat_type: form.chat_type,
        session_id: externalSessionId,
        talk_value: numbers.talkValue,
        mentioned_bot_reply: form.mentioned_bot_reply ? 1 : 0,
        group_profile_mode: form.group_profile_mode,
        planner_smooth: numbers.plannerSmooth,
        session_guidance: guidance,
      })
      await onSaved()
      onClose()
    } catch (requestError) {
      setError(formatApiError(requestError, '保存会话配置失败'))
    } finally {
      setSaving(false)
    }
  }

  const clearGuidance = async () => {
    if (!detailLoaded || !isEditableIdentity) return
    if (creating || !config.has_override) {
      setField('session_guidance', '')
      return
    }
    if (!config.chat_stream_id) return
    setSaving(true)
    setError('')
    try {
      await api.put(`/configs/${encodeURIComponent(config.chat_stream_id)}`, {
        session_guidance: '',
      })
      setField('session_guidance', '')
      await onSaved()
    } catch (requestError) {
      setError(formatApiError(requestError, '清空专属指导失败'))
    } finally {
      setSaving(false)
    }
  }

  const previewDraft = async () => {
    if (!detailLoaded || !identityReady || !isEditableIdentity || guidanceTooLong) return
    setPreviewing(true)
    setError('')
    try {
      const response = await api.post('/prompt/effective-preview', {
        engine: 'prompt',
        platform: form.platform.trim(),
        chat_type: form.chat_type,
        session_id: runtimeSessionId,
        group_id: form.chat_type === 'group' ? externalSessionId : '',
        user_id: form.chat_type === 'private' ? externalSessionId : '',
        user_input: '会话指导草稿预览',
        session_guidance_override: guidance,
      })
      setPreview(response.data || {})
    } catch (requestError) {
      setError(formatApiError(requestError, '预览草稿失败'))
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <>
      <Modal onClose={busy ? undefined : onClose} wide>
        <div className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3 border-b border-slate-800 pb-4">
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-slate-50">
                {creating ? '创建会话覆写' : '编辑会话策略'}
              </h2>
              <p className="mt-1 truncate font-mono text-[11px] text-slate-500" title={config?.chat_stream_id || ''}>
                {config?.chat_stream_id || '保存后生成 canonical 会话 ID'}
              </p>
            </div>
            <ActionButton type="button" onClick={onClose} disabled={busy} className="gap-1">
              <XCircle className="h-3.5 w-3.5" />
              关闭
            </ActionButton>
          </div>

          {loading ? (
            <Spinner />
          ) : (
            <div className="mt-4 space-y-4">
              {error && (
                <div role="alert" className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-300">
                  {error}
                </div>
              )}

              {detailLoadFailed && (
                <div className="flex flex-col gap-2 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="text-xs leading-5 text-red-300">
                    详情尚未载入，已禁止保存、清空和预览，避免覆盖未知配置。
                  </div>
                  <ActionButton type="button" tone="red" onClick={retryDetailLoad} className="shrink-0 gap-1">
                    <RefreshCw className="h-3.5 w-3.5" />
                    重试加载详情
                  </ActionButton>
                </div>
              )}

              {!isEditableIdentity && (
                <div className="flex gap-2 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-300">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  当前身份存在冲突或无法规范化，只能查看摘要，不能编辑指导。
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-3">
                <Field id="session-config-platform" label="平台">
                  <select
                    id="session-config-platform"
                    value={form.platform}
                    disabled={!creating || busy}
                    onChange={event => setField('platform', event.target.value)}
                    className={INPUT_CLASS}
                  >
                    <option value="qq">QQ</option>
                    <option value="web">Web</option>
                  </select>
                </Field>
                <Field id="session-config-chat-type" label="会话类型">
                  <select
                    id="session-config-chat-type"
                    value={form.chat_type}
                    disabled={!creating || busy}
                    onChange={event => setField('chat_type', event.target.value)}
                    className={INPUT_CLASS}
                  >
                    <option value="group">群聊</option>
                    <option value="private">私聊</option>
                  </select>
                </Field>
                <Field id="session-config-session-id" label="Session ID" hint="填写外部会话 ID，不要手写 canonical 前缀">
                  <input
                    id="session-config-session-id"
                    type="text"
                    value={form.session_id}
                    disabled={!creating || busy}
                    onChange={event => setField('session_id', event.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field id="session-config-talk-value" label="发言值" hint="允许范围 0.05 到 1">
                  <input
                    id="session-config-talk-value"
                    type="number"
                    min="0.05"
                    max="1"
                    step="0.05"
                    value={form.talk_value}
                    disabled={busy || !detailLoaded || !isEditableIdentity}
                    onChange={event => setField('talk_value', event.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
                <Field id="session-config-planner-smooth" label="Planner 平滑轮数">
                  <input
                    id="session-config-planner-smooth"
                    type="number"
                    min="1"
                    step="1"
                    value={form.planner_smooth}
                    disabled={busy || !detailLoaded || !isEditableIdentity}
                    onChange={event => setField('planner_smooth', event.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
              </div>

              <div className="grid gap-2">
                <ToggleField id="session-config-mentioned-reply" label="被 @ 时回复" checked={Boolean(form.mentioned_bot_reply)} disabled={busy || !detailLoaded || !isEditableIdentity} onChange={value => setField('mentioned_bot_reply', value)} />
              </div>

              <Field id="session-config-group-profile" label="群画像模式">
                <select
                  id="session-config-group-profile"
                  value={form.group_profile_mode}
                  disabled={busy || !detailLoaded || !isEditableIdentity}
                  onChange={event => setField('group_profile_mode', event.target.value)}
                  className={INPUT_CLASS}
                >
                  <option value="off">off · 不注入</option>
                  <option value="preview">preview · 仅记录预览</option>
                  <option value="on">on · 注入上下文</option>
                </select>
              </Field>

              <div className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-3">
                <div className="flex gap-2 text-xs leading-5 text-amber-200">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>正文会发送给模型并进入高权限 LLM 请求日志，禁止保存 Token、密码和隐私。</span>
                </div>
                <Field id="session-guidance-editor" label="专属指导"
                  hint={`按 Unicode 字符计数：${guidanceChars.toLocaleString()} / 4,000`}
                  className="mt-3"
                >
                  <textarea
                    id="session-guidance-editor"
                    value={guidance}
                    disabled={busy || !detailLoaded || !isEditableIdentity}
                    onChange={event => setField('session_guidance', event.target.value)}
                    rows={8}
                    className={`${INPUT_CLASS} min-h-40 resize-y font-mono text-xs leading-5`}
                  />
                </Field>
                {guidanceTooLong && (
                  <div role="alert" className="mt-2 text-xs text-red-300">
                    超出 {GUIDANCE_MAX_CHARS.toLocaleString()} 字限制，保存和预览已禁用。
                  </div>
                )}
              </div>

              <div className="flex flex-col-reverse gap-2 border-t border-slate-800 pt-4 sm:flex-row sm:items-center sm:justify-between">
                <ActionButton
                  type="button"
                  tone="amber"
                  onClick={clearGuidance}
                  disabled={busy || !detailLoaded || !isEditableIdentity || guidanceChars === 0}
                  className="gap-1"
                >
                  <XCircle className="h-3.5 w-3.5" />
                  清空专属指导
                </ActionButton>
                <div className="flex flex-wrap justify-end gap-2">
                  <ActionButton
                    type="button"
                    onClick={previewDraft}
                    disabled={busy || !detailLoaded || !identityReady || !isEditableIdentity || guidanceTooLong}
                    className="gap-1"
                  >
                    <Eye className="h-3.5 w-3.5" />
                    {previewing ? '预览中' : '预览草稿'}
                  </ActionButton>
                  <ActionButton
                    type="button"
                    tone="emerald"
                    onClick={save}
                    disabled={busy || !detailLoaded || !identityReady || !isEditableIdentity || guidanceTooLong}
                    className="gap-1"
                  >
                    <FilePenLine className="h-3.5 w-3.5" />
                    {saving ? '保存中' : '保存配置'}
                  </ActionButton>
                </div>
              </div>
            </div>
          )}
        </div>
      </Modal>

      {preview && (
        <Modal onClose={() => setPreview(null)} wide>
          <div className="p-4 sm:p-5">
            <div className="flex items-start justify-between gap-3 border-b border-slate-800 pb-4">
              <div>
                <h2 className="text-base font-semibold text-slate-50">草稿有效 Prompt</h2>
                <p className="mt-1 text-xs text-slate-500">只编译，不调用模型，也不保存草稿。</p>
              </div>
              <ActionButton type="button" onClick={() => setPreview(null)}>关闭</ActionButton>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Badge tone={preview.session_guidance_configured ? 'emerald' : 'slate'}>
                {preview.session_guidance_resolution_status || 'unknown'}
              </Badge>
              <Badge tone="blue">{preview.session_guidance_chars || 0} 字</Badge>
              <Badge tone="slate">{preview.token_estimate || 0} tokens</Badge>
            </div>
            {preview.preview_exact === false && (
              <div
                role="alert"
                className="mt-4 flex items-start gap-2 border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs text-amber-200"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  群记忆 reranker 上下文未纳入本次预览
                  实际请求可能包含额外群记忆内容
                </span>
              </div>
            )}
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="min-w-0">
                <h3 className="mb-2 text-xs font-medium text-slate-300">Flow sections</h3>
                <JsonBlock value={preview.prompt_plan?.flow_sections || preview.flow_sections || []} className="max-h-72" />
              </div>
              <div className="min-w-0">
                <h3 className="mb-2 text-xs font-medium text-slate-300">Section hashes</h3>
                <JsonBlock value={preview.section_hashes || {}} className="max-h-72" />
              </div>
            </div>
            <div className="mt-4">
              <h3 className="mb-2 text-xs font-medium text-slate-300">Messages</h3>
              <JsonBlock value={preview.messages || []} className="max-h-[42vh]" />
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}

export function SessionConfigsPage() {
  const [data, setData] = useState({ items: [], total: 0, page: 1 })
  const [page, setPage] = useState(1)
  const [viewMode, setViewMode] = useState('effective')
  const [platform, setPlatform] = useState('')
  const [chatType, setChatType] = useState('')
  const [configured, setConfigured] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [edit, setEdit] = useState(null)
  const [deletingId, setDeletingId] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const params = {
      page,
      limit: PAGE_LIMIT,
      effective: viewMode === 'effective' ? 1 : 0,
    }
    if (platform) params.platform = platform
    if (chatType) params.chat_type = chatType
    if (configured !== '') params.configured = Number.parseInt(configured, 10)
    if (search) params.search = search
    try {
      const response = await api.get('/configs', { params })
      setData(response.data || { items: [], total: 0, page })
    } catch (requestError) {
      setData({ items: [], total: 0, page })
      setError(formatApiError(requestError, '加载会话策略失败'))
    } finally {
      setLoading(false)
    }
  }, [chatType, configured, page, platform, search, viewMode])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      load()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const changeFilter = (setter, value) => {
    setPage(1)
    setter(value)
  }

  const applySearch = event => {
    event.preventDefault()
    setPage(1)
    setSearch(searchInput.trim())
  }

  const deleteConfig = async config => {
    if (!canEditIdentity(config)) return
    const confirmed = window.confirm(
      `确认删除 ${config.chat_stream_id} 的整条覆写？专属指导和其他策略都会恢复为系统默认值。`,
    )
    if (!confirmed) return
    setDeletingId(config.chat_stream_id)
    setError('')
    try {
      await api.delete(`/configs/${encodeURIComponent(config.chat_stream_id)}`)
      await load()
    } catch (requestError) {
      setError(formatApiError(requestError, '删除会话覆写失败'))
    } finally {
      setDeletingId('')
    }
  }

  const items = data.items || []

  return (
    <div>
      <PageHeader
        title="会话策略"
        description="按平台和会话管理发言策略、学习开关、群画像与专属系统指导。列表仅显示指导摘要，正文需进入单条配置后查看。"
        meta={[
          <span key="total">结果：<span className="text-slate-300">{data.total || 0}</span></span>,
          <span key="view">视图：{viewMode === 'effective' ? '有效配置' : '仅覆写'}</span>,
        ]}
        actions={
          <div className="flex flex-wrap gap-2">
            <ActionButton type="button" onClick={load} disabled={loading} className="gap-1">
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </ActionButton>
            <ActionButton
              type="button"
              tone="emerald"
              onClick={() => setEdit({ isNew: true, identity_status: 'canonical' })}
              className="gap-1"
            >
              <Plus className="h-3.5 w-3.5" />
              创建会话覆写
            </ActionButton>
          </div>
        }
      />

      <Toolbar>
        <Field id="session-config-view" label="配置视图" className="min-w-40">
          <div id="session-config-view" className="flex h-9 rounded-lg border border-slate-700 bg-slate-950 p-0.5">
            <button
              type="button"
              onClick={() => changeFilter(setViewMode, 'effective')}
              className={`flex-1 rounded-md px-2 text-xs transition-colors ${viewMode === 'effective' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}
            >
              有效配置
            </button>
            <button
              type="button"
              onClick={() => changeFilter(setViewMode, 'override')}
              className={`flex-1 rounded-md px-2 text-xs transition-colors ${viewMode === 'override' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}
            >
              仅覆写
            </button>
          </div>
        </Field>
        <Field id="session-config-platform-filter" label="平台" className="min-w-28">
          <select id="session-config-platform-filter" value={platform} onChange={event => changeFilter(setPlatform, event.target.value)} className={INPUT_CLASS}>
            <option value="">全部</option>
            <option value="qq">QQ</option>
            <option value="web">Web</option>
          </select>
        </Field>
        <Field id="session-config-chat-type-filter" label="会话类型" className="min-w-32">
          <select id="session-config-chat-type-filter" value={chatType} onChange={event => changeFilter(setChatType, event.target.value)} className={INPUT_CLASS}>
            <option value="">全部</option>
            <option value="group">群聊</option>
            <option value="private">私聊</option>
          </select>
        </Field>
        <Field id="session-config-guidance-filter" label="专属指导" className="min-w-32">
          <select id="session-config-guidance-filter" value={configured} onChange={event => changeFilter(setConfigured, event.target.value)} className={INPUT_CLASS}>
            <option value="">全部</option>
            <option value="1">已配置</option>
            <option value="0">未配置</option>
          </select>
        </Field>
        <form onSubmit={applySearch} className="flex min-w-[15rem] flex-1 items-end gap-2">
          <Field id="session-config-search" label="会话 ID 或名称" className="min-w-0 flex-1">
            <input
              id="session-config-search"
              type="search"
              value={searchInput}
              onChange={event => setSearchInput(event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <ActionButton type="submit" className="h-9 gap-1">
            <Search className="h-3.5 w-3.5" />
            筛选
          </ActionButton>
        </form>
      </Toolbar>

      {error && (
        <div role="alert" className="mb-4 flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <Spinner />
      ) : (
        <SessionConfigResults
          items={items}
          viewMode={viewMode}
          deletingId={deletingId}
          onEdit={setEdit}
          onDelete={deleteConfig}
        />
      )}

      <Pagination page={page} total={data.total || 0} limit={PAGE_LIMIT} onChange={setPage} />

      {edit && (
        <SessionConfigEditor
          config={edit}
          onClose={() => setEdit(null)}
          onSaved={load}
        />
      )}
    </div>
  )
}
