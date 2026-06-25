import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import type { HermesGateway } from '@/hermes'
import {
  type DashboardCost,
  type EnvRow,
  type EnvStatus,
  type NeedItem,
  setDashboardCost,
  setEnvironments,
  setNeeds,
  setSpecialists,
  type Specialist,
  type SpecialistStatus
} from '@/store/dashboard'
import { $activeSessionId } from '@/store/session'

interface RawApproval {
  request_id?: string
  command?: string
  description?: string
  kind?: string
  meta?: string
}

interface RawSpecialist {
  subagent_id?: string
  goal?: string
  status?: string
  model?: string
}

// Matches the gateway `dashboard.snapshot` cost block (see _dashboard_cost).
interface RawCost {
  cost_usd?: null | number
  input?: null | number
  output?: null | number
}

interface RawEnvironment {
  id?: string
  backend?: string
  label?: string
  status?: string
  detail?: string
  idle_seconds?: number
}

interface SnapshotResponse {
  needs?: RawApproval[]
  specialists?: RawSpecialist[]
  cost?: RawCost
  environments?: RawEnvironment[]
}

// Approval choices accepted by the gateway are "once" | "session" | "always" | "deny".
function mapNeeds(raw: RawApproval[]): NeedItem[] {
  return raw
    .filter(a => typeof a.request_id === 'string')
    .map(a => {
      const title = a.description?.trim() || a.command?.trim() || 'Approval requested'

      return {
        id: a.request_id as string,
        kind: 'APPROVAL',
        title,
        detail: a.command,
        meta: a.meta ?? 'approval',
        primaryLabel: 'Approve',
        secondaryLabel: 'Decline',
        primaryChoice: 'once',
        secondaryChoice: 'deny',
        primaryToast: 'Approved',
        secondaryToast: 'Declined'
      } satisfies NeedItem
    })
}

function mapStatus(status?: string): SpecialistStatus {
  switch (status) {
    case 'completed':

    case 'done':

    case 'succeeded':
      return 'done'

    case 'error':

    case 'failed':
      return 'failed'

    case 'pending':

    case 'queued':

    case 'spawn_requested':
      return 'queued'

    default:
      return 'working'
  }
}

function specialistName(raw: RawSpecialist): string {
  const goal = raw.goal?.trim()

  if (goal) {
    // Use the first few words of the goal as a compact name.
    const words = goal.split(/\s+/).slice(0, 3).join(' ')

    return words.length > 28 ? `${words.slice(0, 27)}…` : words
  }

  return raw.model?.trim() || raw.subagent_id?.slice(0, 8) || 'subagent'
}

function mapSpecialists(raw: RawSpecialist[]): Specialist[] {
  return raw.map((s, i) => ({
    id: s.subagent_id || `sub-${i}`,
    name: specialistName(s),
    status: mapStatus(s.status),
    doing: s.goal?.trim() || '—'
  }))
}

function mapCost(raw?: RawCost): DashboardCost | null {
  if (!raw) {
    return null
  }

  return {
    estimatedUsd: raw.cost_usd ?? null,
    inputTokens: raw.input ?? null,
    outputTokens: raw.output ?? null
  }
}

function envStatus(status?: string): EnvStatus {
  switch (status) {
    case 'configured':
      return 'configured'

    case 'error':
      return 'error'

    case 'running':
      return 'running'

    case 'synced':
      return 'synced'

    default:
      return 'idle'
  }
}

function humanizeIdle(seconds: number): string {
  if (seconds < 60) {
    return `${Math.max(0, Math.round(seconds))}s`
  }

  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m`
  }

  return `${Math.floor(seconds / 3600)}h`
}

function envStatusWord(status: EnvStatus, idleSeconds?: number): string {
  if (status === 'configured') {
    return 'not started'
  }

  if (status === 'idle' && typeof idleSeconds === 'number') {
    return `idle ${humanizeIdle(idleSeconds)}`
  }

  return status
}

function mapEnvironments(raw: RawEnvironment[]): EnvRow[] {
  return raw
    .filter(e => typeof e.id === 'string' || typeof e.backend === 'string')
    .map((e, i) => {
      const status = envStatus(e.status)

      return {
        id: e.id || `env-${i}`,
        label: e.label?.trim() || e.backend || 'environment',
        status,
        statusWord: envStatusWord(status, e.idle_seconds)
      } satisfies EnvRow
    })
}

/**
 * Seeds the dashboard store from `dashboard.snapshot` and keeps it live by
 * re-fetching on `approval.request`, `dashboard.update`, and `subagent.*`
 * events. Defensive: if the gateway is missing the new RPCs (backend not yet
 * merged), it leaves the store empty rather than throwing — panels then render
 * their empty states.
 */
export function useDashboardData(
  gateway: HermesGateway | null,
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
) {
  const activeSessionId = useStore($activeSessionId)
  const refreshTimer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    try {
      const params: Record<string, unknown> = {}

      if (activeSessionId) {
        params.session_id = activeSessionId
      }

      const snapshot = await requestGateway<SnapshotResponse>('dashboard.snapshot', params)
      setNeeds(mapNeeds(snapshot.needs ?? []))
      setSpecialists(mapSpecialists(snapshot.specialists ?? []))
      setEnvironments(mapEnvironments(snapshot.environments ?? []))
      setDashboardCost(mapCost(snapshot.cost))
    } catch {
      // Backend RPC not available yet — keep empty states.
    }
  }, [activeSessionId, requestGateway])

  const scheduleRefresh = useCallback(() => {
    window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(() => void refresh(), 180)
  }, [refresh])

  useEffect(() => {
    void refresh()

    return () => window.clearTimeout(refreshTimer.current)
  }, [refresh])

  useEffect(() => {
    if (!gateway) {
      return
    }

    const offApproval = gateway.on('approval.request', scheduleRefresh)
    const offUpdate = gateway.on('dashboard.update', scheduleRefresh)
    const offSubStart = gateway.on('subagent.start', scheduleRefresh)
    const offSubComplete = gateway.on('subagent.complete', scheduleRefresh)
    const offSubProgress = gateway.on('subagent.progress', scheduleRefresh)

    return () => {
      offApproval()
      offUpdate()
      offSubStart()
      offSubComplete()
      offSubProgress()
    }
  }, [gateway, scheduleRefresh])

  return { refresh }
}
