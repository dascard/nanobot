// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api'
import { SelfcheckPage } from './SelfcheckPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}))


const report = {
  run_id: 'sc_latest',
  trigger: 'manual',
  environment: 'production',
  status: 'failed',
  capability_registry_sha256: 'a'.repeat(64),
  probe_registry_sha256: 'b'.repeat(64),
  summary: {
    total: 3,
    passed: 1,
    degraded: 0,
    failed: 1,
    inconclusive: 0,
    skipped: 1,
  },
  started_at: '2026-08-15T12:00:00',
  completed_at: '2026-08-15T12:00:01',
  results: [
    {
      check_id: 'database.connectivity',
      category: 'database',
      status: 'passed',
      severity: 'critical',
      level: 'operational',
      duration_ms: 2,
      detail_code: 'database_connectivity_ok',
      message: '数据库读事务正常',
      capability_ids: ['storage.database'],
      metrics: {},
      evidence: {},
      started_at: '2026-08-15T12:00:00',
      completed_at: '2026-08-15T12:00:00',
    },
    {
      check_id: 'schedule.proactive_outreach',
      category: 'schedule',
      status: 'failed',
      severity: 'critical',
      level: 'quality',
      duration_ms: 4,
      detail_code: 'proactive_outreach_all_forced_fallback',
      message: '近期主动外呼正文全部为强制 fallback',
      capability_ids: [],
      metrics: { fallback_rate: 1 },
      evidence: {},
      started_at: '2026-08-15T12:00:00',
      completed_at: '2026-08-15T12:00:00',
    },
    {
      check_id: 'model.reply-canary.functional',
      category: 'model',
      status: 'skipped',
      severity: 'critical',
      level: 'functional',
      duration_ms: 0,
      detail_code: 'model_check_not_authorized',
      message: '本次运行未显式启用模型自检',
      capability_ids: [],
      metrics: {},
      evidence: {},
      started_at: '2026-08-15T12:00:00',
      completed_at: '2026-08-15T12:00:00',
    },
  ],
}


function response(data) {
  return Promise.resolve({ data })
}


function configureApi() {
  api.get.mockImplementation(path => {
    if (path === '/self-check/capabilities') {
      return response({
        registry: { namespace: 'selfcheck_capability', generation: 1, sha256: 'a'.repeat(64) },
        coverage: {
          total: 12,
          covered: 10,
          unverified: 2,
          exempted: 0,
          required_unverified: 2,
          by_kind: {
            rag_source: { total: 8, covered: 8, unverified: 0, exempted: 0 },
            api: { total: 4, covered: 2, unverified: 2, exempted: 0 },
          },
        },
        items: [],
      })
    }
    if (path === '/self-check/probes') {
      return response({
        registry: { namespace: 'selfcheck_probe', generation: 1, sha256: 'b'.repeat(64) },
        items: report.results.map(item => ({
          check_id: item.check_id,
          category: item.category,
        })),
      })
    }
    if (path === '/self-check/runs?limit=20') {
      return response({ total: 1, items: [report] })
    }
    if (path === '/self-check/runs/sc_latest') return response(report)
    if (path === '/settings') {
      return response({
        version: 7,
        settings: [
          { key: 'selfcheck.watchdog_enabled', value: true, readonly: false },
          { key: 'selfcheck.watchdog_interval_seconds', value: 900, readonly: false },
          { key: 'selfcheck.model_canary_enabled', value: false, readonly: false },
        ],
      })
    }
    return Promise.reject(new Error(`未配置 GET ${path}`))
  })
  api.post.mockResolvedValue({
    data: {
      ...report,
      run_id: 'sc_new',
      status: 'passed',
      summary: { ...report.summary, passed: 2, failed: 0 },
      results: report.results.filter(item => item.status !== 'failed'),
    },
  })
  api.put.mockImplementation((path, body) => response({
    key: decodeURIComponent(path.split('/').at(-1)),
    value: body.value,
    version: 8,
  }))
}


beforeEach(() => {
  vi.clearAllMocks()
  configureApi()
})


afterEach(() => {
  cleanup()
})


describe('系统自检页面', () => {
  it('展示能力覆盖、最近结果和 fallback 失败原因', async () => {
    render(<SelfcheckPage />)

    expect(await screen.findByRole('heading', { name: '系统自检' })).toBeInTheDocument()
    expect(await screen.findByText('近期主动外呼正文全部为强制 fallback')).toBeInTheDocument()
    expect(screen.getByText('10/12')).toBeInTheDocument()
    expect(screen.getByText(/proactive_outreach_all_forced_fallback/)).toBeInTheDocument()
  })

  it('默认不授权模型调用，勾选后才随运行请求提交', async () => {
    render(<SelfcheckPage />)
    await screen.findByRole('heading', { name: '系统自检' })

    fireEvent.click(screen.getByRole('button', { name: '运行自检' }))
    await waitFor(() => {
      expect(api.post).toHaveBeenLastCalledWith('/self-check/runs', {
        trigger: 'manual',
        allow_model_checks: false,
      })
    })

    fireEvent.click(screen.getByLabelText('本次运行包含模型 Canary'))
    fireEvent.click(screen.getByRole('button', { name: '运行自检' }))
    await waitFor(() => {
      expect(api.post).toHaveBeenLastCalledWith('/self-check/runs', {
        trigger: 'manual',
        allow_model_checks: true,
      })
    })
  })

  it('按状态筛选结果，不把 skipped 显示成通过', async () => {
    render(<SelfcheckPage />)
    await screen.findByText('本次运行未显式启用模型自检')

    fireEvent.change(screen.getByLabelText('状态筛选'), {
      target: { value: 'skipped' },
    })

    expect(screen.getByText('本次运行未显式启用模型自检')).toBeInTheDocument()
    expect(screen.queryByText('数据库读事务正常')).not.toBeInTheDocument()
  })

  it('保存周期巡检热开关和模型 Canary 开关', async () => {
    render(<SelfcheckPage />)
    await screen.findByRole('heading', { name: '系统自检' })

    fireEvent.click(screen.getByRole('button', { name: '周期 Watchdog' }))
    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        '/settings/selfcheck.watchdog_enabled',
        { value: false },
      )
    })

    fireEvent.click(screen.getByLabelText('周期巡检包含模型 Canary'))
    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        '/settings/selfcheck.model_canary_enabled',
        { value: true },
      )
    })
  })
})
