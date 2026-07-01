import { useEffect } from 'react'

import type { HermesGateway } from '@/hermes'
import { mapRawProjects, type RawProject, setProjects } from '@/store/dashboard'

interface ProjectsListResponse {
  projects?: RawProject[]
}

/**
 * Keeps the global `$projects` store seeded in the chat context so the sidebar
 * can group sessions by project without opening the Dashboard. Fetches
 * `projects.list` on connect and re-fetches on `dashboard.update` (which the
 * gateway emits on project create/rename and session churn). Defensive: if the
 * RPC is missing (older gateway), it leaves `$projects` untouched — the sidebar
 * then shows only the "Unassigned" bucket.
 */
export function useProjectsSync(
  gateway: HermesGateway | null,
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
) {
  useEffect(() => {
    if (!gateway) {
      return
    }

    let cancelled = false

    const refresh = async () => {
      try {
        const res = await requestGateway<ProjectsListResponse>('projects.list', {})

        if (!cancelled) {
          setProjects(mapRawProjects(res.projects ?? []))
        }
      } catch {
        // Backend RPC not available yet — leave the store as-is.
      }
    }

    void refresh()

    const offUpdate = gateway.on('dashboard.update', () => void refresh())

    return () => {
      cancelled = true
      offUpdate()
    }
  }, [gateway, requestGateway])
}
