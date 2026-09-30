import { useRef, useState } from 'react'
import { api } from '../api/client'
import type { DocumentRecord } from '../api/types'
import { OverallBadge, StageTrack } from './StatusBadges'

interface Props {
  projectId: string
  documents: DocumentRecord[]
  onChange: () => void
  onInspect: (documentId: string) => void
}

/**
 * The core view: a board tracking every submitted document through the
 * pipeline stages, plus its derived stats (blocks / chunks / artifacts) and
 * resolved effective date-time group.
 */
export function PipelineBoard({ projectId, documents, onChange, onInspect }: Props) {
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setUploading(true)
    setErr(null)
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(projectId, file)
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
    <div className="panel">
      <div className="row">
        <strong>Documents in pipeline</strong>
        <span className="muted small">{documents.length} total</span>
        <div className="spacer" />
        <input
          ref={fileInput}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <button
          className="btn"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          {uploading ? 'Uploading…' : 'Upload documents'}
        </button>
      </div>

      <div role="status" aria-live="polite">
        {uploading && <span className="sr-only">Uploading documents…</span>}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>

      <table className="docs" style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Document</th>
            <th>Status</th>
            <th>Pipeline stages</th>
            <th>Blocks</th>
            <th>Chunks</th>
            <th>Artifacts</th>
            <th>Effective DTG</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {documents.length === 0 && (
            <tr>
              <td colSpan={8} className="muted small">
                No documents yet. Upload files or run the seed script.
              </td>
            </tr>
          )}
          {documents.map((d) => (
            <tr key={d.id}>
              <td>
                <div>{d.filename}</div>
                <div className="muted small mono">{d.mime_type}</div>
                {d.error && (
                  <div className="small" style={{ color: 'var(--err)' }}>{d.error}</div>
                )}
              </td>
              <td><OverallBadge status={d.overall_status} /></td>
              <td><StageTrack stages={d.stages} /></td>
              <td>{d.block_count}</td>
              <td>{d.chunk_count}</td>
              <td>{d.artifact_count}</td>
              <td>
                <div className="small">{d.effective_dtg ?? '—'}</div>
                <div className="muted small">{d.effective_dtg_source ?? ''}</div>
              </td>
              <td>
                <button className="btn secondary small" onClick={() => onInspect(d.id)}>
                  Canonical
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
