import { useEffect, useRef, useState } from 'react'

import { api } from '../../api'

export function ModelRepliesTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [groupId, setGroupId] = useState('')
  const [cursorStack, setCursorStack] = useState([])  // 后退历史：每前进一次 push 当前页起点
  const [currentBeforeId, setCurrentBeforeId] = useState(0)
  const [nextBeforeId, setNextBeforeId] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const limit = 30

  const load = (beforeId = 0, pushStack = false) => {
    const params = { limit, kind: 'group_reply' }
    if (groupId) params.group_id = groupId
    if (beforeId) params.before_id = beforeId
    api.get('/model-replies', { params }).then(r => {
      const data = r.data
      if (pushStack) {
        setCursorStack(prev => [...prev, currentBeforeId])
      }
      setItems(data.items || [])
      setTotal(data.count || 0)
      setCurrentBeforeId(beforeId)
      setNextBeforeId(data.page_info?.next_before_id || 0)
      setHasMore(data.page_info?.has_more || false)
    })
  }
  const initialLoadRef = useRef(load)
  useEffect(() => {
    const timer = window.setTimeout(() => { initialLoadRef.current(0) }, 0)
    return () => window.clearTimeout(timer)
  }, [])

  const goBack = () => {
    if (cursorStack.length === 0) return
    const prev = cursorStack[cursorStack.length - 1]
    setCursorStack(s => s.slice(0, -1))
    load(prev, false)
  }

  const formatTime = (ts) => ts ? new Date(ts).toLocaleString('zh-CN', { hour12: false }) : ''

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex shrink-0 items-center gap-2">
        <input value={groupId} onChange={e => setGroupId(e.target.value)} onKeyDown={e => e.key === 'Enter' && load(0)}
          placeholder="群号筛选..." className="w-32 p-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs" />
        <button onClick={() => load(0)} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs">查询</button>
        <span className="text-xs text-slate-500 ml-2">约 {total} 条</span>
        <div className="flex-1" />
        <button disabled={cursorStack.length === 0} onClick={goBack}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded-lg text-xs">‹ 后退</button>
        <button disabled={!hasMore} onClick={() => load(nextBeforeId, true)}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded-lg text-xs">更早 ›</button>
      </div>
      <div className="viewport-scroll min-h-0 flex-1 overflow-auto rounded-xl bg-slate-950 border border-slate-800">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-900 text-slate-400">
            <tr>
              <th className="py-2 px-3 text-left w-36">时间</th>
              <th className="py-2 px-3 text-left w-16">群号</th>
              <th className="py-2 px-3 text-left">回复内容</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {items.map((m, i) => (
              <tr key={m.id || i} className="hover:bg-slate-900/50 transition-colors">
                <td className="py-2 px-3 text-slate-500 whitespace-nowrap align-top">{formatTime(m.created_at)}</td>
                <td className="py-2 px-3 text-slate-400 font-mono align-top">{m.group_id || '-'}</td>
                <td className="py-2 px-3 text-slate-300 whitespace-pre-wrap break-all">
                  <div className="max-h-20 overflow-y-auto">{m.content}</div>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={3} className="py-8 text-center text-slate-600">暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
