export function formatGroupLearningError(error, fallback = '群学习请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => item?.msg || item?.message || String(item))
      .join('；')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.code || JSON.stringify(detail)
  }
  return error?.message || fallback
}

export function createGroupLearningRequestId(prefix = 'web') {
  const randomId = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `gl-${prefix}:${randomId}`.slice(0, 64)
}

export function statusTone(value) {
  if (['active', 'accepted', 'succeeded', 'enabled', 'running'].includes(value)) {
    return 'emerald'
  }
  if (['conflict', 'waiting_for_evidence', 'pending', 'leased'].includes(value)) {
    return 'amber'
  }
  if (['failed', 'rejected', 'disabled', 'cancelled'].includes(value)) {
    return 'red'
  }
  if (['merged', 'model_reviewed', 'candidate_only'].includes(value)) {
    return 'blue'
  }
  return 'slate'
}

export function shortHash(value) {
  return value ? `${String(value).slice(0, 12)}…` : '-'
}
