import { describe, expect, it } from 'vitest'

import type { Project } from '@/store/dashboard'
import type { SessionInfo } from '@/types/hermes'

import { projectGroupsFor, projectSlugForCwd, projectSlugForSession } from './project-groups'

let nextId = 0

function makeSession(cwd: null | string, overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    archived: false,
    cwd,
    ended_at: null,
    id: `s${nextId++}`,
    input_tokens: 0,
    is_active: false,
    last_active: 1_000,
    message_count: 1,
    model: 'claude',
    output_tokens: 0,
    preview: null,
    source: 'cli',
    started_at: 1_000,
    title: null,
    tool_call_count: 0,
    ...overrides
  }
}

function makeProject(slug: string, cwd: string, name = slug): Project {
  return { slug, name, cwd, sessionCount: 0, status: 'idle' }
}

const alpha = makeProject('alpha', '/home/projects/alpha', 'Alpha')
const beta = makeProject('beta', '/home/projects/beta', 'Beta')

describe('projectSlugForCwd', () => {
  it('matches the project root itself and any descendant', () => {
    expect(projectSlugForCwd('/home/projects/alpha', [alpha, beta])).toBe('alpha')
    expect(projectSlugForCwd('/home/projects/alpha/session-1/src', [alpha, beta])).toBe('alpha')
  })

  it('returns "" when the cwd is under no project', () => {
    expect(projectSlugForCwd('/somewhere/else', [alpha, beta])).toBe('')
    expect(projectSlugForCwd('', [alpha, beta])).toBe('')
  })

  it('does not match a sibling whose path only shares a string prefix', () => {
    // /home/projects/alpha-2 must NOT match project "alpha" at /home/projects/alpha.
    expect(projectSlugForCwd('/home/projects/alpha-2/x', [alpha, beta])).toBe('')
  })

  it('picks the deepest (longest) root when projects nest', () => {
    const nested = makeProject('inner', '/home/projects/alpha/inner')
    expect(projectSlugForCwd('/home/projects/alpha/inner/work', [alpha, nested])).toBe('inner')
  })
})

describe('projectSlugForSession', () => {
  it('prefers the explicit project tag over cwd derivation', () => {
    // Retagged session: cwd under alpha but explicitly bound to beta.
    const session = makeSession('/home/projects/alpha/s1', { project: 'beta' })
    expect(projectSlugForSession(session, [alpha, beta])).toBe('beta')
  })

  it('groups off-host (messaging/Modal) sessions with no meaningful cwd', () => {
    const session = makeSession(null, { project: 'alpha', source: 'telegram' })
    expect(projectSlugForSession(session, [alpha, beta])).toBe('alpha')
  })

  it('falls back to cwd matching when no explicit tag', () => {
    const session = makeSession('/home/projects/alpha/s1')
    expect(projectSlugForSession(session, [alpha, beta])).toBe('alpha')
  })

  it('returns "" for unbound sessions', () => {
    expect(projectSlugForSession(makeSession(null), [alpha, beta])).toBe('')
    expect(projectSlugForSession(makeSession(null, { project: '  ' }), [alpha, beta])).toBe('')
  })
})

describe('projectGroupsFor', () => {
  it('groups explicitly tagged messaging sessions under their project', () => {
    const tg = makeSession(null, { project: 'alpha', source: 'telegram' })
    const groups = projectGroupsFor([tg], [alpha, beta], 'Unassigned')
    const alphaGroup = groups.find(g => g.projectSlug === 'alpha')
    expect(alphaGroup?.sessions.map(s => s.id)).toEqual([tg.id])
  })

  it('sends explicitly tagged sessions with an unknown slug to Unassigned', () => {
    const orphan = makeSession(null, { project: 'ghost' })
    const groups = projectGroupsFor([orphan], [alpha], 'Unassigned')
    const unassigned = groups.find(g => g.projectSlug === '')
    expect(unassigned?.sessions.map(s => s.id)).toEqual([orphan.id])
  })

  it('emits a group for every registered project even with zero sessions (drop targets)', () => {
    const groups = projectGroupsFor([], [alpha, beta], 'Unassigned')

    expect(groups.map(g => g.projectSlug)).toEqual(['alpha', 'beta'])
    expect(groups.every(g => g.sessions.length === 0)).toBe(true)
  })

  it('groups sessions from any platform under their project by cwd', () => {
    const groups = projectGroupsFor(
      [
        makeSession('/home/projects/alpha/s1', { source: 'telegram' }),
        makeSession('/home/projects/beta/s2', { source: 'slack' }),
        makeSession('/home/projects/alpha/s3', { source: 'cli' })
      ],
      [alpha, beta],
      'Unassigned'
    )

    const bySlug = Object.fromEntries(groups.map(g => [g.projectSlug, g.sessions.length]))
    expect(bySlug.alpha).toBe(2)
    expect(bySlug.beta).toBe(1)
  })

  it('collects project-less sessions into a single Unassigned group appended last', () => {
    const groups = projectGroupsFor(
      [makeSession('/tmp/scratch'), makeSession(null)],
      [alpha, beta],
      'Unassigned'
    )

    const last = groups[groups.length - 1]
    expect(last.label).toBe('Unassigned')
    expect(last.projectSlug).toBe('')
    expect(last.sessions).toHaveLength(2)
  })

  it('omits the Unassigned group when every session belongs to a project', () => {
    const groups = projectGroupsFor([makeSession('/home/projects/alpha/s1')], [alpha, beta], 'Unassigned')

    expect(groups.some(g => g.label === 'Unassigned')).toBe(false)
  })

  it('sorts rows within a group newest-first', () => {
    const groups = projectGroupsFor(
      [
        makeSession('/home/projects/alpha/old', { started_at: 100 }),
        makeSession('/home/projects/alpha/new', { started_at: 900 })
      ],
      [alpha],
      'Unassigned'
    )

    expect(groups[0].sessions.map(s => s.started_at)).toEqual([900, 100])
  })
})
