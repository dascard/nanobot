const SUCCESS_STATUSES = new Set([
  'success',
  'succeeded',
  'stream_success',
  'no_reply',
])

const FAILED_STATUSES = new Set([
  'error',
  'failed',
  'failure',
  'empty',
  'suppressed',
  'ambiguous',
  'cancelled',
  'canceled',
  'timed_out',
  'timeout',
])

const WAITING_STATUSES = new Set([
  'pending',
  'started',
  'waiting',
  'waiting_approval',
  'waiting_input',
])

export function runStatusTone(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (SUCCESS_STATUSES.has(normalized)) return 'emerald'
  if (FAILED_STATUSES.has(normalized)) return 'red'
  if (normalized === 'running') return 'blue'
  if (WAITING_STATUSES.has(normalized)) return 'amber'
  return 'slate'
}
