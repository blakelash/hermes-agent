import '../dashboard/dashboard.css'

import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { HermesGateway } from '@/hermes'
import { chatMessageText } from '@/lib/chat-messages'
import {
  $environments,
  $needs,
  $projects,
  $workspaceEditApplied,
  $workspaceEnv,
  $workspaceOutTab,
  $workspaceProject,
  $workspaceSession,
  type EnvRow,
  removeNeed,
  setWorkspaceEditApplied,
  setWorkspaceEnv,
  setWorkspaceOutTab,
  showToast
} from '@/store/dashboard'
import { notifyError } from '@/store/notifications'
import { $activeSessionId, $messages } from '@/store/session'

import { DashboardToast } from '../dashboard/toast'
import { useDashboardData } from '../dashboard/use-dashboard-data'

import { useWorkspaceFiles } from './use-workspace-files'

interface WorkspaceViewProps {
  gateway: HermesGateway | null
  onClose: () => void
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

// Shown only until the real environments snapshot arrives (or if none exist).
const FALLBACK_ENVS: EnvRow[] = [{ id: 'session', label: 'workspace', status: 'idle', statusWord: '' }]

function envDotColor(status: string): string {
  switch (status) {
    case 'error':
      return 'var(--hd-crit)'

    case 'running':
      return 'var(--hd-accent)'

    default:
      return 'var(--hd-faint)'
  }
}

export function WorkspaceView({ gateway, onClose, requestGateway }: WorkspaceViewProps) {
  const env = useStore($workspaceEnv)
  const outTab = useStore($workspaceOutTab)
  const applied = useStore($workspaceEditApplied)
  const needs = useStore($needs)
  const messages = useStore($messages)
  const environments = useStore($environments)
  const projects = useStore($projects)
  const workspaceProject = useStore($workspaceProject)
  const workspaceSession = useStore($workspaceSession)
  const activeSessionId = useStore($activeSessionId)

  // Populate the shared dashboard store (needs/specialists/environments/projects)
  // so the Workspace reflects reality even when opened directly.
  useDashboardData(gateway, requestGateway)

  // The Workspace is scoped to a project's filesystem; the file tree roots at
  // the project (when one is focused), and the session focus drives the subdir.
  const focusedProject = workspaceProject ? projects.find(p => p.slug === workspaceProject) : undefined
  const focusedSessionId = workspaceSession ?? activeSessionId

  const envs = environments.length > 0 ? environments : FALLBACK_ENVS
  const activeEnvId = envs.some(e => e.id === env) ? env : envs[0]?.id

  const [activeFile, setActiveFile] = useState<null | string>(null)

  const { content, readError, readFile, tree, treeError } = useWorkspaceFiles(
    requestGateway,
    focusedProject?.slug,
    focusedSessionId
  )

  const composerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault()
        onClose()
      }
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const pendingNeed = needs[0]

  const openFile = useCallback(
    (path: string) => {
      setActiveFile(path)
      void readFile(path)
    },
    [readFile]
  )

  const approve = useCallback(async () => {
    if (!pendingNeed) {
      return
    }

    removeNeed(pendingNeed.id)
    setWorkspaceEditApplied(true)
    setWorkspaceOutTab('term')
    showToast('Approved — applying and re-running')

    try {
      const params: Record<string, unknown> = { choice: 'once', request_id: pendingNeed.id }
      const sid = pendingNeed.sessionId ?? activeSessionId

      if (sid) {
        params.session_id = sid
      }

      await requestGateway('approval.respond', params)
    } catch (error) {
      notifyError(error, 'Could not submit your approval')
    }
  }, [activeSessionId, pendingNeed, requestGateway])

  const decline = useCallback(async () => {
    if (!pendingNeed) {
      return
    }

    removeNeed(pendingNeed.id)
    showToast('Declined')

    try {
      const params: Record<string, unknown> = { choice: 'deny', request_id: pendingNeed.id }
      const sid = pendingNeed.sessionId ?? activeSessionId

      if (sid) {
        params.session_id = sid
      }

      await requestGateway('approval.respond', params)
    } catch (error) {
      notifyError(error, 'Could not submit your decision')
    }
  }, [activeSessionId, pendingNeed, requestGateway])

