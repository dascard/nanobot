export function safeJsonParse(value, fallback = null) {
  if (!value) return fallback
  if (typeof value === 'object') return value
  try { return JSON.parse(value) } catch { return fallback }
}

const HIDDEN_REASONING_KEYS = new Set([
  'reasoning_content',
  'reasoning',
  'reasoning_text',
  'thinking',
  'thinking_content',
])

export function redactHiddenReasoning(value) {
  if (Array.isArray(value)) return value.map(redactHiddenReasoning)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    HIDDEN_REASONING_KEYS.has(key.toLowerCase())
      ? '[隐藏推理正文已省略]'
      : redactHiddenReasoning(item),
  ]))
}

export function hasHiddenReasoning(value) {
  if (Array.isArray(value)) return value.some(hasHiddenReasoning)
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, item]) => (
    HIDDEN_REASONING_KEYS.has(key.toLowerCase()) || hasHiddenReasoning(item)
  ))
}
