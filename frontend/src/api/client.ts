// Thin API client. All calls go through the Vite proxy (/api -> backend).

import type {
  AlignmentLibrary,
  AlignmentOutput,
  Convergence,
  CorrectedReport,
  Diagnostics,
  DocKind,
  DocumentRecord,
  ExportFormat,
  Readiness,
  GenerateResult,
  GovernorEvent,
  GovernorSummary,
  JobStatus,
  JobCounts,
  Project,
  ProjectKind,
  RawDoc,
  RejectCategory,
  RejectionRecord,
  Report,
  ProjectComponent,
  ProjectModels,
  SearchResponse,
  Supplemental,
  SupplementalKind,
  ValueVocab,
} from './types'

const BASE = '/api'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

/** Extract a clean server message from an Error thrown by `json<T>`. The helper
 *  throws `Error("<status>: <rawBody>")`; for a FastAPI HTTPException the raw
 *  body is `{"detail":"msg"}`, so strip the status prefix and parse out detail
 *  (falling back to the stripped body when it isn't JSON). */
export function serverDetail(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  const body = msg.replace(/^\d+:\s*/, '')
  try {
    const parsed = JSON.parse(body)
    return typeof parsed?.detail === 'string' ? parsed.detail : body
  } catch {
    return body
  }
}

