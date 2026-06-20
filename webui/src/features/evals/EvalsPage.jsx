import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import { Badge, Card, JsonBlock, MiniStat, Modal, Pagination } from '../../components/ui'

// ── Eval ──
export function EvalsPage() {
  const [tab, setTab] = useState('candidates')
  const [candidates, setCandidates] = useState({ items: [], total: 0 })
  const [candidateSummary, setCandidateSummary] = useState(null)
  const [candPage, setCandPage] = useState(1)
  const [suiteFilter, setSuiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [preflightOpen, setPreflightOpen] = useState(false)
  const [preflightResult, setPreflightResult] = useState(null)
  const [preflightError, setPreflightError] = useState('')
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [detail, setDetail] = useState(null)
  const [showLabel, setShowLabel] = useState(null)
  const [labelSuite, setLabelSuite] = useState('')
  const [labelFields, setLabelFields] = useState({})
  const [labelShowJson, setLabelShowJson] = useState(false)
  const [labelNote, setLabelNote] = useState('')
  const [labelError, setLabelError] = useState('')
  const [expectedContract, setExpectedContract] = useState(null)
  const [contractError, setContractError] = useState('')
  const [promoteCaseId, setPromoteCaseId] = useState(null)
  const [promoteTargetDataset, setPromoteTargetDataset] = useState('regression')
  const [promotePlan, setPromotePlan] = useState(null)
  const [promoteError, setPromoteError] = useState('')
  const [promoteBusy, setPromoteBusy] = useState(false)
  const [runs, setRuns] = useState([])
  const [runDetail, setRunDetail] = useState(null)
  const [running, setRunning] = useState(false)
  const [sampleInfo, setSampleInfo] = useState(null)

  const loadCandidates = useCallback(() => {
    const params = { page: candPage, limit: 20 }
    if (suiteFilter) params.suite = suiteFilter
    if (statusFilter) params.status = statusFilter
    if (sourceFilter) params.source = sourceFilter
    api.get('/evals/candidates', { params }).then(r => {
      const payload = r.data || { items: [], total: 0 }
      setCandidates(payload)
      setCandidateSummary(payload.summary || null)
    })
  }, [candPage, suiteFilter, statusFilter, sourceFilter])

  const loadRuns = useCallback(() => {
    api.get('/evals/runs', { params: { limit: 20 } }).then(r => setRuns(r.data.items || []))
  }, [])

  useEffect(() => {
    if (tab === 'candidates') loadCandidates()
    if (tab === 'runs') loadRuns()
  }, [tab, loadCandidates, loadRuns])

  useEffect(() => {
    api.get('/evals/expected-contract')
      .then(r => { setExpectedContract(r.data); setContractError('') })
      .catch(e => setContractError(e.response?.data?.detail || e.message))
  }, [])

  const setLabelField = (key, value) => {
    setLabelError('')
    setLabelFields(prev => ({ ...prev, [key]: value }))
  }

  const fieldValue = (key) => labelFields[key] ?? ''

  const fieldSchema = (key) => expectedContract?.field_schema?.[key] || {}

  const readinessReason = (candidate) => {
    const reasons = candidate.readiness?.blocking_reasons || []
    const first = reasons[0]
    if (!first) return ''
    if (first.code && first.message) return `${first.code}: ${first.message}`
    return first.code || first.message || ''
  }

  const fieldsForSuite = (suite) => (
    expectedContract?.suite_presets?.[suite]?.fields
    || expectedContract?.suite_presets?.reply_contract?.fields
    || []
  ).filter(key => !fieldSchema(key).advanced)

  const parseFieldValue = (key, value) => {
    const schema = fieldSchema(key)
    if (value === '' || value === undefined || value === null) return undefined
    if (schema.type === 'boolean') return value === true || value === 'true'
    if (schema.type === 'integer') {
      const parsed = Number.parseInt(value, 10)
      if (Number.isNaN(parsed)) throw new Error(`${schema.label || key} 必须是整数`)
      return parsed
    }
    if (schema.type === 'string_list') {
      return String(value).split(',').map(item => item.trim()).filter(Boolean)
    }
    if (schema.type === 'array' || schema.type === 'object') {
      try { return JSON.parse(value) } catch { throw new Error(`${schema.label || key} 必须是合法 JSON`) }
    }
    if (schema.type === 'string_or_number') {
      const text = String(value).trim()
      if (text === '') return undefined
      const numberValue = Number(text)
      return Number.isNaN(numberValue) ? text : numberValue
    }
    return value
  }

  const buildExpectedFromLabelForm = () => {
    if (!expectedContract) throw new Error('期望字段契约尚未加载')
    if (labelShowJson && labelFields._rawJson) {
      try { return JSON.parse(labelFields._rawJson) } catch { throw new Error('JSON 格式错误') }
    }
    const expectedJson = {}
    for (const key of fieldsForSuite(labelSuite)) {
      const parsed = parseFieldValue(key, labelFields[key])
      if (parsed !== undefined && !(Array.isArray(parsed) && parsed.length === 0)) {
        expectedJson[key] = parsed
      }
    }
    return expectedJson
  }

  const validateExpectedJson = (expectedJson) => {
    if (!expectedContract) return '期望字段契约尚未加载'
    const keys = Object.keys(expectedJson)
    if (keys.length === 0) return '请至少填写一个可评分期望字段'
    if (expectedJson.needs_label === true) return 'expected 不能包含 needs_label=true'
    const deprecated = keys.filter(key => (expectedContract.deprecated_keys || []).includes(key))
    if (deprecated.length > 0) return `以下字段已废弃，不能提交到 expected：${deprecated.join(', ')}`
    const scoreable = new Set(expectedContract.scoreable_keys || [])
    const unknown = keys.filter(key => !scoreable.has(key))
    if (unknown.length > 0) return `以下字段不是 scorer 可评分字段：${unknown.join(', ')}`
    return ''
  }

  const openLabel = (candidate) => {
    setShowLabel(candidate.case_id)
    setLabelSuite(candidate.suite)
    setLabelFields({})
    setLabelNote('')
    setLabelError('')
    setLabelShowJson(false)
  }

  const runEval = () => {
    setRunning(true)
    api.post('/evals/run', { suite: 'regression' })
      .then(r => { alert(`Eval 完成: ${r.data.passed}/${r.data.total} passed`); loadRuns() })
      .catch(e => alert(e.response?.data?.detail || e.message))
      .finally(() => setRunning(false))
  }

  const runSample = () => {
    api.post('/evals/sample/run')
      .then(r => { setSampleInfo(r.data); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const preflightCurrentPage = () => {
    const caseIds = (candidates.items || []).map(candidate => candidate.case_id).filter(Boolean)
    if (caseIds.length === 0) return
    setPreflightLoading(true)
    setPreflightResult(null)
    setPreflightError('')
    api.post('/evals/candidates/preflight', {
      case_ids: caseIds,
      target_dataset: suiteFilter.trim() || 'regression',
      suite: suiteFilter || undefined,
      status: statusFilter || undefined,
      source: sourceFilter || undefined,
      limit: caseIds.length,
    })
      .then(r => { setPreflightResult(r.data); setPreflightOpen(true) })
      .catch(e => { setPreflightError(e.response?.data?.detail || e.message); setPreflightOpen(true) })
      .finally(() => setPreflightLoading(false))
  }

  const loadDetail = (caseId) => {
    api.get(`/evals/candidates/${encodeURIComponent(caseId)}`)
      .then(r => setDetail(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doLabel = (caseId) => {
    let expectedJson
    try {
      expectedJson = buildExpectedFromLabelForm()
    } catch (e) {
      setLabelError(e.message)
      return
    }
    const error = validateExpectedJson(expectedJson)
    if (error) {
      setLabelError(error)
      return
    }
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/label`, { expected: expectedJson, note: labelNote })
      .then(() => { setShowLabel(null); setLabelError(''); loadCandidates() })
      .catch(e => setLabelError(e.response?.data?.detail || e.message))
  }

  const doIgnore = (caseId) => {
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/ignore`)
      .then(() => loadCandidates())
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const openPromote = (candidate) => {
    setPromoteCaseId(candidate.case_id)
    setPromoteTargetDataset(candidate.suite || 'regression')
    setPromotePlan(null)
    setPromoteError('')
  }

  const previewPromote = () => {
    if (!promoteCaseId) return
    const target = promoteTargetDataset.trim()
    if (!target) {
      setPromoteError('请填写目标数据集')
      return
    }
    setPromoteBusy(true)
    setPromoteError('')
    api.post(`/evals/candidates/${encodeURIComponent(promoteCaseId)}/promote`, { dry_run: true, target_dataset: target })
      .then(r => setPromotePlan(r.data))
      .catch(e => { setPromotePlan(null); setPromoteError(e.response?.data?.detail || e.message) })
      .finally(() => setPromoteBusy(false))
  }

  const confirmPromote = () => {
    if (!promoteCaseId) return
    const target = promoteTargetDataset.trim()
    if (!target) {
      setPromoteError('请填写目标数据集')
      return
    }
    setPromoteBusy(true)
    setPromoteError('')
    api.post(`/evals/candidates/${encodeURIComponent(promoteCaseId)}/promote`, { dry_run: false, target_dataset: target })
      .then(() => { setPromoteCaseId(null); setPromotePlan(null); loadCandidates() })
      .catch(e => setPromoteError(e.response?.data?.detail || e.message))
      .finally(() => setPromoteBusy(false))
  }

  const loadRunDetail = (runId) => {
    api.get(`/evals/runs/${runId}`).then(r => setRunDetail(r.data)).catch(e => alert(e.message))
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Eval 评测系统</h1>
          <p className="text-slate-500 text-sm">候选管理、标签、回归测试与运行历史</p>
        </div>
        <div className="flex gap-2">
          <button onClick={runSample} className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">采样</button>
          <button onClick={runEval} disabled={running}
            className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-xs font-medium">
            {running ? '运行中...' : '运行 Eval'}
          </button>
        </div>
      </div>
      {sampleInfo && (
        <div className="mb-3 px-4 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-sm text-emerald-400">
          采样完成: 新增 {sampleInfo.created} 个候选
        </div>
      )}
      <Card className="sticky top-0 z-10 p-2 mb-4 flex gap-1 flex-wrap bg-slate-950/95 backdrop-blur border border-slate-800">
        <button onClick={() => setTab('candidates')}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'candidates' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>候选列表</button>
        <button onClick={() => setTab('runs')}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${tab === 'runs' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'}`}>运行历史</button>
      </Card>

      {tab === 'candidates' && (
        <div>
          <div className="flex gap-2 mb-4">
            <input value={suiteFilter} onChange={e => { setSuiteFilter(e.target.value); setCandPage(1) }}
              placeholder="suite 过滤" className="w-32 p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs" />
            <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setCandPage(1) }}
              className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
              <option value="">全部状态</option>
              <option value="candidate">candidate</option>
              <option value="labeled">labeled</option>
              <option value="ignored">ignored</option>
              <option value="promoted">promoted</option>
            </select>
            <select value={sourceFilter} onChange={e => { setSourceFilter(e.target.value); setCandPage(1) }}
              className="p-2 rounded-lg bg-slate-950 border border-slate-700 text-xs">
              <option value="">全部来源</option>
              <option value="log">log</option>
              <option value="db">db</option>
            </select>
          </div>
          {candidateSummary && (
            <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-8">
              <MiniStat label="summary 总数" value={candidateSummary.total ?? candidates.total} />
              <MiniStat label="待标记" value={candidateSummary.by_status?.candidate || 0} tone="amber" />
              <MiniStat label="已标记" value={candidateSummary.by_status?.labeled || 0} tone="blue" />
              <MiniStat label="可提升" value={candidateSummary.readiness?.ready || 0} tone="emerald" />
              <MiniStat label="阻断" value={candidateSummary.readiness?.blocked || 0} tone="red" />
              <MiniStat label="已忽略" value={candidateSummary.by_status?.ignored || 0} />
              <MiniStat label="已提升" value={candidateSummary.by_status?.promoted || 0} tone="emerald" />
              <button onClick={preflightCurrentPage} disabled={preflightLoading || !(candidates.items || []).length}
                className="min-h-[72px] rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-xs font-medium text-indigo-200 transition-colors hover:bg-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50">
                {preflightLoading ? '预检中...' : '预检当前页'}
              </button>
            </div>
          )}
          <Card>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-2">case_id</th>
                <th className="px-3 py-2">suite</th>
                <th className="px-3 py-2">来源</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">资格</th>
                <th className="px-3 py-2">描述</th>
                <th className="px-3 py-2">创建时间</th>
                <th className="px-3 py-2">操作</th>
              </tr></thead>
              <tbody>
                {(candidates.items || []).map(candidate => (
                  <tr key={candidate.case_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-3 py-2 font-mono max-w-[200px] truncate">{candidate.case_id}</td>
                    <td className="px-3 py-2"><Badge>{candidate.suite}</Badge></td>
                    <td className="px-3 py-2 text-slate-400">{candidate.source}</td>
                    <td className="px-3 py-2">
                      <Badge tone={candidate.status === 'promoted' ? 'emerald' : candidate.status === 'labeled' ? 'blue' : candidate.status === 'ignored' ? 'slate' : 'amber'}>{candidate.status}</Badge>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex max-w-[180px] flex-col gap-1">
                        <Badge tone={candidate.readiness?.ready ? 'emerald' : 'red'}>
                          {candidate.readiness?.ready ? 'ready' : 'blocked'}
                        </Badge>
                        {!candidate.readiness?.ready && (
                          <span className="truncate text-[11px] text-slate-500" title={readinessReason(candidate)}>
                            {readinessReason(candidate) || '-'}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 max-w-[300px] truncate text-slate-400">{candidate.description}</td>
                    <td className="px-3 py-2 text-slate-500">{candidate.created_at}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        <button onClick={() => loadDetail(candidate.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">详情</button>
                        {candidate.status === 'candidate' && (
                          <>
                            <button onClick={() => openLabel(candidate)}
                              className="px-2 py-1 bg-indigo-700/50 hover:bg-indigo-700 text-indigo-300 rounded text-xs">标记</button>
                            <button onClick={() => doIgnore(candidate.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">忽略</button>
                          </>
                        )}
                        {candidate.status === 'labeled' && (
                          <button onClick={() => openPromote(candidate)}
                            disabled={candidate.status === 'labeled' && !candidate.readiness?.ready}
                            title={readinessReason(candidate)}
                            className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50 text-emerald-300 rounded text-xs">提升</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Pagination page={candPage} total={candidates.total} limit={20} onChange={setCandPage} />

          {/* Detail modal */}
          {detail && (
            <Modal onClose={() => setDetail(null)} wide>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">{detail.case_id}</h2>
                <div className="text-xs text-slate-400 mb-4">{detail.description}</div>
                <div className="space-y-3">
                  <div><div className="text-xs text-slate-500 mb-1">input</div><JsonBlock value={detail.input} className="max-h-48" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">expected</div><JsonBlock value={detail.expected} className="max-h-32" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">readiness</div><JsonBlock value={detail.readiness || {}} className="max-h-40" /></div>
                  <div><div className="text-xs text-slate-500 mb-1">来源</div><span className="text-sm">{detail.source}: {detail.source_ref}</span></div>
                  <div><div className="text-xs text-slate-500 mb-1">指纹</div><code className="text-xs bg-slate-950 px-2 py-0.5 rounded">{detail.fingerprint}</code></div>
                </div>
              </div>
            </Modal>
          )}

          {/* Label modal */}
          {showLabel && (
            <Modal onClose={() => setShowLabel(null)}>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">标记期望值</h2>
                <p className="text-xs text-slate-500 mb-2">{showLabel}</p>
                <Badge className="mb-4">{labelSuite || 'unknown'}</Badge>
                {contractError && (
                  <div className="mb-3 px-3 py-2 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-300">
                    契约加载失败：{contractError}
                  </div>
                )}
                {labelError && (
                  <div className="mb-3 px-3 py-2 rounded-lg border border-amber-500/40 bg-amber-500/10 text-xs text-amber-200">
                    {labelError}
                  </div>
                )}

                {labelSuite === 'memory_learning' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">不应学习</div>
                      <select value={fieldValue('no_learn')} onChange={e => setLabelField('no_learn', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">应创建黑话</div>
                      <select value={fieldValue('should_create_jargon')} onChange={e => setLabelField('should_create_jargon', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">应创建表达</div>
                      <select value={fieldValue('should_create_expression')} onChange={e => setLabelField('should_create_expression', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">禁止学习词</div>
                      <input value={fieldValue('forbidden_terms')} onChange={e => setLabelField('forbidden_terms', e.target.value)}
                        placeholder="逗号分隔"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {labelSuite === 'timing_gate' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">期望动作</div>
                      <select value={fieldValue('timing_action')} onChange={e => setLabelField('timing_action', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="continue">continue</option>
                        <option value="wait">wait</option>
                        <option value="no_reply">no_reply</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">是否应该回复</div>
                      <select value={fieldValue('should_reply')} onChange={e => setLabelField('should_reply', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select></div>
                  </div>
                )}

                {labelSuite === 'group_reply' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">是否应该回复</div>
                      <select value={fieldValue('should_reply')} onChange={e => setLabelField('should_reply', e.target.value)}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">必需工具</div>
                      <input value={fieldValue('required_tools')} onChange={e => setLabelField('required_tools', e.target.value)}
                        placeholder="逗号分隔"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">禁止工具</div>
                      <input value={fieldValue('forbidden_tools')} onChange={e => setLabelField('forbidden_tools', e.target.value)}
                        placeholder="逗号分隔"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div className="grid grid-cols-2 gap-2">
                      <div><div className="text-xs text-slate-400 mb-1">必须包含文本</div>
                        <input value={fieldValue('must_contain')} onChange={e => setLabelField('must_contain', e.target.value)}
                          placeholder="逗号分隔"
                          className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                      <div><div className="text-xs text-slate-400 mb-1">禁止包含文本</div>
                        <input value={fieldValue('must_not_contain')} onChange={e => setLabelField('must_not_contain', e.target.value)}
                          placeholder="逗号分隔"
                          className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div><div className="text-xs text-slate-400 mb-1">发送方式</div>
                        <select value={fieldValue('send_mode')} onChange={e => setLabelField('send_mode', e.target.value)}
                          className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                          <option value="">选择...</option>
                          <option value="normal">normal</option>
                          <option value="quote">quote</option>
                          <option value="mention">mention</option>
                        </select></div>
                      <div><div className="text-xs text-slate-400 mb-1">引用消息 ID</div>
                        <input value={fieldValue('reply_to_message_id')} onChange={e => setLabelField('reply_to_message_id', e.target.value)}
                          className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    </div>
                    <div><div className="text-xs text-slate-400 mb-1">提及目标 JSON</div>
                      <textarea value={fieldValue('mentions')} onChange={e => setLabelField('mentions', e.target.value)}
                        rows={2} placeholder='[{"user_id":"123"}]'
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 font-mono text-xs" /></div>
                  </div>
                )}

                {/* 其他 suite：默认表单 + JSON 高级模式 */}
                {!['memory_learning','timing_gate','group_reply'].includes(labelSuite) && (
                  <div className="space-y-3">
                    <p className="text-xs text-slate-500">此 suite 暂无专用表单，请使用高级 JSON 模式填写 scorer 字段。</p>
                  </div>
                )}

                <div className="mt-4">
                  <div className="text-xs text-slate-400 mb-1">人工说明</div>
                  <textarea value={labelNote} onChange={e => setLabelNote(e.target.value)}
                    rows={2} className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" />
                </div>

                {/* 高级 JSON 模式（所有 suite 都有） */}
                <div className="mt-4">
                  <button onClick={() => setLabelShowJson(!labelShowJson)} className="text-xs text-slate-500 hover:text-slate-300">
                    {labelShowJson ? '收起' : '▶'} 高级 JSON 模式
                  </button>
                  {labelShowJson && (
                    <textarea value={labelFields._rawJson || JSON.stringify(labelFields, null, 2)} onChange={e => setLabelFields({...labelFields, _rawJson: e.target.value})}
                      rows={8} className="w-full p-3 mt-2 rounded-xl bg-slate-900 border border-slate-700 font-mono text-xs" />
                  )}
                </div>

                <div className="flex gap-2 justify-end mt-4">
                  <button onClick={() => setShowLabel(null)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
                  <button onClick={() => doLabel(showLabel)}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm font-medium">保存标记</button>
                </div>
              </div>
            </Modal>
          )}

          {/* Promote modal */}
          {promoteCaseId && (
            <Modal onClose={() => setPromoteCaseId(null)}>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">提升候选用例</h2>
                <p className="text-xs text-slate-500 mb-4">{promoteCaseId}</p>
                {promoteError && (
                  <div className="mb-3 px-3 py-2 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-300">
                    {promoteError}
                  </div>
                )}
                <div className="space-y-3">
                  <div>
                    <div className="text-xs text-slate-400 mb-1">目标数据集</div>
                    <input value={promoteTargetDataset} onChange={e => { setPromoteTargetDataset(e.target.value); setPromotePlan(null); setPromoteError('') }}
                      className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" />
                  </div>
                  {promotePlan && (
                    <div className="rounded-xl border border-slate-700 bg-slate-950 p-3 text-xs space-y-2">
                      <div><span className="text-slate-500">target_dataset: </span>{promotePlan.target_dataset}</div>
                      <div><span className="text-slate-500">path: </span><code>{promotePlan.path}</code></div>
                      <div><span className="text-slate-500">case: </span>{promotePlan.case_id || promoteCaseId}</div>
                      <JsonBlock value={promotePlan.case || promotePlan} className="max-h-56" />
                    </div>
                  )}
                </div>
                <div className="flex gap-2 justify-end mt-4">
                  <button onClick={() => setPromoteCaseId(null)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
                  <button onClick={previewPromote} disabled={promoteBusy}
                    className="px-4 py-2 bg-indigo-700/70 hover:bg-indigo-700 disabled:opacity-50 rounded-xl text-sm">生成计划</button>
                  <button onClick={confirmPromote} disabled={promoteBusy || !promotePlan}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">确认提升</button>
                </div>
              </div>
            </Modal>
          )}

          {/* 预检弹窗 */}
          {preflightOpen && (
            <Modal onClose={() => setPreflightOpen(false)} wide>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">预检当前页</h2>
                {preflightError && (
                  <div className="mb-3 px-3 py-2 rounded-lg border border-red-500/40 bg-red-500/10 text-xs text-red-300">
                    {preflightError}
                  </div>
                )}
                {preflightResult && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                      <MiniStat label="total" value={preflightResult.total || 0} />
                      <MiniStat label="ready" value={preflightResult.ready || 0} tone="emerald" />
                      <MiniStat label="blocked" value={preflightResult.blocked || 0} tone="red" />
                      <MiniStat label="target" value={preflightResult.target_dataset || '-'} tone={preflightResult.ok ? 'emerald' : 'amber'} />
                    </div>
                    <div>
                      <div className="text-xs text-slate-500 mb-1">items</div>
                      <JsonBlock value={preflightResult.items || []} className="max-h-96" />
                    </div>
                  </div>
                )}
                <div className="flex justify-end mt-4">
                  <button onClick={() => setPreflightOpen(false)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">关闭</button>
                </div>
              </div>
            </Modal>
          )}
        </div>
      )}

      {tab === 'runs' && (
        <div>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
            <table className="min-w-[640px] w-full text-sm">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-2">ID</th>
                <th className="px-3 py-2">suite</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">通过率</th>
                <th className="px-3 py-2">通过/总数</th>
                <th className="px-3 py-2">git_sha</th>
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">操作</th>
              </tr></thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-3 py-2">{r.id}</td>
                    <td className="px-3 py-2"><Badge>{r.suite}</Badge></td>
                    <td className="px-3 py-2">
                      <Badge tone={r.status === 'completed' ? 'emerald' : 'amber'}>{r.status}</Badge>
                    </td>
                    <td className="px-3 py-2">
                      <span className={r.pass_rate >= 0.8 ? 'text-emerald-400' : r.pass_rate >= 0.5 ? 'text-amber-400' : 'text-red-400'}>
                        {(r.pass_rate * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="px-3 py-2">{r.passed}/{r.total}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">{r.git_sha || '-'}</td>
                    <td className="px-3 py-2 text-slate-500 text-xs">{r.created_at}</td>
                    <td className="px-3 py-2">
                      <button onClick={() => loadRunDetail(r.id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">详情</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>

          {runDetail && (
            <Modal onClose={() => setRunDetail(null)} wide>
              <div className="p-6 max-h-[80vh] overflow-auto">
                <h2 className="text-lg font-bold mb-2">Run #{runDetail.run?.id}</h2>
                <div className="grid grid-cols-4 gap-3 mb-4">
                  <MiniStat label="suite" value={runDetail.run?.suite} />
                  <MiniStat label="通过率" value={`${((runDetail.run?.pass_rate || 0) * 100).toFixed(1)}%`}
                    tone={runDetail.run?.pass_rate >= 0.8 ? 'emerald' : runDetail.run?.pass_rate >= 0.5 ? 'amber' : 'red'} />
                  <MiniStat label="通过" value={runDetail.run?.passed} tone="emerald" />
                  <MiniStat label="失败" value={runDetail.run?.failed} tone={runDetail.run?.failed ? 'red' : 'slate'} />
                </div>
                {(runDetail.results || []).filter(r => !r.passed).length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-red-400 mb-2">失败 case</h3>
                    <div className="space-y-2">
                      {(runDetail.results || []).filter(r => !r.passed).map(res => (
                        <Card key={res.id} className="p-3">
                          <div className="text-sm font-medium mb-1">{res.case_id}</div>
                          <div className="text-xs text-slate-400">score: {res.score}</div>
                          <JsonBlock value={res.errors} className="mt-1 max-h-32" />
                        </Card>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Modal>
          )}
        </div>
      )}
    </div>
  )
}
