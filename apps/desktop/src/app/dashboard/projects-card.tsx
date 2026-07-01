import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { $projects, type ProjectStatus } from '@/store/dashboard'

const STATUS_COLOR: Record<ProjectStatus, string> = {
  blocked: 'var(--hd-crit)',
  idle: 'var(--hd-faint)',
  working: 'var(--hd-accent)'
}

interface ProjectsCardProps {
  onCreate: (name: string) => void
  onOpenProject: (slug: string) => void
  onRename: (slug: string, name: string) => void
}

/** "Active projects" panel with inline create + rename, wired to the project registry. */
export function ProjectsCard({ onCreate, onOpenProject, onRename }: ProjectsCardProps) {
  const projects = useStore($projects)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [renaming, setRenaming] = useState<null | string>(null)
  const [renameDraft, setRenameDraft] = useState('')

  const submitNew = () => {
    const name = draft.trim()

    if (name) {
      onCreate(name)
    }

    setDraft('')
    setAdding(false)
  }

  const submitRename = (slug: string) => {
    const name = renameDraft.trim()

    if (name) {
      onRename(slug, name)
    }

    setRenaming(null)
    setRenameDraft('')
  }

  return (
    <section className="hd-card">
      <div className="hd-card-head">
        <span className="hd-microlabel">Active projects</span>
        <button className="hd-proj-new" onClick={() => setAdding(a => !a)} type="button">
          + New
        </button>
      </div>

      {adding && (
        <div className="hd-row">
          <input
            autoFocus
            className="hd-input"
            onBlur={submitNew}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                submitNew()
              } else if (e.key === 'Escape') {
                setAdding(false)
                setDraft('')
              }
            }}
            placeholder="Project name…"
            value={draft}
          />
        </div>
      )}

      {projects.length > 0 ? (
        projects.map(p => (
          <div className="hd-row" key={p.slug}>
            <span
              className={`hd-dot${p.status === 'working' ? ' hd-anim-breathe' : ''}`}
              style={{ background: STATUS_COLOR[p.status], height: 7, width: 7 }}
            />
            {renaming === p.slug ? (
              <input
                autoFocus
                className="hd-input"
                onBlur={() => submitRename(p.slug)}
                onChange={e => setRenameDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    submitRename(p.slug)
                  } else if (e.key === 'Escape') {
                    setRenaming(null)
                  }
                }}
                value={renameDraft}
              />
            ) : (
              <>
                <span className="hd-row-name" onClick={() => onOpenProject(p.slug)} style={{ cursor: 'pointer' }}>
                  {p.name}
                </span>
                <button
                  className="hd-proj-edit"
                  onClick={() => {
                    setRenaming(p.slug)
                    setRenameDraft(p.name)
                  }}
                  title="Rename project"
                  type="button"
                >
                  ✎
                </button>
                <span className="hd-row-status" style={{ color: 'var(--hd-faint)' }}>
                  {p.sessionCount} {p.sessionCount === 1 ? 'thread' : 'threads'}
                </span>
              </>
            )}
          </div>
        ))
      ) : adding ? null : (
        <div className="hd-empty">No projects yet — make one with “+ New”.</div>
      )}
    </section>
  )
}
