// Thin API client. All calls go through the Vite proxy (/api -> backend).

import type {
  Convergence,
  CorrectedReport,
  DocumentRecord,
  ExportFormat,
  GenerateResult,
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
}
