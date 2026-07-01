import { useCallback, useEffect, useRef } from 'react'

import type { HermesGateway } from '@/hermes'
import {
  type DashboardCost,
  type EnvRow,
  type EnvStatus,
  type NeedItem,
  type Project,
  type ProjectStatus,
  setDashboardCost,
  setEnvironments,
  setNeeds,
  setProjects,
  setSpecialists,
  type Specialist,
  type SpecialistStatus
} from '@/store/dashboard'

interface RawApproval {
  request_id?: string
  session_id?: string
  command?: string
  description?: string
  kind?: string
  meta?: string
}

// projects.list / dashboard.snapshot return camelCase (matches the Project interface).
interface RawProject {
  slug?: string
  name?: string
  cwd?: string
  sessionCount?: number
  status?: string
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
  projects?: RawProject[]
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
        sessionId: a.session_id,
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

function projectStatus(status?: string): ProjectStatus {
  switch (status) {
    case 'blocked':
      return 'blocked'

    case 'working':
      return 'working'

    default:
      return 'idle'
  }
}

function mapProjects(raw: RawProject[]): Project[] {
  return raw
    .filter(p => typeof p.slug === 'string')
    .map(p => ({
      slug: p.slug as string,
      name: p.name?.trim() || (p.slug as string),
      cwd: p.cwd ?? '',
      sessionCount: typeof p.sessionCount === 'number' ? p.sessionCount : 0,
      status: projectStatus(p.status)
    }))
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
  const refreshTimer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    try {
      // Home is profile-global: no session_id → the snapshot aggregates needs
      // across all sessions, the fleet, environments, projects, and cost.
      const snapshot = await requestGateway<SnapshotResponse>('dashboard.snapshot', {})
      setNeeds(mapNeeds(snapshot.needs ?? []))
      setSpecialists(mapSpecialists(snapshot.specialists ?? []))
      setProjects(mapProjects(snapshot.projects ?? []))
      setEnvironments(mapEnvironments(snapshot.environments ?? []))
      setDashboardCost(mapCost(snapshot.cost))
    } catch {
      // Backend RPC not available yet — keep empty states.
    }
  }, [requestGateway])

  const scheduleRefresh = useCallback(() => {
    window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(() => void refresh(), 180)
  }, [refresh])

  useEffect(() => {
    void refresh()

    // Poll as a fallback so the surface stays current across reconnects and
    // activity that doesn't emit a dashboard.update (the snapshot is cheap).
    const poll = window.setInterval(() => void refresh(), 10_000)

    return () => {
      window.clearTimeout(refreshTimer.current)
      window.clearInterval(poll)
    }
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
