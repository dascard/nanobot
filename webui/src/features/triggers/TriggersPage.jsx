import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bot,
  CalendarClock,
  Code2,
  Edit3,
  GitBranch,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Repeat2,
  Save,
  Search,
  Send,
  Timer,
  Variable,
  Wrench,
  X,
} from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  Field,
  IconButton,
  MiniStat,
  PageHeader,
  Spinner,
  Toolbar,
  ViewportPage,
} from '../../components/ui'


const INPUT_CLASS = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50'
const EMPTY_DRAFT = {
  name: '',
  schedule: '0 9 * * *',
  target_type: 'private',
  target_id: '',
  mode: 'prompt',
  prompt_template: '',
  content: '',
  program: JSON.stringify({
    version: 1,
    steps: [
      {
        id: 'generate',
        op: 'model',
        prompt: '生成本次定时推送内容',
        save_as: 'result',
        max_attempts: 1,
      },
      {
        id: 'deliver',
        op: 'emit',
        content: { $ref: 'steps.generate.output' },
      },
    ],
  }, null, 2),
}


function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map(item => item?.msg || item?.message || String(item)).join('；')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.error || JSON.stringify(detail)
  }
  return error?.message || fallback
}


function formatDate(value) {
  if (!value) return '待排程'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleString('zh-CN', { hour12: false })
}


function requestId() {
  const fallback = `${Date.now()}${Math.random().toString(16).slice(2)}`
  const random = globalThis.crypto?.randomUUID?.().replaceAll('-', '') || fallback
  return `trigger_run_${random}`.slice(0, 64)
}


function workflowTone(status) {
  if (status === 'succeeded') return 'emerald'
  if (['pending', 'running', 'waiting'].includes(status)) return 'amber'
  if (['failed', 'blocked', 'ambiguous'].includes(status)) return 'red'
  return 'slate'
}


const PROGRAM_OPERATION_META = {
  set: { label: '设置变量', icon: Variable, tone: 'purple' },
  tool: { label: '调用工具', icon: Wrench, tone: 'blue' },
  model: { label: '调用模型', icon: Bot, tone: 'emerald' },
  branch: { label: '条件分支', icon: GitBranch, tone: 'amber' },
  loop: { label: '循环', icon: Repeat2, tone: 'purple' },
  wait: { label: '等待', icon: Timer, tone: 'amber' },
  emit: { label: '发送消息', icon: Send, tone: 'emerald' },
}


