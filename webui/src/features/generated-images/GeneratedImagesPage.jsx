import { useCallback, useEffect, useState } from 'react'
import { ImagePlus, RefreshCw, Search, X } from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  AuthImage,
  Badge,
  Card,
  Field,
  Modal,
  PageHeader,
  Pagination,
  Spinner,
  Toolbar,
} from '../../components/ui'

function formatBytes(value) {
  const n = Number(value || 0)
  if (!Number.isFinite(n) || n <= 0) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

function metaLine(item) {
  return [item.model, item.size, item.quality].filter(Boolean).join(' · ') || '-'
}

function formatApiError(e, fallback = '请求失败') {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail) return JSON.stringify(detail)
  return e?.message || fallback
}

export function GeneratedImagesPage() {
  const [data, setData] = useState({ items: [], total: 0, page: 1, limit: 24 })
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(24)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [testPrompt, setTestPrompt] = useState('')
  const [testSize, setTestSize] = useState('1024x1024')
  const [testQuality, setTestQuality] = useState('high')
  const [testBackground, setTestBackground] = useState('auto')
  const [generating, setGenerating] = useState(false)
  const [generationError, setGenerationError] = useState('')
  const [generated, setGenerated] = useState(null)

  const load = useCallback((overrides = {}) => {
    const nextSearch = overrides.search ?? search
    const nextPage = overrides.page ?? page
    const nextLimit = overrides.limit ?? limit
    setLoading(true)
    setError('')
    api.get('/generated-images', { params: { search: nextSearch, page: nextPage, limit: nextLimit } })
      .then(r => setData(r.data || { items: [], total: 0, page: nextPage, limit: nextLimit }))
      .catch(e => setError(formatApiError(e, '加载失败')))
      .finally(() => setLoading(false))
  }, [search, page, limit])

  useEffect(() => {
    const timer = window.setTimeout(() => { load() }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const runGeneration = useCallback((event) => {
    event.preventDefault()
    const prompt = testPrompt.trim()
    if (!prompt) {
      setGenerationError('请输入提示词')
      return
    }
    setGenerating(true)
    setGenerationError('')
    api.post('/generated-images', {
      prompt,
      size: testSize,
      quality: testQuality,
      background: testBackground,
    })
      .then(r => {
        const item = r.data?.item || null
        setGenerated(item)
        setSearch('')
        setPage(1)
        load({ search: '', page: 1 })
      })
      .catch(e => setGenerationError(formatApiError(e, '生成失败')))
      .finally(() => setGenerating(false))
  }, [testPrompt, testSize, testQuality, testBackground, load])

  const items = data.items || []

  return (
    <div>
      <PageHeader
        title="生成图片"
        description="查看 image_generation 工具生成的图片和原始提示词。图片文件走 admin 鉴权读取，不暴露本地路径。"
        meta={[
          <span key="total">总数：<span className="text-slate-300">{data.total || 0}</span></span>,
          <span key="page">第 {data.page || page} 页</span>,
        ]}
        actions={
          <ActionButton onClick={load} disabled={loading}>
            <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </ActionButton>
        }
      />

      <Card className="mb-4 overflow-hidden">
        <form onSubmit={runGeneration} className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2">
              <ImagePlus className="h-4 w-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-slate-100">测试生图</h2>
            </div>
            <Field id="generated-image-prompt" label="提示词">
              <textarea
                id="generated-image-prompt"
                value={testPrompt}
                onChange={e => setTestPrompt(e.target.value)}
                rows={5}
                placeholder="画一只红熊猫喝奶茶，贴纸风格，透明背景"
                className="min-h-32 w-full resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm leading-6 text-slate-200 outline-none focus:border-emerald-500"
              />
            </Field>
            {generationError && (
              <div className="mt-2 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
                {generationError}
              </div>
            )}
          </div>
          <div className="grid gap-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 lg:grid-cols-1">
              <Field id="generated-image-size" label="尺寸">
                <select
                  id="generated-image-size"
                  value={testSize}
                  onChange={e => setTestSize(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-200"
                >
                  <option value="1024x1024">1024x1024</option>
                  <option value="1536x1024">1536x1024</option>
                  <option value="1024x1536">1024x1536</option>
                  <option value="auto">auto</option>
                </select>
              </Field>
              <Field id="generated-image-quality" label="质量">
                <select
                  id="generated-image-quality"
                  value={testQuality}
                  onChange={e => setTestQuality(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-200"
                >
                  <option value="high">high</option>
                  <option value="medium">medium</option>
                  <option value="low">low</option>
                  <option value="auto">auto</option>
                </select>
              </Field>
              <Field id="generated-image-background" label="背景">
                <select
                  id="generated-image-background"
                  value={testBackground}
                  onChange={e => setTestBackground(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-200"
                >
                  <option value="auto">auto</option>
                  <option value="transparent">transparent</option>
                  <option value="opaque">opaque</option>
                </select>
              </Field>
            </div>
            <ActionButton
              type="submit"
              tone="emerald"
              disabled={generating || !testPrompt.trim()}
              className="w-full py-2"
            >
              <ImagePlus className="mr-1.5 h-3.5 w-3.5" />
              {generating ? '生成中' : '生成测试'}
            </ActionButton>
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-3">
              <div className="mb-2 text-[11px] font-medium text-slate-500">最近结果</div>
              {generated ? (
                <button
                  type="button"
                  onClick={() => setSelected(generated)}
                  className="grid w-full grid-cols-[72px_minmax(0,1fr)] gap-3 text-left"
                >
                  <div className="h-[72px] w-[72px] overflow-hidden rounded-md bg-slate-800">
                    <AuthImage
                      url={generated.image_url || `/api/v1/admin/generated-images/${generated.id}/image`}
                      alt={generated.prompt_preview || generated.id}
                      className="h-full w-full object-cover"
                    />
                  </div>
                  <div className="min-w-0">
                    <div className="line-clamp-2 text-xs leading-5 text-slate-200">
                      {generated.prompt_preview || generated.prompt || '（无提示词记录）'}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {metaLine(generated)}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {formatTime(generated.created_at)}
                    </div>
                  </div>
                </button>
              ) : (
                <div className="flex min-h-[72px] items-center text-xs text-slate-500">
                  本页生成的图片会显示在这里
                </div>
              )}
            </div>
          </div>
        </form>
      </Card>

      <Toolbar>
        <Field id="generated-image-search" label="搜索提示词" className="min-w-[240px] flex-1">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-500" />
            <input
              id="generated-image-search"
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(1) }}
              placeholder="输入主体、风格或关键词"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-8 pr-8 text-sm text-slate-200 outline-none focus:border-emerald-500"
            />
            {search && (
              <button
                type="button"
                aria-label="清空搜索"
                onClick={() => { setSearch(''); setPage(1) }}
                className="absolute right-2 top-2 rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </Field>
        <Field id="generated-image-limit" label="每页数量">
          <select
            id="generated-image-limit"
            value={limit}
            onChange={e => { setLimit(Number(e.target.value)); setPage(1) }}
            className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-200"
          >
            <option value={12}>12</option>
            <option value={24}>24</option>
            <option value={48}>48</option>
            <option value={96}>96</option>
          </select>
        </Field>
      </Toolbar>

      {error && (
        <Card className="mb-4 border-red-900/60 bg-red-950/30 p-3 text-sm text-red-300">
          {error}
        </Card>
      )}

      {loading ? <Spinner /> : items.length === 0 ? (
        <Card className="flex min-h-64 items-center justify-center p-8 text-center">
          <div>
            <div className="text-sm font-medium text-slate-300">没有生成图片</div>
            <div className="mt-1 text-xs text-slate-500">
              {search ? '当前搜索没有匹配结果。' : '调用 image_generation 工具后会在这里出现。'}
            </div>
          </div>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {items.map(item => (
              <Card key={item.id} className="overflow-hidden">
                <button
                  type="button"
                  onClick={() => setSelected(item)}
                  className="block w-full bg-slate-950 text-left"
                >
                  <div className="aspect-square w-full overflow-hidden bg-slate-800">
                    <AuthImage
                      url={item.image_url || `/api/v1/admin/generated-images/${item.id}/image`}
                      alt={item.prompt_preview || item.id}
                      className="h-full w-full object-cover transition-transform duration-200 hover:scale-[1.02]"
                    />
                  </div>
                </button>
                <div className="space-y-2 p-3">
                  <div className="min-h-[40px] text-sm leading-5 text-slate-200 line-clamp-2" title={item.prompt}>
                    {item.prompt_preview || item.prompt || '（无提示词记录）'}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {item.model && <Badge tone="blue">{item.model}</Badge>}
                    {item.size && <Badge>{item.size}</Badge>}
                    {item.quality && <Badge tone="emerald">{item.quality}</Badge>}
                    {item.missing_file && <Badge tone="red">文件缺失</Badge>}
                  </div>
                  <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
                    <span className="truncate">{formatTime(item.created_at)}</span>
                    <span className="shrink-0">{formatBytes(item.bytes)}</span>
                  </div>
                </div>
              </Card>
            ))}
          </div>
          <Pagination page={page} total={data.total || 0} limit={limit} onChange={setPage} />
        </>
      )}

      {selected && (
        <Modal wide onClose={() => setSelected(null)}>
          <div className="grid max-h-[88vh] grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="flex min-h-[320px] items-center justify-center bg-slate-950 p-3">
              <AuthImage
                url={selected.image_url || `/api/v1/admin/generated-images/${selected.id}/image`}
                alt={selected.prompt || selected.id}
                className="max-h-[72vh] max-w-full rounded object-contain"
              />
            </div>
            <div className="overflow-auto border-t border-slate-800 p-4 md:border-l md:border-t-0">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">图片详情</h2>
                  <div className="mt-1 font-mono text-[11px] text-slate-500">{selected.id}</div>
                </div>
                <button
                  type="button"
                  aria-label="关闭预览"
                  onClick={() => setSelected(null)}
                  className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="mb-4">
                <div className="mb-1 text-[11px] font-medium text-slate-500">完整提示词</div>
                <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs leading-5 text-slate-200 whitespace-pre-wrap">
                  {selected.prompt || '（无提示词记录）'}
                </div>
              </div>
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-slate-950 p-2">
                  <dt className="text-slate-500">模型</dt>
                  <dd className="mt-1 truncate text-slate-200" title={selected.model}>{selected.model || '-'}</dd>
                </div>
                <div className="rounded-lg bg-slate-950 p-2">
                  <dt className="text-slate-500">规格</dt>
                  <dd className="mt-1 truncate text-slate-200">{metaLine(selected)}</dd>
                </div>
                <div className="rounded-lg bg-slate-950 p-2">
                  <dt className="text-slate-500">生成时间</dt>
                  <dd className="mt-1 truncate text-slate-200">{formatTime(selected.created_at)}</dd>
                </div>
                <div className="rounded-lg bg-slate-950 p-2">
                  <dt className="text-slate-500">文件大小</dt>
                  <dd className="mt-1 truncate text-slate-200">{formatBytes(selected.bytes)}</dd>
                </div>
              </dl>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
