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
import { SandboxFilesPage } from './SandboxFilesPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
  },
}))


const workspaceId = '00000000-0000-0000-0000-000000000001'
const revision = 'a'.repeat(64)
const workspace = {
  workspace_id: workspaceId,
  status: 'active',
  used_bytes: 12,
  quota_bytes: 64 * 1024 * 1024,
  sessions: ['qq:10001:private'],
  quota_status: 'applied',
}


function response(data) {
  return Promise.resolve({ data })
}


function configureApi() {
  api.get.mockImplementation((path, config) => {
    if (path === '/sandbox/workspaces') {
      return response({ items: [workspace] })
    }
    if (path === `/sandbox/workspaces/${workspaceId}/files`) {
      const directory = config?.params?.path || ''
      return response({
        entries: directory === ''
          ? [
              { path: 'docs', type: 'directory', size_bytes: 0, modified_at_ns: 1 },
              { path: 'README.md', type: 'file', size_bytes: 12, modified_at_ns: 2 },
            ]
          : [
              { path: 'docs/note.txt', type: 'file', size_bytes: 5, modified_at_ns: 3 },
            ],
        next_cursor: '',
        total_visible: 2,
      })
    }
    if (path === `/sandbox/workspaces/${workspaceId}/files/content`) {
      return response({
        path: config.params.path,
        content: '原始正文\n',
        size_bytes: 13,
        sha256: revision,
      })
    }
    return Promise.reject(new Error(`未配置 GET ${path}`))
  })
  api.put.mockImplementation((_path, body) => response({
    path: body.path,
    size_bytes: new TextEncoder().encode(body.content).length,
    used_bytes: 18,
    usage_delta_bytes: 6,
    sha256: 'b'.repeat(64),
  }))
}


async function renderPage() {
  render(<SandboxFilesPage />)
  await screen.findByRole('heading', { name: 'Sandbox 文件系统' })
  await screen.findByRole('button', { name: /README\.md/ })
}


beforeEach(() => {
  vi.clearAllMocks()
  configureApi()
})


afterEach(() => {
  cleanup()
})


describe('Sandbox 文件系统页', () => {
  it('在 Workspace 边界内浏览目录并读取精确文本', async () => {
    await renderPage()

    expect(screen.getAllByText(workspaceId, { exact: false }).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /docs/ }))
    expect(await screen.findByRole('button', { name: /note\.txt/ })).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith(
      `/sandbox/workspaces/${workspaceId}/files`,
      { params: { path: 'docs', cursor: '', limit: 200 } },
    )

    fireEvent.click(screen.getByRole('button', { name: /note\.txt/ }))
    expect(await screen.findByLabelText('文件内容')).toHaveValue('原始正文\n')
    expect(api.get).toHaveBeenCalledWith(
      `/sandbox/workspaces/${workspaceId}/files/content`,
      { params: { path: 'docs/note.txt' } },
    )
  })

  it('保存既有文件时携带读取到的 SHA-256 版本', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: /README\.md/ }))
    const editor = await screen.findByLabelText('文件内容')
    fireEvent.change(editor, { target: { value: '更新后的正文\n' } })
    fireEvent.click(screen.getByRole('button', { name: '保存文件' }))

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/sandbox/workspaces/${workspaceId}/files/content`,
        {
          path: 'README.md',
          content: '更新后的正文\n',
          expected_sha256: revision,
        },
      )
    })
    expect(await screen.findByText('文件 README.md 已保存。')).toBeInTheDocument()
  })

  it('sandboxd 滚动升级期间仍提供明确的只读兼容预览', async () => {
    const defaultGet = api.get.getMockImplementation()
    api.get.mockImplementation((path, config) => {
      if (path === `/sandbox/workspaces/${workspaceId}/files/content`) {
        return response({
          path: config.params.path,
          content: '兼容预览正文',
          size_bytes: 18,
          sha256: '',
          editable: false,
          preview_only: true,
          preview_truncated: false,
          preview_notice: '宿主 sandboxd 尚未升级，当前为只读兼容预览。',
        })
      }
      return defaultGet(path, config)
    })
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: /README\.md/ }))

    const preview = await screen.findByLabelText('文件内容')
    expect(preview).toHaveValue('兼容预览正文')
    expect(preview).toHaveAttribute('readonly')
    expect(screen.getByRole('status')).toHaveTextContent('只读兼容预览')
    expect(screen.queryByRole('button', { name: '保存文件' })).not.toBeInTheDocument()
  })

  it('新建文本文件使用空版本，并允许填写安全相对路径', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: '新建文本文件' }))

    fireEvent.change(screen.getByLabelText('Workspace 相对路径'), {
      target: { value: 'notes/new.txt' },
    })
    fireEvent.change(screen.getByLabelText('文件内容'), {
      target: { value: '新文件正文' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建文件' }))

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/sandbox/workspaces/${workspaceId}/files/content`,
        {
          path: 'notes/new.txt',
          content: '新文件正文',
          expected_sha256: null,
        },
      )
    })
  })

  it('并发保存冲突时保留编辑内容并显示服务端提示', async () => {
    api.put.mockRejectedValueOnce({
      response: {
        data: {
          detail: {
            code: 'edit_conflict',
            message: '文件已被其他操作修改，请重新加载后再保存',
            hint: '',
          },
        },
      },
    })
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: /README\.md/ }))
    const editor = await screen.findByLabelText('文件内容')
    fireEvent.change(editor, { target: { value: '我的未保存编辑' } })
    fireEvent.click(screen.getByRole('button', { name: '保存文件' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('文件已被其他操作修改')
    expect(screen.getByLabelText('文件内容')).toHaveValue('我的未保存编辑')
  })
})
