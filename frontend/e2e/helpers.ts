import { expect, Page, APIRequestContext } from '@playwright/test'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// The served app proxies /api -> the live backend, so tests reach the backend
// through the same origin the browser uses. Setup/teardown hit it directly;
// assertions go through the real UI.
export const API = '/api'

// sample_docs lives at repo-root/sample_docs; specs run with cwd = frontend/.
export const SAMPLE_DOCS = join(process.cwd(), '..', 'sample_docs', 'project')

/** Fail fast with a clear message if the backend isn't reachable. */
export async function assertBackendUp(request: APIRequestContext): Promise<void> {
  const res = await request.get(`${API}/health`)
  if (!res.ok()) {
    throw new Error(
      `Backend not reachable at /api/health (status ${res.status()}). ` +
      'Bring the stack up: docker compose up.',
    )
  }
}

/** Upload one file into a project's Ingestion store under the given kind, via
 *  the same endpoint the UI's file input calls. Uploading corpus documents
 *  triggers the app's generate-from-documents step server-side. */
async function uploadDocument(
  request: APIRequestContext, projectId: string, path: string, kind: string,
): Promise<void> {
  const name = path.split(/[\\/]/).pop() as string
  const res = await request.post(`${API}/projects/${projectId}/documents`, {
    multipart: {
      file: { name, mimeType: 'application/octet-stream', buffer: readFileSync(path) },
      kind,
    },
  })
  expect(res.ok(), `upload ${name}`).toBeTruthy()
}

/**
 * Build a correction project the REAL way: create an empty project, then upload
 * the standard source document set for sample case `caseId` (corpus .txt/.md +
 * every figure under corpus/figures/). The app generates the project
 * (manifest/template/draft/corrections) from those uploads. Returns the id.
 */
export async function buildProjectFromCorpus(
  request: APIRequestContext, caseId: string, corpus: string[],
): Promise<string> {
  const projectId = await createProject(request, `E2E case ${caseId}`)
  const dir = join(SAMPLE_DOCS, caseId, 'corpus')
  for (const name of corpus) {
    await uploadDocument(request, projectId, join(dir, name), 'corpus')
  }
  const figuresDir = join(dir, 'figures')
  if (existsSync(figuresDir)) {
    for (const f of readdirSync(figuresDir).filter((n) => n.endsWith('.png'))) {
      await uploadDocument(request, projectId, join(figuresDir, f), 'corpus')
    }
  }
  return projectId
}

/** Create an empty project; returns its id. */
export async function createProject(
  request: APIRequestContext, name: string,
): Promise<string> {
  const res = await request.post(`${API}/projects`, { data: { name } })
  expect(res.ok(), 'create project').toBeTruthy()
  return (await res.json()).id as string
}

/** Delete a project (best-effort teardown). */
export async function deleteProject(
  request: APIRequestContext, id: string,
): Promise<void> {
  await request.delete(`${API}/projects/${id}`).catch(() => {})
}

/** Delete EVERY project — a clean slate for a spec. The app is meant to start
 *  empty, so this restores that invariant between runs. */
export async function wipeProjects(request: APIRequestContext): Promise<void> {
  const res = await request.get(`${API}/projects`)
  if (!res.ok()) return
  const projects = (await res.json()) as { id: string }[]
  for (const p of projects) await deleteProject(request, p.id)
}

/** Reconcile result shape (subset we assert on). */
export interface ReconcileResult {
  summary: {
    total_units: number
    unchanged?: number
    filled?: number
    corrected?: number
    needs_review?: number
    conflict?: number
  }
  sections: { fields: { key: string; status: string; value: unknown; candidates?: unknown[] }[] }[]
}

export async function reconcile(
  request: APIRequestContext, projectId: string, mode: 'draft' | 'template',
): Promise<ReconcileResult> {
  const res = await request.get(`${API}/projects/${projectId}/reconcile?mode=${mode}`)
  expect(res.ok(), `reconcile ${projectId} ${mode}`).toBeTruthy()
  return res.json()
}

/** Open a top tab by its accessible name and wait for it to settle. */
export async function openTab(page: Page, name: string | RegExp): Promise<void> {
  await page.getByRole('tab', { name }).click()
  await page.waitForTimeout(300)
}

/** Select a project in the global selector by its id. */
export async function selectProject(page: Page, id: string): Promise<void> {
  await page.locator('#active-project').selectOption(id)
  await page.waitForTimeout(300)
}
