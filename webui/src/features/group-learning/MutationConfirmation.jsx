import { useState } from 'react'

import { ActionButton, Modal } from '../../components/ui'
import { createGroupLearningRequestId } from './utils'

export function MutationConfirmation({
  open,
  ...props
}) {
  if (!open) return null
  return (
    <MutationConfirmationDialog
      key={props.requestPrefix}
      {...props}
    />
  )
}

function MutationConfirmationDialog({
  title,
  description,
  requestPrefix,
  busy,
  children,
  onCancel,
  onConfirm,
}) {
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [requestId] = useState(
    () => createGroupLearningRequestId(requestPrefix),
  )

  const submit = async event => {
    event.preventDefault()
    if (!reason.trim() || !confirmed || busy) return
    await onConfirm({
      reason: reason.trim(),
      requestId,
    })
  }

  return (
    <Modal onClose={busy ? undefined : onCancel} wide>
      <form
        onSubmit={submit}
        className="p-5"
        role="dialog"
        aria-modal="true"
        aria-labelledby="group-learning-mutation-title"
      >
        <h2
          id="group-learning-mutation-title"
          className="text-base font-semibold text-slate-100"
        >
          {title}
        </h2>
        {description && (
          <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>
        )}

        <div className="mt-4 space-y-4">
          {children}
          <label className="block text-xs text-slate-400">
            审计原因
            <textarea
              value={reason}
              onChange={event => setReason(event.target.value)}
              maxLength={500}
              rows={3}
              required
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-500"
              placeholder="说明为什么执行本次变更"
            />
          </label>
          <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[11px] text-slate-500">
            request_id: <span className="break-all font-mono text-slate-400">{requestId}</span>
          </div>
          <label className="flex items-start gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={event => setConfirmed(event.target.checked)}
              className="mt-0.5"
            />
            <span>我已核对目标 session、操作范围和影响，并确认执行。</span>
          </label>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <ActionButton type="button" onClick={onCancel} disabled={busy}>
            取消
          </ActionButton>
          <ActionButton
            type="submit"
            tone="emerald"
            disabled={!reason.trim() || !confirmed || busy}
          >
            {busy ? '提交中…' : '确认提交'}
          </ActionButton>
        </div>
      </form>
    </Modal>
  )
}
