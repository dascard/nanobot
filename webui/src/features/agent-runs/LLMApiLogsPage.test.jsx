// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api'
import { LLMApiLogsPage } from './LLMApiLogsPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
  },
}))


beforeEach(() => {
  vi.useFakeTimers()
  api.get.mockImplementation((_path, { params }) => Promise.resolve({
    data: params.stats_only
      ? {
          items: [],
          total: 64_122,
          stats: { total: 64_122, success: 60_000 },
        }
      : { items: [], total: 64_122, stats: null },
  }))
})


afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})


describe('LLM API 日志页', () => {
  it('拆分列表与统计请求，并对连续筛选输入防抖和取消旧请求', async () => {
    render(<LLMApiLogsPage />)
    await act(async () => {
      await Promise.resolve()
    })

    expect(api.get).toHaveBeenCalledTimes(2)
    const initialSignals = api.get.mock.calls.map(([, config]) => config.signal)
    api.get.mockClear()

    const runFilter = screen.getByLabelText('run_id')
    fireEvent.change(runFilter, { target: { value: 'run-a' } })
    fireEvent.change(runFilter, { target: { value: 'run-ab' } })
    fireEvent.change(runFilter, { target: { value: 'run-abc' } })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(349)
    })
    expect(api.get).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(initialSignals.every(signal => signal.aborted)).toBe(true)
    expect(api.get).toHaveBeenCalledWith(
      '/llm-api-logs',
      expect.objectContaining({
        params: expect.objectContaining({
          page: 1,
          limit: 30,
          include_stats: false,
          run_id: 'run-abc',
        }),
        signal: expect.anything(),
      }),
    )
    expect(api.get).toHaveBeenCalledWith(
      '/llm-api-logs',
      expect.objectContaining({
        params: expect.objectContaining({
          stats_only: true,
          run_id: 'run-abc',
        }),
        signal: expect.anything(),
      }),
    )
  })
})
