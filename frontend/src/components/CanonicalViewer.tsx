import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Props {
  projectId: string
  documentId: string | null
}

/** Shows the canonical JSON for a selected document (the intermediate format). */
export function CanonicalViewer({ projectId, documentId }: Props) {
  const [data, setData] = useState<unknown>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!documentId) {
      setData(null)
      return
    }
    let cancelled = false
    setErr(null)
    api
      .getCanonical(projectId, documentId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      cancelled = true
    }
  }, [projectId, documentId])

  if (!documentId) {
    return (
      <div className="panel">
        <p className="muted small">
          Select a document&apos;s “Canonical” button on the pipeline board to
          inspect its canonical JSON here.
        </p>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="row">
        <strong>Canonical document</strong>
        <span className="muted small mono">{documentId}</span>
      </div>
      <div className="muted small" style={{ marginBottom: 6 }}>
        The normalized, structured form of this document (blocks, artifacts,
        timestamps, provenance) — the internal representation everything else is
        built from. Shown as raw JSON for inspection.
      </div>
      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      {data != null && <pre className="json" tabIndex={0} role="region" aria-label="Canonical document JSON">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  )
}
