import { expect, Page, APIRequestContext } from '@playwright/test'

// The served app proxies /api -> the live backend, so tests reach the backend
// through the same origin the browser uses. Setup/teardown hit it directly;
// assertions go through the real UI.
export const API = '/api'

/** Fail fast with a clear message if the backend isn't reachable. */
export async function assertBackendUp(request: APIRequestContext): Promise<void> {
  const res = await request.get(`${API}/health`)
  if (!res.ok()) {
    throw new Error(
      `Backend not reachable at /api/health (status ${res.status()}). ` +
      'Bring the stack up: docker compose up (SAMPLES_ENABLED=true).',
    )
  }
}

/** Confirm the in-app Samples feature is enabled (needed for sample flows). */
export async function samplesEnabled(request: APIRequestContext): Promise<boolean> {
  const res = await request.get(`${API}/config`)
  if (!res.ok()) return false
  const cfg = await res.json()
  return !!cfg.samples_enabled
}

/** Instantiate a sample case into a fresh project; returns its id. */
export async function instantiateSample(
  request: APIRequestContext, caseId: string,
): Promise<string> {
  const res = await request.post(`${API}/projects/from-template/${caseId}`, {
    data: {},
  })
  expect(res.ok(), `instantiate sample ${caseId}`).toBeTruthy()
  return (await res.json()).id as string
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
