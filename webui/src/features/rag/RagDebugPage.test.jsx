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
import { RagDebugPage } from './RagDebugPage'


vi.mock('../../api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))


function response(data) {
  return Promise.resolve({ data })
}


function debugResponse(sourceType, sourceResults = undefined) {
  return {
    run_id: 9,
    trace_id: 'trace-rag-ui',
    response: {
      source_type: sourceType,
      source_results: sourceResults,
      stages: { reranker_input_pairs: [], final_candidates: [] },
      score_breakdown: {
        degraded: false,
        fallback_reason: '',
        overall_status: sourceType === 'all' ? 'passed' : undefined,
        latency_ms: 12,
        final_items: 0,
      },
      candidates: [],
    },
  }
}


function configureApi() {
  api.get.mockImplementation(path => {
    if (path === '/rag/debug/runs') return response({ items: [] })
    if (path === '/rag/debug/status') {
      return response({
        index: { indexed_items: 1, buildable_chunks: 1, source_types: ['memory'] },
        reranker: {
          configured: true,
          source: 'local_model',
          model: 'bge-reranker-v2-m3',
          load_state: 'not_loaded',
        },
      })
    }
    if (path === '/groups') {
      return response({
        items: [
          { group_id: '4242', session_id: 'group_4242', session_name: '模型研究群' },
        ],
      })
    }
    return Promise.reject(new Error(`未配置 GET ${path}`))
  })
  api.post.mockImplementation((path, payload) => {
    if (path === '/rag/debug/query') {
      return response(debugResponse(payload.source_type))
    }
    if (path === '/rag/debug/build-index') {
      return response({ result: { enqueued: 1 }, index: { indexed_items: 1 } })
    }
    return Promise.reject(new Error(`未配置 POST ${path}`))
  })
}


beforeEach(() => {
  vi.clearAllMocks()
  configureApi()
})


afterEach(() => {
  cleanup()
})


describe('RAG Debug 页面', () => {
  it('group_memory 会提交明确群上下文和当前输入', async () => {
    render(<RagDebugPage />)
    await screen.findByRole('heading', { name: 'RAG Debug' })

    fireEvent.change(screen.getByLabelText('source'), {
      target: { value: 'group_memory' },
    })
    fireEvent.change(screen.getByLabelText('群上下文'), {
      target: { value: '4242' },
    })
    fireEvent.change(screen.getByLabelText('query'), {
      target: { value: '本地模型部署怎么做？' },
    })
    fireEvent.click(screen.getByRole('button', { name: '运行' }))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/rag/debug/query', {
        source_type: 'group_memory',
        query: '本地模型部署怎么做？',
        limit: 10,
        filters: {
          group_id: '4242',
          current_user_input: '本地模型部署怎么做？',
        },
      })
    })
  })

  it('group_analysis 从数据库窗口加载消息而不是提交空 messages', async () => {
    render(<RagDebugPage />)
    await screen.findByRole('heading', { name: 'RAG Debug' })

    fireEvent.change(screen.getByLabelText('source'), {
      target: { value: 'group_analysis' },
    })
    fireEvent.change(screen.getByLabelText('群上下文'), {
      target: { value: '4242' },
    })
    fireEvent.change(screen.getByLabelText('消息窗口（小时）'), {
      target: { value: '48' },
    })
    fireEvent.click(screen.getByRole('button', { name: '运行' }))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/rag/debug/query', {
        source_type: 'group_analysis',
        query: '端口冲突怎么解决',
        limit: 10,
        filters: {
          group_id: '4242',
          window_hours: 48,
          message_limit: 1000,
        },
      })
    })
    const payload = api.post.mock.calls.find(
      ([path]) => path === '/rag/debug/query',
    )[1]
    expect(payload.filters).not.toHaveProperty('messages')
  })

  it('all 展示每个真实来源的结果状态', async () => {
    api.post.mockImplementation((path, payload) => {
      if (path !== '/rag/debug/query') return response({})
      return response(debugResponse(payload.source_type, {
        memory: { status: 'passed', candidate_count: 2, latency_ms: 5 },
        group_memory: { status: 'empty', candidate_count: 0, latency_ms: 3 },
        sticker: { status: 'passed', candidate_count: 1, latency_ms: 2 },
        knowledge: { status: 'passed', candidate_count: 4, latency_ms: 6 },
        group_analysis: { status: 'passed', candidate_count: 3, latency_ms: 7 },
      }))
    })
    render(<RagDebugPage />)
    await screen.findByRole('heading', { name: 'RAG Debug' })

    fireEvent.change(screen.getByLabelText('source'), {
      target: { value: 'all' },
    })
    fireEvent.change(screen.getByLabelText('群上下文'), {
      target: { value: '4242' },
    })
    fireEvent.click(screen.getByRole('button', { name: '运行' }))

    expect(await screen.findByRole('heading', { name: '来源执行状态' })).toBeInTheDocument()
    expect(screen.getAllByText('memory').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('group_analysis').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('passed').length).toBeGreaterThanOrEqual(4)
    expect(screen.getByText('empty')).toBeInTheDocument()
  })

  it('群来源未选择群时在前端阻止请求并给出明确错误', async () => {
    render(<RagDebugPage />)
    await screen.findByRole('heading', { name: 'RAG Debug' })

    fireEvent.change(screen.getByLabelText('source'), {
      target: { value: 'group_memory' },
    })
    fireEvent.click(screen.getByRole('button', { name: '运行' }))

    expect(await screen.findByText('请选择或输入群上下文。')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalledWith(
      '/rag/debug/query',
      expect.anything(),
    )
  })
})
