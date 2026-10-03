import { Fragment, useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  CorrectedField,
  CorrectedReport,
  CorrectionStatus,
  ScenarioComponent,
  ScenarioInfo,
} from '../api/types'
import { ConvergenceView } from './ConvergenceView'

type Mode = 'draft' | 'template'
type View = 'single' | 'rounds'

function StatusTag({ status }: { status: CorrectionStatus }) {
  return <span className={`status-tag ${status}`}>{status.replace('_', ' ')}</span>
}

function Prov({ provenance }: { provenance: CorrectedField['provenance'] }) {
  // Human-readable "where this came from" line. The raw rule/retrieval telemetry
  // (e.g. "...#c1s1@0.316") and internal correction IDs are useful for auditors
  // but confusing as the default, so they're demoted into a hover/expand.
  const human: string[] = []
  if (provenance.corpus.length) {
    human.push(`from ${provenance.corpus.join(', ')}`)
  }
  if (provenance.corrections.length) {
    const n = provenance.corrections.length
    human.push(`${n} reviewer ${n === 1 ? 'comment' : 'comments'}`)
  }
  if (!human.length) return null

  // The full raw detail (internal correction IDs + rule/retrieval string) is
  // kept ONLY as a hover title for the rare auditor case — it is never rendered
  // as visible text, because IDs like "corr_severity_high" and rule strings
  // mean nothing to a reviewer and duplicate what the candidates line shows.
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

/** Strip an email down to a readable name (regional.director@x.com -> regional.director). */
function displaySource(source: unknown): string {
  const s = String(source ?? '')
  return s.includes('@') ? s.split('@')[0] : s
}

function displayValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object') {
    // Discipline findings carry {observed, required}; render readably.
    const o = v as Record<string, unknown>
    if ('observed' in o || 'required' in o) {
      return `observed: ${JSON.stringify(o.observed)} · required: ${JSON.stringify(o.required)}`
    }
  }
  return JSON.stringify(v)
}

function FieldRow({
  field,
  onResolve,
}: {
  field: CorrectedField
  onResolve?: (target: string, value: string) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [manual, setManual] = useState('')

  const resolvable =
    !!onResolve && (field.status === 'conflict' || field.status === 'needs_review')

  async function submit(value: string) {
    if (!onResolve || !value.trim()) return
    setBusy(true)
    try {
      await onResolve(field.key, value.trim())
    } finally {
      setBusy(false)
    }
  }

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

        {resolvable && (
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
        )}

        <Prov provenance={field.provenance} />
      </div>
    </div>
  )
}

