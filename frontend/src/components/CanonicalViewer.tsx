import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { RawDoc } from '../api/types'

interface Props {
  projectId: string
  documentId: string | null
  onClose: () => void
}

type View = 'canonical' | 'raw'

/** Renders the Raw view by preview kind: text, image, native PDF, Office->PDF,
 *  or an unsupported note. All sources are read-only, served inline. */
function RawRender({ projectId, documentId, raw }: {
  projectId: string
  documentId: string
  raw: RawDoc
}) {
  const frameStyle = { width: '100%', height: 600, border: '1px solid var(--border)' }
  switch (raw.preview_kind) {
    case 'text':
      return (
        <pre className="json" tabIndex={0} role="region" aria-label="Raw document text">{raw.text}</pre>
      )
    case 'image':
      return (
        <img
          src={api.rawFileUrl(projectId, documentId)}
          alt={`Original image: ${raw.filename}`}
          style={{ maxWidth: '100%', height: 'auto', border: '1px solid var(--border)' }}
        />
      )
    case 'pdf':
      return (
        <iframe
          title={`PDF preview: ${raw.filename}`}
          src={api.rawFileUrl(projectId, documentId)}
          style={frameStyle}
        />
      )
    case 'office-pdf':
      return (
        <>
          <div className="muted small" style={{ marginBottom: 6 }}>
            Rendered to PDF for preview (original {raw.mime_type}).
          </div>
          <iframe
            title={`Document preview: ${raw.filename}`}
            src={api.previewPdfUrl(projectId, documentId)}
            style={frameStyle}
          />
        </>
      )
    default:
      return (
        <p className="small muted">
          {raw.mime_type} · {raw.byte_size.toLocaleString()} bytes — this file
          type cannot be previewed. Use the Canonical view to see the extracted
          structure.
        </p>
      )
  }
}

/** Inspect a selected document in two views:
 *  - Raw: the original file as submitted (text when decodable, otherwise a note).
 *  - Canonical: the normalized, structured form the pipeline builds from it. */
export function DocumentViewer({ projectId, documentId, onClose }: Props) {
  const [view, setView] = useState<View>('canonical')
  const [canonical, setCanonical] = useState<unknown>(null)
  const [raw, setRaw] = useState<RawDoc | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!documentId) {
      setCanonical(null)
      setRaw(null)
      return
    }
    let cancelled = false
    setErr(null)
    setCanonical(null)
    setRaw(null)
    Promise.all([
      api.getCanonical(projectId, documentId),
      api.getRaw(projectId, documentId),
    ])
      .then(([c, r]) => {
        if (cancelled) return
        setCanonical(c)
        setRaw(r)
      })
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
    return () => { cancelled = true }
  }, [projectId, documentId])

  if (!documentId) return null

  return (
    <div className="panel">
      <div className="row">
        <strong>Document viewer</strong>
        <span className="muted small mono">{raw?.filename ?? documentId}</span>
        <div className="mode-toggle" style={{ marginLeft: 8 }}>
          <button
            className={view === 'raw' ? 'active' : ''}
            aria-pressed={view === 'raw'}
            onClick={() => setView('raw')}
          >
            Raw
          </button>
          <button
            className={view === 'canonical' ? 'active' : ''}
            aria-pressed={view === 'canonical'}
            onClick={() => setView('canonical')}
          >
            Canonical
          </button>
        </div>
        <div className="spacer" />
        <button className="btn secondary small" onClick={onClose}>Close</button>
      </div>

      {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}

      {view === 'raw' ? (
        <>
          <div className="muted small" style={{ margin: '6px 0' }}>
            The original file exactly as submitted.
          </div>
          {raw ? <RawRender projectId={projectId} documentId={documentId} raw={raw} /> : (
            <p className="small muted">Loading…</p>
          )}
        </>
      ) : (
        <>
          <div className="muted small" style={{ margin: '6px 0' }}>
            The normalized, structured form (blocks, artifacts, timestamps,
            provenance) the pipeline builds from this document.
          </div>
          {canonical != null ? (
            <pre className="json" tabIndex={0} role="region" aria-label="Canonical document JSON">{JSON.stringify(canonical, null, 2)}</pre>
          ) : (
            <p className="small muted">Loading…</p>
          )}
        </>
      )}
    </div>
  )
}
