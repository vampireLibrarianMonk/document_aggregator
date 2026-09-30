import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ExportFormat, Report } from '../api/types'
import { SentimentBadge } from './StatusBadges'

const FORMATS: ExportFormat[] = ['json', 'markdown', 'docx', 'pptx', 'pdf']

/**
 * The final intermediate format: the aggregated report assembled from source
 * content, ordered by effective DTG, with export buttons to DOCX / PPTX / PDF.
 */
export function ReportPanel({ projectId }: { projectId: string }) {
  const [report, setReport] = useState<Report | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showJson, setShowJson] = useState(false)

  const load = useCallback(() => {
    setErr(null)
    api
      .getReport(projectId)
      .then(setReport)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [projectId])

  useEffect(() => load(), [load])

  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>Aggregated report (intermediate format)</strong>
          <div className="spacer" />
          <button className="btn secondary" onClick={load}>Rebuild</button>
          {FORMATS.map((f) => (
            <a key={f} href={api.exportUrl(projectId, f)} download>
              <button className="btn">{f.toUpperCase()}</button>
            </a>
          ))}
        </div>
        {report && (
          <div className="row small muted" style={{ marginTop: 8 }}>
            <span>Ordering: {report.ordering}</span>
            <span>·</span>
            <span>{report.summary.source_documents} sources</span>
            <span>·</span>
            <span>{report.summary.supplementals_total} supplementals</span>
            <span>·</span>
            <span style={{ color: 'var(--err)' }}>
              {report.summary.negative_supplementals} negative
            </span>
          </div>
        )}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>

      {report?.sections.map((sec) => (
        <div key={sec.document_id} className="section-card">
          <div className="row">
            <strong>{sec.title}</strong>
            <span className="badge">{sec.parser}</span>
            <div className="spacer" />
            <span className="muted small">
              {sec.effective_dtg} ({sec.effective_dtg_source})
            </span>
          </div>
          <div className="muted small mono" style={{ margin: '4px 0' }}>
            sha256 {sec.provenance.sha256.slice(0, 16)}… · {sec.block_count} blocks ·{' '}
            {sec.artifact_count} artifacts
          </div>
          <div className="small">
            {sec.content.slice(0, 4).map((c, i) =>
              c.table ? (
                <table key={i} className="docs" style={{ margin: '6px 0' }}>
                  <tbody>
                    {c.table.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p key={i} style={{ margin: '4px 0' }}>{c.text}</p>
              ),
            )}
            {sec.content.length > 4 && (
              <span className="muted">+ {sec.content.length - 4} more blocks…</span>
            )}
          </div>
          {sec.supplementals.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {sec.supplementals.map((s) => (
                <div key={s.id} className="small">
                  <SentimentBadge sentiment={s.sentiment} /> [{s.kind}] {s.author}: {s.body}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      <div className="panel">
        <div className="row">
          <strong>Raw report JSON</strong>
          <button className="btn secondary small" onClick={() => setShowJson((v) => !v)}>
            {showJson ? 'Hide' : 'Show'}
          </button>
        </div>
        {showJson && report && (
          <pre className="json" tabIndex={0} role="region" aria-label="Raw report JSON">{JSON.stringify(report, null, 2)}</pre>
        )}
      </div>
    </>
  )
}
