import { useState } from 'react'
import { api } from '../api/client'
import type { ProjectKind } from '../api/types'

interface Props {
  /** Draft form state, owned by the shell so it survives tab navigation
   *  (this panel unmounts when you leave the New Project tab). */
  kind: ProjectKind | null
  name: string
  description: string
  onKindChange: (k: ProjectKind) => void
  onNameChange: (v: string) => void
  onDescriptionChange: (v: string) => void
  /** Called with the new project id and its chosen kind after it is created. */
  onCreated: (projectId: string, kind: ProjectKind) => void | Promise<void>
}

/**
 * Create a new, empty project. The user first chooses what the project IS — a
 * document correction, or a JSON-to-golden-schema conversion — then names it.
 * There is NO default kind: the user drives this choice. The chosen kind is
 * immutable and determines which workflow (and tabs) the project exposes.
 *
 * The draft fields (kind/name/description) are CONTROLLED by the shell so the
 * selection is remembered when the user navigates away and back (this component
 * unmounts on tab switch, which would otherwise reset local state).
 */
export function NewProjectForm({
  kind, name, description, onKindChange, onNameChange, onDescriptionChange, onCreated,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function create() {
    const trimmed = name.trim()
    if (!kind) {
      setErr('Choose what kind of project this is.')
      return
    }
    if (!trimmed) {
      setErr('A project name is required.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const proj = await api.createProject(trimmed, description.trim(), kind)
      await onCreated(proj.id, kind)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <strong>Create a project</strong>
      <p className="small muted" style={{ margin: '4px 0 12px' }}>
        First, choose what this project is for. The choice is permanent and sets
        the workflow you&apos;ll use.
      </p>

      <fieldset className="field" style={{ marginBottom: 12, border: 0, padding: 0 }}>
        <legend className="small muted" style={{ marginBottom: 6 }}>Project kind</legend>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label className={`kind-option${kind === 'correction' ? ' selected' : ''}`}
                 style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
            <input
              type="radio"
              name="project-kind"
              value="correction"
              checked={kind === 'correction'}
              onChange={() => onKindChange('correction')}
              disabled={busy}
              style={{ marginTop: 3 }}
            />
            <span>
              <b>Correct documents</b>
              <span className="small muted" style={{ display: 'block' }}>
                Upload real source documents; the app builds the project and
                reconciles a corrected, source-grounded report.
              </span>
            </span>
          </label>
          <label className={`kind-option${kind === 'json_alignment' ? ' selected' : ''}`}
                 style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
            <input
              type="radio"
              name="project-kind"
              value="json_alignment"
              checked={kind === 'json_alignment'}
              onChange={() => onKindChange('json_alignment')}
              disabled={busy}
              style={{ marginTop: 3 }}
            />
            <span>
              <b>Convert JSON to a golden schema</b>
              <span className="small muted" style={{ display: 'block' }}>
                Define one golden JSON Schema and conform many messy source JSON
                files to it, grouped by shape.
              </span>
            </span>
          </label>
        </div>
      </fieldset>

      <div className="field" style={{ marginBottom: 10 }}>
        <label htmlFor="np-name" className="small muted">Project name</label>
        <input
          id="np-name"
          type="text"
          value={name}
          placeholder="e.g. TGX-9 incident report correction"
          onChange={(e) => onNameChange(e.target.value)}
          disabled={busy}
          style={{ display: 'block', width: '100%', marginTop: 4 }}
        />
      </div>

      <div className="field" style={{ marginBottom: 12 }}>
        <label htmlFor="np-desc" className="small muted">Description</label>
        <textarea
          id="np-desc"
          value={description}
          placeholder="What is this project for? (optional)"
          onChange={(e) => onDescriptionChange(e.target.value)}
          disabled={busy}
          rows={3}
          style={{ display: 'block', width: '100%', marginTop: 4 }}
        />
      </div>

      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}

      <button className="btn" onClick={() => void create()} disabled={busy || !kind || !name.trim()}>
        {busy ? 'Creating…' : 'Create project'}
      </button>
    </div>
  )
}
