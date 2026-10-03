import { useState } from 'react'
import type { CorrectedField, CorrectionStatus } from '../api/types'

export function StatusTag({ status }: { status: CorrectionStatus }) {
  return <span className={`status-tag ${status}`}>{status.replace('_', ' ')}</span>
}

export function Prov({ provenance }: { provenance: CorrectedField['provenance'] }) {
  // Human-readable "where this came from" line. The raw rule/retrieval telemetry
  // and internal correction IDs are demoted to a hover title (confusing as the
  // default, useful only to an auditor).
  const human: string[] = []
  if (provenance.corpus.length) human.push(`from ${provenance.corpus.join(', ')}`)
  if (provenance.corrections.length) {
    const n = provenance.corrections.length
    human.push(`${n} reviewer ${n === 1 ? 'comment' : 'comments'}`)
  }
  if (!human.length) return null

  const rawTitle = [
    provenance.corpus.length ? `corpus: ${provenance.corpus.join(', ')}` : '',
    provenance.corrections.length ? `corrections: ${provenance.corrections.join(', ')}` : '',
    provenance.rule ? `rule: ${provenance.rule}` : '',
  ].filter(Boolean).join('  ·  ')

  return (
    <div className="prov" title={rawTitle}>
      Source: {human.join(' · ')}
    </div>
  )
}

/** Strip an email to a readable name (regional.director@x.com -> regional.director). */
export function displaySource(source: unknown): string {
  const s = String(source ?? '')
  return s.includes('@') ? s.split('@')[0] : s
}

export function displayValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    if ('observed' in o || 'required' in o) {
      return `observed: ${JSON.stringify(o.observed)} · required: ${JSON.stringify(o.required)}`
    }
  }
  return JSON.stringify(v)
}

export type ResolveFn = (target: string, value: string) => Promise<void>

/** The resolve controls shared by the audit and document views: a candidate
 *  picker for conflicts, a value input for needs_review. */
export function ResolveControls({ field, onResolve }: { field: CorrectedField; onResolve: ResolveFn }) {
  const [busy, setBusy] = useState(false)
  const [manual, setManual] = useState('')

  async function submit(value: string) {
    if (!value.trim()) return
    setBusy(true)
    try {
      await onResolve(field.key, value.trim())
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="resolve-controls">
      {field.status === 'conflict' && field.candidates.length > 0 ? (
        <>
          <span className="small muted">Resolve:</span>
          {field.candidates.map((c, i) => (
            <button
              key={i}
              className="btn secondary small"
              disabled={busy}
              onClick={() => void submit(String(c.value))}
              aria-label={`Resolve ${field.label} to ${String(c.value)}`}
            >
              Use {String(c.value)}
            </button>
          ))}
        </>
      ) : (
        <>
          <label htmlFor={`resolve-${field.key}`} className="small muted">
            Resolve — enter a value:
          </label>
          <input
            id={`resolve-${field.key}`}
            className="small"
            value={manual}
            disabled={busy}
            onChange={(e) => setManual(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void submit(manual)}
            placeholder={`${field.label} value`}
          />
          <button
            className="btn secondary small"
            disabled={busy || !manual.trim()}
            onClick={() => void submit(manual)}
          >
            {busy ? 'Saving…' : 'Set value'}
          </button>
        </>
      )}
    </div>
  )
}

/** The audit-style unit row: label + status + value + candidates + note +
 *  provenance + inline resolve when actionable. Used by the Audit view. */
export function FieldRow({ field, onResolve }: { field: CorrectedField; onResolve?: ResolveFn }) {
  const resolvable = !!onResolve && (field.status === 'conflict' || field.status === 'needs_review')
  return (
    <div className="unit-row">
      <div className="label">{field.label}</div>
      <div className="val">
        <StatusTag status={field.status} />{' '}
        {field.status === 'conflict' ? (
          <span className="mono muted">unresolved — choose a candidate below</span>
        ) : (
          <span className="mono">{displayValue(field.value)}</span>
        )}
        {field.candidates.length > 0 && (
          <div className="small" style={{ color: 'var(--err)' }}>
            candidates:{' '}
            {field.candidates
              .map((c) => `${JSON.stringify(c.value)} (${displaySource(c.source)})`)
              .join('  vs  ')}
          </div>
        )}
        {field.note && <div className="small muted">{field.note}</div>}
        {resolvable && onResolve && <ResolveControls field={field} onResolve={onResolve} />}
        <Prov provenance={field.provenance} />
      </div>
    </div>
  )
}

/** Is this unit something the human still needs to act on? */
export function isOpen(f: CorrectedField): boolean {
  return f.status === 'conflict' || f.status === 'needs_review'
}
