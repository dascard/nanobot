import { describe, expect, it } from 'vitest'

import { redactHiddenReasoning } from './traceUtils'


describe('TraceView 隐藏推理脱敏', () => {
  it('递归省略推理正文并保留计量和最终正文', () => {
    const value = {
      choices: [{
        message: {
          content: '最终回复',
          reasoning_content: '隐藏推理不能展示',
        },
        delta: {
          reasoning: '流式隐藏推理不能展示',
          thinking: '兼容字段中的隐藏推理不能展示',
        },
      }],
      usage: {
        output_tokens: 42,
        output_tokens_details: { reasoning_tokens: 12 },
      },
      stream_metrics: {
        reasoning_char_count: 99,
        first_reasoning_ms: 15,
      },
    }

    const redacted = redactHiddenReasoning(value)
    const serialized = JSON.stringify(redacted)

    expect(redacted.choices[0].message.content).toBe('最终回复')
    expect(redacted.choices[0].message.reasoning_content).toBe('[隐藏推理正文已省略]')
    expect(redacted.choices[0].delta.reasoning).toBe('[隐藏推理正文已省略]')
    expect(redacted.choices[0].delta.thinking).toBe('[隐藏推理正文已省略]')
    expect(redacted.usage.output_tokens_details.reasoning_tokens).toBe(12)
    expect(redacted.stream_metrics.reasoning_char_count).toBe(99)
    expect(serialized).not.toContain('隐藏推理不能展示')
    expect(serialized).not.toContain('流式隐藏推理不能展示')
    expect(serialized).not.toContain('兼容字段中的隐藏推理不能展示')
  })
})
