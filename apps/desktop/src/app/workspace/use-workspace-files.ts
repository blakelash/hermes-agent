import { useCallback, useEffect, useState } from 'react'

export interface WorkspaceFile {
  name: string
  path: string
  isDir: boolean
  badge?: 'editing' | 'new'
}

interface TreeResponseObject {
  entries?: unknown
  files?: unknown
  items?: unknown
}

// The gateway file listing shape isn't pinned, so normalize defensively:
// accept a bare array or an object wrapper, with entries as strings or objects.
function normalizeTree(raw: unknown): WorkspaceFile[] {
  const list = Array.isArray(raw)
    ? raw
    : ((raw as TreeResponseObject)?.entries ??
      (raw as TreeResponseObject)?.files ??
      (raw as TreeResponseObject)?.items ??
      [])

  if (!Array.isArray(list)) {
    return []
  }

  return list
    .map((entry): null | WorkspaceFile => {
      if (typeof entry === 'string') {
        return { isDir: entry.endsWith('/'), name: entry.replace(/\/$/, ''), path: entry }
      }

      if (entry && typeof entry === 'object') {
        const e = entry as Record<string, unknown>
        const name = typeof e.name === 'string' ? e.name : typeof e.path === 'string' ? e.path : null

        if (!name) {
          return null
        }

        const isDir =
          e.kind === 'dir' || e.is_dir === true || e.isDir === true || e.type === 'dir' || e.type === 'directory'

        return { isDir, name, path: typeof e.path === 'string' ? e.path : name }
      }

      return null
    })
    .filter((f): f is WorkspaceFile => f !== null)
}

function normalizeContent(raw: unknown): string {
  if (typeof raw === 'string') {
    return raw
  }

  const obj = raw as Record<string, unknown> | null

  if (obj && typeof obj === 'object') {
    if (typeof obj.content === 'string') {
      return obj.content
    }

    if (typeof obj.text === 'string') {
      return obj.text
    }
  }

  return ''
}

/**
 * Loads a project's file tree (or the session's cwd when unscoped) and the
 * selected file's contents via the gateway `files.tree` / `files.read` RPCs.
 * When `project` (slug) is given, the listing is rooted at the project root
 * (independent of the session subdir); `session_id` still scopes the subdir.
 * Degrades gracefully when the backend lacks these RPCs (empty tree + flag).
 */
export function useWorkspaceFiles(
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>,
  project: string | undefined,
  sessionId: null | string
) {
  const [tree, setTree] = useState<WorkspaceFile[]>([])
  const [treeError, setTreeError] = useState(false)
  const [content, setContent] = useState<null | string>(null)
  const [readError, setReadError] = useState(false)

  const baseParams = useCallback((): Record<string, unknown> => {
    const params: Record<string, unknown> = {}

    if (project) {
      params.project = project
    }

    if (sessionId) {
      params.session_id = sessionId
    }

    return params
  }, [project, sessionId])

  useEffect(() => {
    let cancelled = false

    requestGateway<unknown>('files.tree', baseParams())
      .then(raw => {
        if (!cancelled) {
          setTree(normalizeTree(raw))
          setTreeError(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTree([])
          setTreeError(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [baseParams, requestGateway])

  const readFile = useCallback(
    async (path: string) => {
      setContent(null)
      setReadError(false)

      try {
        const params = { ...baseParams(), path }

        const raw = await requestGateway<unknown>('files.read', params)
        const obj = raw as Record<string, unknown> | null

        // files.read resolves with an `{error}` payload (not a thrown RPC error)
        // when read_file's own guards reject the path.
        if (obj && typeof obj === 'object' && typeof obj.error === 'string' && typeof obj.content !== 'string') {
          setReadError(true)

          return
        }

        setContent(normalizeContent(raw))
      } catch {
        setReadError(true)
      }
    },
    [baseParams, requestGateway]
  )

  return { content, readError, readFile, tree, treeError }
}
