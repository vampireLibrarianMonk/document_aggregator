import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AlignmentLibrary, BatchSummary, JobStatus, Project } from '../api/types'

interface Props {
  project: Project
  onProjectChange: (p: Project) => void
}

/** Read N uploaded files as JSON into batch documents. Each file becomes one
 *  document whose `records` is the file's array (or a single object wrapped in
 *  an array). Non-JSON / unparseable files are reported, not silently dropped. */
async function filesToDocs(
  files: FileList,
): Promise<{ docs: { doc_id: string; records: unknown[] }[]; errors: string[] }> {
  const docs: { doc_id: string; records: unknown[] }[] = []
  const errors: string[] = []
  for (const file of Array.from(files)) {
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const records = Array.isArray(parsed) ? parsed : [parsed]
      docs.push({ doc_id: file.name, records })
    } catch {
      errors.push(`${file.name}: not valid JSON`)
    }
  }
  return { docs, errors }
}

/** The relevance dial: reject a source when fewer than X% of required golden
 *  fields can be mapped. Bound to the project's reject_below (persisted). */
function RelevanceDial({ project, onProjectChange }: Props) {
  const [pct, setPct] = useState(Math.round(project.reject_below * 100))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setPct(Math.round(project.reject_below * 100))
  }, [project.reject_below])

  const commit = async (value: number) => {
    setSaving(true)
    try {
      const updated = await api.updateProject(project.id, { reject_below: value / 100 })
      onProjectChange(updated)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="row">
        <strong>Relevance cutoff</strong>
        <span className="muted small">· {pct}%</span>
        {saving && <span className="muted small">saving…</span>}
      </div>
      <label htmlFor="reject-dial" className="small muted" style={{ display: 'block', marginTop: 6 }}>
        Reject a file as unrelated when fewer than this share of the required
        golden fields can be mapped.
      </label>
      <input
        id="reject-dial"
        type="range"
        min={0}
        max={100}
        step={5}
        value={pct}
        aria-valuetext={`${pct} percent`}
        onChange={(e) => setPct(Number(e.target.value))}
        onPointerUp={() => void commit(pct)}
        onKeyUp={(e) => { if (e.key.startsWith('Arrow')) void commit(pct) }}
        style={{ width: '100%' }}
      />
      <p className="small muted" style={{ margin: '4px 0 0' }}>
        A file where <b>zero</b> required fields map is always rejected, whatever
        this is set to.
      </p>
    </div>
  )
}

/** Set/replace the golden target JSON Schema (paste JSON). */
function GoldenSchema({ project }: { project: Project }) {
  const [text, setText] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api
      .getTargetSchema(project.id)
      .then((r) => { setLoaded('set'); setText(JSON.stringify(r.target, null, 2)) })
      .catch(() => setLoaded('unset'))
  }, [project.id])

  const save = async () => {
    setErr(null)
    setStatus(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      setErr('Not valid JSON')
      return
    }
    try {
      const r = await api.setTargetSchema(project.id, parsed)
      setStatus(`Saved: ${r.title} (${r.fields} fields, ${r.required.length} required)`)
      setLoaded('set')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="row">
        <strong>Golden target schema</strong>
        <span className="muted small">
          · {loaded === 'set' ? 'configured' : 'not set'}
        </span>
      </div>
      <label htmlFor="golden-schema" className="small muted" style={{ display: 'block', marginTop: 6 }}>
        The JSON Schema every document in this project is converted to. Paste it
        and save before processing a batch.
      </label>
      <textarea
        id="golden-schema"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={8}
        spellCheck={false}
        style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, marginTop: 6 }}
        placeholder='{ "title": "...", "type": "object", "properties": { ... }, "required": [ ... ] }'
      />
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn small" onClick={() => void save()} disabled={!text.trim()}>
          Save golden schema
        </button>
        <div className="spacer" />
      </div>
      <div role="status" aria-live="polite">
        {status && <p className="small" style={{ color: 'var(--ok)' }}>{status}</p>}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>
    </div>
  )
}

/** The pathway each cluster was routed to, with a human-readable label. */
const PATHWAY_LABELS: Record<string, string> = {
  replay_clean: 'Converted (known shape)',
  drift_repair: 'Drift — needs re-approval',
  novel_research: 'New shape — needs approval',
  review: 'Needs human review',
  reject_irrelevant: 'Quarantined (unrelated)',
}

