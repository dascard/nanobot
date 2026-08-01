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
import { TriggersPage } from './TriggersPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}))


const trigger = {
  id: 7,
  name: '每日资讯',
  enabled: true,
  schedule: '0 9 * * *',
  schedule_display: '0 9 * * *',
  schedule_kind: 'cron',
  next_fire_at: '2026-08-02T01:00:00+00:00',
  target_type: 'private',
  target_id: '10001',
  owner_chat_stream_id: 'qq:10001:private',
  owner_migration_required: false,
  definition_version: 3,
  delivery_status: 'delivered',
  program_error: '',
  latest_execution: {
    execution_id: 12,
    status: 'succeeded',
    error_code: '',
    error_summary: '',
  },
}

const triggerDetail = {
  ...trigger,
  definition: {
    mode: 'prompt',
    prompt_template: '汇总今天最重要的 AI 资讯',
    content: '',
    program: {
      version: 1,
      steps: [
        { id: 'model', op: 'model', prompt: '汇总', save_as: 'output' },
        { id: 'emit', op: 'emit', content: { $ref: 'steps.model.output' } },
      ],
    },
  },
}


function response(data) {
  return Promise.resolve({ data })
}


function configureApi() {
  api.get.mockImplementation(path => {
    if (path === '/triggers') return response({ items: [trigger], total: 1 })
    if (path === `/triggers/${trigger.id}`) return response(triggerDetail)
    return Promise.reject(new Error(`未配置 GET ${path}`))
  })
  api.post.mockImplementation(path => {
    if (path === `/triggers/${trigger.id}/toggle`) {
      return response({ ...trigger, enabled: false, definition_version: 4 })
    }
    if (path === `/triggers/${trigger.id}/run`) {
      return response({ status: 'pending', execution_id: 33, deduplicated: false })
    }
    if (path === '/triggers') return response({ ...triggerDetail, id: 8, name: '新提醒' })
    return Promise.reject(new Error(`未配置 POST ${path}`))
  })
  api.put.mockResolvedValue({
    data: {
      ...triggerDetail,
      name: '每日资讯（固定）',
      definition_version: 4,
    },
  })
}


async function renderPage() {
  render(<TriggersPage />)
  await screen.findByRole('heading', { name: '触发器' })
}


beforeEach(() => {
  vi.clearAllMocks()
  configureApi()
})


afterEach(() => {
  cleanup()
})


describe('触发器管理页', () => {
  it('展示排程、目标和最近执行，并支持版本化启停与立即执行', async () => {
    await renderPage()

    expect(screen.getByText('每日资讯')).toBeInTheDocument()
    expect(screen.getByText('0 9 * * *')).toBeInTheDocument()
    expect(screen.getByText('10001')).toBeInTheDocument()
    expect(screen.getByText('succeeded')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '立即执行 每日资讯' }))
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/triggers/${trigger.id}/run`,
        expect.objectContaining({
          expected_version: 3,
          request_id: expect.stringMatching(/^trigger_run_/),
        }),
      )
    })
    expect(await screen.findByText(/执行 #33/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '停用 每日资讯' }))
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/triggers/${trigger.id}/toggle`,
        { expected_version: 3 },
      )
    })
  })

  it('加载完整定义后可切换为固定正文并按 expected_version 保存', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: '编辑 每日资讯' }))

    expect(api.get).toHaveBeenCalledWith('/triggers/7')
    await screen.findByDisplayValue('汇总今天最重要的 AI 资讯')
    expect(screen.getByRole('heading', { name: '编辑触发器 #7' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('名称'), {
      target: { value: '每日资讯（固定）' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /固定正文/ }))
    fireEvent.change(screen.getByLabelText('固定推送正文'), {
      target: { value: '固定推送内容' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith('/triggers/7', {
        name: '每日资讯（固定）',
        schedule: '0 9 * * *',
        target_type: 'private',
        target_id: '10001',
        expected_version: 3,
        content: '固定推送内容',
      })
    })
  })

  it('创建触发器时只提交所选定义来源', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: '创建触发器' }))

    expect(await screen.findByRole('heading', { name: '创建触发器' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新提醒' } })
    fireEvent.change(screen.getByLabelText('QQ 用户 ID'), { target: { value: '10002' } })
    fireEvent.change(screen.getByLabelText('模型生成指令'), {
      target: { value: '生成一句提醒' },
    })
    fireEvent.click(screen.getAllByRole('button', { name: '创建触发器' }).at(-1))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/triggers', {
        name: '新提醒',
        schedule: '0 9 * * *',
        target_type: 'private',
        target_id: '10002',
        prompt_template: '生成一句提醒',
      })
    })
  })
})
