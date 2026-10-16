import { useCallback, useEffect, useRef, useState } from 'react'
import { api, serverDetail } from '../api/client'
import type {
  AlignmentLibrary,
  AlignmentOutput,
  BatchSummary,
  JobStatus,
  Project,
  RejectCategory,
  VocabField,
} from '../api/types'
import { REJECT_CATEGORIES } from '../api/types'

/** Friendly labels for the structured reject categories. */
const REJECT_CATEGORY_LABELS: Record<RejectCategory, string> = {
  not_our_data: 'Not our data',
  wrong_mapping: 'Wrong field mapping',
  duplicate_shape: 'Duplicate of an existing shape',
  bad_source_data: 'Bad source data',
  other: 'Other (explain)',
}

/** Client mirror of the server's note rule (byte-for-byte identical predicate):
 *  an 'other' note must have at least 10 non-whitespace characters. */
function noteIsInformative(note: string): boolean {
  return note.replace(/\s/g, '').length >= 10
}

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
/** Editable selection box for a golden field's ACCEPTED VALUES: the schema's
 *  built-in taxonomy (read-only) plus the human-learned values (add / remove).
 *  Teaching a value here lets a batch run accept it (e.g. platform 'Linux')
 *  without the matcher ever guessing; removing it re-vetoes the value. Built-in
 *  and learned are shown separately so the list stays auditable. */
