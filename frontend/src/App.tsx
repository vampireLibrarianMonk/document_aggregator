import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { CanonicalViewer } from './components/CanonicalViewer'
import { CorrectionPipeline } from './components/CorrectionPipeline'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { NewProjectForm } from './components/NewProjectForm'
import { PipelineBoard } from './components/PipelineBoard'
import { ReportPanel } from './components/ReportPanel'
import { SearchPanel } from './components/SearchPanel'
import { SupplementalsPanel } from './components/SupplementalsPanel'
import { TemplatePicker } from './components/TemplatePicker'
import { ProjectProvider, useProject } from './hooks/useProject'

type Tab = 'new' | 'correction' | 'pipeline' | 'supplementals' | 'search' | 'report' | 'samples'

interface TabDef { id: Tab; label: string }

const BASE_TABS: TabDef[] = [
  { id: 'new', label: 'New Project' },
  { id: 'correction', label: 'Correction Pipeline' },
  { id: 'pipeline', label: 'Ingestion' },
  { id: 'supplementals', label: 'Supplementals' },
  { id: 'search', label: 'Search' },
  { id: 'report', label: 'Report & Export' },
]

// The Samples tab is appended only when the backend flag enables it. Kept at
// the end so it reads as a secondary, opt-in area.
const SAMPLES_TAB: TabDef = { id: 'samples', label: 'Samples' }

// Diagnostics is a separate page (/diagnostics), handled before this app shell.
const ALL_TAB_IDS = new Set<Tab>([...BASE_TABS.map((t) => t.id), SAMPLES_TAB.id])

/** Map a URL path to a tab. The Correction Pipeline is the root ('/'); every
 *  other tab is '/<id>' (e.g. '/diagnostics'). Unknown paths fall back to the
 *  Correction Pipeline so a stray URL never shows a blank screen. */
function tabFromPath(pathname: string): Tab {
  const seg = pathname.replace(/^\/+|\/+$/g, '').split('/')[0]
  if (!seg) return 'correction'
  return ALL_TAB_IDS.has(seg as Tab) ? (seg as Tab) : 'correction'
}

function pathForTab(tab: Tab): string {
  return tab === 'correction' ? '/' : `/${tab}`
}

export default function App() {
  // Diagnostics is a genuinely separate page: when the URL path is
  // /diagnostics we render ONLY the diagnostics view (its own shell), not the
  // project workflow app. Reached by navigating to /diagnostics.
  const [path, setPath] = useState<string>(() => window.location.pathname)
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  if (path.replace(/^\/+|\/+$/g, '') === 'diagnostics') {
    return <DiagnosticsPage onBack={() => {
      window.history.pushState({}, '', '/')
      setPath('/')
    }} />
  }

  return (
    <ProjectProvider>
      <AppShell />
    </ProjectProvider>
  )
}

/** Standalone Diagnostics page. Not part of the project workflow app; it is
 *  reached at /diagnostics and renders only the service-status view with a link
 *  back to the app. Gated by the DIAGNOSTICS_ENABLED backend flag. */
function DiagnosticsPage({ onBack }: { onBack: () => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  useEffect(() => {
    api
      .getClientConfig()
      .then((c) => setEnabled(!!c.diagnostics_enabled))
      .catch(() => setEnabled(false))
  }, [])

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Diagnostics</h1>
          <div className="subtitle">
            Live status of the local services (embeddings, OCR, LibreOffice,
            Bedrock) and versions. Runs fully offline.
          </div>
        </div>
        <a
          href="/"
          className="btn secondary small"
          onClick={(e) => { e.preventDefault(); onBack() }}
        >
          ← Back to app
        </a>
      </header>
      {enabled === null && <div className="panel"><p className="small muted">Loading…</p></div>}
      {enabled === false && (
        <div className="panel" role="status">
          <strong>Diagnostics is disabled</strong>
          <p className="small muted">
            Set <span className="mono">DIAGNOSTICS_ENABLED=true</span> to enable
            the diagnostics page.
          </p>
        </div>
      )}
      {enabled === true && <DiagnosticsPanel />}
    </div>
  )
}

