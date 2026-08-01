import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ChevronRight,
  File,
  FilePlus2,
  Folder,
  FolderOpen,
  FolderTree,
  Home,
  RefreshCw,
  Save,
} from 'lucide-react'

import { api } from '../../api'
import {
  ActionButton,
  Badge,
  Card,
  Field,
  MiniStat,
  PageHeader,
  Spinner,
} from '../../components/ui'


const MAX_EDITOR_BYTES = 256 * 1024
const INPUT_CLASS = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50'


function formatApiError(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map(item => item?.msg || item?.message || String(item)).join('；')
  }
  if (detail && typeof detail === 'object') {
    const message = detail.message || detail.error || JSON.stringify(detail)
    return detail.hint ? `${message}（${detail.hint}）` : message
  }
  return error?.message || fallback
}


function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let current = bytes
  let unit = -1
  do {
    current /= 1024
    unit += 1
  } while (current >= 1024 && unit < units.length - 1)
  return `${current >= 10 ? current.toFixed(1) : current.toFixed(2)} ${units[unit]}`
}


function formatModifiedAt(value) {
  const nanoseconds = Number(value)
  if (!Number.isFinite(nanoseconds) || nanoseconds <= 0) return '-'
  return new Date(nanoseconds / 1_000_000).toLocaleString('zh-CN', {
    hour12: false,
  })
}


function baseName(path) {
  return String(path || '').split('/').filter(Boolean).at(-1) || '/'
}


function joinPath(parent, child) {
  return [parent, child].filter(Boolean).join('/')
}


function parentPath(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  parts.pop()
  return parts.join('/')
}


function textBytes(value) {
  return new TextEncoder().encode(String(value || '')).length
}


function Breadcrumbs({ path, onNavigate }) {
  const parts = String(path || '').split('/').filter(Boolean)
  return (
    <nav aria-label="Workspace 当前目录" className="flex min-w-0 items-center gap-1 overflow-x-auto text-xs">
      <button
        type="button"
        onClick={() => onNavigate('')}
        className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-slate-400 transition-colors hover:bg-slate-800 hover:text-white"
      >
        <Home className="h-3.5 w-3.5" aria-hidden="true" />
        根目录
      </button>
      {parts.map((part, index) => {
        const target = parts.slice(0, index + 1).join('/')
        return (
          <span key={target} className="flex shrink-0 items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5 text-slate-700" aria-hidden="true" />
            <button
              type="button"
              onClick={() => onNavigate(target)}
              className="cursor-pointer rounded-md px-2 py-1 font-mono text-slate-400 transition-colors hover:bg-slate-800 hover:text-white"
            >
              {part}
            </button>
          </span>
        )
      })}
    </nav>
  )
}


function FileBrowser({ entries, currentPath, loading, selectedPath, onOpen, onNavigate }) {
  if (loading) {
    return <div className="flex min-h-72 items-center justify-center"><Spinner /></div>
  }
  return (
    <div className="min-h-72 divide-y divide-slate-800">
      {currentPath && (
        <button
          type="button"
          onClick={() => onNavigate(parentPath(currentPath))}
          className="flex w-full cursor-pointer items-center gap-3 px-3 py-2.5 text-left text-xs text-slate-400 transition-colors hover:bg-slate-800/70 hover:text-white"
        >
          <FolderOpen className="h-4 w-4 text-amber-300" aria-hidden="true" />
          <span className="font-mono">..</span>
          <span className="ml-auto text-[11px] text-slate-600">上级目录</span>
        </button>
      )}
      {!entries.length && (
        <div className="flex min-h-64 flex-col items-center justify-center px-4 text-center">
          <FolderTree className="h-8 w-8 text-slate-700" aria-hidden="true" />
          <p className="mt-3 text-sm text-slate-400">当前目录为空</p>
          <p className="mt-1 text-xs text-slate-600">可在右侧创建 UTF-8 文本文件。</p>
        </div>
      )}
      {entries.map(entry => {
        const directory = entry.type === 'directory'
        const selected = !directory && selectedPath === entry.path
        return (
          <button
            key={`${entry.type}:${entry.path}`}
            type="button"
            onClick={() => (directory ? onNavigate(entry.path) : onOpen(entry))}
            disabled={!directory && entry.type !== 'file'}
            className={`flex w-full cursor-pointer items-center gap-3 px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${selected ? 'bg-emerald-500/10 text-emerald-200' : 'text-slate-300 hover:bg-slate-800/70'}`}
          >
            {directory
              ? <Folder className="h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
              : <File className="h-4 w-4 shrink-0 text-sky-300" aria-hidden="true" />}
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={entry.path}>{baseName(entry.path)}</span>
            <span className="shrink-0 text-[11px] text-slate-600">{directory ? '目录' : formatBytes(entry.size_bytes)}</span>
          </button>
        )
      })}
    </div>
  )
}


