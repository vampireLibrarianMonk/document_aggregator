// Thin API client. All calls go through the Vite proxy (/api -> backend).

import type {
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
  Project,
  RawDoc,
  Report,
  ProjectComponent,
  ProjectInfo,
  ProjectModels,
  SearchResponse,
  Supplemental,
  SupplementalKind,
} from './types'

const BASE = '/api'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  getClientConfig(): Promise<{ samples_enabled: boolean; diagnostics_enabled: boolean }> {
    return fetch(`${BASE}/config`).then((r) =>
      json<{ samples_enabled: boolean; diagnostics_enabled: boolean }>(r),
    )
  },

  listProjects(): Promise<Project[]> {
    return fetch(`${BASE}/projects`).then((r) => json<Project[]>(r))
  },

  createProject(name: string, description = ''): Promise<Project> {
    return fetch(`${BASE}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
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

  exportUrl(projectId: string, format: ExportFormat): string {
    return `${BASE}/projects/${projectId}/export?format=${format}`
  },

  // ---- Sample-case templates ----
  // The app starts empty. These bundled cases are templates the user can
  // instantiate into a real, persisted project on demand.

  listTemplates(): Promise<ProjectInfo[]> {
    return fetch(`${BASE}/templates`).then((r) => json<ProjectInfo[]>(r))
  },

  instantiateTemplate(caseId: string, name?: string): Promise<Project> {
    return fetch(`${BASE}/projects/from-template/${caseId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(name ? { name } : {}),
    }).then((r) => json<Project>(r))
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
}
