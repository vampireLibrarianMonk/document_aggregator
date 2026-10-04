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
}

const Ctx = createContext<ProjectCtx | null>(null)

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [supplementals, setSupplementals] = useState<Supplemental[]>([])
  const [error, setError] = useState<string | null>(null)

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
      // Keep an active selection: prefer the current one, else proj_demo, else
      // the first available.
      setActiveId((cur) => {
        if (cur && list.some((p) => p.id === cur)) return cur
        const demo = list.find((p) => p.id === 'proj_demo')
        return demo?.id ?? list[0]?.id ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Load the project list once on mount.
  useEffect(() => {
    void refreshProjects()
  }, [refreshProjects])

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
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useProject(): ProjectCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useProject must be used within a ProjectProvider')
  return ctx
}