export function AcceptedValuesEditor({ project }: { project: Project }) {
  const [fields, setFields] = useState<VocabField[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)        // "target" being changed
  const [draft, setDraft] = useState<Record<string, string>>({}) // per-field add input

  const load = useCallback(() => {
    api.getValueVocab(project.id)
      .then((r) => { setFields(r.fields); setErr(null) })
      .catch(() => setFields([]))  // no schema yet
  }, [project.id])

  useEffect(() => { load() }, [load])

  const change = async (
    action: 'add' | 'remove', target: string, value: string,
  ) => {
    if (action === 'add' && !value.trim()) return
    setBusy(target)
    setErr(null)
    try {
      const r = await api.changeValueVocab(project.id, { action, target, value: value.trim() })
      setFields(r.vocab)
      if (action === 'add') setDraft((d) => ({ ...d, [target]: '' }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  // Toggle the per-field free-form marker. The server is authoritative: we
  // refresh from its response so the inactive styling reflects the stored state.
  const toggleFreeForm = async (target: string, on: boolean) => {
    setBusy(target)
    setErr(null)
    try {
      const r = await api.changeValueVocab(project.id, {
        action: on ? 'set_free_form' : 'clear_free_form', target, value: '', free_form: on,
      })
      setFields(r.vocab)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  if (fields === null) return null
  if (fields.length === 0) return null  // no schema set yet

  const inactive = { opacity: 0.45, textDecoration: 'line-through' as const }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="row">
        <strong>Accepted values</strong>
        <span className="muted small">
          · per field: built-in (from the schema) + values you accept, or mark free-form
        </span>
      </div>
      <p className="small muted" style={{ margin: '4px 0 8px' }}>
        Add a value here to allow it for a field (e.g. a platform or rating the
        schema didn’t list). Removing a learned value makes future runs reject it
        again. Built-in values come from the schema and can’t be edited here. Mark
        a field <b>free-form</b> to accept any value — this overrides the lists
        below (they’re kept and reactivate when you turn free-form off).
      </p>
      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      <table className="docs">
        <thead>
          <tr>
            <th>Field</th><th>Built-in</th><th>Accepted (learned)</th>
            <th>Free-form</th><th>Add</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f.target}>
              <td className="mono small" title={f.description}>
                {f.target}
                {f.free_form && (
                  <span className="chip" style={{ marginLeft: 6 }}
                        aria-label="free-form — accepts anything">
                    free-form — accepts anything
                  </span>
                )}
              </td>
              <td className="small">
                {f.builtin.length === 0
                  ? <span className="muted">—</span>
                  : f.builtin.map((v) => (
                      <span key={v} className="chip muted" style={{ marginRight: 4, ...(f.free_form ? inactive : {}) }}
                            aria-label={f.free_form ? 'inactive while free-form is on' : undefined}>
                        {v}
                      </span>
                    ))}
              </td>
              <td className="small">
                {f.learned.length === 0
                  ? <span className="muted">none</span>
                  : f.learned.map((e) => (
                      <span key={e.value} className="chip"
                            title={`added by ${e.added_by || 'unknown'}${e.note ? ' — ' + e.note : ''}`}
                            aria-label={f.free_form ? 'inactive while free-form is on' : undefined}
                            style={{ marginRight: 4, ...(f.free_form ? inactive : {}) }}>
                        {e.value}
                        <button className="chip-x" aria-label={`Remove ${e.value}`}
                                disabled={busy === f.target || f.free_form}
                                onClick={() => void change('remove', f.target, e.value)}>
                          ×
                        </button>
                      </span>
                    ))}
              </td>
              <td>
                <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <input
                    type="checkbox"
                    aria-label={`Mark ${f.target} free-form`}
                    checked={f.free_form}
                    disabled={busy === f.target}
                    onChange={(ev) => void toggleFreeForm(f.target, ev.target.checked)}
                  />
                  free-form
                </label>
              </td>
              <td>
                <div className="row" style={{ gap: 4 }}>
                  <input
                    className="small"
                    aria-label={`Accept a value for ${f.target}`}
                    placeholder="value…"
                    value={draft[f.target] ?? ''}
                    disabled={busy === f.target || f.free_form}
                    onChange={(ev) => setDraft((d) => ({ ...d, [f.target]: ev.target.value }))}
                    onKeyDown={(ev) => {
                      if (ev.key === 'Enter') void change('add', f.target, draft[f.target] ?? '')
                    }}
                    style={{ width: 120 }}
                  />
                  <button className="btn small"
                          disabled={busy === f.target || f.free_form || !(draft[f.target] ?? '').trim()}
                          onClick={() => void change('add', f.target, draft[f.target] ?? '')}>
                    +
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


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

  // Save takes the raw text explicitly (defaulting to the textarea state) so an
  // uploaded file's bytes save directly — a setText(...) + save() sequence would
  // close over the PREVIOUS text (React state hasn't flushed), which would parse
  // '' on the first upload and the prior file on later ones.
  const save = async (raw: string = text) => {
    setErr(null)
    setStatus(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
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

  // Upload a .json schema file: read its bytes, show them in the textarea, and
  // save from the raw text through the SAME save path as paste (same JSON parse,
  // same backend validation). Resetting value lets re-selecting the same file
  // re-fire onChange.
  const onPickSchemaFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const fileText = await file.text()
    setText(fileText)          // display only
    await save(fileText)       // save from the raw bytes, not stale state
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
        <div className="row" style={{ marginTop: 6, gap: 8, alignItems: 'center' }}>
          <button className="btn small" onClick={() => void save()} disabled={!text.trim()}>
            Save golden schema
          </button>
          <label htmlFor="golden-schema-file" className="small muted">
            or upload a .json file:
          </label>
          <input
            id="golden-schema-file"
            type="file"
            accept=".json"
            aria-label="Upload schema file"
            onChange={(e) => void onPickSchemaFile(e)}
          />
          <div className="spacer" />
        </div>
        <div role="status" aria-live="polite">
          {status && <p className="small" style={{ color: 'var(--ok)' }}>{status}</p>}
          {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
        </div>
      </div>

      {loaded === 'set' && <AcceptedValuesEditor project={project} />}

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
  declined: 'Declined (you rejected this shape)',
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

/** Per-cluster approval cards for provisional (novel/drifted) shapes.
 *  Exported for unit testing of the approve flow. */
export function ApprovalCards({ project, library, onApproved }: {
  project: Project
  library: AlignmentLibrary
  onApproved: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [remapping, setRemapping] = useState<string | null>(null)
  // The FULL golden-schema field list (every top-level property), used as the
  // source for the wrong_mapping field picker so the UI never offers fewer
  // fields than the server's validation accepts. Fetched once from the vocab.
  const [goldenFields, setGoldenFields] = useState<string[] | null>(null)
  // Which profile's reject form is open, plus its in-progress reason + any
  // server error (kept open on a 422 so the reviewer can fix the reason).
  const [rejectOpen, setRejectOpen] = useState<string | null>(null)
  const [rejectCat, setRejectCat] = useState<RejectCategory>('not_our_data')
  const [rejectFields, setRejectFields] = useState<string[]>([])
  const [rejectNote, setRejectNote] = useState('')
  const [rejectErr, setRejectErr] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState(false)
  // Per-(profile,field) draft value a reviewer is accepting into the vocabulary,
  // and which field is mid-save, so the inline "accept value" control is local.
  const [acceptDraft, setAcceptDraft] = useState<Record<string, string>>({})
  // Which (profile,field) keys are mid-save. A set (not a single slot) so two
  // different fields accepting/undoing at once don't clear each other's lock.
  const [accepting, setAccepting] = useState<Record<string, boolean>>({})
  const [acceptMsg, setAcceptMsg] = useState<Record<string, string>>({})
  // The value last accepted on each (profile,field), so the success affordance
  // can offer a one-click undo that removes it from the vocabulary again.
  const [accepted, setAccepted] = useState<Record<string, string>>({})
  // Per-profile propagation report from the last override: how many of the
  // shape's records now strictly conform after the human mapping.
  const [propagation, setPropagation] = useState<Record<string, {
    available: boolean; sampled: number; total: number
    conformant: number; needs_review: number
  }>>({})
  const provisional = library.entries.filter((e) => e.state === 'provisional')

  // Load the full golden-field list once (for the wrong_mapping picker).
  useEffect(() => {
    api.getValueVocab(project.id)
      .then((r) => setGoldenFields(r.fields.map((f) => f.target)))
      .catch(() => setGoldenFields(null))
  }, [project.id])

  if (provisional.length === 0) return null

  // Approve a shape: promote its mapping to approved and refresh the library.
  const approve = async (pid: string) => {
    setBusy(pid)
    try {
      await api.approveProfile(project.id, pid)
      onApproved()
    } finally {
      setBusy(null)
    }
  }

  // Open/close the inline reject form for a shape (resets the draft reason).
  const openReject = (pid: string) => {
    setRejectOpen(pid)
    setRejectCat('not_our_data')
    setRejectFields([])
    setRejectNote('')
    setRejectErr(null)
  }

  // The golden fields a wrong_mapping reject may pick: the FULL schema field
  // list when available, else the card-derived union (mapped ∪ needs_review ∪
  // conflicts) so the picker always offers at least the fields on the card.
  const fieldsForPicker = (e: AlignmentLibrary['entries'][number]): string[] => {
    if (goldenFields && goldenFields.length > 0) return goldenFields
    return Array.from(new Set([
      ...Object.keys(e.mapped), ...e.needs_review, ...e.conflicts,
    ]))
  }

  // Client mirror of validate_rejection (UX only; the server re-validates).
  const rejectValid = (): boolean => {
    if (rejectCat === 'wrong_mapping') return rejectFields.length > 0
    if (rejectCat === 'other') return noteIsInformative(rejectNote)
    return true
  }

  // Submit the reject; only refresh after the server confirms state==='rejected'.
  // On 422 keep the form open and show the parsed server message.
  const submitReject = async (pid: string) => {
    if (!rejectValid() || rejecting) return
    setRejecting(true)
    setRejectErr(null)
    try {
      const res = await api.rejectProfile(project.id, {
        profile_id: pid,
        category: rejectCat,
        fields: rejectCat === 'wrong_mapping' ? rejectFields : undefined,
        note: rejectNote.trim() ? rejectNote : undefined,
      })
      if (res.state === 'rejected') {
        setRejectOpen(null)
        onApproved()
      }
    } catch (e) {
      setRejectErr(serverDetail(e))
    } finally {
      setRejecting(false)
    }
  }

  // Human field-by-field remap: point a golden field at a real source path, or
  // '' to leave it unmapped. Refreshes the library so the card reflects it.
  const remap = async (pid: string, target: string, sourcePath: string | null) => {
    setRemapping(`${pid}:${target}`)
    try {
      const res = await api.overrideFieldMapping(project.id, pid, target, sourcePath)
      if (res.propagation) setPropagation((prev) => ({ ...prev, [pid]: res.propagation! }))
      onApproved()  // refreshes the library view
    } finally {
      setRemapping(null)
    }
  }

  // Inline value acceptance: teach the project that `value` is valid for golden
  // field `target` (persisted to the learned value vocabulary). A later run of
  // this shape then accepts that value instead of flagging it. Scoped to the
  // field; precision-safe (only the exact value a human accepts is allowed).
  const acceptValue = async (pid: string, target: string, value: string) => {
    const v = value.trim()
    if (!v) return
    const key = `${pid}:${target}`
    if (accepting[key]) return
    setAccepting((s) => ({ ...s, [key]: true }))
    try {
      const res = await api.changeValueVocab(project.id, {
        action: 'add', target, value: v, added_by: 'reviewer',
        note: 'accepted on review', example: v,
      })
      // Confirm against the server's view: only treat it as accepted (and show
      // the undo) if the value is actually present in the field's learned list.
      const field = res.vocab.find((f) => f.target === target)
      const landed = field?.learned.some((en) => en.value === v) ?? false
      setAcceptDraft((d) => ({ ...d, [key]: '' }))
      if (landed) {
        setAccepted((a) => ({ ...a, [key]: v }))
        setAcceptMsg((m) => ({ ...m, [key]: `accepted “${v}” for ${target}` }))
      } else {
        setAcceptMsg((m) => ({ ...m, [key]: `“${v}” was not recorded for ${target}` }))
      }
    } catch (e) {
      setAcceptMsg((m) => ({ ...m, [key]: e instanceof Error ? e.message : String(e) }))
    } finally {
      setAccepting((s) => {
        const next = { ...s }
        delete next[key]
        return next
      })
    }
  }

  // Undo a just-made acceptance: remove the exact value from the vocabulary so
  // the field flags it again, reverting the human teach. Scoped to the value.
  const undoAccept = async (pid: string, target: string, value: string) => {
    const key = `${pid}:${target}`
    if (accepting[key]) return
    setAccepting((s) => ({ ...s, [key]: true }))
    try {
      const res = await api.changeValueVocab(project.id, {
        action: 'remove', target, value, added_by: 'reviewer',
      })
      // Confirm against the server's view before clearing local state, so the
      // "removed" affordance is server-granted — symmetric with acceptValue.
      const field = res.vocab.find((f) => f.target === target)
      const gone = !(field?.learned.some((en) => en.value === value) ?? false)
      if (gone) {
        setAccepted((a) => {
          const next = { ...a }
          delete next[key]
          return next
        })
        setAcceptMsg((m) => ({ ...m, [key]: `removed “${value}” from ${target}` }))
      } else {
        setAcceptMsg((m) => ({ ...m, [key]: `“${value}” could not be removed from ${target}` }))
      }
    } catch (e) {
      setAcceptMsg((m) => ({ ...m, [key]: e instanceof Error ? e.message : String(e) }))
    } finally {
      setAccepting((s) => {
        const next = { ...s }
        delete next[key]
        return next
      })
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <strong>Shapes awaiting your approval</strong>
      <p className="small muted" style={{ margin: '4px 0 8px' }}>
        This source shape is new, so the system learned it and proposed the
        field mapping below. Confirm the mapping is right by approving it once —
        this one-time check is why a brand-new shape always stops here. After you
        approve, every future file of this shape converts automatically, with no
        more prompts.
      </p>
      <p className="small muted" style={{ margin: '0 0 8px' }}>
        <b>Reject</b> is the opposite of Approve: it declines a shape with a
        reason, and every future file of that shape is quarantined (declined)
        instead of being prompted again. You can review and undo rejections in
        <i> Rejected shapes</i> below.
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
            <button
              className="btn secondary small"
              style={{ marginLeft: 6 }}
              aria-expanded={rejectOpen === e.profile_id}
              onClick={() =>
                rejectOpen === e.profile_id ? setRejectOpen(null) : openReject(e.profile_id)}
            >
              Reject
            </button>
          </div>
          {rejectOpen === e.profile_id && (
            <div className="panel" style={{ marginTop: 6 }}>
              <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                <label htmlFor={`reject-cat-${e.profile_id}`} className="small muted">
                  Reason
                </label>
                <select
                  id={`reject-cat-${e.profile_id}`}
                  className="small"
                  value={rejectCat}
                  onChange={(ev) => setRejectCat(ev.target.value as RejectCategory)}
                >
                  {REJECT_CATEGORIES.map((c) => (
                    <option key={c} value={c}>{REJECT_CATEGORY_LABELS[c]}</option>
                  ))}
                </select>
              </div>
              {rejectCat === 'wrong_mapping' && (
                <fieldset className="small" style={{ marginTop: 6, border: 'none', padding: 0 }}>
                  <legend className="muted">Which golden field(s) are mismapped?</legend>
                  <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                    {fieldsForPicker(e).map((fname) => (
                      <label key={fname} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <input
                          type="checkbox"
                          aria-label={`mismapped field ${fname}`}
                          checked={rejectFields.includes(fname)}
                          onChange={(ev) =>
                            setRejectFields((prev) =>
                              ev.target.checked
                                ? [...prev, fname]
                                : prev.filter((x) => x !== fname))}
                        />
                        <span className="mono">{fname}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
              <div style={{ marginTop: 6 }}>
                <label htmlFor={`reject-note-${e.profile_id}`} className="small muted"
                       style={{ display: 'block' }}>
                  Note{rejectCat === 'other' ? ' (required — at least 10 characters)' : ' (optional)'}
                </label>
                <textarea
                  id={`reject-note-${e.profile_id}`}
                  className="small"
                  rows={2}
                  value={rejectNote}
                  onChange={(ev) => setRejectNote(ev.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              {rejectErr && (
                <p className="small" role="alert" style={{ color: 'var(--err)' }}>{rejectErr}</p>
              )}
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                <button
                  className="btn small"
                  disabled={!rejectValid() || rejecting}
                  onClick={() => void submitReject(e.profile_id)}
                >
                  {rejecting ? 'Rejecting…' : 'Confirm reject'}
                </button>
                <button className="btn secondary small" disabled={rejecting}
                        onClick={() => setRejectOpen(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {e.source_docs && e.source_docs.length > 0 && (
            <p className="small muted" style={{ margin: '2px 0 0' }}>
              Learned from: <span className="mono">{e.source_docs.join(', ')}</span>
              {e.source_doc_count > e.source_docs.length &&
                ` +${e.source_doc_count - e.source_docs.length} more`}
            </p>
          )}
          {propagation[e.profile_id]?.available && (
            <p className="small" style={{ margin: '2px 0 0', color: 'var(--ok)' }} role="status">
              After your mapping, <b>{propagation[e.profile_id].conformant}</b> of{' '}
              {propagation[e.profile_id].sampled} record(s) in this shape now fully
              conform
              {propagation[e.profile_id].needs_review > 0 &&
                ` (${propagation[e.profile_id].needs_review} still need review)`}
              {propagation[e.profile_id].total > propagation[e.profile_id].sampled &&
                ` — sampled ${propagation[e.profile_id].sampled} of ${propagation[e.profile_id].total}`}
              .
            </p>
          )}
          <table className="docs" style={{ marginTop: 6 }}>
            <thead><tr><th>Golden field</th><th>← Source path</th></tr></thead>
            <tbody>
              {Object.entries(e.mapped).map(([tgt, src]) => (
                <tr key={tgt}><td>{tgt}</td><td className="mono small">{src}</td></tr>
              ))}
              {e.needs_review.map((t) => {
                const cands = e.candidates?.[t] ?? []
                // Scores keyed by path, so we can tag options the matcher saw.
                const scored = new Map(cands.map(([p, s]) => [p, s]))
                // Options: every real source path, scored ones first (desc).
                const paths = (e.source_paths ?? []).map((sp) => sp.path)
                const ordered = [...paths].sort(
                  (a, b) => (scored.get(b) ?? -1) - (scored.get(a) ?? -1))
                const selId = `remap-${e.profile_id}-${t}`
                const pending = remapping === `${e.profile_id}:${t}`
                return (
                  <tr key={t}>
                    <td>{t} <span className="small" style={{ color: 'var(--warn)' }}>needs review</span></td>
                    <td>
                      <label htmlFor={selId} className="small muted" style={{ marginRight: 6 }}>
                        map to:
                      </label>
                      <select
                        id={selId}
                        defaultValue=""
                        disabled={pending || paths.length === 0}
                        onChange={(ev) => void remap(e.profile_id, t, ev.target.value || null)}
                        className="small"
                      >
                        <option value="">— leave unmapped (no viable source) —</option>
                        {ordered.map((p) => {
                          const sc = scored.get(p)
                          return (
                            <option key={p} value={p}>
                              {p}{sc != null ? `  (${Math.round(sc * 100)}% match)` : ''}
                            </option>
                          )
                        })}
                      </select>
                      {pending && <span className="muted small" style={{ marginLeft: 6 }}>saving…</span>}
                      {paths.length === 0 && (
                        <span className="muted small" style={{ marginLeft: 6 }}>
                          no source fields available
                        </span>
                      )}
                      {/* Inline value acceptance: when a field is flagged
                          because a VALUE wasn't in the schema's allowed set, a
                          reviewer can accept that value for this field here, and
                          future runs of this shape will allow it. */}
                      {(() => {
                        const akey = `${e.profile_id}:${t}`
                        const asaving = accepting[akey] ?? false
                        return (
                          <div className="row small" style={{ gap: 4, marginTop: 4 }}>
                            <span className="muted">or accept a value:</span>
                            <input
                              className="small"
                              aria-label={`Accept a value for ${t}`}
                              placeholder="value…"
                              value={acceptDraft[akey] ?? ''}
                              disabled={asaving}
                              onChange={(ev) => setAcceptDraft((d) => ({ ...d, [akey]: ev.target.value }))}
                              onKeyDown={(ev) => {
                                if (ev.key === 'Enter') void acceptValue(e.profile_id, t, acceptDraft[akey] ?? '')
                              }}
                              style={{ width: 110 }}
                            />
                            <button className="btn small"
                                    disabled={asaving || !(acceptDraft[akey] ?? '').trim()}
                                    onClick={() => void acceptValue(e.profile_id, t, acceptDraft[akey] ?? '')}>
                              accept
                            </button>
                            {acceptMsg[akey] && (
                              <span className="muted" style={{ marginLeft: 4 }}>{acceptMsg[akey]}</span>
                            )}
                            {accepted[akey] && (
                              <button className="btn small"
                                      disabled={asaving}
                                      title={`Remove “${accepted[akey]}” from ${t}`}
                                      onClick={() => void undoAccept(e.profile_id, t, accepted[akey])}>
                                undo
                              </button>
                            )}
                          </div>
                        )
                      })()}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

/** A collapsed review of REJECTED shapes with per-entry restore/undo. Shown
 *  only when the project has rejected shapes. Restore is server-confirmed: the
 *  library only refreshes after the server returns a non-rejected state (the
 *  shape then reappears as approved or provisional per its prev_state).
 *  Exported for unit testing. */
export function RejectedShapes({ project, library, onRestored }: {
  project: Project
  library: AlignmentLibrary
  onRestored: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const rejected = library.entries.filter((e) => e.state === 'rejected')
  if (rejected.length === 0) return null

  const restore = async (pid: string) => {
    setBusy(pid)
    setErr(null)
    try {
      const res = await api.restoreProfile(project.id, pid)
      // Server-confirmed: refresh only after a non-rejected state comes back.
      if (res.state === 'approved' || res.state === 'provisional') onRestored()
    } catch (e) {
      setErr(serverDetail(e))
    } finally {
      setBusy(null)
    }
  }

  const fmt = (iso: string): string => {
    const d = new Date(iso)
    return isNaN(d.getTime()) ? iso : d.toLocaleString()
  }

  return (
    <details className="panel" style={{ marginBottom: 12 }}>
      <summary>
        <strong>Rejected shapes</strong>{' '}
        <span className="muted small">· {rejected.length} declined — review or undo</span>
      </summary>
      {err && <p className="small" role="alert" style={{ color: 'var(--err)' }}>{err}</p>}
      <table className="docs" style={{ marginTop: 8 }}>
        <thead>
          <tr><th>Shape</th><th>Reason</th><th>Why</th><th>When</th><th /></tr>
        </thead>
        <tbody>
          {rejected.map((e) => {
            const rj = e.rejection
            const cat = rj?.category
            return (
              <tr key={e.profile_id}>
                <td className="mono small">{e.profile_id}</td>
                <td className="small">{cat ? REJECT_CATEGORY_LABELS[cat] : '—'}</td>
                <td className="small muted">{rj?.composed_text ?? ''}</td>
                <td className="small muted">{rj?.rejected_at ? fmt(rj.rejected_at) : ''}</td>
                <td>
                  <button className="btn small" disabled={busy === e.profile_id}
                          onClick={() => void restore(e.profile_id)}>
                    {busy === e.profile_id ? 'Restoring…' : 'Restore'}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </details>
  )
}

/** Render the live batch progress text for the "Upload & convert" header slot.
 *  Falls back to the static "processing…" when progress is absent or the total
 *  is not yet known, so no "0 of 0 files" or "NaN%" can ever appear. */
export function batchProgressLabel(p: { done: number; total: number } | null): string {
  if (!p || p.total <= 0) return 'processing…'
  const pct = Math.round((p.done / p.total) * 100)
  return `${p.done} of ${p.total} files · ${pct}%`
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
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
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
          setProgress(null)
          setBusy(false)
          refreshLibrary()
        } else if (job.state === 'failed') {
          setProgress(null)
          setErr(job.error ?? 'batch failed')
          setBusy(false)
        } else {
          setProgress(job.progress ?? null)
          pollRef.current = window.setTimeout(tick, 250)
        }
      }).catch((e) => { setErr(String(e)); setBusy(false) })
    }
    tick()
  }, [refreshLibrary])

  const process = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setErr(null)
    setSummary(null)
    setProgress(null)
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
            {busy && <span className="muted small" role="status" aria-live="polite">{batchProgressLabel(progress)}</span>}
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
          summary && summary.conformed_records === 0 &&
          !(library && library.entries.some((e) => e.state === 'rejected')) && (
            <p className="small" role="status" style={{ color: 'var(--warn)' }}>
              Nothing conformed and no shapes are awaiting approval. If
              <b> Learn new shapes</b> was off, turn it on and re-upload: new
              shapes will then be learned here for you to approve.
            </p>
          )
        )}

        {library && (
          <RejectedShapes
            project={project}
            library={library}
            onRestored={refreshLibrary}
          />
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
