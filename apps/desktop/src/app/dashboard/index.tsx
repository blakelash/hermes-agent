import './dashboard.css'

import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import type { HermesGateway } from '@/hermes'
import { $dashboardView, type NeedItem, removeNeed, setDashboardView, showToast } from '@/store/dashboard'
import { notifyError } from '@/store/notifications'
import { $activeSessionId } from '@/store/session'

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
  const activeSessionId = useStore($activeSessionId)
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
        const params: Record<string, unknown> = { choice, request_id: item.id }

        if (activeSessionId) {
          params.session_id = activeSessionId
        }

        await requestGateway('approval.respond', params)
      } catch (error) {
        notifyError(error, 'Could not submit your decision')
        void refresh()
      }
    },
    [activeSessionId, onOpenWorkspace, refresh, requestGateway]
  )

  return (
    <div className="hermes-dashboard hd-root">
      <DashboardTopBar onOpenWorkspace={onOpenWorkspace} onView={setDashboardView} view={view} />
      <StreamView onResolveNeed={resolveNeed} />
      <DashboardToast />
    </div>
  )
}
