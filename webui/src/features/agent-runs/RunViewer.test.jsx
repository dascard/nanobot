// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { RunViewer } from './RunViewer'


afterEach(cleanup)

describe('统一离线 Run Viewer', () => {
  it('展示脱敏证据面板且忽略未声明的正文属性', () => {
    render(<RunViewer viewer={{
      schema_version: '1.0',
      summary: {
        status: 'succeeded',
        duration_ms: 1250,
        span_count: 2,
        failed_span_count: 0,
        retry_count: 0,
        recovery_count: 0,
      },
      timeline: [{
        span_id: 'run:one',
        parent_span_id: '',
        kind: 'run',
        name: 'chat',
        status: 'succeeded',
        turn_id: 'turn-one',
        started_at: '2026-08-09T00:00:00+00:00',
        finished_at: '2026-08-09T00:00:01+00:00',
        duration_ms: 1250,
        offset_ms: 0,
        attempt: 0,
        reasoning_content: '不得进入页面的隐藏推理',
      }],
      dag: {
        nodes: [{ id: 'run:one', kind: 'run', name: 'chat', status: 'succeeded' }],
        edges: [],
      },
      waterfall: {
        totals: { input_tokens: 10, output_tokens: 4, cost_microusd: 25 },
        items: [],
      },
      context_manifest: {
        available: false,
        source: 'not_recorded',
        manifest: {},
        fingerprint: {},
      },
      failures: [],
      retries: [],
      recoveries: [],
      versions: { models: ['provider/model'] },
    }} />)

    expect(screen.getByText('统一离线 Run Viewer')).toBeInTheDocument()
    expect(screen.getByText('脱敏时间线')).toBeInTheDocument()
    expect(screen.getByText('Token / Cost Waterfall')).toBeInTheDocument()
    expect(screen.getByText('Context Manifest')).toBeInTheDocument()
    expect(screen.getByText('版本与脱敏合同')).toBeInTheDocument()
    expect(screen.queryByText('不得进入页面的隐藏推理')).not.toBeInTheDocument()
  })
})
