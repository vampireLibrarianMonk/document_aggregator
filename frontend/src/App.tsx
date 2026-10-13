import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { DocumentViewer } from './components/CanonicalViewer'
import { CorrectionPipeline } from './components/CorrectionPipeline'
import { BatchConvertPanel, GoldenSchemaPanel, JsonReportPanel } from './components/JsonAlignment'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { IngestionBoard } from './components/IngestionBoard'
import { NewProjectForm } from './components/NewProjectForm'
import { ReportPanel } from './components/ReportPanel'
import { SearchPanel } from './components/SearchPanel'
import { SupplementalsPanel } from './components/SupplementalsPanel'
import type { ProjectKind } from './api/types'
import { ProjectProvider, useProject } from './hooks/useProject'

type Tab =
  | 'new' | 'pipeline' | 'correction' | 'supplementals' | 'search' | 'report'
  | 'golden' | 'convert'

interface TabDef { id: Tab; label: string }

// Correction-project workflow: create -> ingest -> correct -> search -> export.
const CORRECTION_TABS: TabDef[] = [
  { id: 'new', label: 'New Project' },
  { id: 'pipeline', label: 'Ingestion' },
  { id: 'correction', label: 'Correction Pipeline' },
  { id: 'supplementals', label: 'Supplementals' },
  { id: 'search', label: 'Search' },
  { id: 'report', label: 'Report & Export' },
]

// JSON-alignment workflow: create -> define golden schema -> convert -> export.
const JSON_TABS: TabDef[] = [
  { id: 'new', label: 'New Project' },
  { id: 'golden', label: 'Golden Schema' },
  { id: 'convert', label: 'Batch Convert' },
  { id: 'report', label: 'Report & Export' },
]

/** The tab set a project exposes, driven by its kind. Correction is the
 *  default; json_alignment gets the focused convert flow. */
function tabsForKind(kind: string | undefined): TabDef[] {
  return kind === 'json_alignment' ? JSON_TABS : CORRECTION_TABS
}

// Diagnostics is a separate page (/diagnostics), handled before this app shell.
const ALL_TAB_IDS = new Set<Tab>(
  [...CORRECTION_TABS, ...JSON_TABS].map((t) => t.id),
)

/** Map a URL path to a tab. New Project is the root ('/'); every other tab is
 *  '/<id>' (e.g. '/pipeline'). Unknown paths fall back to New Project so a
 *  stray URL never shows a blank screen. */
function tabFromPath(pathname: string): Tab {
  const seg = pathname.replace(/^\/+|\/+$/g, '').split('/')[0]
  if (!seg) return 'new'
  return ALL_TAB_IDS.has(seg as Tab) ? (seg as Tab) : 'new'
}

