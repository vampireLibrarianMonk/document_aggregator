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

export type DocKind = 'corpus' | 'template' | 'corrections' | 'first_draft'

export interface Readiness {
  counts: Record<DocKind, number>
  ready: boolean
  problems: string[]
}

export type PreviewKind = 'text' | 'image' | 'pdf' | 'office-pdf' | 'unsupported'

export interface RawDoc {
  filename: string
  mime_type: string
  is_text: boolean
  byte_size: number
  text: string | null
  preview_kind: PreviewKind
}

export interface DocumentRecord {
  id: string
  project_id: string
  filename: string
  mime_type: string
  sha256: string
  byte_size: number
  doc_class: string
  kind: DocKind
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
  description?: string
  created_at: string
  /** What the project IS (chosen at creation, immutable). Drives its tab set. */
  kind: ProjectKind
  /** Batch relevance dial (0..1): a source JSON is rejected as unrelated when
   *  the share of required golden fields it can map falls below this. */
  reject_below: number
}

/** A project is one of two kinds, chosen once at creation:
 *  - correction: upload real documents, the app builds + reconciles a report.
 *  - json_alignment: conform many source JSON files to one golden schema. */
export type ProjectKind = 'correction' | 'json_alignment'

/** One source file's conformed output from the latest JSON->golden batch. */
export interface AlignmentOutput {
  /** The original uploaded file name (doc_id). */
  doc_id: string
  /** Filesystem-safe stem used in the download endpoint + default filename. */
  stem: string
  /** How many golden records this file produced. */
  record_count: number
  /** The batch pathway this file's shape took (novel_research, replay_clean, …). */
  pathway: string
}

/** The lifecycle state of a learned shape. */
export type LibraryState = 'provisional' | 'approved' | 'retired' | 'rejected'

/** The fixed set of structured reasons a reviewer may give when rejecting a
 *  learned shape. Mirrors the backend REJECT_CATEGORIES. */
export const REJECT_CATEGORIES = [
  'not_our_data',
  'wrong_mapping',
  'duplicate_shape',
  'bad_source_data',
  'other',
] as const
export type RejectCategory = (typeof REJECT_CATEGORIES)[number]

/** The structured rejection reason stored on a declined shape (server-composed). */
export interface RejectionRecord {
  category: RejectCategory
  fields?: string[]
  note?: string
  composed_text: string
  rejected_at: string
  rejected_by: string
  /** The state the shape held BEFORE the reject, so restore is a clean
   *  round-trip (approved->rejected->restore lands on approved). */
  prev_state?: 'approved' | 'provisional'
}

/** One learned shape in a project's alignment profile library. */
export interface LibraryEntry {
  profile_id: string
  state: LibraryState
  version: number
  mapped: Record<string, string>
  needs_review: string[]
  conflicts: string[]
  /** Per unmapped golden field, the ranked source-path candidates the matcher
   *  saw but did not confidently pick: {golden_field: [[source_path, score]]}.
   *  Empty array = no plausible source was seen. */
  candidates: Record<string, [string, number][]>
  /** Every source field path the shape exposes (with primary type), so a human
   *  remap can choose any real source for an unmapped golden field. */
  source_paths: { path: string; type: string }[]
  /** The source files this shape was learned from (a sample, capped). */
  source_docs: string[]
  /** Total number of source files that fed this shape (may exceed source_docs). */
  source_doc_count: number
  /** The structured rejection reason when this shape was declined (else null). */
  rejection?: RejectionRecord | null
}

export interface AlignmentLibrary {
  project_id: string
  target_title: string
  approved: string[]
  provisional: string[]
  rejected: string[]
  entries: LibraryEntry[]
}

/** One human-accepted value a reviewer taught for a golden field. */
export interface VocabEntry {
  value: string
  alias: string | null
  added_by: string
  added_at: string
  note: string
  example: string
}

/** The accepted values for one golden field: the schema's built-in taxonomy
 *  (read-only) plus the human-learned values (editable). */
export interface VocabField {
  target: string
  description: string
  builtin: string[]
  learned: VocabEntry[]
  /** The per-field free-form marker: when true the field is unconstrained and
   *  built-in/learned values are shown inactive. */
  free_form: boolean
}

/** The per-project learned value vocabulary (one entry per enumerable field). */
export interface ValueVocab {
  project_id: string
  fields: VocabField[]
}

