import { useCallback, useEffect, useState } from 'react'

import { api } from '../../api'
import { Badge, Card, JsonBlock, MiniStat, Modal, Pagination } from '../../components/ui'

// ── Eval ──
export function EvalsPage() {
  const [tab, setTab] = useState('candidates')
  const [candidates, setCandidates] = useState({ items: [], total: 0 })
  const [candPage, setCandPage] = useState(1)
  const [suiteFilter, setSuiteFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [detail, setDetail] = useState(null)
  const [showLabel, setShowLabel] = useState(null)
  const [labelSuite, setLabelSuite] = useState('')
  const [labelFields, setLabelFields] = useState({})
  const [labelShowJson, setLabelShowJson] = useState(false)
  const [runs, setRuns] = useState([])
  const [runDetail, setRunDetail] = useState(null)
  const [running, setRunning] = useState(false)
  const [sampleInfo, setSampleInfo] = useState(null)

  const loadCandidates = useCallback(() => {
    const params = { page: candPage, limit: 20 }
    if (suiteFilter) params.suite = suiteFilter
    if (statusFilter) params.status = statusFilter
    if (sourceFilter) params.source = sourceFilter
    api.get('/evals/candidates', { params }).then(r => setCandidates(r.data))
  }, [candPage, suiteFilter, statusFilter, sourceFilter])

  const loadRuns = useCallback(() => {
    api.get('/evals/runs', { params: { limit: 20 } }).then(r => setRuns(r.data.items || []))
  }, [])

  useEffect(() => {
    if (tab === 'candidates') loadCandidates()
    if (tab === 'runs') loadRuns()
  }, [tab, loadCandidates, loadRuns])

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

  const loadDetail = (caseId) => {
    api.get(`/evals/candidates/${encodeURIComponent(caseId)}`)
      .then(r => setDetail(r.data))
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doLabel = (caseId) => {
    // 从表单构建 expected_json
    let expectedJson = { ...labelFields }
    delete expectedJson._rawJson
    if (labelShowJson && labelFields._rawJson) {
      try { expectedJson = JSON.parse(labelFields._rawJson) } catch { alert('JSON 格式错误'); return }
    }
    if (Object.keys(expectedJson).length === 0 || expectedJson.needs_label) {
      alert('请先选择期望值')
      return
    }
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/label`, { expected_json: expectedJson })
      .then(() => { setShowLabel(null); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doIgnore = (caseId) => {
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/ignore`)
      .then(() => loadCandidates())
      .catch(e => alert(e.response?.data?.detail || e.message))
  }

  const doPromote = (caseId) => {
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`)
      .then(r => { alert(`已提升到 regression: ${r.data.path}`); loadCandidates() })
      .catch(e => alert(e.response?.data?.detail || e.message))
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
          <Card>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="px-3 py-2">case_id</th>
                <th className="px-3 py-2">suite</th>
                <th className="px-3 py-2">来源</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">描述</th>
                <th className="px-3 py-2">创建时间</th>
                <th className="px-3 py-2">操作</th>
              </tr></thead>
              <tbody>
                {candidates.items.map(c => (
                  <tr key={c.case_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-3 py-2 font-mono max-w-[200px] truncate">{c.case_id}</td>
                    <td className="px-3 py-2"><Badge>{c.suite}</Badge></td>
                    <td className="px-3 py-2 text-slate-400">{c.source}</td>
                    <td className="px-3 py-2">
                      <Badge tone={c.status === 'promoted' ? 'emerald' : c.status === 'labeled' ? 'blue' : c.status === 'ignored' ? 'slate' : 'amber'}>{c.status}</Badge>
                    </td>
                    <td className="px-3 py-2 max-w-[300px] truncate text-slate-400">{c.description}</td>
                    <td className="px-3 py-2 text-slate-500">{c.created_at}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        <button onClick={() => loadDetail(c.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">详情</button>
                        {c.status === 'candidate' && (
                          <>
                            <button onClick={() => { setShowLabel(c.case_id); setLabelSuite(c.suite); setLabelFields({}); setLabelShowJson(false) }}
                              className="px-2 py-1 bg-indigo-700/50 hover:bg-indigo-700 text-indigo-300 rounded text-xs">标记</button>
                            <button onClick={() => doIgnore(c.case_id)} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-xs">忽略</button>
                          </>
                        )}
                        {c.status === 'labeled' && (
                          <button onClick={() => doPromote(c.case_id)}
                            className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300 rounded text-xs">提升</button>
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

                {labelSuite === 'memory_learning' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">是否应该学习</div>
                      <select value={labelFields.should_learn || ''} onChange={e => setLabelFields({...labelFields, should_learn: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="true">应该学习</option>
                        <option value="false">不应学习</option>
                        <option value="uncertain">不确定</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">分类</div>
                      <input value={labelFields.category || ''} onChange={e => setLabelFields({...labelFields, category: e.target.value})}
                        placeholder="stock_formula, spam_symbol..."
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="× 不应被学成黑话"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">含义（可选）</div>
                      <textarea value={labelFields.meaning || ''} onChange={e => setLabelFields({...labelFields, meaning: e.target.value})}
                        rows={2} className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {labelSuite === 'timing_gate' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">期望动作</div>
                      <select value={labelFields.expected_action || ''} onChange={e => setLabelFields({...labelFields, expected_action: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="continue">continue</option>
                        <option value="wait">wait</option>
                        <option value="no_reply">no_reply</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">延迟（秒）</div>
                      <input type="number" value={labelFields.delay_seconds || ''} onChange={e => setLabelFields({...labelFields, delay_seconds: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="应该继续回复"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {labelSuite === 'group_reply' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">质量评价</div>
                      <select value={labelFields.quality || ''} onChange={e => setLabelFields({...labelFields, quality: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="good">合适</option>
                        <option value="bad">不合适</option>
                        <option value="interrupt">过度插话</option>
                        <option value="tone">语气不对</option>
                        <option value="context_error">上下文错误</option>
                        <option value="permission_error">权限错误</option>
                      </select></div>
                    <div><div className="text-xs text-slate-400 mb-1">原因</div>
                      <input value={labelFields.reason || ''} onChange={e => setLabelFields({...labelFields, reason: e.target.value})}
                        placeholder="描述问题"
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" /></div>
                  </div>
                )}

                {/* 其他 suite：默认表单 + JSON 高级模式 */}
                {!['memory_learning','timing_gate','group_reply'].includes(labelSuite) && (
                  <div className="space-y-3">
                    <p className="text-xs text-slate-500">此 suite 暂无专用表单，请使用高级 JSON 模式或直接在下方编辑。</p>
                    <textarea value={labelFields._rawJson || JSON.stringify({needs_label: true}, null, 2)} onChange={e => setLabelFields({...labelFields, _rawJson: e.target.value})}
                      rows={8} className="w-full p-3 rounded-xl bg-slate-900 border border-slate-700 font-mono text-xs" />
                  </div>
                )}

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
