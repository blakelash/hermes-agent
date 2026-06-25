import { atom, computed } from 'nanostores'

import { persistString, storedString } from '@/lib/storage'

/* ---------------------------------------------------------------------------
 * Hermes Dashboard + Workspace state.
 *
 * The Home surface (Stream view this phase) and the Workspace IDE read from
 * here. Data that has a real backend source today — the needs-you queue
 * (approval gate), the specialist fleet (active subagents), cost — is pushed
 * in from the gateway event stream + `dashboard.snapshot`. The three panels
 * with no backend source yet (findings, overnight events, environments) keep
 * empty arrays and render honest "not yet wired" states (Phase 2).
 * ------------------------------------------------------------------------- */

export type DashboardView = 'brief' | 'stream' | 'surface'

// ---- Needs-you (real: the gateway approval gate) ----
export type NeedKind = 'APPROVAL' | 'BLOCKED' | 'DECISION' | 'REVIEW'

export interface NeedItem {
  /** The approval `request_id` from the gateway (stable, used to respond). */
  id: string
  kind: NeedKind
  title: string
  detail?: string
  meta?: string
  primaryLabel: string
  secondaryLabel: string
  /** Choice strings sent back via `approvals.respond`. */
  primaryChoice: string
  secondaryChoice: string
  /** Toast copy on each action. */
  primaryToast: string
  secondaryToast: string
  /** REVIEW items route to the Workspace instead of resolving inline. */
  opensWorkspace?: boolean
}

// ---- Specialists / "working now" (real: active subagents) ----
export type SpecialistStatus = 'done' | 'failed' | 'queued' | 'working'

export interface Specialist {
  id: string
  name: string
  status: SpecialistStatus
  doing: string
}

// ---- Phase-2 panels (no backend source yet) ----
export interface Finding {
  id: string
  type: 'DATA' | 'DOC' | 'IMG' | 'LIT'
  title: string
  meta: string
  time: string
}

export interface TimelineEvent {
  id: string
  t: string
  who: string
  text: string
  tone: 'accent' | 'crit' | 'good'
}

export type EnvStatus = 'configured' | 'error' | 'idle' | 'running' | 'synced'

export interface EnvRow {
  id: string
  label: string
  status: EnvStatus
  statusWord: string
}

export interface DashboardCost {
  estimatedUsd: number | null
  inputTokens: number | null
  outputTokens: number | null
}

// ---- View (persisted) ----
const VIEW_KEY = 'hermes-dashboard-view'

const isView = (value: null | string): value is DashboardView =>
  value === 'brief' || value === 'stream' || value === 'surface'

const initialView: DashboardView = isView(storedString(VIEW_KEY)) ? (storedString(VIEW_KEY) as DashboardView) : 'stream'

export const $dashboardView = atom<DashboardView>(initialView)

export const setDashboardView = (view: DashboardView) => {
  persistString(VIEW_KEY, view)
  $dashboardView.set(view)
}

// ---- Live data atoms ----
export const $needs = atom<NeedItem[]>([])
export const $specialists = atom<Specialist[]>([])
export const $findings = atom<Finding[]>([])
export const $events = atom<TimelineEvent[]>([])
export const $environments = atom<EnvRow[]>([])
export const $dashboardCost = atom<DashboardCost | null>(null)

export const $needsCount = computed($needs, needs => needs.length)

export const setNeeds = (needs: NeedItem[]) => $needs.set(needs)
export const removeNeed = (id: string) => $needs.set($needs.get().filter(n => n.id !== id))
export const setSpecialists = (list: Specialist[]) => $specialists.set(list)
export const setEnvironments = (rows: EnvRow[]) => $environments.set(rows)
export const setDashboardCost = (cost: DashboardCost | null) => $dashboardCost.set(cost)

// ---- Toast (design-specific ink pill; see DashboardToast) ----
export const $toast = atom<null | string>(null)
let toastTimer: number | undefined

export const showToast = (message: string) => {
  window.clearTimeout(toastTimer)
  $toast.set(message)
  toastTimer = window.setTimeout(() => $toast.set(null), 2600)
}

export const clearToast = () => {
  window.clearTimeout(toastTimer)
  $toast.set(null)
}

// ---- Workspace state ----
export type WorkspaceOutTab = 'out' | 'term'

export const $workspaceEnv = atom<string>('sandbox')
export const $workspaceOutTab = atom<WorkspaceOutTab>('out')
export const $workspaceEditApplied = atom<boolean>(false)
export const $workspaceActiveFile = atom<string>('analysis.py')

export const setWorkspaceEnv = (id: string) => $workspaceEnv.set(id)
export const setWorkspaceOutTab = (tab: WorkspaceOutTab) => $workspaceOutTab.set(tab)
export const setWorkspaceEditApplied = (applied: boolean) => $workspaceEditApplied.set(applied)
