import './dashboard.css'

import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import type { HermesGateway } from '@/hermes'
import {
  $dashboardView,
  type NeedItem,
  removeNeed,
  setDashboardView,
  setWorkspaceProject,
  showToast
} from '@/store/dashboard'
import { notifyError } from '@/store/notifications'

import { StreamView } from './stream-view'
import { DashboardToast } from './toast'
import { DashboardTopBar } from './top-bar'
import { useDashboardData } from './use-dashboard-data'

interface DashboardViewProps {
  gateway: HermesGateway | null
  onClose: () => void
  onOpenWorkspace: () => void
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function DashboardView({ gateway, onClose, onOpenWorkspace, requestGateway }: DashboardViewProps) {
  const view = useStore($dashboardView)
  const { refresh } = useDashboardData(gateway, requestGateway)

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

  const resolveNeed = useCallback(
    async (item: NeedItem, choice: string, toast: string) => {
      // REVIEW items open the Workspace to inspect the diff instead of resolving.
      if (item.opensWorkspace) {
        onOpenWorkspace()

        return
      }

      // Optimistic: clear the item + toast, then submit. Restore on failure.
      removeNeed(item.id)
      showToast(toast)

      try {
        // The inbox spans sessions — route the response to the need's own session.
        const params: Record<string, unknown> = { choice, request_id: item.id }

        if (item.sessionId) {
          params.session_id = item.sessionId
        }

        await requestGateway('approval.respond', params)
      } catch (error) {
        notifyError(error, 'Could not submit your decision')
        void refresh()
      }
    },
    [onOpenWorkspace, refresh, requestGateway]
  )

  const openProject = useCallback(
    (slug: string) => {
      setWorkspaceProject(slug)
      onOpenWorkspace()
    },
    [onOpenWorkspace]
  )

  // The plain "Workspace →" button opens the active session's workspace, not a
  // previously-focused project.
  const openWorkspaceGeneric = useCallback(() => {
    setWorkspaceProject(null)
    onOpenWorkspace()
  }, [onOpenWorkspace])

  return (
    <div className="hermes-dashboard hd-root">
      <DashboardTopBar onOpenWorkspace={openWorkspaceGeneric} onView={setDashboardView} view={view} />
      <StreamView onOpenProject={openProject} onResolveNeed={resolveNeed} />
      <DashboardToast />
    </div>
  )
}
