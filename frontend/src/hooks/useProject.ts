import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DocumentRecord, Project, Supplemental } from '../api/types'

const DEMO_PROJECT_ID = 'proj_demo'

/**
 * Loads (or creates) the active project and keeps documents + supplementals
 * in sync. Polls while any document is still processing so the pipeline board
 * animates through its stages without a manual refresh.
 */
export function useProject() {
  const [project, setProject] = useState<Project | null>(null)
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

  // Resolve the active project once on mount.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const projects = await api.listProjects()
        const found =
          projects.find((p) => p.id === DEMO_PROJECT_ID) ?? projects[0]
        const active = found ?? (await api.createProject('Default Project'))
        if (cancelled) return
        setProject(active)
        await refresh(active.id)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refresh])

  // Poll while any document is mid-pipeline.
  useEffect(() => {
    if (!project) return
    const busy = documents.some(
      (d) => d.overall_status === 'processing' || d.overall_status === 'pending',
    )
    if (!busy) return
    const id = setInterval(() => void refresh(project.id), 1500)
    return () => clearInterval(id)
  }, [project, documents, refresh])

  return { project, documents, supplementals, error, refresh }
}
