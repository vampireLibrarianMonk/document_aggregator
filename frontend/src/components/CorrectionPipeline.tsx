import { Fragment, useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type {
  CorrectedReport,
  ScenarioComponent,
  ScenarioInfo,
} from '../api/types'
import { ConvergenceView } from './ConvergenceView'
import { GenerateScenario } from './GenerateScenario'
import { ReportView } from './ReportView'

type Mode = 'draft' | 'template'
type View = 'single' | 'rounds'

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
  const [showGenerate, setShowGenerate] = useState(false)

  const refreshScenarios = useCallback(() => {
    return api.scenarios().then(setScenarios).catch((e) =>
      setErr(e instanceof Error ? e.message : String(e)),
    )
  }, [])

  const onGenerated = useCallback(async (newId: string) => {
    await refreshScenarios()
    setScenarioId(newId)   // jump to the freshly generated scenario
    setShowGenerate(false)
  }, [refreshScenarios])

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
      {showGenerate && <GenerateScenario onGenerated={onGenerated} />}
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
          <button
            className="btn secondary small"
            aria-expanded={showGenerate}
            onClick={() => setShowGenerate((v) => !v)}
          >
            {showGenerate ? 'Close generator' : '+ New scenario'}
          </button>
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
        <ReportView report={report} onResolve={handleResolve} />
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
