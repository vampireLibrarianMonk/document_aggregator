import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AlignmentLibrary, AlignmentOutput, BatchSummary, JobStatus, Project } from '../api/types'

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

/**
 * Step: define the golden schema. The Golden Schema tab for a JSON-alignment
 * project — paste/replace the target JSON Schema every uploaded file is
 * conformed to, and set the relevance cutoff. `onConfigured` reports whether a
 * schema is now saved so the shell can gate the Convert step.
 */
export function GoldenSchemaPanel({ project, onProjectChange, onConfigured }: Props & {
  onConfigured?: (configured: boolean) => void
}) {
  const [text, setText] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<'set' | 'unset' | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api
      .getTargetSchema(project.id)
      .then((r) => {
        setLoaded('set')
        setText(JSON.stringify(r.target, null, 2))
        onConfigured?.(true)
      })
      .catch(() => { setLoaded('unset'); onConfigured?.(false) })
  }, [project.id, onConfigured])

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
      onConfigured?.(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <div className="panel" style={{ marginBottom: 12 }}>
        <div className="row">
          <strong>Golden target schema</strong>
          <span className="muted small">
            · {loaded === 'set' ? 'configured' : 'not set'}
          </span>
        </div>
        <label htmlFor="golden-schema" className="small muted" style={{ display: 'block', marginTop: 6 }}>
          The JSON Schema every file in this project is converted to. Paste it and
          save before converting a batch.
        </label>
        <textarea
          id="golden-schema"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={14}
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

      <RelevanceDial project={project} onProjectChange={onProjectChange} />
    </>
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
          {e.source_docs && e.source_docs.length > 0 && (
            <p className="small muted" style={{ margin: '2px 0 0' }}>
              Learned from: <span className="mono">{e.source_docs.join(', ')}</span>
              {e.source_doc_count > e.source_docs.length &&
                ` +${e.source_doc_count - e.source_docs.length} more`}
            </p>
          )}
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
 * Step: convert a batch. The Batch Convert tab for a JSON-alignment project —
 * upload many source JSON files to conform to the project's golden schema.
 * Files are clustered by shape and routed per cluster (known shapes convert
 * automatically; new shapes are learned once and wait for approval; unrelated
 * files are quarantined). Requires a golden schema to be saved first.
 */
export function BatchConvertPanel({ project }: { project: Project }) {
  // Default ON: learning shapes (then approving them) is the normal path to any
  // conforms. With it off, every brand-new shape goes to review and nothing can
  // convert, which reads as "broken" on a first run.
  const [research, setResearch] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [schemaSet, setSchemaSet] = useState<boolean | null>(null)
  const [summary, setSummary] = useState<BatchSummary | null>(null)
  const [library, setLibrary] = useState<AlignmentLibrary | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const pollRef = useRef<number | null>(null)

  const refreshLibrary = useCallback(() => {
    api.getLibrary(project.id).then(setLibrary).catch(() => setLibrary(null))
  }, [project.id])

  useEffect(() => {
    // Is a golden schema saved? Gates the upload control + guides the user back.
    api.getTargetSchema(project.id).then(() => setSchemaSet(true)).catch(() => setSchemaSet(false))
    refreshLibrary()
    return () => { if (pollRef.current) window.clearTimeout(pollRef.current) }
  }, [project.id, refreshLibrary])

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
        <strong>Batch convert</strong>
        <span className="muted small">· many JSON files → golden schema</span>
      </div>

      {schemaSet === false && (
        <p className="small" role="status" style={{ color: 'var(--warn)', marginTop: 8 }}>
          Save a golden schema on the <b>Golden Schema</b> tab before converting a
          batch.
        </p>
      )}

      <div style={{ marginTop: 10 }}>
        <div className="panel" style={{ marginBottom: 12 }}>
          <div className="row">
            <strong>Upload & convert</strong>
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
              disabled={busy || schemaSet === false}
              onClick={() => fileInput.current?.click()}
            >
              Upload JSON files
            </button>
          </div>
          <p className="small muted" style={{ marginTop: 4 }}>
            Each file is one or more source records, grouped by shape. Keep
            <b> Learn new shapes</b> on so new shapes are learned for your
            approval — <b>approve a shape once</b> (below) and every future file
            of that shape converts automatically. With it off, a brand-new shape
            can&apos;t convert on its own: it waits in <i>Needs human review</i>.
          </p>
          <div role="status" aria-live="polite">
            {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
          </div>
        </div>

        {summary && <BatchStatus summary={summary} />}
        {!summary && !busy && (
          <p className="small muted" role="status" style={{ marginBottom: 12 }}>
            No batch run in this view yet. (A batch result isn&apos;t retained
            when you leave the tab — re-upload to see it again.) Shapes you have
            already learned are listed below and persist.
          </p>
        )}

        {library && library.entries.some((e) => e.state === 'provisional') ? (
          <ApprovalCards
            project={project}
            library={library}
            onApproved={refreshLibrary}
          />
        ) : (
          summary && summary.conformed_records === 0 && (
            <p className="small" role="status" style={{ color: 'var(--warn)' }}>
              Nothing conformed and no shapes are awaiting approval. If
              <b> Learn new shapes</b> was off, turn it on and re-upload: new
              shapes will then be learned here for you to approve.
            </p>
          )
        )}
      </div>
    </div>
  )
}

const PATHWAY_LABELS_SHORT: Record<string, string> = {
  replay_clean: 'converted (known shape)',
  drift_repair: 'drift re-approval',
  novel_research: 'new shape',
}

/**
 * Report & Export for a JSON-alignment project. The deliverable is the conformed
 * golden JSON, offered as a PER-FILE download: each source file that produced
 * records is downloadable as '<original><postfix>_<UTC DTG>.json'. The postfix
 * defaults to '_conformed' and is editable. Files that went to review or were
 * quarantined produced nothing and are not listed.
 */
export function JsonReportPanel({ project }: { project: Project }) {
  const [outputs, setOutputs] = useState<AlignmentOutput[] | null>(null)
  const [postfix, setPostfix] = useState('_conformed')
  const [refreshing, setRefreshing] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    setErr(null)
    try {
      const r = await api.listAlignmentOutputs(project.id)
      setOutputs(r.outputs)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRefreshing(false)
    }
  }, [project.id])

  useEffect(() => { void load() }, [load])

  const total = (outputs ?? []).reduce((n, o) => n + o.record_count, 0)

  return (
    <div className="panel">
      <div className="row">
        <strong>Conformed golden JSON (download per file)</strong>
        <div className="spacer" />
        <button className="btn secondary small" onClick={() => void load()} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <p className="small muted" style={{ marginTop: 6 }}>
        Each source file that produced golden records is downloadable here. The
        download is named <span className="mono">&lt;original&gt;{postfix}_&lt;UTC-timestamp&gt;.json</span>.
        Files sent to review or quarantined produced no records and are not listed.
      </p>

      <div className="row" style={{ marginTop: 6, gap: 6, alignItems: 'center' }}>
        <label htmlFor="postfix" className="small muted">Filename postfix</label>
        <input
          id="postfix"
          type="text"
          value={postfix}
          onChange={(e) => setPostfix(e.target.value)}
          spellCheck={false}
          style={{ fontFamily: 'monospace', width: 160 }}
          aria-label="Download filename postfix"
        />
      </div>

      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}

      {outputs !== null && outputs.length === 0 && (
        <p className="small muted" style={{ marginTop: 10 }} role="status">
          No conformed output yet. Convert a batch on the <b>Batch Convert</b> tab
          first; files that produce golden records will appear here to download.
        </p>
      )}

      {outputs && outputs.length > 0 && (
        <>
          <table className="docs" style={{ marginTop: 10 }}>
            <thead>
              <tr><th>Source file</th><th>Records</th><th>Shape</th><th>Download</th></tr>
            </thead>
            <tbody>
              {outputs.map((o) => (
                <tr key={o.stem}>
                  <td className="mono small">{o.doc_id}</td>
                  <td>{o.record_count}</td>
                  <td className="small muted">{PATHWAY_LABELS_SHORT[o.pathway] ?? o.pathway}</td>
                  <td>
                    <a
                      href={api.alignmentOutputDownloadUrl(project.id, o.stem, postfix)}
                      download
                    >
                      <button className="btn small">{o.stem}{postfix}.json</button>
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 8 }}>
            {outputs.length} file(s) · {total} golden record(s) total.
          </p>
        </>
      )}
    </div>
  )
}
