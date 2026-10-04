import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type {
  ApprovedModel,
  GenerateResult,
  GovernorEvent,
  GovernorSummary,
  ProjectModels,
} from '../api/types'

type BriefMode = 'structured' | 'freeform' | 'document'

/** Generate a new project with a chosen model, preview it (dry run), or persist
 *  it. Shows the per-run objective score + cost so you can compare models. */
export function GenerateProject({ onGenerated }: { onGenerated?: (projectId: string) => void }) {
  const [models, setModels] = useState<ProjectModels | null>(null)
  const [model, setModel] = useState<string>('') // '' = offline deterministic
  const [briefMode, setBriefMode] = useState<BriefMode>('structured')
  const [domain, setDomain] = useState('')
  const [docType, setDocType] = useState('incident report')
  const [title, setTitle] = useState('')
  const [freeform, setFreeform] = useState('')
  const [docFile, setDocFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<GenerateResult | null>(null)
  // Governed (decomposed) live run.
  const [events, setEvents] = useState<GovernorEvent[]>([])
  const [summary, setSummary] = useState<GovernorSummary | null>(null)
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<null | (() => void)>(null)

  useEffect(() => () => abortRef.current?.(), []) // abort any open stream on unmount

  useEffect(() => {
    api.projectModels().then((m) => {
      setModels(m)
      // Default to the EARNED recommendation when available; else the configured
      // default. The user can still override in the picker.
      if (m.recommended?.model) setModel(m.recommended.model)
      else if (m.default) setModel(m.default)
    }).catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])

  async function run(dryRun: boolean) {
    setBusy(true)
    setErr(null)
    setResult(null)
    try {
      const common = { model: model || null, dry_run: dryRun }
      let res: GenerateResult
      if (briefMode === 'document') {
        if (!docFile) throw new Error('choose a document first')
        res = await api.projectGenerateFromDocument(docFile, { domain, title, dry_run: dryRun })
      } else if (briefMode === 'freeform') {
        res = await api.projectGenerateFromText({ text: freeform, ...common })
      } else {
        res = await api.projectGenerate({ domain, doc_type: docType, title, ...common })
      }
      setResult(res)
      if (!dryRun && res.project_id && onGenerated) onGenerated(res.project_id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function runGoverned() {
    setErr(null)
    setResult(null)
    setEvents([])
    setSummary(null)
    setStreaming(true)
    const params = briefMode === 'freeform'
      ? { freeform, model: model || null }
      : { domain, doc_type: docType, title, model: model || null }
    abortRef.current = api.projectGovernedStream(params, {
      onEvent: (ev) => setEvents((prev) => [...prev, ev]),
      onResult: (s) => setSummary(s),
      onError: (detail) => setErr(detail),
      onDone: () => {
        setStreaming(false)
        abortRef.current = null
      },
    })
  }

  const canRun =
    briefMode === 'freeform' ? freeform.trim().length > 0
      : briefMode === 'document' ? docFile != null
        : domain.trim().length > 0

  return (
    <div className="panel gen-panel">
      <strong>Generate a project</strong>
      <div className="small muted" style={{ margin: '4px 0 10px' }}>
        Build a new correction project with an approved model (or the offline
        generator). The model only authors the project; the result is validated
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
          {renderModelGroups(models?.models ?? [])}
        </select>
        {models && (
          <span className="small muted bedrock-status">
            <span
              className={`status-dot ${models.available ? 'ok' : 'offline'}`}
              aria-hidden="true"
            />
            {models.available
              ? `Bedrock connected (${models.models.length} approved model${models.models.length === 1 ? '' : 's'})`
              : 'Bedrock unavailable here; only the offline generator is offered'}
          </span>
        )}
      </div>
      {models?.recommended?.model && model === models.recommended.model && (
        <div className="small muted" style={{ margin: '-2px 0 8px' }}>
          Recommended: {models.recommended.reason}. {models.recommended.basis}.
          You can override.
        </div>
      )}

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
        <button
          className={briefMode === 'document' ? 'active' : ''}
          aria-pressed={briefMode === 'document'}
          onClick={() => setBriefMode('document')}
        >
          From document
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
      ) : briefMode === 'freeform' ? (
        <>
          <label htmlFor="gen-freeform" className="sr-only">Describe the project</label>
          <textarea id="gen-freeform"
            placeholder="Describe the situation and its document (the model uses its best judgment)…"
            value={freeform} onChange={(e) => setFreeform(e.target.value)} disabled={busy} />
        </>
      ) : (
        <div className="doc-upload">
          <div className="small muted" style={{ marginBottom: 6 }}>
            Upload your source document. Its text becomes the project's
            ground-truth corpus, so the result is faithful and reproducible (no
            model invents facts). Supported: .txt, .md, .docx, .pdf, .pptx.
          </div>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <label htmlFor="gen-docfile" className="btn secondary small"
              style={{ cursor: busy ? 'default' : 'pointer' }}>
              Choose document
            </label>
            <input id="gen-docfile" type="file"
              accept=".txt,.md,.docx,.pdf,.pptx"
              disabled={busy}
              onChange={(e) => setDocFile(e.target.files?.[0] ?? null)}
              style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }} />
            <span className="small" aria-live="polite">
              {docFile ? docFile.name : 'No file chosen'}
            </span>
          </div>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            <label htmlFor="gen-doc-domain" className="sr-only">Domain (optional)</label>
            <input id="gen-doc-domain" placeholder="domain (optional)"
              value={domain} onChange={(e) => setDomain(e.target.value)}
              disabled={busy} style={{ flex: 1, minWidth: 180 }} />
            <label htmlFor="gen-doc-title" className="sr-only">Title (optional)</label>
            <input id="gen-doc-title" placeholder="title (optional)"
              value={title} onChange={(e) => setTitle(e.target.value)}
              disabled={busy} style={{ flex: 1, minWidth: 160 }} />
          </div>
        </div>
      )}

      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn secondary" disabled={busy || streaming || !canRun} onClick={() => void run(true)}>
          {busy ? 'Working…' : 'Dry run (preview)'}
        </button>
        <button className="btn" disabled={busy || streaming || !canRun} onClick={() => void run(false)}>
          {busy ? 'Working…' : 'Generate & save'}
        </button>
        {briefMode !== 'document' && (
          <button className="btn secondary" disabled={busy || streaming || !canRun}
            title="Decomposed generation with a live progress log"
            onClick={runGoverned}>
            {streaming ? 'Running…' : 'Governed (live log)'}
          </button>
        )}
      </div>

      <div role="status" aria-live="polite">
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
        {result && <GenerationReadout result={result} />}
      </div>

      {(events.length > 0 || summary) && (
        <GovernorLog events={events} summary={summary} streaming={streaming} />
      )}
    </div>
  )
}

