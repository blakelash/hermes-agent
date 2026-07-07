import { useLocation, useNavigate } from 'react-router-dom'

import { appViewForPath, DASHBOARD_ROUTE, NEW_CHAT_ROUTE, WORKSPACE_ROUTE } from '@/app/routes'
import { cn } from '@/lib/utils'

type Tab = 'chat' | 'dashboard' | 'workspace'

const TABS: { id: Tab; label: string; route: string }[] = [
  { id: 'chat', label: 'Chat', route: NEW_CHAT_ROUTE },
  { id: 'dashboard', label: 'Dashboard', route: DASHBOARD_ROUTE },
  { id: 'workspace', label: 'Workspace', route: WORKSPACE_ROUTE }
]

/**
 * Top-level mode switcher (Chat | Dashboard | Workspace), centered in the
 * titlebar and always visible above every surface. Chat is the persistent base
 * (its PTY survives); Dashboard/Workspace are route-gated full-bleed views.
 */
export function AppTabs() {
  const location = useLocation()
  const navigate = useNavigate()
  const view = appViewForPath(location.pathname)
  const active: Tab = view === 'dashboard' || view === 'workspace' ? view : 'chat'

  return (
    <div
      className={cn(
        'fixed left-1/2 top-1 z-[80] -translate-x-1/2',
        'pointer-events-auto select-none [-webkit-app-region:no-drag]',
        'flex items-center gap-0.5 rounded-lg p-0.5',
        'bg-(--ui-bg-quaternary) border border-(--ui-stroke-tertiary) shadow-sm'
      )}
      role="tablist"
    >
      {TABS.map(tab => {
        const isActive = active === tab.id

        return (
          <button
            aria-selected={isActive}
            className={cn(
              'rounded-md px-3 py-1 text-xs font-medium transition-colors cursor-pointer',
              'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-(--ui-accent)',
              isActive
                ? 'bg-(--ui-bg-elevated) text-(--ui-text-primary) shadow-sm'
                : 'text-(--ui-text-secondary) hover:text-(--ui-text-primary)'
            )}
            key={tab.id}
            onClick={() => navigate(tab.route)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