  const sendSteer = useCallback(async () => {
    const node = composerRef.current
    const text = node?.innerText.trim()

    if (!text || !activeSessionId) {
      return
    }

    try {
      await requestGateway('session.steer', { session_id: activeSessionId, text })
      showToast('Sent to Hermes')

      if (node) {
        node.innerText = ''
      }
    } catch (error) {
      notifyError(error, 'Could not send your message')
    }
  }, [activeSessionId, requestGateway])

  const conversation = messages
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => ({ id: m.id, role: m.role, text: chatMessageText(m).trim() }))
    .filter(m => m.text.length > 0)
    .slice(-8)

  return (
    <div className="hermes-dashboard hd-root">
      {/* TOP BAR */}
      <header className="hd-ws-topbar">
        <div className="hd-ws-envs">
          <span className="hd-microlabel" style={{ marginRight: 2 }}>
            files in
          </span>
          {envs.map(e => (
            <button
              className={`hd-env-pill${activeEnvId === e.id ? ' hd-env-pill--active' : ''}`}
              key={e.id}
              onClick={() => setWorkspaceEnv(e.id)}
              type="button"
            >
              <span
                className="hd-dot"
                style={{
                  background: activeEnvId === e.id ? 'var(--hd-accent)' : envDotColor(e.status),
                  height: 6,
                  width: 6
                }}
              />
              {e.label}
            </button>
          ))}
        </div>
        <div className="hd-topbar-right">
          <span className="hd-working">
            <span className="hd-dot hd-anim-breathe" style={{ background: 'var(--hd-accent)', height: 6, width: 6 }} />
            watching Hermes work
          </span>
          <span className="hd-avatar" style={{ fontSize: 13, height: 28, width: 28 }}>
            H
          </span>
        </div>
      </header>

      {/* STAGE */}
      <div className="hd-ws-stage">
        {/* CONVERSATION RAIL */}
        <section className="hd-ws-rail">
          <div className="hd-ws-rail-scroll">
            {conversation.length > 0 ? (
              conversation.map(m =>
                m.role === 'user' ? (
                  <div className="hd-bubble-row-user" key={m.id}>
                    <div className="hd-bubble-user">{m.text}</div>
                  </div>
                ) : (
                  <div className="hd-bubble-agent" key={m.id}>
                    <span className="hd-avatar" style={{ fontSize: 13, height: 26, marginTop: 1, width: 26 }}>
                      H
                    </span>
                    <div className="hd-bubble-agent-body">{m.text}</div>
                  </div>
                )
              )
            ) : (
              <div className="hd-empty" style={{ border: 0, padding: '8px 2px' }}>
                Open a chat to see the conversation alongside the code.
              </div>
            )}

            {pendingNeed && (
              <div className="hd-focuschip">
                <span className="hd-mono" style={{ color: 'var(--hd-accent)' }}>
                  ✎
                </span>
                <span>
                  {pendingNeed.title}
                  <span aria-hidden className="hd-wave">
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
              </div>
            )}
          </div>
          <div className="hd-ws-composer">
            <div className="hd-ws-composer-box">
              <div
                aria-label="Message Hermes"
                className="hd-ws-composer-field"
                contentEditable
                data-ph="Steer it, or edit the file yourself…"
                onKeyDown={event => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void sendSteer()
                  }
                }}
                ref={composerRef}
                role="textbox"
                suppressContentEditableWarning
              />
              <button aria-label="Send" className="hd-ws-send" onClick={() => void sendSteer()} type="button">
                ↑
              </button>
            </div>
          </div>
        </section>

        {/* WORKSPACE COLUMN */}
        <section className="hd-ws-work">
          <div className="hd-ws-editor-area">
            {/* FILE TREE */}
            <nav className="hd-ws-tree">
              <div className="hd-tree-head">
                {focusedProject ? focusedProject.name : (envs.find(e => e.id === activeEnvId)?.label ?? activeEnvId)}
              </div>
              {tree.length > 0 ? (
                tree.map(file => (
                  <div
                    className={`hd-tree-row${file.isDir ? ' hd-tree-row--dir' : ''}${
                      activeFile === file.path ? ' hd-tree-row--active' : ''
                    }`}
                    key={file.path}
                    onClick={() => (file.isDir ? undefined : openFile(file.path))}
                  >
                    <span className="hd-tree-glyph">{file.isDir ? '▾' : '◦'}</span>
                    {file.name}
                  </div>
                ))
              ) : (
                <div className="hd-empty" style={{ border: 0 }}>
                  {treeError ? 'File listing isn’t available for this environment yet.' : 'No files.'}
                </div>
              )}
            </nav>

            {/* EDITOR */}
            <div className="hd-editor">
              <div className="hd-editor-tabs">
                <div className="hd-editor-tab hd-editor-tab--active">
                  <span className="hd-dot" style={{ background: 'var(--hd-accent)', height: 6, width: 6 }} />
                  {activeFile ?? 'no file open'}
                </div>
              </div>

              {pendingNeed && !applied && (
                <div className="hd-proposal hd-proposal--pending">
                  <span className="hd-proposal-badge" style={{ background: 'var(--hd-accent-13)', color: '#5FCAD4' }}>
                    H
                  </span>
                  <span>Hermes is waiting on your approval — {pendingNeed.title}</span>
                  <span className="hd-proposal-actions">
                    <button className="hd-pbtn hd-pbtn--accept" onClick={() => void approve()} type="button">
                      Approve
                    </button>
                    <button className="hd-pbtn hd-pbtn--ghost" onClick={() => void decline()} type="button">
                      Decline
                    </button>
                  </span>
                </div>
              )}
              {applied && (
                <div className="hd-proposal hd-proposal--applied">
                  <span className="hd-proposal-badge" style={{ background: '#2E9E6622', color: '#5BC98C' }}>
                    ✓
                  </span>
                  <span>
                    You approved — <b>re-running</b> in {activeEnvId}
                  </span>
                  <button className="hd-pbtn hd-pbtn--view" onClick={() => setWorkspaceOutTab('term')} type="button">
                    View run →
                  </button>
                </div>
              )}

              <div className="hd-diff">
                {activeFile && content !== null ? (
                  <div className="hd-diff-inner">
                    {content.split('\n').map((raw, i) => {
                      // read_file emits `<lineNo>|<code>`; split it back into gutter + code.
                      const match = /^(\d+)\|(.*)$/.exec(raw)
                      const lineNo = match ? match[1] : String(i + 1)
                      const code = match ? match[2] : raw

                      return (
                        <div className="hd-diff-line" key={i}>
                          <span className="hd-diff-gutter">{lineNo}</span>
                          <span className="hd-diff-code">{code || ' '}</span>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div className="hd-diff-inner" style={{ color: 'var(--hd-dark-faint)', padding: '14px' }}>
                    {readError
                      ? 'Could not read this file from the environment.'
                      : activeFile
                        ? 'Loading…'
                        : 'Select a file from the tree to view it.'}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* OUTPUT / TERMINAL DOCK */}
          <div className="hd-dock">
            <div className="hd-dock-tabs">
              <button
                className={`hd-dock-tab${outTab === 'out' ? ' hd-dock-tab--active' : ''}`}
                onClick={() => setWorkspaceOutTab('out')}
                type="button"
              >
                Output
              </button>
              <button
                className={`hd-dock-tab${outTab === 'term' ? ' hd-dock-tab--active' : ''}`}
                onClick={() => setWorkspaceOutTab('term')}
                type="button"
              >
                Terminal
              </button>
              <span className="hd-dock-meta">{activeEnvId}</span>
            </div>
            <div className="hd-dock-body">
              {outTab === 'out' ? (
                <div className="hd-empty" style={{ border: 0 }}>
                  Rendered output (result tables and figures) will appear here once the deliverables/artifacts registry
                  is wired up.
                </div>
              ) : (
                <div className="hd-empty" style={{ border: 0 }}>
                  Live run output will stream here once the run/PTY stream is wired to this surface.
                </div>
              )}
            </div>
          </div>
        </section>
      </div>

      <DashboardToast />
    </div>
  )
}