function BatchStatus({ summary }: { summary: BatchSummary }) {
  const counts = summary.doc_counts_by_pathway
  return (
    <div className="panel" role="status" style={{ marginBottom: 12 }}>
      <strong>Batch result</strong>
      <ul className="small" style={{ margin: '6px 0 0' }}>
        <li>{summary.clusters} shape cluster(s)</li>
        <li>{summary.conformed_records} record(s) conformed to the golden schema</li>
        {summary.needs_review_records > 0 && (
          <li>{summary.needs_review_records} record(s) need review</li>
        )}
        {summary.quarantined_docs > 0 && (
          <li>{summary.quarantined_docs} document(s) quarantined as unrelated</li>
        )}
      </ul>
      <table className="docs" style={{ marginTop: 8 }}>
        <thead><tr><th>Pathway</th><th>Documents</th></tr></thead>
        <tbody>
          {Object.entries(counts).map(([k, n]) => (
            <tr key={k}><td>{PATHWAY_LABELS[k] ?? k}</td><td>{n}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Per-cluster approval cards for provisional (novel/drifted) shapes. */
function ApprovalCards({ project, library, onApproved }: {
  project: Project
  library: AlignmentLibrary
  onApproved: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const provisional = library.entries.filter((e) => e.state === 'provisional')
  if (provisional.length === 0) return null

  const approve = async (pid: string) => {
    setBusy(pid)
    try {
      await api.approveProfile(project.id, pid)
      onApproved()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <strong>Shapes awaiting your approval</strong>
      <p className="small muted" style={{ margin: '4px 0 8px' }}>
        A new or changed source shape was learned. Approve its mapping once and
        every future file of that shape converts automatically.
      </p>
      {provisional.map((e) => (
        <div key={e.profile_id} className="panel" style={{ marginBottom: 8 }}>
          <div className="row">
            <span className="mono small">{e.profile_id}</span>
            <span className="muted small">· v{e.version}</span>
            <div className="spacer" />
            <button
              className="btn small"
              disabled={busy === e.profile_id}
              onClick={() => void approve(e.profile_id)}
            >
              {busy === e.profile_id ? 'Approving…' : 'Approve'}
            </button>
          </div>
          <table className="docs" style={{ marginTop: 6 }}>
            <thead><tr><th>Golden field</th><th>← Source path</th></tr></thead>
            <tbody>
              {Object.entries(e.mapped).map(([tgt, src]) => (
                <tr key={tgt}><td>{tgt}</td><td className="mono small">{src}</td></tr>
              ))}
              {e.needs_review.map((t) => (
                <tr key={t}>
                  <td>{t}</td>
                  <td className="small" style={{ color: 'var(--warn)' }}>needs review</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

/**
 * Batch conversion: flick the switch, upload many JSON files, and queue them to
 * be converted to the project's golden schema. Documents are clustered by shape
 * and routed per cluster (known shapes convert automatically; new shapes are
 * learned once and wait for your approval; unrelated files are quarantined).
 */
export function BatchPanel({ project, onProjectChange }: Props) {
  const [on, setOn] = useState(false)
  const [research, setResearch] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [summary, setSummary] = useState<BatchSummary | null>(null)
  const [library, setLibrary] = useState<AlignmentLibrary | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const pollRef = useRef<number | null>(null)

  const refreshLibrary = useCallback(() => {
    api.getLibrary(project.id).then(setLibrary).catch(() => setLibrary(null))
  }, [project.id])

  useEffect(() => {
    if (on) refreshLibrary()
    return () => { if (pollRef.current) window.clearTimeout(pollRef.current) }
  }, [on, refreshLibrary])

  const poll = useCallback((jobId: string) => {
    const tick = () => {
      api.getJob(jobId).then((job: JobStatus) => {
        if (job.state === 'completed') {
          const result = job.result as { summary?: BatchSummary } | null
          if (result?.summary) setSummary(result.summary)
          setBusy(false)
          refreshLibrary()
        } else if (job.state === 'failed') {
          setErr(job.error ?? 'batch failed')
          setBusy(false)
        } else {
          pollRef.current = window.setTimeout(tick, 600)
        }
      }).catch((e) => { setErr(String(e)); setBusy(false) })
    }
    tick()
  }, [refreshLibrary])

  const process = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setErr(null)
    setSummary(null)
    setBusy(true)
    try {
      const { docs, errors } = await filesToDocs(files)
      if (errors.length) setErr(errors.join('; '))
      if (docs.length === 0) { setBusy(false); return }
      const { job_id } = await api.submitBatch(project.id, docs, research)
      poll(job_id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setBusy(false)
    } finally {
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12, borderColor: 'var(--accent, #888)' }}>
      <div className="row">
        <strong>Batch conversion</strong>
        <span className="muted small">· many JSON files → golden schema</span>
        <div className="spacer" />
        <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={on}
            onChange={(e) => setOn(e.target.checked)}
            aria-label="Enable batch conversion mode"
          />
          Batch mode
        </label>
      </div>

      {on && (
        <div style={{ marginTop: 10 }}>
          <GoldenSchema project={project} />
          <RelevanceDial project={project} onProjectChange={onProjectChange} />

          <div className="panel" style={{ marginBottom: 12 }}>
            <div className="row">
              <strong>Upload & process</strong>
              <div className="spacer" />
              {busy && <span className="muted small">processing…</span>}
              <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="checkbox"
                  checked={research}
                  onChange={(e) => setResearch(e.target.checked)}
                />
                Learn new shapes
              </label>
              <input
                ref={fileInput}
                type="file"
                multiple
                accept=".json"
                style={{ display: 'none' }}
                onChange={(e) => void process(e.target.files)}
              />
              <button
                className="btn small"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
              >
                Upload JSON files
              </button>
            </div>
            <p className="small muted" style={{ marginTop: 4 }}>
              Each file is one or more source records. Files are grouped by shape
              and queued for conversion.
            </p>
            <div role="status" aria-live="polite">
              {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
            </div>
          </div>

          {summary && <BatchStatus summary={summary} />}
          {library && (
            <ApprovalCards
              project={project}
              library={library}
              onApproved={refreshLibrary}
            />
          )}
        </div>
      )}
    </div>
  )
}
