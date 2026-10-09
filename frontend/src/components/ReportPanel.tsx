import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CorrectedReport, ExportFormat, ProjectComponent, Report } from '../api/types'
import { ReportView } from './ReportView'
import { SentimentBadge } from './StatusBadges'

const FORMATS: ExportFormat[] = ['json', 'markdown', 'docx', 'pptx', 'pdf']
type Mode = 'draft' | 'template'

/**
 * Report & Export. For a CORRECTION project (one with a first-attempt draft or
 * template) this shows and exports the CORRECTED report — the finished
 * deliverable with every fix applied and your manual resolutions reflected.
 * For an aggregation-only project it falls back to the aggregated corpus report.
 */
export function ReportPanel({ projectId }: { projectId: string }) {
  const [components, setComponents] = useState<ProjectComponent[] | null>(null)
  const [mode, setMode] = useState<Mode>('draft')
  const [corrected, setCorrected] = useState<CorrectedReport | null>(null)
  const [aggregated, setAggregated] = useState<Report | null>(null)
  const [formats, setFormats] = useState<ExportFormat[]>(FORMATS)
  const [err, setErr] = useState<string | null>(null)
  const [showJson, setShowJson] = useState(false)

  const hasPipeline =
    !!components &&
    (components.find((c) => c.id === 'first_attempt')?.items.length ?? 0) > 0

  const load = useCallback(() => {
    setErr(null)
    if (hasPipeline) {
      api
        .projectReconcile(mode, projectId, 'json')
        .then(setCorrected)
        .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    } else {
      api
        .getReport(projectId)
        .then(setAggregated)
        .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    }
  }, [projectId, hasPipeline, mode])

  // Discover whether this project has a correction pipeline, then load.
  useEffect(() => {
    setComponents(null)
    setCorrected(null)
    setAggregated(null)
    api
      .projectComponents(projectId)
      .then(setComponents)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    api
      .exportFormats(projectId)
      .then((r) => setFormats(r.formats))
      .catch(() => setFormats(FORMATS))
  }, [projectId])

  useEffect(() => {
    if (components !== null) load()
  }, [components, load])

  // ---- Aggregation-only project: the raw aggregated report ----
  if (components !== null && !hasPipeline) {
    return <AggregatedReport report={aggregated} err={err} projectId={projectId} onReload={load} />
  }

  // ---- Correction project: the corrected deliverable ----
  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>Corrected report (final deliverable)</strong>
          <div className="mode-toggle" style={{ marginLeft: 8 }}
               title="Draft: the corrected version of the flawed draft. Template: the blank template filled from the sources.">
            <button className={mode === 'draft' ? 'active' : ''} onClick={() => setMode('draft')}>
              Draft
            </button>
            <button className={mode === 'template' ? 'active' : ''} onClick={() => setMode('template')}>
              Template
            </button>
          </div>
          <div className="spacer" />
          <button className="btn secondary" onClick={load}>Rebuild</button>
          {formats.map((f) => (
            <a key={f} href={api.exportUrl(projectId, f, { kind: 'corrected', mode })} download>
              <button className="btn">{f === 'markdown' ? 'MARKDOWN (ZIP)' : f.toUpperCase()}</button>
            </a>
          ))}
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          This is the Correction Pipeline&apos;s output: every value grounded in your
          sources, conflicts shown unresolved for you to decide, and your
          resolutions reflected. Rebuild re-runs the correction. Export downloads
          this corrected report.
        </div>
        {corrected && (
          <div className="row small muted" style={{ marginTop: 8 }}>
            {Object.entries(corrected.summary)
              .filter(([k]) => k !== 'total_units')
              .map(([k, v]) => (
                <span key={k}>
                  <span className={`status-tag ${k}`}>{k.replace('_', ' ')}</span> {v}
                </span>
              ))}
          </div>
        )}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
      </div>

      {corrected && (
        <ReportView
          report={corrected}
          onResolve={noResolve}
          onUnresolve={async (target) => {
            try {
              const updated = await api.projectUnresolve(target, mode, projectId, 'json')
              setCorrected(updated)
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e))
            }
          }}
          projectId={projectId}
        />
      )}

      <div className="panel">
        <div className="row">
          <strong>Raw corrected JSON</strong>
          <button className="btn secondary small" onClick={() => setShowJson((v) => !v)}>
            {showJson ? 'Hide' : 'Show'}
          </button>
        </div>
        {showJson && corrected && (
          <pre className="json" tabIndex={0} role="region" aria-label="Raw corrected report JSON">
            {JSON.stringify(corrected, null, 2)}
          </pre>
        )}
      </div>
    </>
  )
}

/** Resolve is done on the Correction Pipeline tab; here the report is read-only. */
const noResolve = async () => {}

/** The legacy aggregated-corpus report, shown only for aggregation-only projects. */
function AggregatedReport(
  { report, err, projectId, onReload }:
  { report: Report | null; err: string | null; projectId: string; onReload: () => void },
) {
  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>Aggregated report (intermediate format)</strong>
          <div className="spacer" />
          <button className="btn secondary" onClick={onReload}>Rebuild</button>
          {FORMATS.map((f) => (
            <a key={f} href={api.exportUrl(projectId, f, { kind: 'aggregated' })} download>
              <button className="btn">{f === 'markdown' ? 'MARKDOWN (ZIP)' : f.toUpperCase()}</button>
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
    </>
  )
}
