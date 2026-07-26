// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../../api'
import { SandboxPage } from './SandboxPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}))


const statusData = {
  feature: {
    infrastructure_enable_allowed: true,
    session_execution_allowed: true,
    developer_network_allowed: true,
    enabled: true,
    exec_enabled: true,
    group_enabled: false,
  },
  controller: {
    health: { ok: true, service: 'sandboxd' },
    ready: {
      ok: true,
      docker: true,
      policy_matches_server: true,
      catalog_generation: '20260725.2',
      project_quota_ready: true,
      disk_used_percent: 20,
      disk_free_bytes: 100 * 1024 * 1024 * 1024,
      profiles: {
        restricted: {
          ready: true,
          grantable: true,
          execution_mode: 'oneshot',
          image_id: `sha256:${'a'.repeat(64)}`,
          apparmor_profile: 'nanobot-sandbox-restricted',
          error_code: '',
        },
        developer: {
          ready: true,
          grantable: true,
          execution_mode: 'lease',
          image_id: `sha256:${'b'.repeat(64)}`,
          apparmor_profile: 'nanobot-sandbox-developer',
          error_code: '',
        },
        trusted_developer: {
          ready: false,
          grantable: false,
          execution_mode: 'lease',
          image_id: '',
          apparmor_profile: 'nanobot-sandbox-developer',
          error_code: 'profile_not_grantable',
        },
      },
    },
  },
  usage: {
    workspace_count: 1,
    workspace_used_bytes: 1024,
    workspace_quota_bytes: 64 * 1024 * 1024,
    asset_count: 0,
    asset_physical_bytes: 0,
  },
  limits: {
    workspace_default_quota_bytes: 64 * 1024 * 1024,
    asset_max_bytes: 512 * 1024 * 1024,
    total_quota_bytes: 10 * 1024 * 1024 * 1024,
  },
  disk_watermark: {
    max_used_percent: 80,
    min_free_bytes: 50 * 1024 * 1024 * 1024,
  },
}

const session = {
  chat_stream_id: 'qq:session-ui:private',
  platform: 'qq',
  chat_type: 'private',
  session_id: 'private_session-ui',
  actor_user_id: 'user-ui',
  label: '前端测试会话',
  recent_at: '2026-07-25T08:00:00',
}

const lease = {
  lease_id: 'sbxlease_webui_test',
  session_summary: 'qq:private:0123456789ab',
  workspace_id: '00000000-0000-0000-0000-000000000001',
  profile_id: 'developer',
  status: 'active',
  image_digest: `sha256:${'b'.repeat(64)}`,
  catalog_generation: '20260725.2',
  policy_sha256: 'c'.repeat(64),
  controller_epoch: `sbxctl_${'d'.repeat(32)}`,
  quota_generation: 3,
  runtime_present: true,
  runtime_running: true,
  active_process_count: 1,
  last_active_at: '2026-07-25T08:01:00',
  idle_expires_at: '2026-07-25T08:30:00',
  max_expires_at: '2026-07-25T16:00:00',
  last_error_code: '',
  last_error_summary: '',
  command: '不得显示的命令正文',
  stdout: '不得显示的输出正文',
  host_path: '/srv/nanobot/不得显示',
}

const controllerOnlyLease = {
  ...lease,
  lease_id: 'sbxlease_controller_only',
  session_summary: '',
}

const run = {
  run_id: 'sbxrun_webui_test',
  workspace_id: lease.workspace_id,
  profile_id: 'developer',
  execution_mode: 'lease',
  lease_id: lease.lease_id,
  process_state: 'running',
  image_digest: lease.image_digest,
  status: 'running',
  exit_code: null,
  termination_reason: '',
  cpu_time_ms: 10,
  peak_memory_bytes: 1024,
  stdout_bytes: 20,
  stderr_bytes: 0,
  stdout_truncated: true,
  stderr_truncated: false,
  started_at: '2026-07-25T08:01:00',
  finished_at: null,
}

function response(data) {
  return Promise.resolve({ data })
}

function configureApi({ leases = [lease] } = {}) {
  api.get.mockImplementation(path => {
    if (path === '/sandbox/status') return response(statusData)
    if (path === '/sandbox/sessions') return response({ items: [session] })
    if (path === '/sandbox/access-grants') return response({ items: [] })
    if (path === '/sandbox/workspaces') return response({ items: [] })
    if (path === '/sandbox/leases') return response({ items: leases })
    if (path === '/sandbox/operations') return response({ items: [] })
    if (path === '/sandbox/audit-logs') return response({ items: [] })
    if (path === '/sandbox/runs') return response({ items: [run] })
    if (path.startsWith('/sandbox/operations/')) {
      return response({
        operation: {
          operation_id: path.split('/').at(-1),
          status: 'succeeded',
          step: 'completed',
        },
      })
    }
    return Promise.reject(new Error(`未配置 GET ${path}`))
  })
  api.post.mockImplementation(path => {
    if (path === '/sandbox/access-grants') {
      return response({
        operation: {
          operation_id: 'sbxop_webui_access',
          status: 'pending',
          step: 'queued',
        },
      })
    }
    if (path.endsWith('/recreate')) {
      return response({
        replayed: false,
        environment_action: 'maintenance',
        data_preserved: true,
      })
    }
    if (path === '/sandbox/kill-switch') {
      return response({
        terminated_lease_count: 1,
        terminated_run_count: 2,
        failed_count: 0,
        data_preserved: true,
      })
    }
    if (path.startsWith('/sandbox/runs/')) return response({ ok: true })
    return Promise.reject(new Error(`未配置 POST ${path}`))
  })
  api.put.mockResolvedValue({ data: { ok: true } })
}