function FileEditor({ editor, loading, saving, onChange, onSave, onReload }) {
  if (loading) {
    return <div className="flex min-h-[34rem] items-center justify-center"><Spinner /></div>
  }
  if (!editor) {
    return (
      <div className="flex min-h-[34rem] flex-col items-center justify-center px-6 text-center">
        <File className="h-10 w-10 text-slate-800" aria-hidden="true" />
        <p className="mt-4 text-sm text-slate-400">选择文件以查看和编辑</p>
        <p className="mt-1 max-w-md text-xs leading-5 text-slate-600">
          编辑器只加载不超过 256 KiB 的 UTF-8 文本；二进制文件与大文件不会进入浏览器内存。
        </p>
      </div>
    )
  }
  const sizeBytes = textBytes(editor.content)
  const tooLarge = sizeBytes > MAX_EDITOR_BYTES
  const dirty = editor.isNew
    || editor.content !== editor.originalContent
    || editor.path !== editor.originalPath

  return (
    <div className="flex min-h-[34rem] flex-col">
      <div className="border-b border-slate-800 p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <Field
            id="sandbox-file-path"
            label="Workspace 相对路径"
            hint={editor.isNew ? '可填写尚不存在的目录和文件名，父目录将按安全规则创建' : '既有文件保存时固定原路径'}
            className="min-w-0 flex-1"
          >
            <input
              id="sandbox-file-path"
              value={editor.path}
              onChange={event => onChange({ ...editor, path: event.target.value })}
              readOnly={!editor.isNew}
              className={`${INPUT_CLASS} font-mono text-xs read-only:text-slate-500`}
            />
          </Field>
          <div className="flex shrink-0 gap-2">
            {!editor.isNew && (
              <ActionButton type="button" onClick={onReload} disabled={saving} className="gap-1.5">
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                重新加载
              </ActionButton>
            )}
            <ActionButton
              type="button"
              tone="emerald"
              onClick={onSave}
              disabled={saving || !dirty || !editor.path.trim() || tooLarge}
              className="gap-1.5"
            >
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
              {saving ? '保存中…' : editor.isNew ? '创建文件' : '保存文件'}
            </ActionButton>
          </div>
        </div>
      </div>
      <label htmlFor="sandbox-file-content" className="sr-only">文件内容</label>
      <textarea
        id="sandbox-file-content"
        value={editor.content}
        onChange={event => onChange({ ...editor, content: event.target.value })}
        spellCheck="false"
        className="min-h-[28rem] flex-1 resize-y bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-200 outline-none placeholder:text-slate-700 focus:ring-1 focus:ring-inset focus:ring-emerald-500"
        placeholder="输入 UTF-8 文本内容"
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-800 px-3 py-2 text-[11px] text-slate-500">
        <span>{formatBytes(sizeBytes)} / 256 KiB</span>
        <span>{editor.content.split('\n').length.toLocaleString()} 行</span>
        <span>{dirty ? '有未保存修改' : '已保存'}</span>
        {!editor.isNew && <span className="font-mono">SHA-256 {String(editor.sha256 || '').slice(0, 12)}…</span>}
        {tooLarge && <span className="text-red-300">内容超过在线保存上限</span>}
      </div>
    </div>
  )
}


