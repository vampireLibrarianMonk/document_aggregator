import { useState } from 'react'
import { api } from '../api/client'
import type { DocumentRecord, Supplemental, SupplementalKind } from '../api/types'
import { SentimentBadge } from './StatusBadges'

interface Props {
  projectId: string
  supplementals: Supplemental[]
  documents: DocumentRecord[]
  onChange: () => void
}

const KINDS: SupplementalKind[] = ['comment', 'email', 'correction', 'interview_note']

/** Comments, angry emails, corrections and interview notes attached to the corpus. */
export function SupplementalsPanel({ projectId, supplementals, documents, onChange }: Props) {
  const [kind, setKind] = useState<SupplementalKind>('comment')
  const [author, setAuthor] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function submit() {
    if (!body.trim()) return
    setBusy(true)
    setErr(null)
    setSaved(false)
    try {
      await api.addSupplemental(projectId, {
        kind,
        author: author || 'unknown',
        subject,
        body,
        target_document_id: target || null,
      })
      setSubject('')
      setBody('')
      setAuthor('')
      setTarget('')
      setSaved(true)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="panel">
        <strong>Add supplemental</strong>
        <div className="row" style={{ marginTop: 10 }}>
          <select
            aria-label="Supplemental kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as SupplementalKind)}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <input
            placeholder="author"
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
          />
          <input
            placeholder="subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            style={{ flex: 1, minWidth: 160 }}
          />
          <select
            aria-label="Target document"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            <option value="">(project-wide)</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>{d.filename}</option>
            ))}
          </select>
        </div>
        <textarea
          style={{ marginTop: 8 }}
          placeholder="Message body (sentiment is auto-classified)…"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
        <div className="row" style={{ marginTop: 8 }}>
          <span role="status" aria-live="polite">
            {err && <span className="small" style={{ color: 'var(--err)' }}>{err}</span>}
            {saved && !err && <span className="small" style={{ color: 'var(--ok)' }}>Added ✓</span>}
          </span>
          <div className="spacer" />
          <button className="btn" disabled={busy || !body.trim()} onClick={() => void submit()}>
            {busy ? 'Saving…' : 'Add supplemental'}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="row">
          <strong>Supplementals</strong>
          <span className="muted small">{supplementals.length} total</span>
        </div>
        {supplementals.length === 0 && (
          <p className="muted small">No supplementals yet.</p>
        )}
        {supplementals.map((s) => (
          <div key={s.id} className="supp-card">
            <div className="head">
              <span className="badge">{s.kind}</span>
              <SentimentBadge sentiment={s.sentiment} />
              <strong className="small">{s.author}</strong>
              {s.subject && <span className="muted small">— {s.subject}</span>}
              <div className="spacer" />
              {s.target_document_id && (
                <span className="muted small mono">→ {s.target_document_id}</span>
              )}
            </div>
            <div className="small">{s.body}</div>
          </div>
        ))}
      </div>
    </>
  )
}
