import { useRef, useState } from 'react'
import { api } from '../api/client'
import type { DocKind, DocumentRecord, Project, Readiness } from '../api/types'
import { BatchPanel } from './BatchPanel'
import { OverallBadge, StageTrack } from './StatusBadges'

interface Props {
  projectId: string
  projects: Project[]
  documents: DocumentRecord[]
  readiness: Readiness | null
  onChange: () => void
  onInspect: (documentId: string) => void
  onProjectChange?: (p: Project) => void
}

interface AreaDef {
  kind: DocKind
  title: string
  blurb: string
  optional: string
}

// The four intake areas, in the order the workflow expects them. You upload
// your REAL documents; the app builds the project from them automatically.
const AREAS: AreaDef[] = [
  {
    kind: 'corpus',
    title: '1. Original corpus',
    blurb: 'Your source documents (notes, the report, specs) — the ground truth. '
      + 'The app builds the project from these. Figures (.png) are catalogued too.',
    optional: 'required',
  },
  {
    kind: 'corrections',
    title: '2. Corrections',
    blurb: 'Optional reviewer feedback (emails). Used to resolve figure references; '
      + 'the app derives the value corrections from the corpus itself.',
    optional: 'optional',
  },
  {
    kind: 'template',
    title: '3. Template (optional)',
    blurb: 'Optional. A separate structure/rubric document, if you have one. '
      + 'Normally the app derives the structure from your corpus.',
    optional: 'optional',
  },
  {
    kind: 'first_draft',
    title: '4. First draft (optional)',
    blurb: 'Optional. A completed-but-flawed attempt, if you have one, as extra '
      + 'source text. The app also synthesizes a draft to correct.',
    optional: 'optional',
  },
]

const ACCEPT = '.txt,.md,.json,.docx,.pdf,.pptx,.png,.jpg,.jpeg'

/** A small, indeterminate circular progress indicator (no number). */
function Spinner() {
  return (
    <span className="upload-spinner" role="status" aria-label="Uploading" title="Uploading">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle className="spinner-track" cx="12" cy="12" r="9" fill="none" strokeWidth="3" />
        <circle className="spinner-head" cx="12" cy="12" r="9" fill="none" strokeWidth="3"
                strokeLinecap="round" />
      </svg>
    </span>
  )
}