export const api = {
  getClientConfig(): Promise<{ diagnostics_enabled: boolean }> {
    return fetch(`${BASE}/config`).then((r) =>
      json<{ diagnostics_enabled: boolean }>(r),
    )
  },

  listProjects(): Promise<Project[]> {
    return fetch(`${BASE}/projects`).then((r) => json<Project[]>(r))
  },

  createProject(name: string, description = '', kind: ProjectKind = 'correction'): Promise<Project> {
    return fetch(`${BASE}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description, kind }),
    }).then((r) => json<Project>(r))
  },

  deleteProject(projectId: string): Promise<{ deleted: string }> {
    return fetch(`${BASE}/projects/${projectId}`, { method: 'DELETE' }).then((r) =>
      json<{ deleted: string }>(r),
    )
  },

  updateProject(
    projectId: string,
    patch: { name?: string; description?: string; reject_below?: number },
  ): Promise<Project> {
    return fetch(`${BASE}/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).then((r) => json<Project>(r))
  },

  listDocuments(projectId: string): Promise<DocumentRecord[]> {
    return fetch(`${BASE}/projects/${projectId}/documents`).then((r) =>
      json<DocumentRecord[]>(r),
    )
  },

  uploadDocument(
    projectId: string,
    file: File,
    kind: DocKind = 'corpus',
  ): Promise<{ document_id: string; kind: DocKind }> {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    return fetch(`${BASE}/projects/${projectId}/documents`, {
      method: 'POST',
      body: form,
    }).then((r) => json<{ document_id: string; kind: DocKind }>(r))
  },

  getReadiness(projectId: string): Promise<Readiness> {
    return fetch(`${BASE}/projects/${projectId}/readiness`).then((r) =>
      json<Readiness>(r),
    )
  },

  deleteDocument(projectId: string, documentId: string): Promise<{ deleted: string }> {
    return fetch(`${BASE}/projects/${projectId}/documents/${documentId}`, {
      method: 'DELETE',
    }).then((r) => json<{ deleted: string }>(r))
  },

  moveDocument(
    projectId: string,
    documentId: string,
    targetProjectId: string,
  ): Promise<{ moved: string; to: string; new_document_id: string }> {
    return fetch(`${BASE}/projects/${projectId}/documents/${documentId}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_project_id: targetProjectId }),
    }).then((r) => json<{ moved: string; to: string; new_document_id: string }>(r))
  },

  getCanonical(projectId: string, documentId: string): Promise<unknown> {
    return fetch(
      `${BASE}/projects/${projectId}/documents/${documentId}/canonical`,
    ).then((r) => json<unknown>(r))
  },

  getRaw(projectId: string, documentId: string): Promise<RawDoc> {
    return fetch(
      `${BASE}/projects/${projectId}/documents/${documentId}/raw`,
    ).then((r) => json<RawDoc>(r))
  },

  // Inline URL for a project's corpus figure (served by the backend), used as
  // an <img> src so the corrected report shows the real figure.
  figureUrl(projectId: string, name: string): string {
    return `${BASE}/projects/${projectId}/figures/${encodeURIComponent(name)}`
  },

  // Inline-byte URLs used as <img>/<iframe> src for in-browser rendering.
  rawFileUrl(projectId: string, documentId: string): string {
    return `${BASE}/projects/${projectId}/documents/${documentId}/rawfile`
  },

  previewPdfUrl(projectId: string, documentId: string): string {
    return `${BASE}/projects/${projectId}/documents/${documentId}/preview.pdf`
  },

  listSupplementals(projectId: string): Promise<Supplemental[]> {
    return fetch(`${BASE}/projects/${projectId}/supplementals`).then((r) =>
      json<Supplemental[]>(r),
    )
  },

  addSupplemental(
    projectId: string,
    payload: {
      kind: SupplementalKind
      author: string
      subject: string
      body: string
      target_document_id?: string | null
    },
  ): Promise<Supplemental> {
    return fetch(`${BASE}/projects/${projectId}/supplementals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((r) => json<Supplemental>(r))
  },

  search(projectId: string, query: string): Promise<SearchResponse> {
    return fetch(`${BASE}/projects/${projectId}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, top_k: 10 }),
    }).then((r) => json<SearchResponse>(r))
  },

  getReport(projectId: string): Promise<Report> {
    return fetch(`${BASE}/projects/${projectId}/report`).then((r) =>
      json<Report>(r),
    )
  },

  exportUrl(
    projectId: string,
    format: ExportFormat,
    opts: { kind?: 'corrected' | 'aggregated'; mode?: 'draft' | 'template' } = {},
  ): string {
    const qs = new URLSearchParams({ format })
    if (opts.kind) qs.set('kind', opts.kind)
    if (opts.mode) qs.set('mode', opts.mode)
    return `${BASE}/projects/${projectId}/export?${qs.toString()}`
  },

  /** URL for the ZIP bundle containing every offered format in one download. */
  exportAllUrl(
    projectId: string,
    opts: { kind?: 'corrected' | 'aggregated'; mode?: 'draft' | 'template' } = {},
  ): string {
    const qs = new URLSearchParams()
    if (opts.kind) qs.set('kind', opts.kind)
    if (opts.mode) qs.set('mode', opts.mode)
    const q = qs.toString()
    return `${BASE}/projects/${projectId}/export-all${q ? `?${q}` : ''}`
  },

  exportFormats(projectId: string): Promise<{ formats: ExportFormat[] }> {
    return fetch(`${BASE}/projects/${projectId}/export-formats`).then((r) =>
      json<{ formats: ExportFormat[] }>(r),
    )
  },

  // ---- Correction project (four-component pipeline) ----

  // Project operations are now project-scoped: the projectId IS the project
  // id, passed in the path (/projects/{id}/project/...). A project is a
  // project in the unified model.
  projectComponents(projectId: string): Promise<ProjectComponent[]> {
    return fetch(`${BASE}/projects/${projectId}/components`).then((r) =>
      json<ProjectComponent[]>(r),
    )
  },

  projectComponent(
    componentId: string,
    mode: string,
    projectId: string,
    sourceFormat: string,
  ): Promise<{ data: unknown }> {
    return fetch(
      `${BASE}/projects/${projectId}/component/${componentId}?mode=${mode}&source_format=${sourceFormat}`,
    ).then((r) => json<{ data: unknown }>(r))
  },

  projectReconcile(
    mode: 'draft' | 'template',
    projectId: string,
    sourceFormat: string,
  ): Promise<CorrectedReport> {
    return fetch(
      `${BASE}/projects/${projectId}/reconcile?mode=${mode}&source_format=${sourceFormat}`,
    ).then((r) => json<CorrectedReport>(r))
  },

  projectConverge(
    mode: 'draft' | 'template',
    projectId: string,
    sourceFormat: string,
  ): Promise<Convergence> {
    return fetch(
      `${BASE}/projects/${projectId}/converge?mode=${mode}&source_format=${sourceFormat}`,
    ).then((r) => json<Convergence>(r))
  },

  // Apply a human decision to one unresolved unit (a conflict candidate choice
  // or a needs_review value) and get the re-reconciled report back.
  projectResolve(
    target: string,
    value: string,
    mode: 'draft' | 'template',
    projectId: string,
    sourceFormat: string,
  ): Promise<CorrectedReport> {
    return fetch(`${BASE}/projects/${projectId}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target,
        value,
        mode,
        source_format: sourceFormat,
      }),
    }).then((r) => json<CorrectedReport>(r))
  },

  // Undo a human resolution for a unit so it reverts to the engine result and
  // can be set again. Returns the re-reconciled report.
  projectUnresolve(
    target: string,
    mode: 'draft' | 'template',
    projectId: string,
    sourceFormat: string,
  ): Promise<CorrectedReport> {
    return fetch(`${BASE}/projects/${projectId}/unresolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, mode, source_format: sourceFormat }),
    }).then((r) => json<CorrectedReport>(r))
  },

  // ---- Diagnostics (live service status) ----

  getDiagnostics(): Promise<Diagnostics> {
    return fetch(`${BASE}/diagnostics`).then((r) => json<Diagnostics>(r))
  },

  // ---- Project generation (model picker + per-run metrics) ----

  projectModels(): Promise<ProjectModels> {
    return fetch(`${BASE}/generate/models`).then((r) => json<ProjectModels>(r))
  },

  projectGenerate(body: {
    domain?: string
    doc_type?: string
    title?: string
    model?: string | null
    dry_run?: boolean
  }): Promise<GenerateResult> {
    return fetch(`${BASE}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<GenerateResult>(r))
  },

  projectGenerateFromText(body: {
    text: string
    model?: string | null
    dry_run?: boolean
  }): Promise<GenerateResult> {
    return fetch(`${BASE}/generate/from-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<GenerateResult>(r))
  },

  // Generate a project FROM an uploaded document: the document's own text
  // becomes the ground-truth corpus (deterministic, no model invents facts).
  projectGenerateFromDocument(
    file: File,
    opts: { domain?: string; title?: string; dry_run?: boolean } = {},
  ): Promise<GenerateResult> {
    const qs = new URLSearchParams()
    if (opts.domain) qs.set('domain', opts.domain)
    if (opts.title) qs.set('title', opts.title)
    qs.set('dry_run', String(opts.dry_run ?? false))
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/generate/from-document?${qs.toString()}`, {
      method: 'POST',
      body: form,
    }).then((r) => json<GenerateResult>(r))
  },

  // Generate a project FROM a SET of uploaded real documents (the standard
  // flow): every text document becomes ground-truth corpus; figure images are
  // accepted and catalogued from the documents' own Figures sections. The app
  // derives the whole project deterministically (no model invents facts).
  projectGenerateFromDocuments(
    files: File[],
    opts: { domain?: string; title?: string; dry_run?: boolean } = {},
  ): Promise<GenerateResult> {
    const qs = new URLSearchParams()
    if (opts.domain) qs.set('domain', opts.domain)
    if (opts.title) qs.set('title', opts.title)
    qs.set('dry_run', String(opts.dry_run ?? false))
    const form = new FormData()
    for (const f of files) form.append('files', f)
    return fetch(`${BASE}/generate/from-documents?${qs.toString()}`, {
      method: 'POST',
      body: form,
    }).then((r) => json<GenerateResult>(r))
  },

  // ---- Governed (decomposed) generation with a live progress stream ----
  // Consumes Server-Sent Events: calls onEvent for each step, onResult for the
  // final summary. Returns an abort function so the caller can cancel.
  projectGovernedStream(
    params: {
      domain?: string
      doc_type?: string
      title?: string
      freeform?: string
      model?: string | null
      adjudicator?: string
    },
    handlers: {
      onEvent: (ev: GovernorEvent) => void
      onResult: (summary: GovernorSummary) => void
      onError?: (detail: string) => void
      onDone?: () => void
    },
  ): () => void {
    const qs = new URLSearchParams()
    if (params.freeform?.trim()) qs.set('freeform', params.freeform)
    else {
      if (params.domain) qs.set('domain', params.domain)
      if (params.doc_type) qs.set('doc_type', params.doc_type)
      if (params.title) qs.set('title', params.title)
    }
    if (params.model) qs.set('model', params.model)
    qs.set('adjudicator', params.adjudicator || 'deterministic')

    const ctrl = new AbortController()
    void (async () => {
      try {
        const res = await fetch(`${BASE}/generate/governed/stream?${qs.toString()}`, {
          signal: ctrl.signal,
          headers: { Accept: 'text/event-stream' },
        })
        if (!res.ok || !res.body) throw new Error(`${res.status}: ${res.statusText}`)
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          // SSE frames are separated by a blank line.
          const frames = buf.split('\n\n')
          buf = frames.pop() ?? ''
          for (const frame of frames) {
            let evName = 'message'
            let data = ''
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) evName = line.slice(6).trim()
              else if (line.startsWith('data:')) data += line.slice(5).trim()
            }
            if (!data) continue
            const parsed = JSON.parse(data)
            if (evName === 'event') handlers.onEvent(parsed as GovernorEvent)
            else if (evName === 'result') handlers.onResult(parsed as GovernorSummary)
            else if (evName === 'error') handlers.onError?.(String(parsed.detail ?? 'error'))
          }
        }
        handlers.onDone?.()
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          handlers.onError?.(e instanceof Error ? e.message : String(e))
        }
        handlers.onDone?.()
      }
    })()
    return () => ctrl.abort()
  },

  // ---- Batch JSON -> golden alignment ----

  setTargetSchema(
    projectId: string,
    target: unknown,
  ): Promise<{ project_id: string; title: string; fields: number; required: string[] }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/target-schema`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target }),
    }).then((r) => json(r))
  },

  getTargetSchema(projectId: string): Promise<{ project_id: string; target: unknown }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/target-schema`).then((r) =>
      json(r),
    )
  },

  submitBatch(
    projectId: string,
    docs: { doc_id?: string; records: unknown[]; descriptions?: Record<string, string> }[],
    research?: boolean,
  ): Promise<{ job_id: string; state: string; documents: number }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(research === undefined ? { docs } : { docs, research }),
    }).then((r) => json(r))
  },

  getLibrary(projectId: string): Promise<AlignmentLibrary> {
    return fetch(`${BASE}/projects/${projectId}/alignment/library`).then((r) =>
      json<AlignmentLibrary>(r),
    )
  },

  /** Approve a provisional shape: promote it to approved and persist. */
  approveProfile(
    projectId: string,
    profileId: string,
  ): Promise<{ project_id: string; approved: string; state: string }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId }),
    }).then((r) => json(r))
  },

  /** Reject a learned shape with a server-validated structured reason. The
   *  server composes + stores a canonical reason and declines future files of
   *  this shape. Throws on 422 (hollow reason) / 404 (unknown profile). */
  rejectProfile(
    projectId: string,
    body: { profile_id: string; category: RejectCategory; fields?: string[]; note?: string },
  ): Promise<{ project_id: string; profile_id: string; state: 'rejected'; rejection: RejectionRecord }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json(r))
  },

  /** Un-reject a shape, returning it to its pre-reject state (approved or
   *  provisional). Throws on 409 (not rejected) / 404 (unknown profile). */
  restoreProfile(
    projectId: string,
    profileId: string,
  ): Promise<{ project_id: string; profile_id: string; state: 'approved' | 'provisional' }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId }),
    }).then((r) => json(r))
  },

  /** Record a human field-by-field correspondence on a learned shape: map a
   *  golden field to a real source path, or null to leave it unmapped. */
  overrideFieldMapping(
    projectId: string, profileId: string, target: string, sourcePath: string | null,
  ): Promise<{
    mapped: Record<string, string>; needs_review: string[]; conflicts: string[]
    propagation?: {
      available: boolean; sampled: number; total: number
      conformant: number; needs_review: number
    }
  }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId, target, source_path: sourcePath }),
    }).then((r) => json(r))
  },

  /** The per-project learned value vocabulary: each enumerable golden field's
   *  built-in (schema) values plus the human-accepted (learned) ones. */
  getValueVocab(projectId: string): Promise<ValueVocab> {
    return fetch(`${BASE}/projects/${projectId}/alignment/value-vocab`).then((r) =>
      json<ValueVocab>(r),
    )
  },

  /** CRUD a learned accepted value for a golden field. `action` is
   *  'add' | 'update' | 'remove'. On update, omitted optional fields (null) are
   *  left unchanged; a sent value sets them ('' clears). Returns the refreshed
   *  per-field vocabulary. */
  changeValueVocab(
    projectId: string,
    change: {
      action:
        | 'add'
        | 'update'
        | 'remove'
        | 'set_free_form'
        | 'clear_free_form'
        | 'add_alias'
        | 'remove_alias'
      target: string
      value?: string
      new_value?: string | null
      alias?: string | null
      added_by?: string
      note?: string | null
      example?: string | null
      free_form?: boolean
    },
  ): Promise<{ project_id: string; change: unknown; vocab: ValueVocab['fields'] }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/value-vocab`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    }).then((r) => json(r))
  },

  /** The latest batch's conformed output, one entry per source file that
   *  produced golden records. */
  listAlignmentOutputs(projectId: string): Promise<{ outputs: AlignmentOutput[] }> {
    return fetch(`${BASE}/projects/${projectId}/alignment/outputs`).then((r) =>
      json<{ outputs: AlignmentOutput[] }>(r),
    )
  },

  /** The download URL for one source file's conformed records. The server names
   *  the file '<stem><postfix>_<UTC DTG>.json'. */
  alignmentOutputDownloadUrl(projectId: string, stem: string, postfix: string): string {
    const qs = new URLSearchParams({ postfix })
    return `${BASE}/projects/${projectId}/alignment/outputs/${encodeURIComponent(stem)}/download?${qs.toString()}`
  },

  getJob(jobId: string): Promise<JobStatus> {
    return fetch(`${BASE}/jobs/${jobId}`).then((r) => json<JobStatus>(r))
  },

  /** Live queue tallies by job state (GET /jobs). Drives the worker/queue
   *  visibility panel; absent keys mean a zero count for that state. */
  getJobCounts(): Promise<{ counts: JobCounts }> {
    return fetch(`${BASE}/jobs`).then((r) => json<{ counts: JobCounts }>(r))
  },
}
