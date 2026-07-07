import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from '@/components/ui/context-menu'
import { CopyButton } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { renameSession, type SessionInfo } from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { exportSession } from '@/lib/session-export'
import { $projects } from '@/store/dashboard'
import { activeGateway } from '@/store/gateway'
import { notify, notifyError } from '@/store/notifications'
import {
  $activeSessionId,
  $selectedStoredSessionId,
  setMessagingSessions,
  setSessions
} from '@/store/session'
import { canOpenSessionWindow, openSessionInNewWindow } from '@/store/windows'

import type { SessionTitleResponse } from '../../types'

// Rename a session, preferring the gateway's session.title RPC over REST.
//
// A freshly *branched* session (and any brand-new chat) lives only in the
// gateway's in-memory _sessions map keyed by its RUNTIME id — no row is
// persisted to state.db until the first turn. REST PATCH /api/sessions/{id}
// resolves against the stored sessions table, so it 404s ("Session not found")
// on these runtime-only sessions. The session.title RPC resolves the live
// runtime session AND persists the row on demand, so it succeeds where REST
// cannot. This mirrors the /title slash command's fix (use-prompt-actions.ts).
//
// We only take the RPC path for the ACTIVE/selected session: its runtime id is
// known ($activeSessionId) and it lives on the active gateway, so there is no
// profile-routing ambiguity. Every other row (already persisted, possibly on a
// background profile) keeps the REST path, which handles profile scoping and a
// non-empty title is required by the RPC (it rejects clears), so clears stay on
// REST too.
export async function renameSessionPreferringRpc(
  storedSessionId: string,
  title: string,
  profile?: string
): Promise<{ title?: string }> {
  const isActiveRow = storedSessionId === $selectedStoredSessionId.get()
  const runtimeId = isActiveRow ? $activeSessionId.get() : null
  const gateway = activeGateway()

  if (title && runtimeId && gateway) {
    try {
      const result = await gateway.request<SessionTitleResponse>('session.title', {
        session_id: runtimeId,
        title
      })

      return { title: result?.title ?? title }
    } catch (err) {
      // Fall through to REST — e.g. the socket is mid-reconnect. REST still
      // works for any session that already has a persisted row. Log so a
      // genuine RPC-side failure (which then surfaces a REST 404 for the
      // runtime id) is at least diagnosable instead of silently swallowed.
      console.warn('session.title RPC rename failed; falling back to REST', err)
    }
  }

  return renameSession(storedSessionId, title, profile)
}

// Reassign a session to a project (or with '' unassign it) via the
// session.project.set RPC. It stamps the durable sessions.project column —
// grouping updates immediately — and, when the row carries its chat identity,
// pins the chat's sticky default so the chat's NEXT session actually works in
// the project. Both sidebar slices are updated optimistically so the row
// moves without waiting for a refetch.
async function moveSessionToProject(sessionId: string, slug: string): Promise<void> {
  const gateway = activeGateway()

  if (!gateway) {
    throw new Error('gateway unavailable')
  }

  await gateway.request('session.project.set', { project: slug, session_id: sessionId })

  const retag = (list: SessionInfo[]) =>
    list.map(session => (session.id === sessionId ? { ...session, project: slug } : session))

  setSessions(retag)
  setMessagingSessions(retag)
}

