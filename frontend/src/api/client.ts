// Thin API client. All calls go through the Vite proxy (/api -> backend).

import type {
  Convergence,
  CorrectedReport,
  DocumentRecord,
  ExportFormat,
  GenerateResult,
  GovernorEvent,
  GovernorSummary,
  Project,
  Report,
  ScenarioComponent,
  ScenarioInfo,
  ScenarioModels,
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
  listProjects(): Promise<Project[]> {
    return fetch(`${BASE}/projects`).then((r) => json<Project[]>(r))
  },

  createProject(name: string): Promise<Project> {
    return fetch(`${BASE}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then((r) => json<Project>(r))
  },

  listDocuments(projectId: string): Promise<DocumentRecord[]> {
    return fetch(`${BASE}/projects/${projectId}/documents`).then((r) =>
      json<DocumentRecord[]>(r),
    )
  },

  uploadDocument(projectId: string, file: File): Promise<{ document_id: string }> {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/projects/${projectId}/documents`, {
      method: 'POST',
      body: form,
    }).then((r) => json<{ document_id: string }>(r))
  },

  getCanonical(projectId: string, documentId: string): Promise<unknown> {
    return fetch(
      `${BASE}/projects/${projectId}/documents/${documentId}/canonical`,
    ).then((r) => json<unknown>(r))
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

  // ---- Correction scenario (four-component pipeline) ----

  scenarios(): Promise<ScenarioInfo[]> {
    return fetch(`${BASE}/scenarios`).then((r) => json<ScenarioInfo[]>(r))
  },

  scenarioComponents(scenarioId: string): Promise<ScenarioComponent[]> {
    return fetch(`${BASE}/scenario/components?scenario_id=${scenarioId}`).then((r) =>
      json<ScenarioComponent[]>(r),
    )
  },

  scenarioComponent(
    componentId: string,
    mode: string,
    scenarioId: string,
    sourceFormat: string,
  ): Promise<{ data: unknown }> {
    return fetch(
      `${BASE}/scenario/component/${componentId}?mode=${mode}&scenario_id=${scenarioId}&source_format=${sourceFormat}`,
    ).then((r) => json<{ data: unknown }>(r))
  },

  scenarioReconcile(
    mode: 'draft' | 'template',
    scenarioId: string,
    sourceFormat: string,
  ): Promise<CorrectedReport> {
    return fetch(
      `${BASE}/scenario/reconcile?mode=${mode}&scenario_id=${scenarioId}&source_format=${sourceFormat}`,
    ).then((r) => json<CorrectedReport>(r))
  },

  scenarioConverge(
    mode: 'draft' | 'template',
    scenarioId: string,
    sourceFormat: string,
  ): Promise<Convergence> {
    return fetch(
      `${BASE}/scenario/converge?mode=${mode}&scenario_id=${scenarioId}&source_format=${sourceFormat}`,
    ).then((r) => json<Convergence>(r))
  },

  // Apply a human decision to one unresolved unit (a conflict candidate choice
  // or a needs_review value) and get the re-reconciled report back.
  scenarioResolve(
    target: string,
    value: string,
    mode: 'draft' | 'template',
    scenarioId: string,
    sourceFormat: string,
  ): Promise<CorrectedReport> {
    return fetch(`${BASE}/scenario/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target,
        value,
        mode,
        scenario_id: scenarioId,
        source_format: sourceFormat,
      }),
    }).then((r) => json<CorrectedReport>(r))
  },

  // ---- Scenario generation (model picker + per-run metrics) ----

  scenarioModels(): Promise<ScenarioModels> {
    return fetch(`${BASE}/scenario/models`).then((r) => json<ScenarioModels>(r))
  },

  scenarioGenerate(body: {
    domain?: string
    doc_type?: string
    title?: string
    model?: string | null
    dry_run?: boolean
  }): Promise<GenerateResult> {
    return fetch(`${BASE}/scenario/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<GenerateResult>(r))
  },

  scenarioGenerateFromText(body: {
    text: string
    model?: string | null
    dry_run?: boolean
  }): Promise<GenerateResult> {
    return fetch(`${BASE}/scenario/generate/from-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<GenerateResult>(r))
  },

  // Generate a scenario FROM an uploaded document: the document's own text
  // becomes the ground-truth corpus (deterministic, no model invents facts).
  scenarioGenerateFromDocument(
    file: File,
    opts: { domain?: string; title?: string; dry_run?: boolean } = {},
  ): Promise<GenerateResult> {
    const qs = new URLSearchParams()
    if (opts.domain) qs.set('domain', opts.domain)
    if (opts.title) qs.set('title', opts.title)
    qs.set('dry_run', String(opts.dry_run ?? false))
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/scenario/generate/from-document?${qs.toString()}`, {
      method: 'POST',
      body: form,
    }).then((r) => json<GenerateResult>(r))
  },

  // ---- Governed (decomposed) generation with a live progress stream ----
  // Consumes Server-Sent Events: calls onEvent for each step, onResult for the
  // final summary. Returns an abort function so the caller can cancel.
  scenarioGovernedStream(
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
        const res = await fetch(`${BASE}/scenario/governed/stream?${qs.toString()}`, {
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
