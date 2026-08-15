import { describe, expect, it } from 'vitest'

import { runStatusTone } from './statusTone'


describe('Agent Run 状态颜色', () => {
  it.each([
    ['success', 'emerald'],
    ['succeeded', 'emerald'],
    ['stream_success', 'emerald'],
    ['failed', 'red'],
    ['error', 'red'],
    ['running', 'blue'],
    ['waiting_input', 'amber'],
    ['unknown', 'slate'],
  ])('%s 映射为 %s', (status, tone) => {
    expect(runStatusTone(status)).toBe(tone)
  })
})
