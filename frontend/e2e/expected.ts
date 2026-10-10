// Single source of truth for the values the E2E specs assert.
//
// These are the numbers the REAL user flow produces: create a project, upload
// the standard set of source documents (corpus .txt/.md + figures/*.png), and
// let the app GENERATE the project (manifest/template/draft/corrections) and
// reconcile it. There is no longer an in-app Samples feature or hand-authored
// JSON upload — those were removed when the upload-real-documents flow landed.
//
// All values below were captured from the live served flow (upload the bundled
// sample_docs corpus for each project, then GET /reconcile) so a spec and the
// running app can never silently drift. The reconcile summary's `total_units`
// counts every reconciled unit (section bodies included), which is what the
// Correction Pipeline UI shows and what these specs assert.

export interface CorrectionCounts {
  total: number
  unchanged: number
  filled: number
  corrected: number
  needs_review: number
  conflict: number
}

export interface SampleCase {
  id: string
  title: string
  // The corpus documents uploaded to build this project (relative to
  // sample_docs/project/<id>/corpus/). Figures are discovered under figures/.
  corpus: string[]
  draft: CorrectionCounts
  template: CorrectionCounts
  // A few key field values to spot-check are visible on the generated report.
  keyFields: { value: string }[]
}

// Draft/template summaries are identical across projects 1, 3, 4, 5 (the
// generator derives the same unit shape from their shared corpus structure).
const COMMON_DRAFT: CorrectionCounts = {
  total: 16, unchanged: 1, filled: 8, corrected: 5, needs_review: 2, conflict: 0,
}
const COMMON_TEMPLATE: CorrectionCounts = {
  total: 16, unchanged: 0, filled: 12, corrected: 2, needs_review: 2, conflict: 0,
}

export const SAMPLE_CASES: SampleCase[] = [
  {
    id: '1',
    title: 'TGX-9 Root Cause Analysis Notes',
    corpus: ['field_report_2026-03-02.txt', 'root_cause_notes_2026-03-15.md'],
    draft: COMMON_DRAFT,
    template: COMMON_TEMPLATE,
    keyFields: [{ value: 'North Ridge Relay Station' }, { value: 'J. Okafor' }],
  },
  {
    id: '2',
    title: 'Customer Portal Breach Attempt — Incident Review',
    corpus: ['incident_review_2026-05-20.md', 'soc_alert_2026-05-11.txt'],
    draft: { total: 15, unchanged: 1, filled: 7, corrected: 5, needs_review: 2, conflict: 0 },
    template: { total: 15, unchanged: 0, filled: 11, corrected: 2, needs_review: 2, conflict: 0 },
    keyFields: [{ value: 'Customer Portal (web-tier)' }],
  },
  {
    id: '3',
    title: 'CLINICAL LABORATORY SAFETY EVENT REPORT',
    corpus: ['lab_event_2026-07-03.txt', 'safety_review_2026-07-14.md'],
    draft: COMMON_DRAFT,
    template: COMMON_TEMPLATE,
    keyFields: [{ value: 'Automated Chemistry Analyzer Bay 2' }],
  },
  {
    id: '4',
    title: 'MANUFACTURING QUALITY DEFECT REPORT',
    corpus: ['qc_report_2026-08-09.txt', 'rca_review_2026-08-19.md'],
    draft: COMMON_DRAFT,
    template: COMMON_TEMPLATE,
    keyFields: [{ value: 'Injection Molding Line 3' }],
  },
  {
    id: '5',
    title: 'AIRCRAFT MAINTENANCE DISCREPANCY REPORT',
    corpus: ['maintenance_log_2026-09-06.txt', 'reliability_review_2026-09-16.md'],
    draft: COMMON_DRAFT,
    template: COMMON_TEMPLATE,
    keyFields: [{ value: 'Regional Jet Tail 512 Hydraulic System B' }],
  },
  {
    id: '6',
    title: 'Nav Bus ICD Review Board Notes',
    corpus: ['interface_spec_2026-04-10.txt', 'review_board_notes_2026-04-22.md'],
    draft: { total: 12, unchanged: 1, filled: 4, corrected: 5, needs_review: 2, conflict: 0 },
    template: { total: 12, unchanged: 0, filled: 8, corrected: 2, needs_review: 2, conflict: 0 },
    keyFields: [{ value: 'Navigation Data Bus (Nav Bus)' }],
  },
]

// Ingestion readiness after uploading the standard document set for project 1
// (2 text corpus docs + 3 figures, all tagged corpus; the app generates the
// manifest/template/draft/corrections from them).
export const INGESTION_READY = {
  corpus: 5, template: 0, corrections: 0, first_draft: 0,
}

// Batch pathways the JSON->golden guide exercises.
export const BATCH_PATHWAYS = {
  novel: 'novel_research',
  replay: 'replay_clean',
  review: 'review',
  reject: 'reject_irrelevant',
  drift: 'drift_repair',
} as const
