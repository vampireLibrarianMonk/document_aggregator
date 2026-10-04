import { useState } from 'react'
import { api } from '../api/client'

interface Props {
  /** Called with the new project id after it is created. */
  onCreated: (projectId: string) => void | Promise<void>
}

/**
 * Create a new, empty project: just a name and a description. Documents (the
 * original corpus, a template, corrections, and a first draft) are added
 * afterward on the Ingestion tab.
 */
export function NewProjectForm({ onCreated }: Props) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function create() {
    const trimmed = name.trim()
    if (!trimmed) {
      setErr('A project name is required.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const proj = await api.createProject(trimmed, description.trim())
      await onCreated(proj.id)
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
        Name your project and describe it. Next, open the <b>Ingestion</b> tab to
        add your original corpus, a template, corrections, and a first draft.
      </p>

      <div className="field" style={{ marginBottom: 10 }}>
        <label htmlFor="np-name" className="small muted">Project name</label>
        <input
          id="np-name"
          type="text"
          value={name}
          placeholder="e.g. TGX-9 incident report correction"
          onChange={(e) => setName(e.target.value)}
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
          onChange={(e) => setDescription(e.target.value)}
          disabled={busy}
          rows={3}
          style={{ display: 'block', width: '100%', marginTop: 4 }}
        />
      </div>

      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}

      <button className="btn" onClick={() => void create()} disabled={busy || !name.trim()}>
        {busy ? 'Creating…' : 'Create project'}
      </button>
    </div>
  )
}
