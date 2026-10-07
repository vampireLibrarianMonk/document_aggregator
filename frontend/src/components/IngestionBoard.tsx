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

// The four intake areas, in the order the workflow expects them.
const AREAS: AreaDef[] = [
  {
    kind: 'corpus',
    title: '1. Original corpus',
    blurb: 'The source documents — the ground truth everything is checked against.',
    optional: 'required',
  },
  {
    kind: 'template',
    title: '2. Template',
    blurb: 'The required structure / rubric the output must conform to.',
    optional: 'template OR first draft required',
  },
  {
    kind: 'corrections',
    title: '3. Corrections',
    blurb: 'Reviewer feedback (comments / emails) saying what is wrong.',
    optional: 'required',
  },
  {
    kind: 'first_draft',
    title: '4. First draft',
    blurb: 'The completed-but-flawed attempt to be corrected.',
    optional: 'template OR first draft required',
  },
]

const ACCEPT = '.txt,.md,.docx,.pdf,.pptx,.png,.jpg,.jpeg'

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
 * Ingestion: four separate intake areas (original corpus, template,
 * corrections, first draft). The area a file is dropped into tags its kind.
 * A readiness banner shows whether the inputs satisfy the correction pipeline's
 * prerequisites (at least one of template/first-draft; a first draft needs
 * corrections).
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
      {readiness && (
        <div
          className="panel"
          role="status"
          style={{ borderColor: readiness.ready ? 'var(--ok)' : 'var(--warn)' }}
        >
          <strong>{readiness.ready ? 'Inputs ready' : 'Inputs incomplete'}</strong>
          {readiness.ready ? (
            <p className="small muted" style={{ margin: '4px 0 0' }}>
              The Correction Pipeline tab is unlocked. You can also keep adding
              documents.
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
