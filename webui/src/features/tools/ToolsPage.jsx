import { useCallback, useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'

import {
  deleteToolOverride,
  listAuditLogs,
  listTools,
  listToolTargets,
  setToolOverride,
  updateToolDefaults,
} from '../../api/generated/adminClient'
import { Badge, Card } from '../../components/ui'

// ── Tools ──
export function ToolsPage() {
  const tabs = [
    { key: 'defaults', label: '默认模板' },
    { key: 'overrides', label: '指定覆盖' },
    { key: 'audit', label: '修改记录' },
  ]
  const templates = [
    { key: 'private_default', label: '普通私聊', chatType: 'private', help: '普通私聊的基础工具模板' },
    { key: 'private_superuser_default', label: '超级用户私聊', chatType: 'private_superuser', help: '已识别为超级用户的私聊基础模板；不会按消息内容切换' },
    { key: 'group_default', label: '群聊', chatType: 'group', help: '群聊的基础工具模板' },
  ]
  const platformOptions = [
    { key: 'qq', label: 'QQ' },
    { key: 'web', label: 'Web' },
    { key: 'synergy', label: 'Synergy' },
  ]
  const [tab, setTab] = useState('defaults')
  const [templateKey, setTemplateKey] = useState('group_default')
  const [platform, setPlatform] = useState('qq')
  const [tools, setTools] = useState([])
  const [regInfo, setRegInfo] = useState(null)
  const [regAvail, setRegAvail] = useState(false)
  const [regEmpty, setRegEmpty] = useState(false)
  const [bridgeCt, setBridgeCt] = useState(0)
  const [overrideScope, setOverrideScope] = useState('group')
  const [targetId, setTargetId] = useState('')
  const [targetSearch, setTargetSearch] = useState('')
  const [targetOptions, setTargetOptions] = useState([])
  const [targetPickerOpen, setTargetPickerOpen] = useState(false)
  const [auditLogs, setAuditLogs] = useState([])
  const [expandAudit, setExpandAudit] = useState(null)
  const activeTemplate = templates.find(t => t.key === templateKey) || templates[0]
  const load = useCallback(() => {
    const isUserOverride = tab === 'overrides' && overrideScope === 'user'
    listTools({
      chat_type: tab === 'defaults' ? activeTemplate.chatType : isUserOverride ? 'private' : 'group',
      platform,
      group_id: tab === 'overrides' && overrideScope === 'group' ? targetId : '',
      user_id: isUserOverride ? targetId : '',
    }).then(data => {
      setTools(data.tools || [])
      setRegInfo(data.registry_info || null)
      setRegAvail(data.registry_available)
      setRegEmpty(data.registry_empty)
      setBridgeCt(data.bridge_count || 0)
    })
  }, [tab, overrideScope, targetId, activeTemplate.chatType, platform])
  const loadTargets = useCallback(() => {
    if (tab !== 'overrides') return
    listToolTargets({
      scope_type: overrideScope,
      search: targetSearch,
      limit: 50,
    }).then(data => setTargetOptions(data.items || []))
  }, [tab, overrideScope, targetSearch])
  const loadAudit = useCallback(() => listAuditLogs({
    target_type: 'tool',
    limit: 50,
  }).then(data => setAuditLogs(data.items || [])), [])
  useEffect(() => { if (tab === 'audit') loadAudit(); else load() }, [tab, load, loadAudit])
  useEffect(() => { loadTargets() }, [loadTargets])

  const toggleDefault = (t, field) => {
    const val = !t[field]
    updateToolDefaults({
      tool_name: t.name,
      body: { [field]: val },
    }).then(load)
  }

  const scopeForTab = () => {
    if (tab !== 'overrides') return null
    if (overrideScope === 'platform') {
      return {
        scope_type: 'platform',
        scope_id: platform.trim(),
      }
    }
    if (!targetId.trim()) return null
    return {
      scope_type: overrideScope,
      scope_id: targetId.trim(),
    }
  }

  const setOverride = (t, enabled) => {
    const scope = scopeForTab()
    if (!scope) return
    setToolOverride({
      tool_name: t.name,
      body: { ...scope, enabled, reason: '' },
    }).then(load)
  }

  const clearOverride = (t) => {
    const scope = scopeForTab()
    if (!scope) return
    deleteToolOverride({
      tool_name: t.name,
      ...scope,
    }).then(load)
  }
  const selectTarget = (target) => {
    if (overrideScope === 'platform') {
      setPlatform(target.id)
    }
    setTargetId(target.id)
    setTargetSearch(target.label)
    setTargetPickerOpen(false)
  }
  const onTargetInput = (value) => {
    setTargetSearch(value)
    const match = targetOptions.find(item => item.id === value || item.label === value)
    setTargetId(match ? match.id : '')
    setTargetPickerOpen(true)
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">工具管理</h1>
      <p className="text-slate-500 text-sm mb-2">管理通用工具配置：普通聊天不会根据消息文本自动裁剪工具，默认模板和指定覆盖共同决定实际工具集合。</p>
      <p className="mb-4 text-xs text-amber-300">
        七个 Sandbox 工具不接受默认模板或 ToolOverride 授权，统一由
        <NavLink to="/sandbox" className="mx-1 underline decoration-amber-500/50 hover:text-amber-200">Sandbox 管理</NavLink>
        按 canonical session 控制。
      </p>
      <div className="flex gap-2 mb-6 border-b border-slate-800 pb-2">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.key ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>{t.label}</button>
        ))}
      </div>
      {tab !== 'audit' && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label htmlFor="tool-platform-select" className="text-xs text-slate-500">
            预览平台
            <select id="tool-platform-select" value={platform} onChange={e => setPlatform(e.target.value)}
              className="mt-1 block min-w-[140px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
              {platformOptions.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <span className="pb-1.5 text-xs text-slate-500">用于预览该平台下的运行时工具状态。</span>
        </div>
      )}
      {tab === 'defaults' && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label htmlFor="tool-template-select" className="text-xs text-slate-500">
            当前模板
            <select id="tool-template-select" value={templateKey} onChange={e => setTemplateKey(e.target.value)}
              className="mt-1 block min-w-[160px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
              {templates.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <span className="pb-1.5 text-xs text-slate-500">{activeTemplate.help}</span>
        </div>
      )}
      {tab === 'overrides' && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label htmlFor="tool-override-scope" className="text-xs text-slate-500">
            覆盖对象
            <select id="tool-override-scope" value={overrideScope} onChange={e => { setOverrideScope(e.target.value); setTargetId(''); setTargetSearch('') }}
              className="mt-1 block min-w-[120px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
              <option value="group">指定群聊</option>
              <option value="user">指定私聊</option>
              <option value="platform">指定平台</option>
            </select>
          </label>
          <div className="relative">
            <label htmlFor="tool-override-target" className="text-xs text-slate-500">
              {overrideScope === 'platform' ? '选择平台' : overrideScope === 'group' ? '搜索群聊' : '搜索私聊用户'}
              <input id="tool-override-target" value={targetSearch}
                onFocus={() => setTargetPickerOpen(true)}
                onBlur={() => setTimeout(() => setTargetPickerOpen(false), 120)}
                onChange={e => onTargetInput(e.target.value)}
                placeholder={overrideScope === 'platform' ? '选择 QQ、Web 或 Synergy' : overrideScope === 'group' ? '输入群名或群号' : '输入昵称或用户 ID'}
                className="mt-1 block w-72 rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200" />
            </label>
            {targetPickerOpen && (
              <div className="absolute z-20 mt-1 w-72 max-h-64 overflow-auto rounded-lg border border-slate-700 bg-slate-950 shadow-xl">
                {targetOptions.length > 0 ? targetOptions.map(item => (
                  <button key={item.id} type="button" onMouseDown={e => e.preventDefault()} onClick={() => selectTarget(item)}
                    className="block w-full px-3 py-2 text-left text-xs hover:bg-slate-800">
                    <div className="text-slate-200">{item.label}</div>
                    <div className="mt-0.5 flex gap-2 text-[11px] text-slate-500">
                      <span>{item.scope_type}</span>
                      <span>{item.source}</span>
                      {item.recent_at && <span>{item.recent_at.slice(5, 16)}</span>}
                    </div>
                  </button>
                )) : (
                  <div className="px-3 py-3 text-xs text-slate-500">没有匹配的真实会话</div>
                )}
              </div>
            )}
          </div>
          <span className="pb-1.5 text-xs text-slate-500">
            {overrideScope === 'platform'
              ? `当前平台：${platform}`
              : targetId ? `当前目标：${targetId}` : '请选择一个已记录的真实目标；覆盖不会对手填未知 ID 生效。'}
          </span>
        </div>
      )}
      {tab !== 'audit' && <div className="mb-4 flex gap-4 text-xs text-slate-400">
        {!regAvail ? (
          bridgeCt === 0 ? (
            <span className="text-slate-500">会话 bridge 尚未创建（{bridgeCt} 个活跃），触发一条消息后可用</span>
          ) : (
            <span className="text-slate-500">运行时注册状态未知（bridge 未就绪，{bridgeCt} 个活跃）</span>
          )
        ) : regEmpty ? (
          <span className="text-amber-400">KT registry 返回空，请检查 bridge/list_tools</span>
        ) : regInfo ? (
          <>
            <span>会话 bridge: <span className="text-slate-200 font-medium">{bridgeCt}</span> 个</span>
            <span>KT 已加载: <span className="text-slate-200 font-medium">{regInfo.kt_loaded?.length || 0}</span> 个</span>
            {regInfo.missing_meta?.length > 0 && <span className="text-amber-400">元数据缺失: {regInfo.missing_meta.length} 个 ({regInfo.missing_meta.join(', ')})</span>}
            {regInfo.missing_kt?.length > 0 && <span className="text-red-400">KT 未加载: {regInfo.missing_kt.length} 个 ({regInfo.missing_kt.join(', ')})</span>}
          </>
        ) : null}
      </div>}
      {tab !== 'audit' && <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-500 border-b border-slate-800">
            <th className="py-2 px-2">工具</th><th className="py-2 px-2">类别</th><th className="py-2 px-2">风险</th>
            {tab === 'defaults' && <th className="py-2 px-2">{activeTemplate.label}</th>}
            {tab === 'overrides' && <th className="py-2 px-2">配置状态</th>}
            <th className="py-2 px-2">说明</th>
          </tr></thead>
          <tbody>
            {tools.map(t => {
              const isSandboxManaged = t.sandbox_managed === true
              const isForced = overrideScope === 'group' && t.force_disabled_group
              const isLocked = t.force_enabled
              const isSubagent = t.is_subagent
              const configured = t.configured_enabled ?? t.effective
              const overrideReady = overrideScope === 'platform' ? Boolean(platform.trim()) : Boolean(targetId.trim())
              const overrideValue = t.override_present
                ? (t.override_enabled ? 'enabled' : 'disabled')
                : 'inherit'
              return (
                <tr key={t.name} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="py-2 px-2 font-mono text-slate-200">
                  {t.name}
                  {t.is_subagent && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-purple-900/30 text-purple-400">subagent</span>}
                  {regAvail && t.registered === false && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-red-900/30 text-red-400">未注册</span>}
                  {regAvail && t.registered === null && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-slate-800 text-slate-500">未知</span>}
                  {regAvail && t.registered === true && <span className="ml-1 px-1 py-0.5 rounded text-xs bg-emerald-900/30 text-emerald-400">已注册</span>}
                </td>
                  <td className="py-2 px-2 text-slate-400">{t.category}</td>
                  <td className="py-2 px-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${t.risk_level === 'high' ? 'bg-red-900/30 text-red-400' : t.risk_level === 'medium' ? 'bg-amber-900/30 text-amber-400' : 'bg-slate-700 text-slate-300'}`}>{t.risk_level}</span>
                  </td>
                  {tab === 'defaults' && (
                    <td className="py-2 px-2">
                      <button onClick={() => !t.force_enabled && !(templateKey === 'group_default' && t.force_disabled_group) && toggleDefault(t, templateKey)}
                        disabled={isSandboxManaged || t.force_enabled || (templateKey === 'group_default' && t.force_disabled_group)}
                        title={isSandboxManaged ? '由 Sandbox 管理页按 canonical session 控制' : activeTemplate.help}
                        className={`px-2 py-1 rounded text-xs ${t[templateKey] ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-600/30 text-slate-500'} ${(isSandboxManaged || t.force_enabled || (templateKey === 'group_default' && t.force_disabled_group)) ? 'opacity-50 cursor-not-allowed' : ''}`}>
                        {isSandboxManaged ? '专用管理' : t[templateKey] ? 'ON' : 'OFF'}
                      </button>
                      {templateKey === 'group_default' && t.force_disabled_group && <span className="ml-2 text-xs text-slate-500">群聊强制禁用</span>}
                    </td>
                  )}
                  {tab === 'overrides' && (
                    <td className="py-2 px-2">
                      {isForced && <span className="text-xs text-slate-500">群聊强制禁用</span>}
                      {isLocked && <span className="text-xs text-emerald-400">强制启用</span>}
                      {isSubagent && <span className="text-xs text-purple-400">subagent（运行时禁用有限）</span>}
                      {isSandboxManaged && <NavLink to="/sandbox" className="text-xs text-amber-300 underline">由 Sandbox 管理页控制</NavLink>}
                      {!isSandboxManaged && !isForced && !isLocked && !isSubagent && (
                        <select value={overrideValue}
                          onChange={e => { const v = e.target.value; if (v === 'inherit') clearOverride(t); else setOverride(t, v === 'enabled') }}
                          disabled={!overrideReady}
                          className={`p-1 rounded text-xs bg-slate-900 border border-slate-700 ${!overrideReady ? 'opacity-40' : ''}`}>
                          <option value="inherit">继承（{configured ? '启用' : '禁用'}）</option>
                          <option value="enabled">启用</option>
                          <option value="disabled">禁用</option>
                        </select>
                      )}
                    </td>
                  )}
                  <td className="py-2 px-2 text-slate-500 text-xs max-w-[200px] truncate" title={t.description}>{t.description}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>}
      {tab === 'audit' && (
        <Card className="mt-4 overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-slate-800">
              <th className="py-2 px-2">时间</th><th className="py-2 px-2">动作</th><th className="py-2 px-2">工具</th><th className="py-2 px-2">操作者</th><th className="py-2 px-2">IP</th>
            </tr></thead>
            <tbody>
              {auditLogs.map(d => (
                <tr key={d.id} className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer" onClick={() => setExpandAudit(expandAudit === d.id ? null : d.id)}>
                  <td className="py-2 px-2 text-slate-500 whitespace-nowrap">{d.created_at ? d.created_at.slice(5, 19) : ''}</td>
                  <td className="py-2 px-2"><Badge tone={d.action === 'tool_default_update' ? 'emerald' : 'amber'}>{d.action}</Badge></td>
                  <td className="py-2 px-2 text-slate-300 font-mono">{d.target_id}</td>
                  <td className="py-2 px-2 text-slate-400">{d.admin_user}</td>
                  <td className="py-2 px-2 text-slate-500">{d.ip_address || '-'}</td>
                </tr>
              ))}
              {auditLogs.length === 0 && (
                <tr><td colSpan={5} className="py-8 text-center text-slate-600">暂无工具修改记录</td></tr>
              )}
            </tbody>
          </table>
          {expandAudit && (
            <div className="p-3 bg-slate-900 border-t border-slate-800">
              {(() => {
                const d = auditLogs.find(x => x.id === expandAudit)
                if (!d) return null
                return (
                  <pre className="text-xs text-slate-300 whitespace-pre-wrap break-all">{JSON.stringify(d.detail_json || {}, null, 2)}</pre>
                )
              })()}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
