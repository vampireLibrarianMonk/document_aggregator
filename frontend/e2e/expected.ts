// Single source of truth for the values the E2E specs assert — the SAME numbers
// the project guides quote (docs/user-guide/projects/*). Keeping them here means
// a guide and its test can never silently drift: change one, update both.
//
// All values were captured from the live reconciliation API and match the
// per-project walkthroughs.

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
  draft: CorrectionCounts
  template: CorrectionCounts
  // A few key field values to spot-check (key -> expected status + value).
  keyFields: { key: string; status: string; value?: string }[]
}

// Template-mode summary is identical across the five incident projects:
const INCIDENT_TEMPLATE: CorrectionCounts = {
  total: 16, unchanged: 0, filled: 11, corrected: 3, needs_review: 1, conflict: 1,
}

export const SAMPLE_CASES: SampleCase[] = [
  {
    id: '1',
    title: 'TGX-9 Telemetry Gateway Incident',
    draft: { total: 17, unchanged: 3, filled: 4, corrected: 8, needs_review: 1, conflict: 1 },
    template: INCIDENT_TEMPLATE,
    keyFields: [
      { key: 'identifiers.site', status: 'unchanged', value: 'North Ridge Relay Station' },
      { key: 'contributing_factors.firmware', status: 'filled', value: '4.2.1' },
      { key: 'description.duration', status: 'filled', value: 'four hour' },
      { key: 'identifiers.severity', status: 'conflict' },
    ],
  },
  {
    id: '2',
    title: 'Customer Portal Credential-Stuffing Incident',
    draft: { total: 17, unchanged: 2, filled: 4, corrected: 9, needs_review: 1, conflict: 1 },
    template: INCIDENT_TEMPLATE,
    keyFields: [
      { key: 'identifiers.site', status: 'corrected', value: 'Customer Portal (web-tier)' },
      { key: 'contributing_factors.auth_version', status: 'filled', value: '3.4.0' },
      { key: 'description.duration', status: 'filled', value: 'six hour' },
      { key: 'identifiers.severity', status: 'conflict' },
    ],
  },
  {
    id: '3',
    title: 'Clinical Lab Reagent Spill Safety Event',
    draft: { total: 17, unchanged: 2, filled: 4, corrected: 9, needs_review: 1, conflict: 1 },
    template: INCIDENT_TEMPLATE,
    keyFields: [
      { key: 'identifiers.site', status: 'corrected', value: 'Automated Chemistry Analyzer Bay 2' },
      { key: 'contributing_factors.software_version', status: 'filled', value: '7.1.2' },
      { key: 'description.duration', status: 'filled', value: 'three hour' },
      { key: 'identifiers.severity', status: 'conflict' },
    ],
  },
  {
    id: '4',
    title: 'Injection Molding Line Defect Event',
    draft: { total: 17, unchanged: 3, filled: 4, corrected: 8, needs_review: 1, conflict: 1 },
    template: INCIDENT_TEMPLATE,
    keyFields: [
      { key: 'identifiers.site', status: 'unchanged', value: 'Injection Molding Line 3' },
      { key: 'contributing_factors.firmware', status: 'filled', value: '2.0.5' },
      { key: 'description.duration', status: 'filled', value: 'five hour' },
      { key: 'identifiers.severity', status: 'conflict' },
    ],
  },
  {
    id: '5',
    title: 'Aircraft Hydraulic Decay Maintenance Event',
    draft: { total: 17, unchanged: 1, filled: 4, corrected: 10, needs_review: 1, conflict: 1 },
    template: INCIDENT_TEMPLATE,
    keyFields: [
      { key: 'contributing_factors.software_version', status: 'filled', value: 'B2.3' },
      { key: 'description.duration', status: 'filled', value: 'seven hour' },
      { key: 'identifiers.severity', status: 'conflict' },
    ],
  },
  {
    id: '6',
    title: 'Nav Bus Interface Control Document',
    draft: { total: 17, unchanged: 2, filled: 3, corrected: 9, needs_review: 2, conflict: 1 },
    template: { total: 16, unchanged: 0, filled: 11, corrected: 2, needs_review: 2, conflict: 1 },
    keyFields: [
      { key: 'scope.system', status: 'unchanged', value: 'Navigation Data Bus (Nav Bus)' },
      { key: 'scope.approval_status', status: 'conflict' },
    ],
  },
]

// Ingestion readiness (TGX-9 four pathways uploaded).
export const INGESTION_READY = {
  corpus: 2, template: 1, corrections: 1, first_draft: 1,
}

// Batch pathways the JSON->golden guide exercises.
export const BATCH_PATHWAYS = {
  novel: 'novel_research',
  replay: 'replay_clean',
  review: 'review',
  reject: 'reject_irrelevant',
  drift: 'drift_repair',
} as const