/** The per-cluster outcome summary a batch job returns on completion. */
export interface BatchSummary {
  clusters: number
  doc_counts_by_pathway: Record<string, number>
  produced_records: number
  conformed_records: number
  needs_review_records: number
  quarantined_docs: number
  provisional_profiles_awaiting_approval: string[]
}

export interface JobStatus {
  job_id: string
  op: string
  state: 'pending' | 'processing' | 'completed' | 'failed' | 'retryable'
  attempts: number
  error: string | null
  result: unknown
  progress?: { done: number; total: number }
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

// ---- Correction project (four-component pipeline) ----

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
    // Dynamic, template-declared page elements (authoritative, in order).
    elements: CorrectedField[]
    cross_references: CorrectedField[]
    // Backward-compatible named accessors (present only for document types
    // that have them; null otherwise).
    header: CorrectedField | null
    footer: CorrectedField | null
    classification: CorrectedField | null
    page_numbers: CorrectedField | null
  }
  discipline_findings: CorrectedField[]
  summary: Record<string, number>
  note: string
}

export interface ProjectComponent {
  id: string
  order: number
  title: string
  subtitle: string
  items: Record<string, unknown>[]
}

export interface ProjectInfo {
  id: string
  title: string
  domain: string
}

// ---- Project generation (model picker + metrics) ----

export interface ApprovedModel {
  id: string
  name: string
  family: string
  kind?: 'foundation' | 'inference_profile'
  is_default: boolean
  recommended?: boolean
}

export interface ModelRecommendation {
  model: string
  reason: string
  basis: string
}

export interface ProjectModels {
  available: boolean
  bedrock_enabled: boolean
  default: string
  allowlist: string[]
  region: string
  models: ApprovedModel[]
  recommended?: ModelRecommendation
  error?: string
}

export interface GenerationMetrics {
  model: string
  valid_first_try: boolean
  repair_rounds: number
  fabrication_rejections: number
  salvage_dropped: number
  has_conflict: boolean
  has_needs_review: boolean
  graphic_defects: number
  fell_back: boolean
  input_tokens: number
  output_tokens: number
  latency_ms: number
  est_usd: number
}

export interface GenerateResult {
  generator: string
  dry_run: boolean
  project_id: string | null
  title?: string
  domain?: string
  spec?: unknown
  metrics?: GenerationMetrics
  price_note?: { pinned: string; note: string }
  corpus_docs?: { name: string; chars: number }[]
  skipped?: { name: string; reason: string }[]
}

// ---- Governed (decomposed) generation: live progress events ----

export interface GovernorEvent {
  step: string
  status: string
  detail?: string
  model?: string
  adjudicator?: string
  verdict?: string
  confidence?: number
  ts: number
}

export interface GovernorSummary {
  adjudicator: string
  author_model: string
  sections_planned: number
  sections_filled: number
  sections_needs_review: number
  retries: number
  downshifts: number
  rejects: number
  fabrications_caught: number
  fell_back: boolean
  author_calls: number
  input_tokens: number
  output_tokens: number
  latency_ms: number
  est_usd: number
  decisions_total: number
  decision_agreement: number | null
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

// ---- Diagnostics (live service status) ----

export type ServiceState = 'ok' | 'degraded' | 'error' | 'offline'

export interface EmbeddingStatus {
  state: ServiceState
  enabled: boolean
  configured_backend: string
  provider: string
  is_real_model: boolean
  model: string | null
  dimensions: number | null
  detail: string
}

export interface OcrStatus {
  state: ServiceState
  enabled: boolean
  text_engine: string | null
  pdf_rasterizer: string | null
  available: boolean
  lang: string
}

export interface GeometryStatus {
  state: ServiceState
  soffice_available: boolean
  note: string
}

export interface BedrockStatus {
  state: ServiceState
  enabled: boolean
  available: boolean
  region: string
  models: ApprovedModel[]
  default?: string
  allowlist?: string[]
}

export interface Diagnostics {
  overall: ServiceState
  services: {
    embedding: EmbeddingStatus
    ocr: OcrStatus
    geometry: GeometryStatus
    bedrock: BedrockStatus
  }
  versions: { pipeline: string; schema: string }
  offline_guard: { hf_hub_offline: boolean; transformers_offline: boolean }
  data_dir: string
}