async function renderPage() {
  render(<SandboxPage />)
  await screen.findByRole('heading', { name: 'Sandbox 管理' })
}

beforeEach(() => {
  vi.clearAllMocks()
  configureApi()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Sandbox 管理页', () => {
  it('按 Profile 展示 readiness，不再投影单一镜像事实', async () => {
    await renderPage()

    expect(screen.getByText('Restricted')).toBeInTheDocument()
    expect(screen.getByText('Developer')).toBeInTheDocument()
    expect(screen.getByText('Trusted Developer')).toBeInTheDocument()
    expect(screen.getByText(/profile_not_grantable/)).toBeInTheDocument()
    expect(screen.queryByText('镜像 ID')).not.toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/sandbox/leases')
  })

  it('只展示 Lease 安全字段并携带幂等 ID 执行重建', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('tab', { name: 'Lease' }))

    const leaseId = await screen.findByText(lease.lease_id)
    const row = leaseId.closest('tr')
    expect(row).not.toBeNull()
    expect(within(row).getByText(lease.session_summary)).toBeInTheDocument()
    expect(screen.queryByText(lease.command)).not.toBeInTheDocument()
    expect(screen.queryByText(lease.stdout)).not.toBeInTheDocument()
    expect(screen.queryByText(lease.host_path)).not.toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: '重建' }))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/sandbox/leases/${lease.lease_id}/recreate`,
        expect.objectContaining({
          request_id: expect.stringMatching(/^sbx_lease_recreate_/),
          reason: expect.stringContaining('重建并重新准备环境'),
        }),
      )
    })
    expect(await screen.findByText(/环境动作 maintenance/)).toBeInTheDocument()
  })

  it('sandboxd-only Lease 只可观测，不暴露无法建账的单条操作', async () => {
    configureApi({ leases: [controllerOnlyLease] })
    await renderPage()
    fireEvent.click(screen.getByRole('tab', { name: 'Lease' }))

    const leaseId = await screen.findByText(controllerOnlyLease.lease_id)
    const row = leaseId.closest('tr')
    expect(row).not.toBeNull()
    expect(row).toHaveTextContent('controller-only')
    expect(row).toHaveTextContent('等待 reconciler 收敛')
    expect(within(row).getByRole('button', { name: '停止' })).toBeDisabled()
    expect(within(row).getByRole('button', { name: '销毁' })).toBeDisabled()
    expect(within(row).getByRole('button', { name: '重建' })).toBeDisabled()
  })

  it('授权请求显式提交 developer Profile', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('tab', { name: '访问授权' }))
    fireEvent.click(await screen.findByRole('button', { name: /前端测试会话/ }))
    fireEvent.click(screen.getByRole('radio', { name: /Exec/ }))
    fireEvent.click(screen.getByRole('radio', { name: /Developer/ }))
    fireEvent.click(screen.getByRole('button', { name: '保存授权与配额' }))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/sandbox/access-grants',
        expect.objectContaining({
          capability: 'exec',
          execution_profile: 'developer',
          quota_bytes: 64 * 1024 * 1024,
        }),
      )
    })
  })

  it('Run 页展示 Profile、模式、Lease 和进程状态', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('tab', { name: '运行记录' }))

    const runId = await screen.findByText(run.run_id)
    const row = runId.closest('tr')
    expect(within(row).getByText('developer')).toBeInTheDocument()
    expect(within(row).getByText('lease')).toBeInTheDocument()
    expect(row).toHaveTextContent(lease.lease_id)
    expect(within(row).getAllByText('running')).toHaveLength(2)
    expect(row).toHaveTextContent(/stdout.*截断/)
  })

  it('kill switch 提交 request_id 并显示真实终止计数', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', {
      name: 'Kill switch（无损关闭）',
    }))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/sandbox/kill-switch',
        expect.objectContaining({
          request_id: expect.stringMatching(/^sbx_kill_/),
          reason: 'Web 管理员触发真实终止',
        }),
      )
    })
    expect(await screen.findByText(
      /确认终止 1 个 Lease、2 个 Run；失败 0 个/,
    )).toBeInTheDocument()
  })
})
