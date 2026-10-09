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
import type { DocumentRecord, Project, Readiness, Supplemental } from '../api/types'

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
  readiness: Readiness | null
  /** True when the active project carries correction-pipeline fixtures (a
   *  first-attempt draft or template), e.g. a sample/correction project. Such
   *  projects unlock the Correction Pipeline tab even with no uploaded docs. */
  hasCorrectionData: boolean
  error: string | null
  setActiveId: (id: string) => void
  refreshProjects: () => Promise<void>
  refresh: (projectId: string) => Promise<void>
  deleteProject: (projectId: string) => Promise<void>
  diagnosticsEnabled: boolean
}

const Ctx = createContext<ProjectCtx | null>(null)

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [supplementals, setSupplementals] = useState<Supplemental[]>([])
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [hasCorrectionData, setHasCorrectionData] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [diagnosticsEnabled, setDiagnosticsEnabled] = useState(false)

  const refresh = useCallback(async (projectId: string) => {
    try {
      const [docs, supps, rdy] = await Promise.all([
        api.listDocuments(projectId),
        api.listSupplementals(projectId),
        api.getReadiness(projectId),
      ])
      setDocuments(docs)
      setSupplementals(supps)
      setReadiness(rdy)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    // Separately determine whether this project carries correction-pipeline
    // fixtures (first-attempt draft/template). This is independent of uploaded
    // Ingestion documents, so a sample/correction project unlocks the
    // Correction Pipeline tab even though it has no uploaded docs. Best-effort:
    // a project without components simply reports false.
    try {
      const components = await api.projectComponents(projectId)
      const fa = components.find((c) => c.id === 'first_attempt')
      setHasCorrectionData((fa?.items.length ?? 0) > 0)
    } catch {
      setHasCorrectionData(false)
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

  // Load the project list once on mount.
  useEffect(() => {
    void refreshProjects()
  }, [refreshProjects])

  // Read client feature flags once on mount (whether the Diagnostics page is
  // available). Defaults to off if the call fails.
  useEffect(() => {
    api
      .getClientConfig()
      .then((c) => {
        setDiagnosticsEnabled(!!c.diagnostics_enabled)
      })
      .catch(() => {
        setDiagnosticsEnabled(false)
      })
  }, [])

  // Load the active project's data whenever the selection changes. Clear the
  // previous project's data IMMEDIATELY so a newly-created or newly-selected
  // project never momentarily shows another project's documents while the
  // fresh fetch is in flight.
  useEffect(() => {
    setDocuments([])
    setSupplementals([])
    setReadiness(null)
    setHasCorrectionData(false)
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
    readiness,
    hasCorrectionData,
    project,
    documents,
    supplementals,
    error,
    setActiveId,
    refreshProjects,
    refresh,
    deleteProject,
    diagnosticsEnabled,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useProject(): ProjectCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useProject must be used within a ProjectProvider')
  return ctx
}