function truncateText(value, maxLength = 180) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim()
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength)}…`
    : normalized
}


function expressionSummary(value) {
  if (value === null || value === undefined) return '未设置'
  if (typeof value === 'string') return truncateText(value) || '空文本'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return `列表，共 ${value.length} 项`
  if (typeof value !== 'object') return String(value)
  if (Object.hasOwn(value, '$ref')) {
    const reference = String(value.$ref || '')
    const stepMatch = reference.match(/^steps\.([^.]+)\.output$/)
    if (stepMatch) return `使用步骤“${stepMatch[1]}”的输出`
    const variableMatch = reference.match(/^variables\.([^.]+)$/)
    if (variableMatch) return `使用变量“${variableMatch[1]}”`
    return `引用 ${reference}`
  }
  const operator = Object.keys(value).find(key => key.startsWith('$'))
  if (operator) {
    const labels = {
      $eq: '等于',
      $ne: '不等于',
      $lt: '小于',
      $lte: '小于等于',
      $gt: '大于',
      $gte: '大于等于',
      $and: '并且',
      $or: '或者',
      $not: '取反',
      $exists: '存在性判断',
      $concat: '拼接内容',
      $coalesce: '选择首个有效值',
      $json_parse: '解析 JSON',
    }
    return `${labels[operator] || operator}表达式`
  }
  return `结构化对象，共 ${Object.keys(value).length} 个字段`
}


function programStepDescription(step) {
  switch (step.op) {
    case 'set':
      return `将 ${expressionSummary(step.value)} 保存为变量“${step.name || '-'}”`
    case 'tool':
      return `运行工具“${step.tool || '-'}”；参数为 ${expressionSummary(step.args || {})}`
    case 'model':
      return expressionSummary(step.prompt)
    case 'branch':
      return `当 ${expressionSummary(step.condition)} 时进入“满足条件”分支`
    case 'loop':
      return step.items !== undefined
        ? `遍历 ${expressionSummary(step.items)}，最多 ${step.max_iterations || '-'} 次`
        : `条件为 ${expressionSummary(step.condition)} 时继续，最多 ${step.max_iterations || '-'} 次`
    case 'wait':
      return `等待 ${expressionSummary(step.seconds)} 秒后继续`
    case 'emit':
      return expressionSummary(step.content)
    default:
      return '未知步骤类型'
  }
}


function ProgramSteps({ steps = [], prefix = '' }) {
  return (
    <div className="space-y-2">
      {steps.map((step, index) => {
        const position = prefix ? `${prefix}.${index + 1}` : String(index + 1)
        const meta = PROGRAM_OPERATION_META[step.op] || {
          label: step.op || '未知步骤',
          icon: Code2,
          tone: 'slate',
        }
        const Icon = meta.icon
        return (
          <div key={`${position}:${step.id || step.op}`} className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-slate-300">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-slate-600">步骤 {position}</span>
                  <span className="font-medium text-slate-200">{step.id || '未命名步骤'}</span>
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                </div>
                <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-slate-400">
                  {programStepDescription(step)}
                </p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
                  {step.save_as && <span>结果变量：{step.save_as}</span>}
                  {step.max_attempts && <span>最多尝试：{step.max_attempts} 次</span>}
                  {step.recovery && <span>恢复策略：{step.recovery}</span>}
                </div>
              </div>
            </div>
            {step.op === 'branch' && (
              <div className="mt-3 grid gap-3 border-t border-slate-800 pt-3 xl:grid-cols-2">
                <div>
                  <div className="mb-2 text-[11px] font-medium text-emerald-300">满足条件</div>
                  <ProgramSteps steps={step.then || []} prefix={`${position}A`} />
                </div>
                <div>
                  <div className="mb-2 text-[11px] font-medium text-slate-400">不满足条件</div>
                  {(step.else || []).length
                    ? <ProgramSteps steps={step.else} prefix={`${position}B`} />
                    : <div className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-600">无后续步骤</div>}
                </div>
              </div>
            )}
            {step.op === 'loop' && (
              <div className="mt-3 border-t border-slate-800 pt-3">
                <div className="mb-2 text-[11px] font-medium text-purple-300">每轮执行</div>
                <ProgramSteps steps={step.steps || []} prefix={`${position}L`} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}


function countProgramSteps(steps = []) {
  return steps.reduce((total, step) => (
    total
    + 1
    + countProgramSteps(step.then || [])
    + countProgramSteps(step.else || [])
    + countProgramSteps(step.steps || [])
  ), 0)
}


function ProgramOverview({ value, onShowJson }) {
  let program
  let parseError = ''
  try {
    program = JSON.parse(value)
  } catch (error) {
    parseError = error.message
  }
  const steps = Array.isArray(program?.steps) ? program.steps : []
  const limits = program?.limits || {}
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60">
      <div className="flex flex-col gap-3 border-b border-slate-800 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-medium text-slate-200">工作流步骤</div>
          <div className="mt-1 text-[11px] text-slate-500">
            默认显示可读的执行顺序；仅在需要修改底层表达式时进入高级 JSON。
          </div>
        </div>
        <ActionButton type="button" onClick={onShowJson} className="gap-1.5">
          <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
          高级 JSON
        </ActionButton>
      </div>
      {parseError ? (
        <div role="alert" className="m-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-300">
          当前 Program JSON 无法解析：{parseError}。请进入高级 JSON 修复。
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-slate-800 px-3 py-2 text-[11px] text-slate-500">
            <span>协议版本：{program?.version || '-'}</span>
            <span>步骤总数：{countProgramSteps(steps)}</span>
            {limits.max_steps && <span>执行步数上限：{limits.max_steps}</span>}
            {limits.max_duration_seconds && <span>最长运行：{limits.max_duration_seconds} 秒</span>}
            {limits.max_loop_iterations && <span>循环上限：{limits.max_loop_iterations}</span>}
          </div>
          <div className="p-3">
            {steps.length
              ? <ProgramSteps steps={steps} />
              : <div className="rounded-lg border border-dashed border-slate-800 px-3 py-8 text-center text-xs text-slate-600">没有可显示的步骤</div>}
          </div>
        </>
      )}
    </div>
  )
}


function TriggerTable({ items, workingId, onEdit, onToggle, onRun }) {
  if (!items.length) {
    return (
      <Card className="flex min-h-0 flex-1 flex-col items-center justify-center py-16 text-center">
        <CalendarClock className="mx-auto h-8 w-8 text-slate-700" aria-hidden="true" />
        <p className="mt-3 text-sm text-slate-400">没有符合条件的触发器</p>
        <p className="mt-1 text-xs text-slate-600">创建后可在这里查看排程、执行状态并编辑定义。</p>
      </Card>
    )
  }

  return (
    <Card className="min-h-0 flex-1 overflow-hidden">
      <div className="viewport-scroll h-full overflow-auto">
        <table className="min-w-[980px] w-full text-left text-xs">
          <thead className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">触发器</th>
              <th className="px-4 py-3 font-medium">排程</th>
              <th className="px-4 py-3 font-medium">投递目标</th>
              <th className="px-4 py-3 font-medium">下次触发</th>
              <th className="px-4 py-3 font-medium">最近工作流</th>
              <th className="px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {items.map(item => {
              const latest = item.latest_execution
              const working = workingId === item.id
              return (
                <tr key={item.id} className="bg-slate-900 transition-colors hover:bg-slate-800/45">
                  <td className="px-4 py-3 align-top">
                    <button
                      type="button"
                      onClick={() => onEdit(item)}
                      className="cursor-pointer text-left font-medium text-slate-100 transition-colors hover:text-emerald-300"
                    >
                      {item.name}
                    </button>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Badge tone={item.enabled ? 'emerald' : 'slate'}>
                        {item.enabled ? '已启用' : '已停用'}
                      </Badge>
                      <span className="font-mono text-[11px] text-slate-600">#{item.id} · v{item.definition_version}</span>
                      {item.owner_migration_required && <Badge tone="red">Owner 待迁移</Badge>}
                      {item.program_error && <Badge tone="red">定义异常</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="font-mono text-slate-300">{item.schedule_display || item.schedule}</div>
                    <div className="mt-1 text-[11px] text-slate-600">{item.schedule_kind || '-'}</div>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Badge tone="blue">{item.target_type === 'group' ? '群聊' : '私聊'}</Badge>
                    <div className="mt-1 max-w-44 truncate font-mono text-[11px] text-slate-500" title={item.target_id}>
                      {item.target_id || '-'}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-slate-300">
                    {item.enabled ? formatDate(item.next_fire_at) : '已停用'}
                  </td>
                  <td className="px-4 py-3 align-top">
                    {latest ? (
                      <div>
                        <Badge tone={workflowTone(latest.status)}>{latest.status}</Badge>
                        <div className="mt-1 max-w-48 truncate text-[11px] text-slate-500" title={latest.error_summary || ''}>
                          {latest.error_code || `执行 #${latest.execution_id}`}
                        </div>
                      </div>
                    ) : <span className="text-slate-600">从未执行</span>}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex justify-end gap-1.5">
                      <IconButton
                        label={`编辑 ${item.name}`}
                        icon={Edit3}
                        onClick={() => onEdit(item)}
                        disabled={working}
                      />
                      <IconButton
                        label={`立即执行 ${item.name}`}
                        icon={Play}
                        onClick={() => onRun(item)}
                        disabled={working || item.owner_migration_required || Boolean(item.program_error)}
                        className="text-emerald-300"
                      />
                      <IconButton
                        label={`${item.enabled ? '停用' : '启用'} ${item.name}`}
                        icon={item.enabled ? Pause : Play}
                        onClick={() => onToggle(item)}
                        disabled={working}
                        className={item.enabled ? 'text-amber-300' : 'text-emerald-300'}
                      />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}