function AppShell() {
  const {
    projects,
    activeId,
    documents,
    supplementals,
    error,
    setActiveId,
    refreshProjects,
    refresh,
    deleteProject,
    samplesEnabled,
    diagnosticsEnabled,
  } = useProject()
  // The visible tab set: Samples is appended only when the backend flag is on.
  const TABS: TabDef[] = samplesEnabled ? [...BASE_TABS, SAMPLES_TAB] : BASE_TABS
  // Tab state is URL-aware so views deep-link (e.g. /diagnostics opens the
  // Diagnostics tab) and browser back/forward work.
  const [tab, setTabState] = useState<Tab>(() => tabFromPath(window.location.pathname))
  const [inspecting, setInspecting] = useState<string | null>(null)

  const setTab = useCallback((next: Tab) => {
    setTabState(next)
    const path = pathForTab(next)
    if (window.location.pathname !== path) {
      window.history.pushState({ tab: next }, '', path)
    }
  }, [])

  // Reflect browser back/forward (and the initial deep link) into tab state.
  useEffect(() => {
    const onPop = () => setTabState(tabFromPath(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const reload = () => activeId && void refresh(activeId)
  const isEmpty = projects.length === 0

  // Prerequisite gating: a tab is only usable once its upstream step is done.
  //  - New Project: always available (it is how you create a project).
  //  - Correction Pipeline / Ingestion / Supplementals: need a selected project.
  //  - Search / Report & Export: additionally need at least one ingested
  //    (completed) document, since there is nothing to search or report on yet.
  const hasProject = !!activeId
  const hasCompletedDocs = documents.some((d) => d.overall_status === 'completed')
  const tabDisabled = useCallback((id: Tab): boolean => {
    switch (id) {
      case 'new':
        return false
      case 'correction':
      case 'pipeline':
      case 'supplementals':
        return !hasProject
      case 'search':
      case 'report':
        return !hasProject || !hasCompletedDocs
      default:
        return false
    }
  }, [hasProject, hasCompletedDocs])
  const tabReason = (id: Tab): string => {
    if (!hasProject) return 'Select or create a project first'
    if ((id === 'search' || id === 'report') && !hasCompletedDocs)
      return 'Upload documents on the Ingestion tab first'
    return ''
  }

  // If the Samples tab is reached via URL but the feature is disabled, send the
  // user to New Project (the samples area does not exist when the flag is off).
  useEffect(() => {
    if (tab === 'samples' && !samplesEnabled) setTab('new')
  }, [tab, samplesEnabled, setTab])

  // If the current tab becomes disabled (e.g. its prerequisite is no longer
  // met), fall back to a safe tab so the user is never stuck on a dead view.
  useEffect(() => {
    if (tabDisabled(tab)) {
      setTab(hasProject ? 'correction' : 'new')
    }
  }, [tab, tabDisabled, hasProject, setTab])

  // On a fresh/empty app, land on the New Project page so there is always a
  // clear next step. Leave the user alone on Samples (its own opt-in area).
  useEffect(() => {
    if (isEmpty && tab !== 'new' && tab !== 'samples') {
      setTab('new')
    }
  }, [isEmpty, tab, setTab])

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Document Aggregation Pipeline</h1>
          <div className="subtitle">
            Reconcile raw source documents, a template, and a first-draft report
            into one clean, corrected report. Every change traces to a source,
            and every rule the template sets is checked.
          </div>
        </div>
        {/* Global project selector: one project scopes every tab. */}
        <div className="project-selector">
          <label htmlFor="active-project" className="small muted">Project</label>
          <select
            id="active-project"
            value={activeId ?? ''}
            onChange={(e) => setActiveId(e.target.value)}
            disabled={isEmpty}
          >
            {isEmpty && <option value="">No projects yet</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button
            className="btn small"
            onClick={() => setTab('new')}
          >
            + New Project
          </button>
          {activeId && (
            <button
              className="btn secondary small"
              onClick={() => {
                const name = projects.find((p) => p.id === activeId)?.name ?? activeId
                if (window.confirm(`Delete project "${name}"? This permanently removes its data.`)) {
                  void deleteProject(activeId)
                }
              }}
            >
              Delete project
            </button>
          )}
        </div>
      </header>

      <div className="panel intro">
        <strong>What this does</strong>
        <p className="small">
          A <b>project</b> brings together the raw source documents (the{' '}
          <b>corpus</b>), a <b>template</b> that defines the required structure
          and formatting, a first attempt at the report, and any reviewer
          comments. The platform reads the template to learn its rules, then
          produces a <b>corrected report</b>: fixing wrong values, filling gaps,
          applying the formatting rules, and flagging anything it cannot resolve
          on its own. Nothing is invented. Every change traces back to a source,
          and anything left unresolved is yours to decide.
        </p>
        <p className="small muted">
          The app starts empty. Open <b>New Project</b> to create one: generate a
          project from a brief or an uploaded document, or upload documents on the
          <b> Ingestion</b> tab. Everything you see after that is a project you
          created.
        </p>
      </div>

      {error && (
        <div className="panel" role="alert" style={{ borderColor: 'var(--err)' }}>
          <strong style={{ color: 'var(--err)' }}>Backend error</strong>
          <p className="small">{error}</p>
          <p className="muted small">
            Is the API running? Start it with:{' '}
            <span className="mono">uvicorn app.main:app --reload</span> from{' '}
            <span className="mono">backend/</span>.
          </p>
        </div>
      )}

      <nav className="tabs" role="tablist" aria-label="Views">
        {TABS.map((t) => {
          const disabled = tabDisabled(t.id)
          return (
            <button
              key={t.id}
              id={`tab-${t.id}`}
              role="tab"
              aria-selected={tab === t.id}
              aria-controls={tab === t.id ? `panel-${t.id}` : undefined}
              aria-disabled={disabled}
              tabIndex={tab === t.id ? 0 : -1}
              title={disabled ? tabReason(t.id) : undefined}
              className={`tab ${tab === t.id ? 'active' : ''}${disabled ? ' disabled' : ''}`}
              onClick={() => { if (!disabled) setTab(t.id) }}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
                e.preventDefault()
                // Move to the nearest ENABLED tab in the chosen direction.
                const step = e.key === 'ArrowRight' ? 1 : -1
                const i = TABS.findIndex((x) => x.id === t.id)
                for (let k = 1; k <= TABS.length; k++) {
                  const cand = TABS[(i + step * k + TABS.length * k) % TABS.length]
                  if (!tabDisabled(cand.id)) {
                    setTab(cand.id)
                    document.getElementById(`tab-${cand.id}`)?.focus()
                    break
                  }
                }
              }}
            >
              {t.label}
            </button>
          )
        })}
      </nav>

      {tab === 'new' && (
        <div id="panel-new" role="tabpanel" aria-labelledby="tab-new">
          <NewProjectForm
            onCreated={async (id) => {
              await refreshProjects()
              setActiveId(id)
              setTab('pipeline')   // next step: Ingestion
            }}
          />
          {samplesEnabled && (
            <p className="small muted" style={{ marginTop: 10 }}>
              Looking for the bundled sample cases? They live on the{' '}
              <b>Samples</b> tab.
            </p>
          )}
        </div>
      )}

      {tab === 'samples' && samplesEnabled && (
        <div id="panel-samples" role="tabpanel" aria-labelledby="tab-samples">
          <TemplatePicker
            onInstantiated={() => {
              // instantiateTemplate already refreshed the list + selected the
              // new project; move the user to the Correction Pipeline.
              setTab('correction')
            }}
          />
        </div>
      )}

      {tab === 'correction' && (
        <div id="panel-correction" role="tabpanel" aria-labelledby="tab-correction">
          <CorrectionPipeline
            projectId={activeId}
            onProjectsChanged={refreshProjects}
            onSelectProject={setActiveId}
          />
        </div>
      )}

      {activeId ? (
        <>
          {tab === 'pipeline' && (
            <div id="panel-pipeline" role="tabpanel" aria-labelledby="tab-pipeline">
              <PipelineBoard
                projectId={activeId}
                documents={documents}
                onChange={reload}
                onInspect={(id) => setInspecting(id)}
              />
              <CanonicalViewer projectId={activeId} documentId={inspecting} />
            </div>
          )}

          {tab === 'supplementals' && (
            <div id="panel-supplementals" role="tabpanel" aria-labelledby="tab-supplementals">
              <SupplementalsPanel
                projectId={activeId}
                supplementals={supplementals}
                documents={documents}
                onChange={reload}
              />
            </div>
          )}

          {tab === 'search' && (
            <div id="panel-search" role="tabpanel" aria-labelledby="tab-search">
              <SearchPanel projectId={activeId} />
            </div>
          )}

          {tab === 'report' && (
            <div id="panel-report" role="tabpanel" aria-labelledby="tab-report">
              <ReportPanel projectId={activeId} />
            </div>
          )}
        </>
      ) : (
        tab !== 'new' && tab !== 'correction' && tab !== 'samples' && !error && (
          <div className="panel" role="status">
            <strong>{isEmpty ? 'No projects yet' : 'No project selected'}</strong>
            <p className="small muted">
              {isEmpty
                ? 'Open the New Project tab to generate a project, or upload documents on the Ingestion tab.'
                : 'Pick a project from the selector above to use this view.'}
            </p>
          </div>
        )
      )}

      {diagnosticsEnabled && (
        <footer className="app-footer small muted">
          <a href="/diagnostics">Diagnostics</a>
        </footer>
      )}
    </div>
  )
}