/** One upload area for a single document kind. */
function UploadArea({
  area, docs, projectId, otherProjects, onChange, onInspect,
}: {
  area: AreaDef
  docs: DocumentRecord[]
  projectId: string
  otherProjects: Project[]
  onChange: () => void
  onInspect: (id: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [moving, setMoving] = useState<string | null>(null)   // doc id with open move popup
  const [moveTarget, setMoveTarget] = useState<string>('')
  const [busyDoc, setBusyDoc] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  async function remove(docId: string, filename: string) {
    if (!window.confirm(`Delete "${filename}"? This removes it from the project.`)) return
    setBusyDoc(docId)
    setErr(null)
    try {
      await api.deleteDocument(projectId, docId)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyDoc(null)
    }
  }

  async function confirmMove(docId: string) {
    if (!moveTarget) return
    setBusyDoc(docId)
    setErr(null)
    try {
      await api.moveDocument(projectId, docId, moveTarget)
      setMoving(null)
      setMoveTarget('')
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyDoc(null)
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setUploading(true)
    setErr(null)
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(projectId, file, area.kind)
      }
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="row">
        <strong>{area.title}</strong>
        <span className="muted small">({area.optional})</span>
        <span className="muted small">· {docs.length} file{docs.length === 1 ? '' : 's'}</span>
        <div className="spacer" />
        {uploading && <Spinner />}
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ACCEPT}
          style={{ display: 'none' }}
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <button
          className="btn small"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          Upload
        </button>
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>{area.blurb}</div>

      <div role="status" aria-live="polite">
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>

      {docs.length > 0 && (
        <table className="docs" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Document</th><th>Status</th><th>Stages</th>
              <th>Blocks</th><th>Chunks</th><th>Artifacts</th><th />
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>
                  <div>{d.filename}</div>
                  <div className="muted small mono">{d.mime_type}</div>
                  {d.error && <div className="small" style={{ color: 'var(--err)' }}>{d.error}</div>}
                </td>
                <td><OverallBadge status={d.overall_status} /></td>
                <td><StageTrack stages={d.stages} /></td>
                <td>{d.block_count}</td>
                <td>{d.chunk_count}</td>
                <td>{d.artifact_count}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn secondary small" onClick={() => onInspect(d.id)}>
                    View
                  </button>
                  {' '}
                  <button
                    className="btn secondary small"
                    disabled={busyDoc === d.id || otherProjects.length === 0}
                    title={otherProjects.length === 0 ? 'No other project to move to' : 'Move to another project'}
                    onClick={() => { setMoving(moving === d.id ? null : d.id); setMoveTarget('') }}
                  >
                    Move
                  </button>
                  {' '}
                  <button
                    className="btn secondary small"
                    disabled={busyDoc === d.id}
                    onClick={() => void remove(d.id, d.filename)}
                  >
                    Delete
                  </button>
                  {moving === d.id && (
                    <div className="row" style={{ marginTop: 6, gap: 6, alignItems: 'center' }}>
                      <label htmlFor={`move-${d.id}`} className="sr-only">Move to project</label>
                      <select
                        id={`move-${d.id}`}
                        value={moveTarget}
                        onChange={(e) => setMoveTarget(e.target.value)}
                      >
                        <option value="">Choose a project…</option>
                        {otherProjects.map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                      <button
                        className="btn small"
                        disabled={!moveTarget || busyDoc === d.id}
                        onClick={() => void confirmMove(d.id)}
                      >
                        {busyDoc === d.id ? 'Moving…' : 'Move'}
                      </button>
                      <button
                        className="btn secondary small"
                        onClick={() => { setMoving(null); setMoveTarget('') }}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/**
 * Ingestion: upload your real source documents and the app GENERATES the
 * project from them (manifest, structure, a flawed draft, and grounded
 * corrections) — no hand-authored JSON. The corpus area is all most projects
 * need; the other areas are optional extra source material. A readiness banner
 * shows when the Correction Pipeline has been built and is ready to run.
 */
export function IngestionBoard({
  projectId, projects, documents, readiness, onChange, onInspect, onProjectChange,
}: Props) {
  const byKind = (k: DocKind) => documents.filter((d) => d.kind === k)
  const otherProjects = projects.filter((p) => p.id !== projectId)
  const activeProject = projects.find((p) => p.id === projectId)
  // BatchPanel needs a project-change callback; fall back to a no-op refresh.
  const handleProjectChange = onProjectChange ?? (() => onChange())

  return (
    <div>
      <div className="panel" style={{ marginBottom: 12 }}>
        <strong>Upload your documents — the app builds the project</strong>
        <p className="small muted" style={{ margin: '4px 0 0' }}>
          Drop your real source documents into <b>Original corpus</b> (the notes,
          the report, specs, and any figure images). The app derives everything
          the Correction Pipeline needs from them automatically — the structure,
          the fields, a flawed draft, and grounded corrections. No JSON to
          assemble. The other areas are optional extra source material.
        </p>
      </div>
      {readiness && (
        <div
          className="panel"
          role="status"
          style={{ borderColor: readiness.ready ? 'var(--ok)' : 'var(--warn)' }}
        >
          <strong>{readiness.ready ? 'Project ready' : 'Add your documents'}</strong>
          {readiness.ready ? (
            <p className="small muted" style={{ margin: '4px 0 0' }}>
              The app built the project from your uploads. The Correction Pipeline
              tab is unlocked. Add more documents any time to rebuild it.
            </p>
          ) : (
            <ul className="small" style={{ margin: '4px 0 0' }}>
              {readiness.problems.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          )}
        </div>
      )}

      {activeProject && (
        <BatchPanel project={activeProject} onProjectChange={handleProjectChange} />
      )}

      {AREAS.map((area) => (
        <UploadArea
          key={area.kind}
          area={area}
          docs={byKind(area.kind)}
          projectId={projectId}
          otherProjects={otherProjects}
          onChange={onChange}
          onInspect={onInspect}
        />
      ))}
    </div>
  )
}
