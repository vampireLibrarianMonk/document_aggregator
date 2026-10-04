import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ProjectInfo } from '../api/types'
import { useProject } from '../hooks/useProject'

interface Props {
  /** Called with the new project id after a template is instantiated. */
  onInstantiated: (projectId: string) => void | Promise<void>
}

/**
 * Lists the bundled sample cases and lets the user instantiate one into a new,
 * persisted project. The app starts empty; this is the quickest way to get a
 * fully worked project to explore without uploading anything. Each sample is
 * read-only in the repo and is COPIED on instantiation, so the user's project
 * is independent and the sample can be reused.
 */
export function TemplatePicker({ onInstantiated }: Props) {
  const { instantiateTemplate } = useProject()
  const [templates, setTemplates] = useState<ProjectInfo[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api
      .listTemplates()
      .then(setTemplates)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])

  async function instantiate(caseId: string) {
    setBusyId(caseId)
    setErr(null)
    try {
      // The context method creates the project, refreshes the global list, and
      // selects it; we just need the id to let the parent close/switch tabs.
      const id = await instantiateTemplate(caseId)
      if (id) await onInstantiated(id)
      else setErr('Could not create a project from that sample.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="panel">
      <strong>Start from a sample case</strong>
      <p className="small muted" style={{ marginTop: 4 }}>
        Each sample is a complete worked correction project (source corpus, a
        first-attempt report, and reviewer comments). Instantiating one creates
        your own copy as a new project you can explore and change. Nothing is
        preloaded, so this is the fastest way to see the Correction Pipeline in
        action.
      </p>
      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      <ul className="template-list" style={{ listStyle: 'none', padding: 0, marginTop: 12 }}>
        {templates.map((t) => (
          <li
            key={t.id}
            className="row"
            style={{ alignItems: 'center', gap: 12, marginBottom: 8 }}
          >
            <div style={{ flex: 1 }}>
              <div><b>{t.title}</b></div>
              <div className="muted small">{t.domain}</div>
            </div>
            <button
              className="btn"
              disabled={busyId !== null}
              aria-busy={busyId === t.id}
              onClick={() => void instantiate(t.id)}
            >
              {busyId === t.id ? 'Creating…' : 'Use this sample'}
            </button>
          </li>
        ))}
        {templates.length === 0 && !err && (
          <li className="muted small">Loading samples…</li>
        )}
      </ul>
    </div>
  )
}