function pathForTab(tab: Tab): string {
  return tab === 'new' ? '/' : `/${tab}`
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
    project,
    documents,
    supplementals,
    readiness,
    hasCorrectionData,
    error,
    setActiveId,
    refreshProjects,
    refresh,
    deleteProject,
    diagnosticsEnabled,
  } = useProject()
  // The tab set is driven by the active project's kind. With no project yet,
  // show the correction tab set (all gated) so the empty app still presents the
  // familiar workflow; the kind-specific set applies once a project exists.
  const isJson = project?.kind === 'json_alignment'
  const TABS: TabDef[] = activeId ? tabsForKind(project?.kind) : CORRECTION_TABS
  // Tab state is URL-aware so views deep-link (e.g. /diagnostics opens the
  // Diagnostics tab) and browser back/forward work.
  const [tab, setTabState] = useState<Tab>(() => tabFromPath(window.location.pathname))
  const [inspecting, setInspecting] = useState<string | null>(null)
  // New Project draft form state lives here (not inside NewProjectForm) so the
  // kind/name/description the user picked SURVIVE navigating away and back — the
  // form panel unmounts on tab switch, which would otherwise reset it.
  const [draftKind, setDraftKind] = useState<ProjectKind | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftDescription, setDraftDescription] = useState('')
  // Whether the active JSON-alignment project has a saved golden schema. Gates
  // the Batch Convert tab. Reset on project switch; refreshed from the backend.
  const [goldenConfigured, setGoldenConfigured] = useState(false)
  useEffect(() => {
    if (!activeId || !isJson) { setGoldenConfigured(false); return }
    api.getTargetSchema(activeId).then(() => setGoldenConfigured(true)).catch(() => setGoldenConfigured(false))
  }, [activeId, isJson])

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
  //  - Ingestion: needs a selected project.
  //  - Search: needs at least one completed ingested document.
  //  - Correction Pipeline / Report & Export: usable once the project can be
  //    corrected — either it already carries correction fixtures (a sample /
  //    correction project), OR the uploaded Ingestion inputs satisfy the
  //    pipeline prerequisites (readiness.ready). Either path unlocks the tab.
  const hasProject = !!activeId
  const hasCompletedDocs = documents.some((d) => d.overall_status === 'completed')
  const inputsReady = !!readiness?.ready
  // A project is correctable if it ships correction data OR its uploads are ready.
  const correctable = hasCorrectionData || inputsReady
  const tabDisabled = useCallback((id: Tab): boolean => {
    switch (id) {
      case 'new':
        return false
      case 'pipeline':
      case 'supplementals':
        return !hasProject
      case 'search':
        return !hasProject || !hasCompletedDocs
      case 'correction':
        return !hasProject || !correctable
      case 'golden':
        // JSON-alignment: defining the schema needs only a project.
        return !hasProject
      case 'convert':
        // JSON-alignment: convert needs a project AND a saved golden schema.
        return !hasProject || !goldenConfigured
      case 'report':
        // Correction projects need correction inputs; JSON projects can open
        // the report once they exist (it shows nothing until a batch runs).
        return !hasProject || (!isJson && !correctable)
      default:
        return false
    }
  }, [hasProject, hasCompletedDocs, correctable, isJson, goldenConfigured])
  const tabReason = (id: Tab): string => {
    if (!hasProject) return 'Create a project first'
    if (id === 'search' && !hasCompletedDocs)
      return 'Upload documents on the Ingestion tab first'
    if (id === 'correction' && !correctable)
      return 'Add correction inputs first (upload on the Ingestion tab, or use a sample)'
    if (id === 'convert' && !goldenConfigured)
      return 'Save a golden schema on the Golden Schema tab first'
    if (id === 'report' && !isJson && !correctable)
      return 'Add correction inputs first (upload on the Ingestion tab, or use a sample)'
    return ''
  }

  // If the current tab is not part of the active project's tab set, or becomes
  // disabled, fall back to a safe tab in THIS set so the user is never stuck on
  // a dead view (e.g. a correction tab lingering after switching to a JSON
  // project, or vice versa).
  const inSet = useCallback((id: Tab): boolean => TABS.some((t) => t.id === id), [TABS])
  const inThisSet = inSet(tab)
  useEffect(() => {
    if (!inThisSet || tabDisabled(tab)) {
      const firstUsable = TABS.find((t) => !tabDisabled(t.id))
      setTab(firstUsable ? firstUsable.id : 'new')
    }
  }, [tab, inThisSet, tabDisabled, TABS, setTab])

  // On a fresh/empty app, land on the New Project page so there is always a
  // clear next step.
  useEffect(() => {
    if (isEmpty && tab !== 'new') {
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
            kind={draftKind}
            name={draftName}
            description={draftDescription}
            onKindChange={setDraftKind}
            onNameChange={setDraftName}
            onDescriptionChange={setDraftDescription}
            onCreated={async (id, newKind) => {
              // Clear the draft now that it has become a real project.
              setDraftKind(null); setDraftName(''); setDraftDescription('')
              await refreshProjects()
              setActiveId(id)
              // Land on the first step after New Project for the chosen kind:
              // Ingestion for correction, Golden Schema for json_alignment.
              setTab(newKind === 'json_alignment' ? 'golden' : 'pipeline')
            }}
          />
        </div>
      )}

      {tab === 'correction' && inSet('correction') && (
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
          {tab === 'pipeline' && inSet('pipeline') && (
            <div id="panel-pipeline" role="tabpanel" aria-labelledby="tab-pipeline">
              <IngestionBoard
                projectId={activeId}
                projects={projects}
                documents={documents}
                readiness={readiness}
                onChange={reload}
                onInspect={(id) => setInspecting(id)}
              />
              <DocumentViewer
                projectId={activeId}
                documentId={inspecting}
                onClose={() => setInspecting(null)}
              />
            </div>
          )}

          {tab === 'supplementals' && inSet('supplementals') && (
            <div id="panel-supplementals" role="tabpanel" aria-labelledby="tab-supplementals">
              <SupplementalsPanel
                projectId={activeId}
                supplementals={supplementals}
                documents={documents}
                onChange={reload}
              />
            </div>
          )}

          {tab === 'golden' && inSet('golden') && project && (
            <div id="panel-golden" role="tabpanel" aria-labelledby="tab-golden">
              <GoldenSchemaPanel
                project={project}
                onProjectChange={() => void refreshProjects()}
                onConfigured={setGoldenConfigured}
              />
            </div>
          )}

          {tab === 'convert' && inSet('convert') && project && (
            <div id="panel-convert" role="tabpanel" aria-labelledby="tab-convert">
              <BatchConvertPanel project={project} />
            </div>
          )}

          {tab === 'search' && inSet('search') && (
            <div id="panel-search" role="tabpanel" aria-labelledby="tab-search">
              <SearchPanel projectId={activeId} />
            </div>
          )}

          {tab === 'report' && (
            <div id="panel-report" role="tabpanel" aria-labelledby="tab-report">
              {isJson && project
                ? <JsonReportPanel project={project} />
                : <ReportPanel projectId={activeId} />}
            </div>
          )}
        </>
      ) : (
        tab !== 'new' && tab !== 'correction' && !error && (
          <div className="panel" role="status">
            <strong>{isEmpty ? 'No projects yet' : 'No project selected'}</strong>
            <p className="small muted">
              {isEmpty
                ? 'Open the New Project tab to create a project, then add documents on the Ingestion tab.'
                : 'Pick a project from the selector above to use this view.'}
            </p>
          </div>
        )
      )}

      {TABS.some((t) => t.id === tab) && (
        <StepNav
          steps={TABS}
          current={tab}
          disabled={tabDisabled}
          reason={tabReason}
          onGo={setTab}
        />
      )}

      {diagnosticsEnabled && (
        <footer className="app-footer small muted">
          <a href="/diagnostics">Diagnostics</a>
        </footer>
      )}
    </div>
  )
}

