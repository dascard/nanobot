import { useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  PlugZap,
  RotateCw,
  Save,
  Settings2,
  Trash2,
  XCircle,
} from 'lucide-react'

import { ActionButton, Badge, Card, Field, Modal, PageHeader, Spinner } from '../../components/ui'
import { listWebSearchProviders, testWebSearchProvider, updateWebSearchProvider } from './api'

function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item?.msg || item?.message || JSON.stringify(item)).join('\n')
  if (detail && typeof detail === 'object') return detail.message || detail.error || JSON.stringify(detail)
  return error?.message || fallback
}

function sourceLabel(provider) {
  if (!provider.api_key_configured) return '未配置'
  if (provider.api_key_source === 'env') return '来自环境变量'
  if (provider.api_key_source === 'db') return '已配置'
  return '已配置'
}

function keyTone(provider) {
  if (!provider.requires_api_key && !provider.api_key_configured) return 'slate'
  return provider.api_key_configured ? 'emerald' : 'amber'
}

function errorLabel(code) {
  const labels = {
    missing_api_key: '请先配置 API Key',
    invalid_base_url: 'Base URL 无效',
    provider_auth_failed: '认证失败，请检查 API Key',
    provider_rate_limited: 'Provider 限流，请稍后重试',
    provider_timeout: 'Provider 请求超时',
    provider_bad_response: 'Provider 响应异常',
    dependency_missing: '服务端缺少依赖',
    not_implemented: 'Provider 未接入搜索运行时',
  }
  return labels[code] || code || '测试失败'
}

