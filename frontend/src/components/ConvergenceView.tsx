import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Convergence } from '../api/types'

type Mode = 'draft' | 'template'

const STATUS_ORDER = ['unchanged', 'filled', 'corrected', 'needs_review', 'conflict']

/** Multi-round convergence: shows the per-round trajectory of unresolved units
 * (needs_review + conflict) so you can watch the report converge — or stall on
 * a contradictory round. Last-good-wins across rounds. */
export function ConvergenceView({
  scenarioId,
  mode,
  sourceFormat,
}: {
  scenarioId: string
  mode: Mode
  sourceFormat: string
}) {
  const [conv, setConv] = useState<Convergence | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setErr(null)
    setConv(null)
    api
      .scenarioConverge(mode, scenarioId, sourceFormat)
      .then(setConv)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [scenarioId, mode, sourceFormat])

  if (err) {
    return (
      <div className="panel">
        <p className="small muted">
          No multi-round feedback for this scenario ({err}). Scenario 1 has a
          rounds.json demonstrating convergence.
        </p>
      </div>
    )
  }
  if (!conv) return <div className="panel"><span className="muted small">Loading convergence…</span></div>

  const maxUnresolved = Math.max(1, ...conv.trajectory.map((t) => t.unresolved))

  return (
    <div className="panel">
      <div className="row">
        <strong>Convergence across correction rounds</strong>
        <span className={`badge ${conv.converged ? 'completed' : 'failed'}`}>
          {conv.converged ? 'converged' : 'not converged'}
        </span>
        <span className="muted small">
          {conv.rounds + 1} revisions · final unresolved {conv.final_unresolved}
        </span>
      </div>

      <div style={{ marginTop: 12 }}>
        {conv.trajectory.map((t) => (
          <div key={t.round} className="unit-row" style={{ alignItems: 'center' }}>
            <div className="label">
              Round {t.round} → rev {t.revision}
            </div>
            <div className="val">
              <div className="row" style={{ gap: 4, alignItems: 'center' }}>
                {/* unresolved bar */}
                <div
                  style={{
                    height: 14,
                    width: `${(t.unresolved / maxUnresolved) * 240}px`,
                    minWidth: t.unresolved > 0 ? 12 : 0,
                    background: t.unresolved === 0 ? 'var(--ok)' : 'var(--warn)',
                    borderRadius: 3,
                  }}
                />
                <span className="small">
                  {t.unresolved} unresolved
                </span>
                <div className="spacer" />
                {STATUS_ORDER.filter((s) => t.summary[s]).map((s) => (
                  <span key={s} className={`status-tag ${s}`}>
                    {s.replace('_', ' ')} {t.summary[s]}
                  </span>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      <p className="muted small" style={{ marginTop: 8 }}>
        Unresolved = needs_review + conflict. A contradictory round stays high;
        a later clean round supersedes it (last-good-wins) and drives it to zero.
      </p>
    </div>
  )
}