function MoveToProjectDialog({
  onOpenChange,
  open,
  sessionId
}: {
  onOpenChange: (open: boolean) => void
  open: boolean
  sessionId: string
}) {
  const { t } = useI18n()
  const r = t.sidebar.row
  const projects = useStore($projects)
  const [busy, setBusy] = useState(false)

  const choose = (slug: string, name: string) => {
    setBusy(true)

    void moveSessionToProject(sessionId, slug)
      .then(() => {
        notify(slug ? r.movedToProject(name) : r.removedFromProject)
        onOpenChange(false)
      })
      .catch(err => notifyError(err, r.moveToProjectFailed))
      .finally(() => setBusy(false))
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{r.moveToProject}</DialogTitle>
          <DialogDescription>{r.moveToProjectDesc}</DialogDescription>
        </DialogHeader>
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {projects.map(project => (
            <Button
              className="justify-start"
              disabled={busy}
              key={project.slug}
              onClick={() => choose(project.slug, project.name || project.slug)}
              variant="ghost"
            >
              <Codicon name="folder" size="0.875rem" />
              <span className="truncate">{project.name || project.slug}</span>
            </Button>
          ))}
          <Button
            className="justify-start"
            disabled={busy}
            onClick={() => choose('', '')}
            variant="ghost"
          >
            <Codicon name="circle-slash" size="0.875rem" />
            <span>{r.moveToProjectNone}</span>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface SessionActions {
  sessionId: string
  title: string
  pinned?: boolean
  profile?: string
  onPin?: () => void
  onArchive?: () => void
  onDelete?: () => void
}

type MenuItem = typeof DropdownMenuItem | typeof ContextMenuItem

interface ItemSpec {
  className?: string
  disabled: boolean
  icon: string
  label: string
  onSelect: (event: Event) => void
  variant?: 'destructive'
}

function useSessionActions({ sessionId, title, pinned = false, profile, onPin, onArchive, onDelete }: SessionActions) {
  const { t } = useI18n()
  const r = t.sidebar.row
  const [renameOpen, setRenameOpen] = useState(false)
  const [moveOpen, setMoveOpen] = useState(false)

  const pinItem: ItemSpec = {
    disabled: !onPin,
    icon: 'pin',
    label: pinned ? r.unpin : r.pin,
    onSelect: () => {
      triggerHaptic('selection')
      onPin?.()
    }
  }

  const items: ItemSpec[] = [
    ...(canOpenSessionWindow()
      ? [
          {
            disabled: !sessionId,
            icon: 'link-external',
            label: r.newWindow,
            onSelect: () => {
              triggerHaptic('selection')
              void openSessionInNewWindow(sessionId)
            }
          }
        ]
      : []),
    {
      disabled: !sessionId,
      icon: 'cloud-download',
      label: r.export,
      onSelect: () => {
        triggerHaptic('selection')
        void exportSession(sessionId, { profile, title })
      }
    },
    {
      disabled: !sessionId,
      icon: 'edit',
      label: r.rename,
      onSelect: () => {
        triggerHaptic('selection')
        setRenameOpen(true)
      }
    },
    {
      disabled: !sessionId,
      icon: 'folder-opened',
      label: r.moveToProject,
      onSelect: () => {
        triggerHaptic('selection')
        setMoveOpen(true)
      }
    },
    {
      disabled: !onArchive,
      icon: 'archive',
      label: r.archive,
      onSelect: () => {
        triggerHaptic('selection')
        onArchive?.()
      }
    },
    {
      className: 'text-destructive focus:text-destructive',
      disabled: !onDelete,
      icon: 'trash',
      label: t.common.delete,
      onSelect: () => {
        triggerHaptic('warning')
        onDelete?.()
      },
      variant: 'destructive'
    }
  ]

  const renderMenuItem = (Item: MenuItem, { className, disabled, icon, label, onSelect, variant }: ItemSpec) => (
    <Item className={className} disabled={disabled} key={label} onSelect={onSelect} variant={variant}>
      <Codicon name={icon} size="0.875rem" />
      <span>{label}</span>
    </Item>
  )

  const renderItems = (Item: MenuItem) => (
    <>
      {renderMenuItem(Item, pinItem)}
      <CopyButton
        appearance={Item === DropdownMenuItem ? 'menu-item' : 'context-menu-item'}
        disabled={!sessionId}
        errorMessage={r.copyIdFailed}
        key={r.copyId}
        label={r.copyId}
        onCopyError={err => notifyError(err, r.copyIdFailed)}
        text={sessionId}
      />
      {items.map(spec => renderMenuItem(Item, spec))}
    </>
  )

  const renameDialog = (
    <>
      <RenameSessionDialog
        currentTitle={title}
        onOpenChange={setRenameOpen}
        open={renameOpen}
        profile={profile}
        sessionId={sessionId}
      />
      <MoveToProjectDialog onOpenChange={setMoveOpen} open={moveOpen} sessionId={sessionId} />
    </>
  )

  return { renameDialog, renderItems }
}

interface SessionActionsMenuProps
  extends SessionActions, Pick<React.ComponentProps<typeof DropdownMenuContent>, 'align' | 'sideOffset'> {
  children: React.ReactNode
}

export function SessionActionsMenu({ children, align = 'end', sideOffset = 6, ...actions }: SessionActionsMenuProps) {
  const { t } = useI18n()
  const { renameDialog, renderItems } = useSessionActions(actions)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
        <DropdownMenuContent
          align={align}
          aria-label={t.sidebar.row.actionsFor(actions.title)}
          className="w-40"
          sideOffset={sideOffset}
        >
          {renderItems(DropdownMenuItem)}
        </DropdownMenuContent>
      </DropdownMenu>
      {renameDialog}
    </>
  )
}

interface SessionContextMenuProps extends SessionActions {
  children: React.ReactNode
}

export function SessionContextMenu({ children, ...actions }: SessionContextMenuProps) {
  const { t } = useI18n()
  const { renameDialog, renderItems } = useSessionActions(actions)

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent aria-label={t.sidebar.row.actionsFor(actions.title)} className="w-40">
          {renderItems(ContextMenuItem)}
        </ContextMenuContent>
      </ContextMenu>
      {renameDialog}
    </>
  )
}

interface RenameSessionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sessionId: string
  currentTitle: string
  profile?: string
}

function RenameSessionDialog({ open, onOpenChange, sessionId, currentTitle, profile }: RenameSessionDialogProps) {
  const { t } = useI18n()
  const r = t.sidebar.row
  const [value, setValue] = useState(currentTitle)
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setValue(currentTitle)
      window.setTimeout(() => inputRef.current?.select(), 0)
    }
  }, [currentTitle, open])

  const submit = async () => {
    const next = value.trim()

    if (!sessionId || submitting) {
      return
    }

    if (next === currentTitle.trim()) {
      onOpenChange(false)

      return
    }

    setSubmitting(true)

    try {
      const result = await renameSessionPreferringRpc(sessionId, next, profile)
      const finalTitle = result.title || next || ''
      setSessions(prev => prev.map(s => (s.id === sessionId ? { ...s, title: finalTitle || null } : s)))
      notify({ durationMs: 2_000, kind: 'success', message: r.renamed })
      onOpenChange(false)
    } catch (err) {
      notifyError(err, r.renameFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{r.renameTitle}</DialogTitle>
          <DialogDescription>{r.renameDesc}</DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          disabled={submitting}
          onChange={event => setValue(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void submit()
            } else if (event.key === 'Escape') {
              onOpenChange(false)
            }
          }}
          placeholder={r.untitledPlaceholder}
          ref={inputRef}
          value={value}
        />
        <DialogFooter>
          <Button disabled={submitting} onClick={() => onOpenChange(false)} type="button" variant="ghost">
            {t.common.cancel}
          </Button>
          <Button disabled={submitting} onClick={() => void submit()} type="button">
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
