import { Fragment, useMemo, useState } from 'react'
import type { CorrectedField, CorrectedReport } from '../api/types'
import {
  FieldRow,
  Prov,
  ResolveControls,
  StatusTag,
  displayValue,
  isOpen,
  type ResolveFn,
} from './reportShared'

type RenderMode = 'document' | 'audit'

/** The corrected report, rendered either as a navigable DOCUMENT (projected into
 *  the shape the scenario's template defines) or as the AUDIT list (every unit
 *  with status + provenance). A navigator rail jumps between sections and
 *  surfaces the units that still need the user. Driven entirely by the
 *  CorrectedReport, so it adapts to any document type (incident, ICD, ...). */
export function ReportView({ report, onResolve }: { report: CorrectedReport; onResolve: ResolveFn }) {
  const [renderMode, setRenderMode] = useState<RenderMode>('document')

  // Navigator model: one entry per section + page-elements + checks, each with
  // a count of still-open (conflict/needs_review) units.
  const nav = useMemo(() => buildNav(report), [report])
  const openTotal = nav.reduce((n, s) => n + s.open, 0)

  return (
    <div className="report-view">
      {/* Toolbar: mode toggle + open-items summary */}
      <div className="row report-toolbar">
        <div className="mode-toggle" role="group" aria-label="Report display mode">
          <button
            className={renderMode === 'document' ? 'active' : ''}
            aria-pressed={renderMode === 'document'}
            onClick={() => setRenderMode('document')}
          >
            Document
          </button>
          <button
            className={renderMode === 'audit' ? 'active' : ''}
            aria-pressed={renderMode === 'audit'}
            onClick={() => setRenderMode('audit')}
          >
            Audit
          </button>
        </div>
        <div className="spacer" />
        <span className="small muted" role="status">
          {openTotal === 0
            ? 'All units resolved'
            : `${openTotal} unit${openTotal === 1 ? '' : 's'} need your input`}
        </span>
      </div>

      <div className="report-layout">
        {/* Navigator rail */}
        <nav className="report-nav" aria-label="Report sections">
          <ul>
            {nav.map((s) => (
              <li key={s.id}>
                <a href={`#rv-${s.id}`} className="report-nav-link">
                  <span className="report-nav-label">{s.label}</span>
                  {s.open > 0 && (
                    <span className="report-nav-badge" title={`${s.open} need input`}>
                      {s.open}
                    </span>
                  )}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        {/* Body */}
        <div className="report-body">
          {renderMode === 'document'
            ? <DocumentRender report={report} onResolve={onResolve} />
            : <AuditRender report={report} onResolve={onResolve} />}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// Navigator model
// --------------------------------------------------------------------------

interface NavEntry { id: string; label: string; open: number }

function buildNav(report: CorrectedReport): NavEntry[] {
  const entries: NavEntry[] = []
  for (const sec of report.sections) {
    const open =
      sec.fields.filter(isOpen).length +
      sec.graphics.filter((g) => isOpen(g as unknown as CorrectedField)).length +
      sec.tables.filter((t) => isOpen(t as unknown as CorrectedField)).length
    entries.push({ id: sec.key, label: sec.heading, open })
  }
  const pe = report.furniture.elements
  entries.push({
    id: 'page-elements',
    label: 'Page elements',
    open: pe.filter(isOpen).length + report.furniture.cross_references.filter(isOpen).length,
  })
  if (report.discipline_findings.length > 0) {
    entries.push({
      id: 'checks',
      label: 'Formatting & placement',
      open: report.discipline_findings.filter(isOpen).length,
    })
  }
  return entries
}

// --------------------------------------------------------------------------
// DOCUMENT render — project the corrected report into the document's shape
// --------------------------------------------------------------------------

/** A subtle status chip + inline resolve, shown only when a unit needs action,
 *  so the document reads like a document and annotations stay out of the way. */
function Annotation({ field, onResolve }: { field: CorrectedField; onResolve: ResolveFn }) {
  if (field.status === 'unchanged') return null
  const actionable = isOpen(field)
  return (
    <span className={`doc-annotation ${field.status}`}>
      <StatusTag status={field.status} />
      {actionable && <ResolveControls field={field} onResolve={onResolve} />}
    </span>
  )
}

function DocumentRender({ report, onResolve }: { report: CorrectedReport; onResolve: ResolveFn }) {
  const pe = report.furniture.elements
  const header = pe.find((e) => e.key === 'furniture.header')

  return (
    <article className="doc">
      {/* Running header (if the doc type has one) */}
      {header && header.value ? (
        <div className="doc-runhead">{String(header.value)}</div>
      ) : null}
      <h1 className="doc-title">{report.title}</h1>

      {report.sections.map((sec) => (
        <section key={sec.key} id={`rv-${sec.key}`} className="doc-section">
          <h2>{sec.heading}</h2>

          {sec.fields.map((f) => (
            <DocField key={f.key} field={f} onResolve={onResolve} />
          ))}

          {sec.tables.map((t) => (
            <figure key={t.key} className="doc-table-wrap">
              <figcaption className="doc-table-title">
                {t.table_number ? `Table ${t.table_number}: ` : ''}
                {stripTablePrefix(t.title) || 'Table'}
              </figcaption>
              <table className="doc-table">
                <thead>
                  <tr>{t.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {t.rows.length === 0 ? (
                    <tr><td colSpan={t.columns.length} className="muted small">
                      (filled from the corpus during correction)
                    </td></tr>
                  ) : t.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td key={ci}>
                          {cell === '[needs_review]'
                            ? <span className="cell-needs-review">needs review</span>
                            : cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {t.status !== 'unchanged' && <Annotation field={t as unknown as CorrectedField} onResolve={onResolve} />}
              <Prov provenance={t.provenance} />
            </figure>
          ))}

          {sec.graphics.map((g) => (
            <figure key={g.graphic_id} className="doc-figure">
              <div className="doc-figure-placeholder" aria-hidden="true">▦</div>
              <figcaption>
                {g.figure_number ? `Figure ${g.figure_number}: ` : ''}{g.caption || g.name}
              </figcaption>
              {g.status !== 'unchanged' && (
                <div className="doc-annotation-row">
                  <StatusTag status={g.status} />
                  {g.note && <span className="small muted"> {g.note}</span>}
                </div>
              )}
              <Prov provenance={g.provenance} />
            </figure>
          ))}
        </section>
      ))}

      {/* Page elements — rendered from the DYNAMIC template-declared list */}
      <section id="rv-page-elements" className="doc-section doc-furniture">
        <h2>Page elements</h2>
        <div className="small muted" style={{ marginBottom: 6 }}>
          The parts that repeat on every page for this document type.
        </div>
        {pe.map((f) => <DocField key={f.key} field={f} onResolve={onResolve} />)}
        {report.furniture.cross_references.map((x) => (
          <DocField key={x.key} field={x} onResolve={onResolve} />
        ))}
      </section>

      {report.discipline_findings.length > 0 && (
        <section id="rv-checks" className="doc-section">
          <h2>Formatting &amp; placement checks</h2>
          {report.discipline_findings.map((f) => (
            <DocField key={f.key} field={f} onResolve={onResolve} />
          ))}
        </section>
      )}
    </article>
  )
}

/** One field rendered in document voice: "Label: value" with an annotation only
 *  when it is not an untouched value. */
function DocField({ field, onResolve }: { field: CorrectedField; onResolve: ResolveFn }) {
  return (
    <p className="doc-field">
      <span className="doc-field-label">{field.label}:</span>{' '}
      {field.status === 'conflict'
        ? <span className="muted">unresolved</span>
        : <span className="doc-field-value">{displayValue(field.value)}</span>}
      {' '}
      <Annotation field={field} onResolve={onResolve} />
      {field.note && field.status !== 'unchanged' && (
        <span className="small muted doc-field-note">{field.note}</span>
      )}
    </p>
  )
}

function stripTablePrefix(title: string): string {
  // "Table 1: Corrective Actions" -> "Corrective Actions" (number added above).
  return title.replace(/^table\s*\d*\s*:?\s*/i, '').trim() || title
}

// --------------------------------------------------------------------------
// AUDIT render — the provenance-rich flat view (every unit + status + source)
// --------------------------------------------------------------------------

function AuditRender({ report, onResolve }: { report: CorrectedReport; onResolve: ResolveFn }) {
  return (
    <>
      {report.sections.map((sec) => (
        <div key={sec.key} id={`rv-${sec.key}`} className="section-card">
          <strong>{sec.heading}</strong>
          <div className="section-scroll" tabIndex={0} role="group" aria-label={`${sec.heading} items`}>
            {sec.fields.map((f) => <FieldRow key={f.key} field={f} onResolve={onResolve} />)}
            {sec.graphics.map((g) => (
              <div key={g.graphic_id} className="unit-row">
                <div className="label">Figure {g.figure_number}</div>
                <div className="val">
                  <StatusTag status={g.status} />{' '}
                  <span className="mono">{g.name}</span> — {g.caption}
                  {g.note && <div className="small muted">{g.note}</div>}
                  <Prov provenance={g.provenance} />
                </div>
              </div>
            ))}
            {sec.tables.map((t) => (
              <div key={t.key} className="unit-row">
                <div className="label">{t.title || 'Table'}</div>
                <div className="val">
                  <StatusTag status={t.status} />{' '}
                  <span className="mono">[{t.columns.join(' | ')}] · {t.font}/{t.header_style}</span>
                  {Object.keys(t.formatting).length > 0 && (
                    <div className="small muted">normalized: {Object.keys(t.formatting).join(', ')}</div>
                  )}
                  {t.rows.map((row, ri) => (
                    <div key={ri} className="small mono">
                      {row.map((cell, ci) => (
                        <Fragment key={ci}>
                          {ci > 0 && '  |  '}
                          {cell === '[needs_review]'
                            ? <span className="cell-needs-review">needs review</span>
                            : cell}
                        </Fragment>
                      ))}
                    </div>
                  ))}
                  {t.note && <div className="small muted">{t.note}</div>}
                  <Prov provenance={t.provenance} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      <div id="rv-page-elements" className="section-card">
        <strong>Page elements</strong>
        <div className="small muted" style={{ marginBottom: 6 }}>
          The parts that repeat on every page for this document type.
        </div>
        <div className="section-scroll" tabIndex={0} role="group" aria-label="Page elements">
          {report.furniture.elements.map((f) => (
            <FieldRow key={f.key} field={f} onResolve={onResolve} />
          ))}
          {report.furniture.cross_references.map((x) => (
            <FieldRow key={x.key} field={x} onResolve={onResolve} />
          ))}
        </div>
      </div>

      {report.discipline_findings.length > 0 && (
        <div id="rv-checks" className="section-card">
          <div className="row">
            <strong>Formatting &amp; placement checks</strong>
            <span className="muted small">
              {report.discipline_findings.length} findings from document inspection
            </span>
          </div>
          <div className="section-scroll" tabIndex={0} role="group" aria-label="Formatting and placement findings">
            {report.discipline_findings.map((f) => <FieldRow key={f.key} field={f} />)}
          </div>
        </div>
      )}
    </>
  )
}