/** A live, cumulative, append-only log of the governed run's operations. */
function GovernorLog({
  events, summary, streaming,
}: { events: GovernorEvent[]; summary: GovernorSummary | null; streaming: boolean }) {
  const endRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [events.length, summary])

  return (
    <div className="gov-log" style={{ marginTop: 12 }}>
      <div className="small muted" style={{ marginBottom: 4 }}>
        Governed run {streaming ? '(live)' : '(done)'} — operations as they transpire
      </div>
      <div className="gov-log-scroll" tabIndex={0} role="group"
        aria-label="Governor progress log"
        style={{ maxHeight: 260, overflow: 'auto' }}>
        <ol className="gov-log-list" aria-live="polite"
          style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {events.map((ev, i) => (
            <li key={i} className="gov-log-row small">
              <code className="gov-log-step">{ev.step}</code>
              <span className={`gov-badge gov-${ev.verdict || ev.status}`}>
                {ev.verdict || ev.status}
              </span>
              {ev.confidence != null && (
                <span className="muted"> {(ev.confidence * 100).toFixed(0)}%</span>
              )}
              {ev.detail && <span className="muted"> — {ev.detail}</span>}
            </li>
          ))}
        </ol>
        <div ref={endRef} />
      </div>
      {summary && (
        <div className="gen-metric-grid small" style={{ marginTop: 8 }}>
          <Metric label="Adjudicator" value={summary.adjudicator} />
          <Metric label="Author" value={summary.author_model} />
          <Metric label="Sections (filled / planned)"
            value={`${summary.sections_filled} / ${summary.sections_planned}`} />
          <Metric label="Needs review" value={String(summary.sections_needs_review)}
            warn={summary.sections_needs_review > 0} />
          <Metric label="Rejected" value={String(summary.rejects)} warn={summary.rejects > 0} />
          <Metric label="Fabrications caught" value={String(summary.fabrications_caught)}
            warn={summary.fabrications_caught > 0} />
          <Metric label="Fell back" value={summary.fell_back ? 'yes' : 'no'} warn={summary.fell_back} />
          <Metric label="Decisions (agree w/ truth)"
            value={`${summary.decisions_total}${summary.decision_agreement != null
              ? ` (${(summary.decision_agreement * 100).toFixed(0)}%)` : ''}`} />
          {summary.output_tokens > 0 && (
            <Metric label="Tokens (in / out)"
              value={`${summary.input_tokens} / ${summary.output_tokens}`} />
          )}
          {summary.est_usd > 0 && (
            <Metric label="Est. cost" value={`$${summary.est_usd.toFixed(5)}`} />
          )}
        </div>
      )}
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
          : <>Saved as project <b>{result.project_id}</b> via <b>{result.generator}</b>.</>}
      </div>
      {result.corpus_docs && result.corpus_docs.length > 0 && (
        <div className="small muted" style={{ marginTop: 4 }}>
          Ground-truth corpus from your document:{' '}
          {result.corpus_docs.map((d) => `${d.name} (${d.chars} chars)`).join(', ')}.
          This generation is deterministic: the same document reproduces the same project.
        </div>
      )}
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

/** Group the model options so a user never sees two identical-looking entries
 *  with no way to tell them apart. Foundation models and cross-region inference
 *  profiles are separated into labeled groups; a profile's option notes the
 *  "(cross-region)" nature inline for extra clarity. */
function renderModelGroups(models: ApprovedModel[]) {
  const foundation = models.filter((m) => m.kind !== 'inference_profile')
  const profiles = models.filter((m) => m.kind === 'inference_profile')

  const opt = (m: ApprovedModel, suffix = '') => (
    <option key={m.id} value={m.id}>
      {m.name} [{m.family}]{suffix}{m.recommended ? ' (recommended)' : ''}
    </option>
  )

  return (
    <>
      {foundation.length > 0 && (
        <optgroup label="Foundation models">
          {foundation.map((m) => opt(m))}
        </optgroup>
      )}
      {profiles.length > 0 && (
        <optgroup label="Cross-region inference profiles">
          {profiles.map((m) => opt(m, ' (cross-region)'))}
        </optgroup>
      )}
    </>
  )
}
