import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'
import { mapRawProjects, type RawProject, setProjects } from '@/store/dashboard'
import { activeGateway } from '@/store/gateway'
import { notify, notifyError } from '@/store/notifications'

// Create a project from the sidebar's project-grouped view — the same
// projects.create RPC the Dashboard's "Active projects" card uses, followed
// by a projects.list refetch so the new (empty) group appears immediately
// (project mutations don't emit dashboard.update; clients re-poll after
// their own actions).
async function createProject(name: string): Promise<void> {
  const gateway = activeGateway()

  if (!gateway) {
    throw new Error('gateway unavailable')
  }

  await gateway.request('projects.create', { name })

  const res = await gateway.request<{ projects?: RawProject[] }>('projects.list', {})

  setProjects(mapRawProjects(res.projects ?? []))
}

export function NewProjectDialog({
  onOpenChange,
  open
}: {
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useI18n()
  const s = t.sidebar
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = () => {
    const trimmed = name.trim()

    if (!trimmed || busy) {
      return
    }

    setBusy(true)

    void createProject(trimmed)
      .then(() => {
        notify({ durationMs: 2_000, kind: 'success', message: s.newProjectCreated(trimmed) })
        setName('')
        onOpenChange(false)
      })
      .catch(err => notifyError(err, s.newProjectFailed))
      .finally(() => setBusy(false))
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{s.newProject}</DialogTitle>
          <DialogDescription>{s.newProjectDesc}</DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          disabled={busy}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              submit()
            }
          }}
          placeholder={s.newProjectPlaceholder}
          value={name}
        />
        <DialogFooter>
          <Button disabled={!name.trim() || busy} onClick={submit}>
            {t.common.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
