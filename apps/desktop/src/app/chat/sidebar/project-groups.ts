import type { SessionInfo } from '@/hermes'
import type { Project } from '@/store/dashboard'

import type { SidebarSessionGroup } from './workspace-groups'

const UNASSIGNED_ID = '__unassigned__'

/** Trim + strip trailing separators so prefix comparison is separator-stable. */
const normalize = (path: string): string => path.trim().replace(/[/\\]+$/, '')

/** True when *child* is *root* itself or a descendant (either separator). */
function isWithin(root: string, child: string): boolean {
  if (!root) {
    return false
  }

  return child === root || child.startsWith(`${root}/`) || child.startsWith(`${root}\\`)
}

/**
 * Longest-prefix match of a session cwd against the registered project roots —
 * the client-side twin of the backend `project_for_cwd`. A cwd under a project
 * root (or the root itself) belongs to that project; when roots nest, the
 * deepest wins. Returns the winning slug, or '' when the cwd is under none.
 *
 * This is why the sidebar can group sessions from EVERY platform (desktop,
 * Telegram, Slack) with no per-platform backend change: each session already
 * carries its `cwd`, and membership derives from the same registry the
 * Dashboard uses.
 */
export function projectSlugForCwd(cwd: string, projects: Project[]): string {
  const target = normalize(cwd)

  if (!target) {
    return ''
  }

  let best = ''
  let bestLen = -1

  for (const project of projects) {
    const root = normalize(project.cwd || '')

    if (root && isWithin(root, target) && root.length > bestLen) {
      best = project.slug
      bestLen = root.length
    }
  }

  return best
}

/**
 * A session's project slug: the EXPLICIT `project` field wins (messaging
 * sessions whose work lives off-host — Modal volume — carry it from the
 * backend's `sessions.project` column), falling back to cwd-prefix matching
 * for sessions that only know their directory.
 */
export function projectSlugForSession(session: SessionInfo, projects: Project[]): string {
  const explicit = session.project?.trim() || ''

  if (explicit) {
    return explicit
  }

  return projectSlugForCwd(session.cwd?.trim() || '', projects)
}

/**
 * Group sessions under their project, reusing `workspaceGroupsFor`'s
 * {@link SidebarSessionGroup} shape so the existing row / virtual-list rendering
 * is reused unchanged.
 *
 * EVERY registered project gets a group (even with zero sessions) so the
 * project structure is always visible and each project is a valid drop target
 * for drag-to-reassign. Projects keep registry order; sessions matching no
 * project fall into a single "Unassigned" group appended last. Rows within a
 * group sort newest-first (stable muscle memory), matching `workspaceGroupsFor`.
 *
 * Membership prefers a session's explicit `project` tag over cwd derivation
 * ({@link projectSlugForSession}) — an explicitly bound session whose slug is
 * not (yet) in the registry falls into "Unassigned" rather than vanishing.
 */
export function projectGroupsFor(
  sessions: SessionInfo[],
  projects: Project[],
  unassignedLabel: string,
  options: { preserveSessionOrder?: boolean } = {}
): SidebarSessionGroup[] {
  const groups = new Map<string, SidebarSessionGroup>()

  for (const project of projects) {
    groups.set(project.slug, {
      id: `project:${project.slug}`,
      label: project.name || project.slug,
      path: project.cwd || null,
      sessions: [],
      mode: 'project',
      projectSlug: project.slug
    })
  }

  const unassigned: SessionInfo[] = []

  for (const session of sessions) {
    const slug = projectSlugForSession(session, projects)
    const group = slug ? groups.get(slug) : undefined

    if (group) {
      group.sessions.push(session)
    } else {
      unassigned.push(session)
    }
  }

  if (!options.preserveSessionOrder) {
    for (const group of groups.values()) {
      group.sessions.sort((a, b) => b.started_at - a.started_at)
    }

    unassigned.sort((a, b) => b.started_at - a.started_at)
  }

  const result = [...groups.values()]

  if (unassigned.length) {
    result.push({
      id: UNASSIGNED_ID,
      label: unassignedLabel,
      path: null,
      sessions: unassigned,
      mode: 'project',
      projectSlug: ''
    })
  }

  return result
}
