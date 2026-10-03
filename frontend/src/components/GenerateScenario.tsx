import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { GenerateResult, ScenarioModels } from '../api/types'

type BriefMode = 'structured' | 'freeform'

/** Generate a new scenario with a chosen model, preview it (dry run), or persist
 *  it. Shows the per-run objective score + cost so you can compare models. */
export function GenerateScenario({ onGenerated }: { onGenerated?: (scenarioId: string) => void }) {
  const [models, setModels] = useState<ScenarioModels | null>(null)
  const [model, setModel] = useState<string>('') // '' = offline deterministic
  const [briefMode, setBriefMode] = useState<BriefMode>('structured')
  const [domain, setDomain] = useState('')
  const [docType, setDocType] = useState('incident report')
  const [title, setTitle] = useState('')
  const [freeform, setFreeform] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<GenerateResult | null>(null)

  useEffect(() => {
    api.scenarioModels().then((m) => {
      setModels(m)
      if (m.default) setModel(m.default)
    }).catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])

  async function run(dryRun: boolean) {
    setBusy(true)
    setErr(null)
    setResult(null)
    try {
      const common = { model: model || null, dry_run: dryRun }
      const res = briefMode === 'freeform'
        ? await api.scenarioGenerateFromText({ text: freeform, ...common })
        : await api.scenarioGenerate({ domain, doc_type: docType, title, ...common })
      setResult(res)
      if (!dryRun && res.scenario_id && onGenerated) onGenerated(res.scenario_id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const canRun =
    briefMode === 'freeform' ? freeform.trim().length > 0 : domain.trim().length > 0

  return (
    <div className="panel gen-panel">
      <strong>Generate a scenario</strong>
      <div className="small muted" style={{ margin: '4px 0 10px' }}>
        Build a new correction scenario with an approved model (or the offline
        generator). The model only authors the scenario; the result is validated
        and saved as fixed data, so it stays reproducible.
      </div>

      <div className="row" style={{ marginBottom: 8 }}>
        <label htmlFor="gen-model" className="small muted">Model</label>
        <select
          id="gen-model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={busy}
        >
          <option value="">Offline (deterministic, no model)</option>
          {models?.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} [{m.family}]{m.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>
        {models && !models.available && (
          <span className="small muted">
            (Bedrock unavailable here — only the offline generator is offered)
          </span>
        )}
      </div>

      <div className="mode-toggle" role="group" aria-label="Brief type" style={{ marginBottom: 8 }}>
        <button
          className={briefMode === 'structured' ? 'active' : ''}
          aria-pressed={briefMode === 'structured'}
          onClick={() => setBriefMode('structured')}
        >
          Structured brief
        </button>
        <button
          className={briefMode === 'freeform' ? 'active' : ''}
          aria-pressed={briefMode === 'freeform'}
          onClick={() => setBriefMode('freeform')}
        >
          Freeform
        </button>
      </div>

      {briefMode === 'structured' ? (
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <label htmlFor="gen-domain" className="sr-only">Domain</label>
          <input id="gen-domain" placeholder="domain (e.g. avionics interface validation)"
            value={domain} onChange={(e) => setDomain(e.target.value)}
            disabled={busy} style={{ flex: 2, minWidth: 220 }} />
          <label htmlFor="gen-doctype" className="sr-only">Document type</label>
          <input id="gen-doctype" placeholder="document type"
            value={docType} onChange={(e) => setDocType(e.target.value)}
            disabled={busy} style={{ flex: 1, minWidth: 160 }} />
          <label htmlFor="gen-title" className="sr-only">Title (optional)</label>
          <input id="gen-title" placeholder="title (optional)"
            value={title} onChange={(e) => setTitle(e.target.value)}
            disabled={busy} style={{ flex: 1, minWidth: 160 }} />
        </div>
      ) : (
        <>
          <label htmlFor="gen-freeform" className="sr-only">Describe the scenario</label>
          <textarea id="gen-freeform"
            placeholder="Describe the situation and its document (the model uses its best judgment)…"
            value={freeform} onChange={(e) => setFreeform(e.target.value)} disabled={busy} />
        </>
      )}

      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn secondary" disabled={busy || !canRun} onClick={() => void run(true)}>
          {busy ? 'Working…' : 'Dry run (preview)'}
        </button>
        <button className="btn" disabled={busy || !canRun} onClick={() => void run(false)}>
          {busy ? 'Working…' : 'Generate & save'}
        </button>
      </div>

      <div role="status" aria-live="polite">
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
        {result && <GenerationReadout result={result} />}
      </div>
    </div>
  )
}

/** The per-run score + cost readout. */
function GenerationReadout({ result }: { result: GenerateResult }) {
  const m = result.metrics
  return (
    <div className="gen-readout" style={{ marginTop: 10 }}>
      <div className="small">
        {result.dry_run
          ? <>Preview via <b>{result.generator}</b>.</>
          : <>Saved as scenario <b>{result.scenario_id}</b> via <b>{result.generator}</b>.</>}
      </div>
      {m && (
        <>
          <div className="gen-metric-grid small" style={{ marginTop: 6 }}>
            <Metric label="Model" value={m.model} />
            <Metric label="Outcome" value={
              m.fell_back ? 'fell back to offline'
                : m.valid_first_try ? 'valid first try'
                : m.repair_rounds > 0 ? 'valid after repair' : 'salvaged'
            } warn={m.fell_back} />
            <Metric label="Fabrications caught" value={String(m.fabrication_rejections)}
              warn={m.fabrication_rejections > 0} />
            <Metric label="Dropped (salvage)" value={String(m.salvage_dropped)} />
            <Metric label="Conflict + needs-review" value={
              `${m.has_conflict ? 'conflict' : 'none'} / ${m.has_needs_review ? 'needs-review' : 'none'}`
            } />
            <Metric label="Tokens (in / out)" value={`${m.input_tokens} / ${m.output_tokens}`} />
            <Metric label="Latency" value={`${(m.latency_ms / 1000).toFixed(1)}s`} />
            <Metric label="Est. cost" value={`$${m.est_usd.toFixed(5)}`} />
          </div>
          {result.price_note && (
            <div className="small muted" style={{ marginTop: 4 }}>
              Cost is an estimate (pinned price table: {result.price_note.pinned}).
              Tokens and latency are measured.
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="gen-metric">
      <span className="muted">{label}</span>
      <span className={warn ? 'gen-metric-warn' : ''}>{value}</span>
    </div>
  )
}
