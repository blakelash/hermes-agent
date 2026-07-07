export type LiveLogTone = 'accent' | 'crit' | 'dim' | 'good'

export interface LiveLogLine {
  t: string
  tone: LiveLogTone
  text: string
}

const TONE_COLOR: Record<LiveLogTone, string> = {
  accent: '#6CCFD8',
  crit: '#e0635f',
  dim: '#6B7390',
  good: '#5BC98C'
}

/**
 * The "right now" / leading-edge log. Isolated as its own component so its
 * frequent updates don't re-render the rest of the dashboard. Fed real
 * activity lines (derived from the active session's subagent stream); shows a
 * calm placeholder when there's nothing live rather than faking output.
 */
export function LiveLog({ lines }: { lines: LiveLogLine[] }) {
  if (lines.length === 0) {
    return (
      <div className="hd-livelog">
        <div className="hd-livelog-line">
          <span className="msg" style={{ color: TONE_COLOR.dim }}>
            Waiting for live activity…
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="hd-livelog">
      {lines.map((line, i) => (
        <div className="hd-livelog-line" key={`${line.t}-${i}`}>
          <span className="t">{line.t}</span>
          <span className="msg" style={{ color: TONE_COLOR[line.tone] }}>
            {line.text}
          </span>
        </div>
      ))}
    </div>
  )
}
