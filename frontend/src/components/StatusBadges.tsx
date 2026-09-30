import type { OverallStatus, Stage } from '../api/types'

export function OverallBadge({ status }: { status: OverallStatus }) {
  return <span className={`badge ${status}`}>{status}</span>
}

export function SentimentBadge({ sentiment }: { sentiment: string }) {
  return <span className={`badge ${sentiment}`}>{sentiment}</span>
}

/** Renders the pipeline stages (ingest -> parse -> chunk -> embed -> index). */
export function StageTrack({ stages }: { stages: Stage[] }) {
  return (
    <div className="stages">
      {stages.map((s) => (
        <span
          key={s.name}
          className={`stage-pill ${s.status}`}
          title={s.detail || s.status}
        >
          {s.name}
        </span>
      ))}
    </div>
  )
}
