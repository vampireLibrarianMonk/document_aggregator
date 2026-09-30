// Shared types mirroring the backend contracts (backend/app/models.py).

export type StageStatus =
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'skipped'

export interface Stage {
  name: string
  status: StageStatus
  detail: string
  started_at: string | null
  completed_at: string | null
}

export type OverallStatus = 'pending' | 'processing' | 'completed' | 'failed'

export interface DocumentRecord {
  id: string
  project_id: string
  filename: string
  mime_type: string
  sha256: string
  byte_size: number
  doc_class: string
  ingested_at: string
  stages: Stage[]
  error: string | null
  block_count: number
  chunk_count: number
  artifact_count: number
  effective_dtg: string | null
  effective_dtg_source: string | null
  overall_status: OverallStatus
}

export type SupplementalKind =
  | 'comment'
  | 'email'
  | 'correction'
  | 'interview_note'

export interface Supplemental {
  id: string
  kind: SupplementalKind
  author: string
  subject: string
  body: string
  sentiment: 'neutral' | 'negative' | 'positive'
  target_document_id: string | null
  created_at: string
}

export interface Project {
  id: string
  name: string
  created_at: string
}

export interface SearchHit {
  _id: string
  _score: number
  _source: {
    document_id: string
    text: string
    page_start: number | null
    page_end: number | null
    section_path: string[]
    block_ids: string[]
    method: string
    vector_score: number
    lexical_score: number
  }
}

export interface SearchResponse {
  hits: {
    total: { value: number; relation: string }
    max_score: number
    hits: SearchHit[]
  }
}

export interface ReportSection {
  document_id: string
  title: string
  effective_dtg: string | null
  effective_dtg_source: string | null
  parser: string
  block_count: number
  artifact_count: number
  content: { type: string; text: string; table?: string[][]; page?: number; slide?: number }[]
  supplementals: Supplemental[]
  provenance: { sha256: string; parser: string; method: string }
}

export interface Report {
  schema_version: string
  report_kind: string
  generated_at: string
  project: { id: string; name: string }
  ordering: string
  summary: {
    source_documents: number
    supplementals_total: number
    negative_supplementals: number
  }
  sections: ReportSection[]
  project_supplementals: Supplemental[]
  note: string
}

export type ExportFormat = 'json' | 'markdown' | 'docx' | 'pptx' | 'pdf'

// ---- Correction scenario (four-component pipeline) ----

export type CorrectionStatus =
  | 'unchanged'
  | 'filled'
  | 'corrected'
  | 'needs_review'
  | 'conflict'

export type DefectClass = 'value' | 'graphic' | 'table' | 'furniture'

export interface CorrProvenance {
  corpus: string[]
  corrections: string[]
  rule: string | null
}

export interface CorrectedField {
  key: string
  label: string
  value: unknown
  status: CorrectionStatus
  defect_class: DefectClass
  provenance: CorrProvenance
  note: string
  candidates: { value: unknown; source: string; correction_id: string }[]
}

export interface CorrectedGraphic {
  graphic_id: string
  name: string
  caption: string
  figure_number: number | null
  section: string
  status: CorrectionStatus
  provenance: CorrProvenance
  note: string
}

export interface CorrectedTable {
  key: string
  title: string
  table_number: number | null
  columns: string[]
  rows: string[][]
  font: string
  header_style: string
  status: CorrectionStatus
  provenance: CorrProvenance
  formatting: Record<string, { was: unknown; now: unknown; rule: string }>
  note: string
}

export interface CorrectedSection {
  key: string
  heading: string
  fields: CorrectedField[]
  graphics: CorrectedGraphic[]
  tables: CorrectedTable[]
}

export interface CorrectedReport {
  schema_version: string
  kind: string
  mode: 'draft' | 'template'
  title: string
  generated_at: string
  sections: CorrectedSection[]
  furniture: {
    header: CorrectedField
    footer: CorrectedField
    classification: CorrectedField
    page_numbers: CorrectedField
    cross_references: CorrectedField[]
  }
  discipline_findings: CorrectedField[]
  summary: Record<string, number>
  note: string
}

export interface ScenarioComponent {
  id: string
  order: number
  title: string
  subtitle: string
  items: Record<string, unknown>[]
}

export interface ScenarioInfo {
  id: string
  title: string
  domain: string
}

export interface ConvergenceStep {
  round: number
  revision: number
  summary: Record<string, number>
  unresolved: number
  report: CorrectedReport
}

export interface Convergence {
  rounds: number
  converged: boolean
  final_unresolved: number | null
  trajectory: ConvergenceStep[]
}
