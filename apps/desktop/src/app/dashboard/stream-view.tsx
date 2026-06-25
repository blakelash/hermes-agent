import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import {
  $environments,
  $events,
  $findings,
  $needs,
  $specialists,
  type NeedItem,
  type SpecialistStatus
} from '@/store/dashboard'
import { $activeSessionId } from '@/store/session'
import { $subagentsBySession } from '@/store/subagents'

import { LiveLog, type LiveLogLine, type LiveLogTone } from './live-log'

interface StreamViewProps {
  onResolveNeed: (item: NeedItem, choice: string, toast: string) => void
}

const STATUS_COLOR: Record<SpecialistStatus, string> = {
  done: 'var(--hd-good)',
  failed: 'var(--hd-crit)',
  queued: 'var(--hd-faint)',
  working: 'var(--hd-accent)'
}

function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export function StreamView({ onResolveNeed }: StreamViewProps) {
  const needs = useStore($needs)
  const specialists = useStore($specialists)
  const events = useStore($events)
  const findings = useStore($findings)
  const environments = useStore($environments)
  const subagentsBySession = useStore($subagentsBySession)
  const activeSessionId = useStore($activeSessionId)

  // Real "right now" log: the most recent stream entries across the active
  // session's subagents. No fabricated lines — empty falls back to a calm note.
  const liveLines = useMemo<LiveLogLine[]>(() => {
    const subs = activeSessionId ? (subagentsBySession[activeSessionId] ?? []) : []

    return subs
      .flatMap(sub => sub.stream)
      .slice()
      .sort((a, b) => a.at - b.at)
      .slice(-6)
      .map(entry => {
        const tone: LiveLogTone = entry.isError ? 'crit' : entry.kind === 'summary' ? 'good' : 'dim'

        return { t: formatClock(entry.at), text: entry.text, tone }
      })
  }, [activeSessionId, subagentsBySession])

  const working = specialists.filter(s => s.status === 'working')

  const nowTitle =
    working[0]?.doing ?? (specialists.length > 0 ? 'Specialists are wrapping up' : 'Idle — nothing running')

  const plural = (n: number) => (n === 1 ? '' : 's')

  return (
    <main className="hd-main">
      <div className="hd-stream">
        {/* ---- FEED ---- */}
        <div style={{ minWidth: 0 }}>
          <div className="hd-summary">
            <span className="hd-avatar" style={{ fontSize: 16, height: 34, marginTop: 2, width: 34 }}>
              H
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="hd-summary-head">
                <span className="hd-summary-name">Hermes</span>
                <span className="hd-summary-time">caught you up · {formatClock(Date.now())}</span>
              </div>
              <div className="hd-headline hd-serif">
                {needs.length > 0 ? 'A few things need your call.' : "You're all caught up."}
              </div>
              <p className="hd-summary-body">
                I have <b>{specialists.length}</b> specialist{plural(specialists.length)}{' '}
                {working.length > 0 ? 'working' : 'idle'} right now
                {needs.length > 0 ? (
                  <>
                    , and I&apos;ve paused on <b>{needs.length}</b> thing{plural(needs.length)} that need your decision.
                  </>
                ) : (
                  '. Nothing needs your attention.'
                )}
              </p>
            </div>
          </div>

          <div className="hd-timeline">
            <div className="hd-timeline-rail" />

            {/* Overnight events — no structured activity log yet (Phase 2). */}
            {events.length > 0 ? (
              events.map(ev => (
                <div className="hd-event" key={ev.id}>
                  <span
                    className="hd-event-dot"
                    style={{
                      background:
                        ev.tone === 'good'
                          ? 'var(--hd-good)'
                          : ev.tone === 'crit'
                            ? 'var(--hd-crit)'
                            : 'var(--hd-accent)'
                    }}
                  />
                  <div className="hd-event-time">{ev.t}</div>
                  <div className="hd-event-text">
                    <b>{ev.who}</b> <span>{ev.text}</span>
                  </div>
                </div>
              ))
            ) : (
              <div className="hd-event">
                <span className="hd-event-dot" style={{ background: 'var(--hd-faint)' }} />
                <div className="hd-event-text">
                  <span>The overnight activity timeline isn&apos;t wired up yet.</span>
                </div>
              </div>
            )}

            {/* Delivered findings — no deliverables registry yet (Phase 2). */}
            <div className="hd-event">
              <span className="hd-event-dot" style={{ background: 'var(--hd-good)' }} />
              <div className="hd-node-label">delivered overnight</div>
              {findings.length > 0 ? (
                <div className="hd-findings">
                  {findings.map(f => (
                    <div className="hd-finding" key={f.id}>
                      <span
                        className="hd-finding-badge"
                        style={{ background: 'var(--hd-accent-07)', color: 'var(--hd-accent)' }}
                      >
                        {f.type}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="hd-finding-title">{f.title}</div>
                        <div className="hd-finding-meta">{f.meta}</div>
                      </div>
                      <span className="hd-finding-time">{f.time}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ color: 'var(--hd-faint)', fontSize: 12.5, lineHeight: 1.5 }}>
                  Fresh findings &amp; deliverables aren&apos;t wired up yet.
                </div>
              )}
            </div>

            {/* Now node */}
            <div style={{ position: 'relative' }}>
              <span className="hd-now-dot hd-anim-glow" />
              <div className="hd-now-head">
                <span className="hd-now-tag">right now</span>
                <span className="hd-now-title">{nowTitle}</span>
              </div>
              <LiveLog lines={liveLines} />
            </div>
          </div>
        </div>

        {/* ---- STICKY RAIL ---- */}
        <aside className="hd-rail">
          <section className="hd-card">
            <div className="hd-card-head">
              <span className="hd-microlabel">Needs you</span>
              {needs.length > 0 && <span className="hd-count">{needs.length}</span>}
            </div>
            {needs.length > 0 ? (
              needs.map(item => (
                <div className="hd-need" key={item.id}>
                  <div className="hd-need-kind" style={{ color: kindColor(item.kind) }}>
                    {item.kind}
                  </div>
                  <div className="hd-need-title">{item.title}</div>
                  <div className="hd-need-actions">
                    <button
                      className="hd-btn-primary"
                      onClick={() => onResolveNeed(item, item.primaryChoice, item.primaryToast)}
                      type="button"
                    >
                      {item.primaryLabel}
                    </button>
                    <button
                      className="hd-btn-text"
                      onClick={() => onResolveNeed(item, item.secondaryChoice, item.secondaryToast)}
                      type="button"
                    >
                      {item.secondaryLabel}
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="hd-empty" style={{ alignItems: 'center', display: 'flex', gap: 8 }}>
                <span className="hd-toast-check" style={{ background: 'var(--hd-accent-07)', color: 'var(--hd-good)' }}>
                  ✓
                </span>
                All clear.
              </div>
            )}
          </section>

          <section className="hd-card">
            <div className="hd-card-head">
              <span className="hd-microlabel">Working now</span>
            </div>
            {specialists.length > 0 ? (
              specialists.map(sp => (
                <div className="hd-row" key={sp.id}>
                  <span
                    className={`hd-dot${sp.status === 'working' ? ' hd-anim-breathe' : ''}`}
                    style={{ background: STATUS_COLOR[sp.status], height: 7, width: 7 }}
                  />
                  <span className="hd-row-name">{sp.name}</span>
                  <span className="hd-row-status" style={{ color: STATUS_COLOR[sp.status] }}>
                    {sp.status}
                  </span>
                </div>
              ))
            ) : (
              <div className="hd-empty">No specialists are active right now.</div>
            )}
          </section>

          <section className="hd-card">
            <div className="hd-card-head">
              <span className="hd-microlabel">Environments</span>
            </div>
            {environments.length > 0 ? (
              environments.map(env => (
                <div className="hd-row" key={env.id}>
                  <span className="hd-dot" style={{ background: envColor(env.status), height: 7, width: 7 }} />
                  <span className="hd-row-name">{env.label}</span>
                  <span className="hd-row-status" style={{ color: envColor(env.status) }}>
                    {env.statusWord}
                  </span>
                </div>
              ))
            ) : (
              <div className="hd-empty">The environments registry isn&apos;t wired up yet.</div>
            )}
          </section>
        </aside>
      </div>
    </main>
  )
}

function kindColor(kind: NeedItem['kind']): string {
  switch (kind) {
    case 'BLOCKED':
      return 'var(--hd-crit)'

    case 'DECISION':
      return 'var(--hd-warn)'

    default:
      return 'var(--hd-accent)'
  }
}

function envColor(status: string): string {
  switch (status) {
    case 'configured':
      return 'var(--hd-faint)'

    case 'error':
      return 'var(--hd-crit)'

    case 'idle':
      return 'var(--hd-faint)'

    case 'running':
      return 'var(--hd-accent)'

    default:
      return 'var(--hd-good)'
  }
}