/** The four-component correction pipeline and the corrected intermediate JSON. */
export function CorrectionPipeline() {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([])
  const [scenarioId, setScenarioId] = useState<string>('1')
  const [sourceFormat, setSourceFormat] = useState<string>('json')
  const [components, setComponents] = useState<ScenarioComponent[]>([])
  const [mode, setMode] = useState<Mode>('draft')
  const [report, setReport] = useState<CorrectedReport | null>(null)
  const [selected, setSelected] = useState<string>('intermediate_json')
  const [view, setView] = useState<View>('single')
  const [raw, setRaw] = useState<unknown>(null)
  const [err, setErr] = useState<string | null>(null)

  const loadReport = useCallback((m: Mode, sid: string, fmt: string) => {
    setErr(null)
    api.scenarioReconcile(m, sid, fmt).then(setReport).catch((e) =>
      setErr(e instanceof Error ? e.message : String(e)),
    )
  }, [])

  // Apply a human decision to one unresolved unit, then re-render with the
  // updated report returned by the backend.
  const handleResolve = useCallback(
    async (target: string, value: string) => {
      setErr(null)
      try {
        const updated = await api.scenarioResolve(target, value, mode, scenarioId, sourceFormat)
        setReport(updated)
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
      }
    },
    [mode, scenarioId, sourceFormat],
  )

  useEffect(() => {
    api.scenarios().then(setScenarios).catch((e) =>
      setErr(e instanceof Error ? e.message : String(e)),
    )
  }, [])

  useEffect(() => {
    api.scenarioComponents(scenarioId).then(setComponents).catch((e) =>
      setErr(e instanceof Error ? e.message : String(e)),
    )
  }, [scenarioId])

  useEffect(() => loadReport(mode, scenarioId, sourceFormat), [mode, scenarioId, sourceFormat, loadReport])

  useEffect(() => {
    api
      .scenarioComponent(selected, mode, scenarioId, sourceFormat)
      .then((r) => setRaw(r.data))
      .catch(() => setRaw(null))
  }, [selected, mode, scenarioId, sourceFormat])

  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>Correction pipeline</strong>
          <select
            aria-label="Scenario"
            value={scenarioId}
            onChange={(e) => setScenarioId(e.target.value)}
          >
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}. {s.title}
              </option>
            ))}
          </select>
          <div className="spacer" />
          <label htmlFor="source-fidelity" className="muted small" title="Which pre-converted copy of the demo first-attempt document to read (this tab does not convert an uploaded file). Higher fidelity = more structure preserved.">source fidelity</label>
          <select id="source-fidelity" value={sourceFormat} onChange={(e) => setSourceFormat(e.target.value)}>
            <option value="json">JSON (baseline)</option>
            <option value="docx">DOCX (high)</option>
            <option value="pptx">PPTX (good)</option>
            <option value="pdf">PDF (partial)</option>
          </select>
          <div className="mode-toggle" title="Draft: fix a completed-but-flawed report. Template: fill a blank report template from the corpus.">
            <button
              className={mode === 'draft' ? 'active' : ''}
              onClick={() => setMode('draft')}
            >
              Draft mode
            </button>
            <button
              className={mode === 'template' ? 'active' : ''}
              onClick={() => setMode('template')}
            >
              Template mode
            </button>
          </div>
          <div className="mode-toggle" title="Single pass: one round of corrections. Rounds: watch multiple feedback rounds converge.">
            <button
              className={view === 'single' ? 'active' : ''}
              onClick={() => setView('single')}
            >
              Single pass
            </button>
            <button
              className={view === 'rounds' ? 'active' : ''}
              onClick={() => setView('rounds')}
            >
              Rounds
            </button>
          </div>
        </div>

        <div className="small muted" style={{ marginTop: 6 }}>
          <b>{mode === 'draft' ? 'Draft mode' : 'Template mode'}</b>
          {mode === 'draft'
            ? ': correcting a flawed first-attempt report against the source material and comments.'
            : ': filling a blank report template from the source material.'}
          {'  '}Source = which pre-converted copy of the demo first attempt is
          read (DOCX highest fidelity, PDF lowest; JSON is the clean baseline).
          Uploading your own file uses the API&apos;s convert endpoint, not this tab.
        </div>

        {components.length === 0 && !err && (
          <div className="muted small" style={{ marginTop: 12 }}>Loading scenario…</div>
        )}

        <div className="flow" style={{ marginTop: 12 }}>
          {components.map((c, i) => (
            <Fragment key={c.id}>
              <div
                className={`flow-box ${selected === c.id ? 'active' : ''}`}
                role="button"
                tabIndex={0}
                aria-pressed={selected === c.id}
                onClick={() => setSelected(c.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setSelected(c.id)
                  }
                }}
              >
                <h4>
                  {c.order}. {c.title}
                </h4>
                <div className="sub">{c.subtitle}</div>
                <div className="count">
                  {c.id === 'intermediate_json'
                    ? (report?.summary.total_units ?? '—')
                    : c.items.length}
                </div>
                <div className="muted small">
                  {c.id === 'intermediate_json' ? 'units' : 'items'}
                </div>
              </div>
              {i < components.length - 1 && <div className="flow-arrow">→</div>}
            </Fragment>
          ))}
        </div>

        {report && (
          <div className="row small muted">
            {Object.entries(report.summary)
              .filter(([k]) => k !== 'total_units')
              .map(([k, v]) => (
                <span key={k}>
                  <span className={`status-tag ${k}`}>{k.replace('_', ' ')}</span> {v}
                </span>
              ))}
          </div>
        )}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>

      {view === 'rounds' && (
        <ConvergenceView scenarioId={scenarioId} mode={mode} sourceFormat={sourceFormat} />
      )}

      {view === 'single' && selected === 'intermediate_json' && report ? (
        <>
          {report.sections.map((sec) => (
            <div key={sec.key} className="section-card">
              <strong>{sec.heading}</strong>
              <div className="section-scroll" tabIndex={0} role="group" aria-label={`${sec.heading} items`}>
              {sec.fields.map((f) => (
                <FieldRow key={f.key} field={f} onResolve={handleResolve} />
              ))}
              {sec.graphics.map((g) => (
                <div key={g.graphic_id} className="unit-row">
                  <div className="label">Figure {g.figure_number}</div>
                  <div className="val">
                    <StatusTag status={g.status} />{' '}
                    <span className="mono">{g.name}</span> — {g.caption}
                    {g.note && <div className="small muted">{g.note}</div>}
                    <Prov provenance={g.provenance} />
                  </div>
                </div>
              ))}
              {sec.tables.map((t) => (
                <div key={t.key} className="unit-row">
                  <div className="label">{t.title || 'Table'}</div>
                  <div className="val">
                    <StatusTag status={t.status} />{' '}
                    <span className="mono">
                      [{t.columns.join(' | ')}] · {t.font}/{t.header_style}
                    </span>
                    {Object.keys(t.formatting).length > 0 && (
                      <div className="small muted">
                        normalized: {Object.keys(t.formatting).join(', ')}
                      </div>
                    )}
                    {t.rows.map((row, ri) => (
                      <div key={ri} className="small mono">
                        {row.map((cell, ci) => (
                          <Fragment key={ci}>
                            {ci > 0 && '  |  '}
                            {cell === '[needs_review]' ? (
                              <span className="cell-needs-review">needs review</span>
                            ) : (
                              cell
                            )}
                          </Fragment>
                        ))}
                      </div>
                    ))}
                    {t.note && <div className="small muted">{t.note}</div>}
                    <Prov provenance={t.provenance} />
                  </div>
                </div>
              ))}
              </div>
            </div>
          ))}

          <div className="section-card">
            <strong>Page elements</strong>
            <div className="small muted" style={{ marginBottom: 6 }}>
              The parts that repeat on every page: header, footer, page numbers,
              and the classification marking.
            </div>
            <div className="section-scroll" tabIndex={0} role="group" aria-label="Page elements">
              <FieldRow field={report.furniture.header} onResolve={handleResolve} />
              <FieldRow field={report.furniture.footer} onResolve={handleResolve} />
              <FieldRow field={report.furniture.page_numbers} onResolve={handleResolve} />
              <FieldRow field={report.furniture.classification} onResolve={handleResolve} />
              {report.furniture.cross_references.map((x) => (
                <FieldRow key={x.key} field={x} onResolve={handleResolve} />
              ))}
            </div>
          </div>

          {report.discipline_findings.length > 0 && (
            <div className="section-card">
              <div className="row">
                <strong>Formatting &amp; placement checks</strong>
                <span className="muted small">
                  {report.discipline_findings.length} findings from document inspection
                </span>
              </div>
              <div className="small muted" style={{ marginBottom: 6 }}>
                Where the document breaks the template&apos;s layout and
                formatting rules (fonts, captions, table styles, placement).
              </div>
              <div className="section-scroll" tabIndex={0} role="group" aria-label="Formatting and placement findings">
                {report.discipline_findings.map((f) => (
                  <FieldRow key={f.key} field={f} />
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        view === 'single' && (
          <div className="panel">
            <div className="muted small" style={{ marginBottom: 6 }}>
              Raw contents of the selected component:
            </div>
            <pre className="json" tabIndex={0} role="region" aria-label="Raw component JSON">{JSON.stringify(raw, null, 2)}</pre>
          </div>
        )
      )}
    </>
  )
}