/** A simple, elegant step navigator pinned under the workflow: Back / Next
 *  through the ordered steps, each enabled only when its gate is clear. */
function StepNav({ steps, current, disabled, reason, onGo }: {
  steps: TabDef[]
  current: Tab
  disabled: (id: Tab) => boolean
  reason: (id: Tab) => string
  onGo: (id: Tab) => void
}) {
  const i = steps.findIndex((s) => s.id === current)
  if (i < 0) return null
  const prev = i > 0 ? steps[i - 1] : null
  const next = i < steps.length - 1 ? steps[i + 1] : null
  const prevBlocked = prev ? disabled(prev.id) : true
  const nextBlocked = next ? disabled(next.id) : true

  return (
    <nav className="step-nav" aria-label="Workflow steps">
      <button
        className="btn secondary"
        disabled={!prev || prevBlocked}
        title={prev && prevBlocked ? reason(prev.id) : undefined}
        onClick={() => prev && !prevBlocked && onGo(prev.id)}
      >
        ← {prev ? prev.label : 'Back'}
      </button>

      <div className="step-nav-dots" role="list">
        {steps.map((s, idx) => (
          <span
            key={s.id}
            role="listitem"
            className={`step-dot ${idx === i ? 'current' : ''}${disabled(s.id) ? ' disabled' : ''}`}
            title={s.label}
            aria-label={`Step ${idx + 1}: ${s.label}${idx === i ? ' (current)' : ''}`}
          />
        ))}
        <span className="muted small" style={{ marginLeft: 8 }}>
          Step {i + 1} of {steps.length}: {steps[i].label}
        </span>
      </div>

      <button
        className="btn"
        disabled={!next || nextBlocked}
        title={next && nextBlocked ? reason(next.id) : undefined}
        onClick={() => next && !nextBlocked && onGo(next.id)}
      >
        {next ? next.label : 'Done'} →
      </button>
    </nav>
  )
}