export function SandboxFilesPage() {
  const [workspaces, setWorkspaces] = useState([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [currentPath, setCurrentPath] = useState('')
  const [entries, setEntries] = useState([])
  const [nextCursor, setNextCursor] = useState('')
  const [loading, setLoading] = useState(true)
  const [browserLoading, setBrowserLoading] = useState(false)
  const [editorLoading, setEditorLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editor, setEditor] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const activeWorkspaces = useMemo(
    () => workspaces.filter(workspace => workspace.status === 'active'),
    [workspaces],
  )
  const selectedWorkspace = useMemo(
    () => workspaces.find(workspace => workspace.workspace_id === workspaceId) || null,
    [workspaceId, workspaces],
  )

  const loadWorkspaces = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get('/sandbox/workspaces')
      const next = response.data?.items || []
      setWorkspaces(next)
      setWorkspaceId(current => {
        if (next.some(item => item.workspace_id === current && item.status === 'active')) return current
        return next.find(item => item.status === 'active')?.workspace_id || ''
      })
    } catch (loadError) {
      setError(formatApiError(loadError, '加载 Workspace 列表失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDirectory = useCallback(async (
    targetWorkspaceId,
    path,
    { append = false, cursor = '' } = {},
  ) => {
    if (!targetWorkspaceId) {
      setEntries([])
      return
    }
    setBrowserLoading(true)
    setError('')
    try {
      const response = await api.get(
        `/sandbox/workspaces/${encodeURIComponent(targetWorkspaceId)}/files`,
        { params: { path, cursor, limit: 200 } },
      )
      const next = response.data?.entries || []
      setEntries(current => append ? [...current, ...next] : next)
      setNextCursor(response.data?.next_cursor || '')
    } catch (loadError) {
      if (!append) setEntries([])
      setError(formatApiError(loadError, '读取 Workspace 目录失败'))
    } finally {
      setBrowserLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { loadWorkspaces() }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [loadWorkspaces])

  useEffect(() => {
    if (!workspaceId) return undefined
    const timer = globalThis.setTimeout(() => {
      loadDirectory(workspaceId, currentPath)
    }, 0)
    return () => globalThis.clearTimeout(timer)
  }, [currentPath, loadDirectory, workspaceId])

  const navigate = path => {
    setCurrentPath(path)
    setEditor(null)
    setNotice('')
  }

  const openFile = async entry => {
    setEditorLoading(true)
    setError('')
    setNotice('')
    try {
      const response = await api.get(
        `/sandbox/workspaces/${encodeURIComponent(workspaceId)}/files/content`,
        { params: { path: entry.path } },
      )
      const data = response.data || {}
      setEditor({
        isNew: false,
        path: data.path,
        originalPath: data.path,
        content: data.content || '',
        originalContent: data.content || '',
        sha256: data.sha256 || '',
      })
    } catch (loadError) {
      setEditor(null)
      setError(formatApiError(loadError, '读取文件失败'))
    } finally {
      setEditorLoading(false)
    }
  }

  const createFile = () => {
    setError('')
    setNotice('')
    const path = joinPath(currentPath, 'untitled.txt')
    setEditor({
      isNew: true,
      path,
      originalPath: path,
      content: '',
      originalContent: '',
      sha256: '',
    })
  }

  const saveFile = async () => {
    if (!editor || !workspaceId) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const response = await api.put(
        `/sandbox/workspaces/${encodeURIComponent(workspaceId)}/files/content`,
        {
          path: editor.path.trim(),
          content: editor.content,
          expected_sha256: editor.isNew ? null : editor.sha256,
        },
      )
      const data = response.data || {}
      const savedPath = data.path || editor.path.trim()
      setEditor({
        isNew: false,
        path: savedPath,
        originalPath: savedPath,
        content: editor.content,
        originalContent: editor.content,
        sha256: data.sha256 || editor.sha256,
      })
      setNotice(`文件 ${savedPath} 已保存。`)
      await Promise.all([
        loadDirectory(workspaceId, currentPath),
        loadWorkspaces(),
      ])
    } catch (saveError) {
      setError(formatApiError(saveError, '保存文件失败'))
    } finally {
      setSaving(false)
    }
  }

  if (loading && !workspaces.length) return <Spinner />

  return (
    <div>
      <PageHeader
        title="Sandbox 文件系统"
        description="在 Workspace 作用域内浏览和编辑持久文件。页面只使用容器内逻辑相对路径，不显示或接收宿主机路径。"
        meta={(
          <>
            <span>活跃 Workspace：{activeWorkspaces.length}</span>
            <span>文本编辑上限：256 KiB</span>
            <span>并发保护：SHA-256 乐观锁</span>
          </>
        )}
        actions={(
          <>
            <ActionButton
              type="button"
              onClick={() => loadDirectory(workspaceId, currentPath)}
              disabled={!workspaceId || browserLoading}
              className="gap-1.5"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${browserLoading ? 'animate-spin' : ''}`} aria-hidden="true" />
              刷新目录
            </ActionButton>
            <ActionButton type="button" tone="emerald" onClick={createFile} disabled={!workspaceId} className="gap-1.5">
              <FilePlus2 className="h-3.5 w-3.5" aria-hidden="true" />
              新建文本文件
            </ActionButton>
          </>
        )}
      />

      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat label="Workspace 状态" value={selectedWorkspace?.status || '未选择'} tone={selectedWorkspace ? 'emerald' : 'slate'} />
        <MiniStat label="当前占用" value={formatBytes(selectedWorkspace?.used_bytes)} />
        <MiniStat label="空间配额" value={formatBytes(selectedWorkspace?.quota_bytes)} />
        <MiniStat label="当前目录可见项" value={entries.length} />
      </div>

      <Card className="mb-4 p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(18rem,30rem)_1fr] lg:items-end">
          <Field id="sandbox-workspace-select" label="Workspace">
            <select
              id="sandbox-workspace-select"
              value={workspaceId}
              onChange={event => {
                setWorkspaceId(event.target.value)
                setCurrentPath('')
                setEditor(null)
              }}
              className={`${INPUT_CLASS} font-mono text-xs`}
            >
              {!activeWorkspaces.length && <option value="">暂无活跃 Workspace</option>}
              {activeWorkspaces.map(workspace => (
                <option key={workspace.workspace_id} value={workspace.workspace_id}>
                  {workspace.workspace_id} · {workspace.sessions?.[0] || '未绑定会话'}
                </option>
              ))}
            </select>
          </Field>
          <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-950 px-2 py-1.5">
            <Breadcrumbs path={currentPath} onNavigate={navigate} />
          </div>
        </div>
      </Card>

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-300">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs leading-5 text-emerald-300">
          {notice}
        </div>
      )}

      {!workspaceId ? (
        <Card className="flex min-h-80 flex-col items-center justify-center px-6 text-center">
          <FolderTree className="h-10 w-10 text-slate-800" aria-hidden="true" />
          <p className="mt-4 text-sm text-slate-400">没有可浏览的活跃 Workspace</p>
          <p className="mt-1 text-xs text-slate-600">请先在 Sandbox 管理页为会话授予 Workspace 能力。</p>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(20rem,26rem)_minmax(0,1fr)]">
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2.5">
              <div className="min-w-0">
                <h2 className="truncate text-xs font-medium text-slate-200" title={currentPath || '/'}>{currentPath || '/'}</h2>
                <p className="mt-0.5 text-[11px] text-slate-600">{entries.length} 个可见项</p>
              </div>
              <Badge tone="slate">只显示安全文件类型</Badge>
            </div>
            <FileBrowser
              entries={entries}
              currentPath={currentPath}
              loading={browserLoading}
              selectedPath={editor?.path}
              onOpen={openFile}
              onNavigate={navigate}
            />
            {nextCursor && (
              <div className="border-t border-slate-800 p-2">
                <ActionButton
                  type="button"
                  onClick={() => loadDirectory(workspaceId, currentPath, { append: true, cursor: nextCursor })}
                  disabled={browserLoading}
                  className="w-full"
                >
                  加载更多
                </ActionButton>
              </div>
            )}
          </Card>

          <Card className="min-w-0 overflow-hidden">
            <FileEditor
              editor={editor}
              loading={editorLoading}
              saving={saving}
              onChange={setEditor}
              onSave={saveFile}
              onReload={() => {
                if (editor) openFile({ path: editor.path })
              }}
            />
          </Card>
        </div>
      )}

      {selectedWorkspace && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
          <span className="font-mono">Workspace {selectedWorkspace.workspace_id}</span>
          <span>绑定会话：{selectedWorkspace.sessions?.length || 0}</span>
          <span>配额状态：{selectedWorkspace.quota_status || 'unknown'}</span>
          {entries[0]?.modified_at_ns && <span>最近可见修改：{formatModifiedAt(Math.max(...entries.map(entry => Number(entry.modified_at_ns) || 0)))}</span>}
        </div>
      )}
    </div>
  )
}