function ProviderModal({ provider, onClose, onSaved }) {
  const [form, setForm] = useState({
    enabled: provider.enabled,
    base_url: provider.base_url || provider.default_base_url || '',
    api_key: '',
  })
  const [clearApiKey, setClearApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const hasKeyField = provider.requires_api_key || provider.api_key_optional

  const save = async () => {
    setError('')
    setSaving(true)
    const payload = { enabled: !!form.enabled }
    if (provider.supports_base_url) payload.base_url = form.base_url.trim()
    if (clearApiKey) payload.clear_api_key = true
    if (form.api_key.trim()) payload.api_key = form.api_key.trim()
    try {
      await updateWebSearchProvider(provider.id, payload)
      setForm(prev => ({ ...prev, api_key: '' }))
      setClearApiKey(false)
      onSaved()
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} wide>
      <div className="p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-white">{provider.name}</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">{provider.id}</p>
          </div>
          <Badge tone={provider.testable ? 'emerald' : 'slate'}>{provider.testable ? '可测试' : '测试未启用'}</Badge>
        </div>

        <div className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
            <div>
              <div className="text-sm text-slate-200">启用</div>
              <div className="text-[11px] text-slate-500">{form.enabled ? 'ON' : 'OFF'}</div>
            </div>
            <button
              type="button"
              onClick={() => setForm(prev => ({ ...prev, enabled: !prev.enabled }))}
              className={`relative h-6 w-11 rounded-full transition-colors ${form.enabled ? 'bg-emerald-600' : 'bg-slate-700'}`}
              aria-label="切换启用状态"
            >
              <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-transform ${form.enabled ? 'left-6' : 'left-1'}`} />
            </button>
          </div>

          {provider.supports_base_url && (
            <Field id={`web-search-base-url-${provider.id}`} label="Base URL">
              <input
                id={`web-search-base-url-${provider.id}`}
                value={form.base_url}
                onChange={e => setForm(prev => ({ ...prev, base_url: e.target.value }))}
                placeholder={provider.default_base_url || 'https://search.example.com'}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
              />
            </Field>
          )}

          {hasKeyField && (
            <Field id={`web-search-api-key-${provider.id}`} label="API Key" hint={provider.api_key_configured ? `当前状态：${sourceLabel(provider)}` : ''}>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  id={`web-search-api-key-${provider.id}`}
                  type="password"
                  value={form.api_key}
                  onChange={e => {
                    setClearApiKey(false)
                    setForm(prev => ({ ...prev, api_key: e.target.value }))
                  }}
                  placeholder={provider.api_key_configured ? '留空表示不修改现有 API Key' : '请输入 API Key'}
                  className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
                />
                {provider.api_key_configured && (
                  <ActionButton
                    type="button"
                    tone={clearApiKey ? 'red' : 'slate'}
                    onClick={() => {
                      setClearApiKey(prev => !prev)
                      setForm(prev => ({ ...prev, api_key: '' }))
                    }}
                    className="gap-1"
                  >
                    <Trash2 className="h-3.5 w-3.5" /> 清空
                  </ActionButton>
                )}
              </div>
              {clearApiKey && <div className="mt-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">保存后清空后台覆盖密钥</div>}
            </Field>
          )}
        </div>

        {error && <div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <ActionButton type="button" onClick={onClose}>取消</ActionButton>
          <ActionButton type="button" tone="emerald" onClick={save} disabled={saving} className="gap-1">
            <Save className="h-3.5 w-3.5" /> {saving ? '保存中...' : '保存'}
          </ActionButton>
        </div>
      </div>
    </Modal>
  )
}

function TestResult({ result }) {
  if (!result) return null
  if (result.loading) {
    return <div className="text-xs text-slate-500">测试中...</div>
  }
  const ok = !!result.ok
  const Icon = ok ? CheckCircle2 : XCircle
  return (
    <div className={`mt-3 rounded-lg border px-3 py-2 text-xs ${ok ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : 'border-red-500/20 bg-red-500/10 text-red-300'}`}>
      <div className="flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5" />
        <span>{ok ? result.message : (result.message || errorLabel(result.error_code))}</span>
        {Number.isFinite(result.duration_ms) && <span className="text-slate-500">{result.duration_ms}ms</span>}
      </div>
      {!ok && result.error_code && <div className="mt-1 text-slate-400">{errorLabel(result.error_code)}</div>}
      {ok && Number.isFinite(result.sample_count) && <div className="mt-1 text-slate-400">样本 {result.sample_count}</div>}
    </div>
  )
}

export function WebSearchPage() {
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [testResults, setTestResults] = useState({})

  const load = async () => {
    setError('')
    setLoading(true)
    try {
      const data = await listWebSearchProviders()
      setProviders(data.providers || [])
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const stats = useMemo(() => {
    const enabled = providers.filter(p => p.enabled).length
    const configured = providers.filter(p => !p.requires_api_key || p.api_key_configured).length
    const testable = providers.filter(p => p.testable).length
    return { enabled, configured, testable }
  }, [providers])

  const runTest = async (provider) => {
    setTestResults(prev => ({ ...prev, [provider.id]: { loading: true } }))
    try {
      const result = await testWebSearchProvider(provider.id, 'nanobot')
      setTestResults(prev => ({ ...prev, [provider.id]: result }))
    } catch (e) {
      setTestResults(prev => ({
        ...prev,
        [provider.id]: { ok: false, message: formatApiError(e), error_code: 'request_failed' },
      }))
    }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeader
        title="搜索 API"
        description="配置用于 Web Search 的外部服务密钥和自托管搜索端点。"
        meta={[
          <span key="enabled">启用 {stats.enabled}/{providers.length}</span>,
          <span key="configured">可用配置 {stats.configured}/{providers.length}</span>,
          <span key="testable">可测试 {stats.testable}/{providers.length}</span>,
        ]}
        actions={
          <ActionButton type="button" onClick={load} className="gap-1">
            <RotateCw className="h-3.5 w-3.5" /> 刷新
          </ActionButton>
        }
      />

      {error && <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {providers.map(provider => {
          const testResult = testResults[provider.id]
          return (
            <Card key={provider.id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-semibold text-slate-100">{provider.name}</h2>
                    <span className="font-mono text-[11px] text-slate-500">{provider.id}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{provider.description}</p>
                </div>
                <Badge tone={provider.enabled ? 'emerald' : 'slate'}>{provider.enabled ? '启用' : '禁用'}</Badge>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {(provider.capabilities || []).map(capability => (
                  <Badge key={capability} tone="blue">{capability}</Badge>
                ))}
                <Badge tone={keyTone(provider)}>
                  <KeyRound className="mr-1 h-3 w-3" />
                  {sourceLabel(provider)}
                </Badge>
                <Badge tone={provider.testable ? 'emerald' : 'slate'}>{provider.testable ? '可测试' : '测试未启用'}</Badge>
              </div>

              <div className="mt-3 space-y-1 text-xs text-slate-400">
                <div className="flex gap-2">
                  <span className="shrink-0 text-slate-500">Base URL</span>
                  <span className="min-w-0 break-all font-mono text-slate-300">{provider.supports_base_url ? (provider.base_url || provider.default_base_url || '未配置') : '不适用'}</span>
                </div>
                <div className="flex gap-2">
                  <span className="shrink-0 text-slate-500">文档</span>
                  <a href={provider.docs_url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 text-emerald-300 hover:text-emerald-200">
                    <span className="truncate">{provider.requires_api_key ? '获取 API Key' : '查看文档'}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                </div>
              </div>

              <TestResult result={testResult} />

              <div className="mt-4 flex flex-wrap justify-end gap-2">
                <ActionButton type="button" onClick={() => setEditing(provider)} className="gap-1">
                  <Settings2 className="h-3.5 w-3.5" /> 配置
                </ActionButton>
                <ActionButton type="button" tone="emerald" onClick={() => runTest(provider)} disabled={testResult?.loading} className="gap-1">
                  <PlugZap className="h-3.5 w-3.5" /> {testResult?.loading ? '测试中...' : '测试'}
                </ActionButton>
              </div>
            </Card>
          )
        })}
      </div>

      {editing && (
        <ProviderModal
          provider={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            load()
          }}
        />
      )}
    </div>
  )
}
