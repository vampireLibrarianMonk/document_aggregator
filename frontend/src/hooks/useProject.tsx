import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import type { DocumentRecord, Project, Supplemental } from '../api/types'

/**
 * Shared ACTIVE-PROJECT context. A project is the single top-level container
 * (a project IS a correction project in the unified model), so one selected
 * project scopes every tab. Replaces the old single-project hook: it now holds
 * the full project list + the active selection + that project's documents and
 * supplementals, and keeps them in sync (polling while a doc is processing).
 */
interface ProjectCtx {
  projects: Project[]
  activeId: string | null
  project: Project | null
  documents: DocumentRecord[]
  supplementals: Supplemental[]
  error: string | null
  setActiveId: (id: string) => void
  refreshProjects: () => Promise<void>
  refresh: (projectId: string) => Promise<void>
  instantiateTemplate: (caseId: string) => Promise<string | null>
  deleteProject: (projectId: string) => Promise<void>
  samplesEnabled: boolean
}

const Ctx = createContext<ProjectCtx | null>(null)

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [supplementals, setSupplementals] = useState<Supplemental[]>([])
  const [error, setError] = useState<string | null>(null)
  const [samplesEnabled, setSamplesEnabled] = useState(false)

  const refresh = useCallback(async (projectId: string) => {
    try {
      const [docs, supps] = await Promise.all([
        api.listDocuments(projectId),
        api.listSupplementals(projectId),
      ])
      setDocuments(docs)
      setSupplementals(supps)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const refreshProjects = useCallback(async () => {
    try {
      const list = await api.listProjects()
      setProjects(list)
      // The app starts empty. Keep the current selection if it still exists,
      // else select the first available (or none when there are no projects).
      setActiveId((cur) => {
        if (cur && list.some((p) => p.id === cur)) return cur
        return list[0]?.id ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Delete a project the user created, then refresh the list. The active
  // selection falls back to the first remaining project (or none).
  const deleteProject = useCallback(async (projectId: string) => {
    try {
      await api.deleteProject(projectId)
      const list = await api.listProjects()
      setProjects(list)
      setActiveId((cur) => (cur === projectId ? (list[0]?.id ?? null) : cur))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Instantiate a bundled sample case into a new persisted project, then
  // refresh the list and jump the selector to it.
  const instantiateTemplate = useCallback(async (caseId: string) => {
    try {
      const proj = await api.instantiateTemplate(caseId)
      const list = await api.listProjects()
      setProjects(list)
      setActiveId(proj.id)
      return proj.id
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    }
  }, [])

  // Load the project list once on mount.
  useEffect(() => {
    void refreshProjects()
  }, [refreshProjects])

  // Read client feature flags once on mount (whether the in-app Samples page
  // is available). Defaults to off if the call fails.
  useEffect(() => {
    api
      .getClientConfig()
      .then((c) => setSamplesEnabled(!!c.samples_enabled))
      .catch(() => setSamplesEnabled(false))
  }, [])

  // Load the active project's data whenever the selection changes.
  useEffect(() => {
    if (activeId) void refresh(activeId)
  }, [activeId, refresh])

  // Poll the active project while any document is mid-pipeline.
  useEffect(() => {
    if (!activeId) return
    const busy = documents.some(
      (d) => d.overall_status === 'processing' || d.overall_status === 'pending',
    )
    if (!busy) return
    const id = setInterval(() => void refresh(activeId), 1500)
    return () => clearInterval(id)
  }, [activeId, documents, refresh])

  const project = useMemo(
    () => projects.find((p) => p.id === activeId) ?? null,
    [projects, activeId],
  )

  const value: ProjectCtx = {
    projects,
    activeId,
    project,
    documents,
    supplementals,
    error,
    setActiveId,
    refreshProjects,
    refresh,
    instantiateTemplate,
    deleteProject,
    samplesEnabled,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useProject(): ProjectCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useProject must be used within a ProjectProvider')
  return ctx
}
