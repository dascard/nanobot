import { useCallback, useEffect, useState } from 'react'

import { getRuntimeModuleDiagnostics } from '../../api/generated/adminClient'
import {
  ActionButton,
  Badge,
  Card,
  MiniStat,
  PageHeader,
  Spinner,
} from '../../components/ui'

function errorMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return error?.message || '模块诊断数据加载失败'
}

function healthTone(health) {
  if (!health) return 'slate'
  if (health.ready) return 'emerald'
  if (health.status === 'degraded' || health.status === 'starting') {
    return 'amber'
  }
  return 'red'
}

function shortHash(value) {
  return value ? `${value.slice(0, 12)}…` : '-'
}

function securityTone(level) {
  if (level === 'production_write' || level === 'host_privileged') return 'red'
  if (level === 'networked') return 'amber'
  return 'slate'
}

export function RuntimeDiagnosticsPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async ({ silent = false } = {}) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      setData(await getRuntimeModuleDiagnostics())
    } catch (loadError) {
      setError(errorMessage(loadError))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { load() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [load])

  if (loading) return <Spinner />

  const modules = data?.modules || []
  const verificationSuites = data?.verification_suites || []
  const readyCount = modules.filter(module => module.health?.ready).length

  return (
    <div>
      <PageHeader
        title="Registry / Module 诊断"
        description="只读展示当前 Composition Root、冻结 Registry 与内建模块生命周期；不支持运行时插件发现或远程模块加载。"
        actions={(
          <ActionButton
            type="button"
            onClick={() => load({ silent: true })}
            disabled={refreshing}
          >
            {refreshing ? '刷新中…' : '刷新'}
          </ActionButton>
        )}
        meta={(
          <>
            <span>state: {data?.composition_state || 'unavailable'}</span>
            <span>generation: {data?.composition_generation || 0}</span>
            <span>composition: {shortHash(data?.composition_sha256)}</span>
          </>
        )}
      />

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {data && !data.available && (
        <div role="status" className="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          Composition Root 当前不可用：{data.error_code || 'unknown'}
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        <MiniStat
          label="Composition"
          value={data?.ready ? 'ready' : data?.composition_state || 'unavailable'}
          tone={data?.ready ? 'emerald' : 'amber'}
        />
        <MiniStat label="模块数" value={modules.length} />
        <MiniStat
          label="Ready 模块"
          value={`${readyCount}/${modules.length}`}
          tone={readyCount === modules.length && modules.length > 0 ? 'emerald' : 'amber'}
        />
        <MiniStat
          label="验证套件"
          value={verificationSuites.length}
          tone="blue"
        />
      </div>

      <div className="mb-4 grid gap-3 md:grid-cols-3">
        {[
          ['Module Registry', data?.module_registry],
          ['Contribution Registry', data?.contribution_registry],
          ['Verification Suite Registry', data?.verification_registry],
        ].map(([label, registry]) => (
          <Card key={label} className="p-3">
            <div className="text-xs font-medium text-slate-300">{label}</div>
            <div className="mt-2 grid gap-1 text-[11px] text-slate-500">
              <span>namespace: {registry?.namespace || '-'}</span>
              <span>generation: {registry?.generation || 0}</span>
              <span className="break-all font-mono">sha256: {registry?.sha256 || '-'}</span>
            </div>
          </Card>
        ))}
      </div>

      <Card className="mb-4 p-4">
        <details>
          <summary className="cursor-pointer text-sm font-medium text-slate-200">
            Verification Suite 合同（{verificationSuites.length}）
          </summary>
          <div className="mt-3 space-y-2">
            {verificationSuites.map(suite => (
              <div
                key={suite.suite_id}
                className="rounded-lg border border-slate-800 bg-slate-950/50 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-slate-200">{suite.suite_id}</span>
                  <Badge tone={securityTone(suite.security_level)}>
                    {suite.security_level}
                  </Badge>
                  {suite.always_required && <Badge tone="blue">commit gate</Badge>}
                  <Badge tone={suite.allow_skip ? 'amber' : 'emerald'}>
                    {suite.allow_skip ? 'allow skip' : 'fail closed'}
                  </Badge>
                </div>
                <div className="mt-2 grid gap-1 text-[11px] text-slate-500 md:grid-cols-2">
                  <span>owner: {suite.owner}</span>
                  <span>timeout: {suite.timeout_seconds}s</span>
                  <span className="md:col-span-2">
                    preconditions: {suite.preconditions.join(', ') || '-'}
                  </span>
                  <span className="md:col-span-2">
                    profiles: {suite.artifact_profiles.join(', ') || '-'}
                  </span>
                  <span className="break-all font-mono md:col-span-2">
                    command: {suite.command.join(' ')}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </details>
      </Card>

      <div className="space-y-3">
        {modules.map(module => (
          <Card key={module.module_id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-slate-100">{module.module_id}</span>
                  <Badge tone={healthTone(module.health)}>
                    {module.health?.status || data?.composition_state || 'unavailable'}
                  </Badge>
                  <Badge>{module.lifecycle}</Badge>
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                  owner {module.owner} · domain {module.domain} · version {module.version}
                </div>
              </div>
              <div className="text-right text-[11px] text-slate-500">
                <div>start {module.startup_phase}</div>
                <div>stop {module.shutdown_phase}</div>
              </div>
            </div>

            <div className="mt-3 grid gap-3 text-xs md:grid-cols-2 xl:grid-cols-4">
              <div>
                <div className="mb-1 text-[11px] text-slate-500">Required modules</div>
                <div className="break-words text-slate-300">{module.required_modules.join(', ') || '-'}</div>
              </div>
              <div>
                <div className="mb-1 text-[11px] text-slate-500">Optional modules</div>
                <div className="break-words text-slate-300">{module.optional_modules.join(', ') || '-'}</div>
              </div>
              <div>
                <div className="mb-1 text-[11px] text-slate-500">Capabilities</div>
                <div className="break-words text-slate-300">{module.provided_capabilities.join(', ') || '-'}</div>
              </div>
              <div>
                <div className="mb-1 text-[11px] text-slate-500">Feature flag</div>
                <div className="break-words font-mono text-slate-300">{module.feature_flag || '-'}</div>
              </div>
            </div>

            <details className="mt-3 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
              <summary className="cursor-pointer text-xs text-slate-400">
                贡献与健康检查
              </summary>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="space-y-1 text-[11px] text-slate-400">
                  {(module.contributions || []).map(item => (
                    <div key={`${item.kind}:${item.contribution_id}`}>
                      <Badge>{item.kind}</Badge>
                      <span className="ml-2 font-mono">{item.contribution_id}</span>
                    </div>
                  ))}
                  {(module.contributions || []).length === 0 && <span>-</span>}
                </div>
                <div className="space-y-1 text-[11px]">
                  {(module.health?.checks || []).map(check => (
                    <div key={check.name} className={check.healthy ? 'text-emerald-300' : 'text-red-300'}>
                      {check.name}: {check.healthy ? 'healthy' : check.detail_code || 'unhealthy'}
                    </div>
                  ))}
                  {!module.health && <span className="text-slate-500">当前状态没有运行时健康快照</span>}
                </div>
              </div>
            </details>
          </Card>
        ))}
        {modules.length === 0 && (
          <Card className="py-16 text-center text-sm text-slate-600">
            当前没有可展示的模块快照
          </Card>
        )}
      </div>
    </div>
  )
}
