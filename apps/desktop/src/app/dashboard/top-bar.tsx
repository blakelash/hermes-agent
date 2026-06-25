import type { DashboardView } from '@/store/dashboard'

interface TopBarProps {
  envLabel?: string
  onOpenWorkspace: () => void
  onView: (view: DashboardView) => void
  view: DashboardView
  working?: boolean
}

const ACCENT_DOT: React.CSSProperties = { background: 'var(--hd-accent)', height: 6, width: 6 }

export function DashboardTopBar({ envLabel = 'sb-9f2a', onOpenWorkspace, onView, view, working = true }: TopBarProps) {
  const seg = (target: DashboardView) => `hd-seg${view === target ? ' hd-seg--active' : ''}`

  return (
    <header className="hd-topbar">
      <div className="hd-brand">
        Hermes <span>· research collaborator</span>
      </div>

      <div className="hd-switcher">
        {/* Brief and Surface are scoped for a later phase; shown disabled. */}
        <button className={seg('brief')} disabled type="button">
          Brief
        </button>
        <button className={seg('stream')} onClick={() => onView('stream')} type="button">
          Stream
        </button>
        <button className={seg('surface')} disabled type="button">
          Surface
        </button>
      </div>

      <button className="hd-btn-secondary" onClick={onOpenWorkspace} type="button">
        Workspace →
      </button>

      <div className="hd-topbar-right">
        <span className="hd-chip">
          <span className="hd-dot hd-anim-breathe" style={ACCENT_DOT} />
          {envLabel}
        </span>
        {working && (
          <span className="hd-working">
            <span className="hd-dot hd-anim-breathe" style={ACCENT_DOT} />
            working
          </span>
        )}
        <span className="hd-avatar" style={{ fontSize: 13, height: 28, width: 28 }}>
          H
        </span>
      </div>
    </header>
  )
}