function draftFromDetail(detail) {
  const definition = detail.definition || {}
  return {
    name: detail.name || '',
    schedule: detail.schedule || '',
    target_type: detail.target_type || 'private',
    target_id: detail.target_id || '',
    mode: definition.mode || 'program',
    prompt_template: definition.prompt_template || '',
    content: definition.content || '',
    program: JSON.stringify(definition.program || {}, null, 2),
  }
}


function TriggerEditor({ detail, isNew, loading, onClose, onSaved }) {
  const [draft, setDraft] = useState(() => (
    isNew ? { ...EMPTY_DRAFT } : draftFromDetail(detail || {})
  ))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showProgramJson, setShowProgramJson] = useState(false)

  const setField = (key, value) => {
    setDraft(current => ({ ...current, [key]: value }))
  }

  const save = async event => {
    event.preventDefault()
    setError('')
    if (!draft.name.trim() || !draft.schedule.trim() || !draft.target_id.trim()) {
      setError('名称、触发规则和投递目标不能为空。')
      return
    }
    const body = {
      name: draft.name.trim(),
      schedule: draft.schedule.trim(),
      target_type: draft.target_type,
      target_id: draft.target_id.trim(),
    }
    if (!isNew) body.expected_version = detail.definition_version
    if (draft.mode === 'prompt') {
      if (!draft.prompt_template.trim()) {
        setError('模型生成指令不能为空。')
        return
      }
      body.prompt_template = draft.prompt_template
    } else if (draft.mode === 'content') {
      if (!draft.content.trim()) {
        setError('固定推送正文不能为空。')
        return
      }
      body.content = draft.content
    } else {
      try {
        body.program = JSON.parse(draft.program)
      } catch (parseError) {
        setError(`Program JSON 无法解析：${parseError.message}`)
        return
      }
    }

    setSaving(true)
    try {
      const response = isNew
        ? await api.post('/triggers', body)
        : await api.put(`/triggers/${detail.id}`, body)
      await onSaved(response.data)
    } catch (saveError) {
      setError(formatApiError(saveError, isNew ? '创建触发器失败' : '保存触发器失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="max-h-[94vh] w-[min(96vw,58rem)] overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 shadow-2xl shadow-black/50">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-800 bg-slate-900/95 px-5 py-4 backdrop-blur">
          <div>
            <h2 className="text-base font-semibold text-slate-50">{isNew ? '创建触发器' : `编辑触发器 #${detail?.id || ''}`}</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              所有时间按 Asia/Shanghai 解释；保存既有触发器时会校验定义版本，防止覆盖并发修改。
            </p>
          </div>
          <IconButton label="关闭触发器编辑器" icon={X} onClick={onClose} />
        </div>

        {loading ? <Spinner /> : (
          <form onSubmit={save} className="space-y-5 p-5">
            {error && (
              <div role="alert" className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-300">
                {error}
              </div>
            )}
            {detail?.program_error && (
              <div role="alert" className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-200">
                {detail.program_error}。可切换定义类型并提交完整新定义进行修复。
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <Field id="trigger-name" label="名称">
                <input
                  id="trigger-name"
                  value={draft.name}
                  onChange={event => setField('name', event.target.value)}
                  maxLength={120}
                  className={INPUT_CLASS}
                  autoFocus
                />
              </Field>
              <Field
                id="trigger-schedule"
                label="触发规则"
                hint="支持 30m、every 2h、五段 cron、2026-08-01T15:00"
              >
                <input
                  id="trigger-schedule"
                  value={draft.schedule}
                  onChange={event => setField('schedule', event.target.value)}
                  className={`${INPUT_CLASS} font-mono`}
                />
              </Field>
            </div>

            <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
              <Field id="trigger-target-type" label="投递类型">
                <select
                  id="trigger-target-type"
                  value={draft.target_type}
                  onChange={event => setField('target_type', event.target.value)}
                  className={INPUT_CLASS}
                >
                  <option value="private">QQ 私聊</option>
                  <option value="group">QQ群聊</option>
                </select>
              </Field>
              <Field
                id="trigger-target-id"
                label={draft.target_type === 'group' ? '群号' : 'QQ 用户 ID'}
                hint="同时作为该触发器的安全 Owner 会话边界"
              >
                <input
                  id="trigger-target-id"
                  value={draft.target_id}
                  onChange={event => setField('target_id', event.target.value)}
                  className={`${INPUT_CLASS} font-mono`}
                />
              </Field>
            </div>

            <fieldset>
              <legend className="text-[11px] font-medium text-slate-400">任务定义</legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-3">
                {[
                  ['prompt', '模型生成', '运行时调用模型生成正文'],
                  ['content', '固定正文', '直接推送固定内容，不调用模型'],
                  ['program', '高级 Program', '编辑 version=1 工作流 JSON'],
                ].map(([value, label, description]) => (
                  <label
                    key={value}
                    className={`cursor-pointer rounded-lg border p-3 transition-colors ${draft.mode === value ? 'border-emerald-500/60 bg-emerald-500/10' : 'border-slate-800 bg-slate-950 hover:border-slate-700'}`}
                  >
                    <span className="flex items-center gap-2 text-xs font-medium text-slate-200">
                      <input
                        type="radio"
                        name="trigger-definition-mode"
                        value={value}
                        checked={draft.mode === value}
                        onChange={() => {
                          setField('mode', value)
                          setShowProgramJson(false)
                        }}
                        className="accent-emerald-500"
                      />
                      {label}
                    </span>
                    <span className="mt-1 block pl-5 text-[11px] leading-4 text-slate-500">{description}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            {draft.mode === 'prompt' && (
              <Field
                id="trigger-prompt"
                label="模型生成指令"
                hint={`${draft.prompt_template.length.toLocaleString()} / 16,000 字符`}
              >
                <textarea
                  id="trigger-prompt"
                  value={draft.prompt_template}
                  onChange={event => setField('prompt_template', event.target.value)}
                  maxLength={16000}
                  rows={10}
                  className={`${INPUT_CLASS} min-h-48 resize-y leading-6`}
                />
              </Field>
            )}

            {draft.mode === 'content' && (
              <Field
                id="trigger-content"
                label="固定推送正文"
                hint="此模式编译为单个 emit 步骤"
              >
                <textarea
                  id="trigger-content"
                  value={draft.content}
                  onChange={event => setField('content', event.target.value)}
                  rows={10}
                  className={`${INPUT_CLASS} min-h-48 resize-y leading-6`}
                />
              </Field>
            )}

            {draft.mode === 'program' && (
              showProgramJson ? (
                <div>
                  <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                      <label htmlFor="trigger-program" className="text-[11px] font-medium text-slate-400">Program JSON</label>
                      <p className="mt-1 text-[11px] leading-4 text-slate-500">保存时由服务端执行完整 Schema、引用和步骤上限校验。</p>
                    </div>
                    <ActionButton type="button" onClick={() => setShowProgramJson(false)}>
                      返回可视化步骤
                    </ActionButton>
                  </div>
                  <textarea
                    id="trigger-program"
                    value={draft.program}
                    onChange={event => setField('program', event.target.value)}
                    rows={18}
                    spellCheck="false"
                    className={`${INPUT_CLASS} min-h-80 resize-y font-mono text-xs leading-5`}
                  />
                </div>
              ) : (
                <ProgramOverview
                  value={draft.program}
                  onShowJson={() => setShowProgramJson(true)}
                />
              )
            )}

            {!isNew && detail?.latest_execution && (
              <Card className="p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-500">最近执行</span>
                  <Badge tone={workflowTone(detail.latest_execution.status)}>{detail.latest_execution.status}</Badge>
                  <code className="text-slate-400">#{detail.latest_execution.execution_id}</code>
                  {detail.latest_execution.error_code && <Badge tone="red">{detail.latest_execution.error_code}</Badge>}
                </div>
                {detail.latest_execution.error_summary && (
                  <p className="mt-2 text-xs leading-5 text-red-300">{detail.latest_execution.error_summary}</p>
                )}
              </Card>
            )}

            <div className="flex flex-col-reverse gap-2 border-t border-slate-800 pt-4 sm:flex-row sm:justify-end">
              <ActionButton type="button" onClick={onClose}>取消</ActionButton>
              <ActionButton type="submit" tone="emerald" disabled={saving} className="gap-1.5">
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
                {saving ? '保存中…' : isNew ? '创建触发器' : '保存修改'}
              </ActionButton>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}


export function TriggersPage() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [workingId, setWorkingId] = useState(null)
  const [editor, setEditor] = useState(null)
  const [editorLoading, setEditorLoading] = useState(false)

  const load = useCallback(async ({ silent = false } = {}) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const response = await api.get('/triggers')
      setItems(response.data?.items || [])
    } catch (loadError) {
      setError(formatApiError(loadError, '加载触发器失败'))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { load() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [load])

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return items.filter(item => {
      if (statusFilter === 'enabled' && !item.enabled) return false
      if (statusFilter === 'disabled' && item.enabled) return false
      if (!normalized) return true
      return [item.name, item.target_id, item.schedule_display, item.owner_chat_stream_id]
        .some(value => String(value || '').toLowerCase().includes(normalized))
    })
  }, [items, query, statusFilter])

  const counts = useMemo(() => ({
    enabled: items.filter(item => item.enabled).length,
    unhealthy: items.filter(item => item.program_error || item.owner_migration_required).length,
    failed: items.filter(item => ['failed', 'blocked', 'ambiguous'].includes(item.latest_execution?.status)).length,
  }), [items])

  const openEdit = async item => {
    setEditor({ isNew: false, detail: item })
    setEditorLoading(true)
    setError('')
    try {
      const response = await api.get(`/triggers/${item.id}`)
      setEditor({ isNew: false, detail: response.data })
    } catch (loadError) {
      setEditor(null)
      setError(formatApiError(loadError, '加载触发器详情失败'))
    } finally {
      setEditorLoading(false)
    }
  }

  const toggle = async item => {
    setWorkingId(item.id)
    setError('')
    setNotice('')
    try {
      const response = await api.post(`/triggers/${item.id}/toggle`, {
        expected_version: item.definition_version,
      })
      setNotice(`触发器“${item.name}”已${response.data.enabled ? '启用' : '停用'}。`)
      await load({ silent: true })
    } catch (toggleError) {
      setError(formatApiError(toggleError, '切换触发器状态失败'))
    } finally {
      setWorkingId(null)
    }
  }

  const run = async item => {
    setWorkingId(item.id)
    setError('')
    setNotice('')
    try {
      const response = await api.post(`/triggers/${item.id}/run`, {
        expected_version: item.definition_version,
        request_id: requestId(),
      })
      setNotice(`触发器“${item.name}”已入队，执行 #${response.data.execution_id}。`)
      await load({ silent: true })
    } catch (runError) {
      setError(formatApiError(runError, '立即执行触发器失败'))
    } finally {
      setWorkingId(null)
    }
  }

  if (loading) return <Spinner />

  return (
    <ViewportPage>
      <PageHeader
        title="触发器"
        description="查看和编辑定时任务的排程、投递目标与执行程序。定时规则统一按 Asia/Shanghai 解释，立即执行采用幂等入队。"
        meta={(
          <>
            <span>总数：{items.length}</span>
            <span>启用：{counts.enabled}</span>
            <span>异常定义：{counts.unhealthy}</span>
          </>
        )}
        actions={(
          <>
            <ActionButton type="button" onClick={() => load({ silent: true })} disabled={refreshing} className="gap-1.5">
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
              刷新
            </ActionButton>
            <ActionButton type="button" tone="emerald" onClick={() => setEditor({ isNew: true, detail: null })} className="gap-1.5">
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              创建触发器
            </ActionButton>
          </>
        )}
      />

      <div className="mb-4 grid shrink-0 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat label="全部触发器" value={items.length} />
        <MiniStat label="已启用" value={counts.enabled} tone="emerald" />
        <MiniStat label="最近失败或阻塞" value={counts.failed} tone={counts.failed ? 'red' : 'slate'} />
        <MiniStat label="定义或 Owner 异常" value={counts.unhealthy} tone={counts.unhealthy ? 'amber' : 'slate'} />
      </div>

      <Toolbar className="shrink-0">
        <Field id="trigger-search" label="搜索" className="min-w-[16rem] flex-1">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-600" aria-hidden="true" />
            <input
              id="trigger-search"
              type="search"
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="名称、目标、排程或 Owner"
              className={`${INPUT_CLASS} pl-9`}
            />
          </div>
        </Field>
        <Field id="trigger-status-filter" label="状态" className="min-w-36">
          <select
            id="trigger-status-filter"
            value={statusFilter}
            onChange={event => setStatusFilter(event.target.value)}
            className={INPUT_CLASS}
          >
            <option value="all">全部</option>
            <option value="enabled">仅启用</option>
            <option value="disabled">仅停用</option>
          </select>
        </Field>
      </Toolbar>

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-300">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs leading-5 text-emerald-300">
          {notice}
        </div>
      )}

      <TriggerTable
        items={filtered}
        workingId={workingId}
        onEdit={openEdit}
        onToggle={toggle}
        onRun={run}
      />

      {editor && (
        <TriggerEditor
          key={editor.isNew
            ? 'new'
            : `edit-${editor.detail?.id || 'loading'}-${editorLoading ? 'loading' : 'ready'}`}
          detail={editor.detail}
          isNew={editor.isNew}
          loading={editorLoading}
          onClose={() => setEditor(null)}
          onSaved={async saved => {
            setEditor(null)
            setNotice(`触发器“${saved.name}”已保存。`)
            await load({ silent: true })
          }}
        />
      )}
    </ViewportPage>
  )
}
