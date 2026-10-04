import { useState } from 'react'
import { CanonicalViewer } from './components/CanonicalViewer'
import { CorrectionPipeline } from './components/CorrectionPipeline'
import { PipelineBoard } from './components/PipelineBoard'
import { ReportPanel } from './components/ReportPanel'
import { SearchPanel } from './components/SearchPanel'
import { SupplementalsPanel } from './components/SupplementalsPanel'
import { ProjectProvider, useProject } from './hooks/useProject'

type Tab = 'correction' | 'pipeline' | 'supplementals' | 'search' | 'report'

const TABS: { id: Tab; label: string }[] = [
  { id: 'correction', label: 'Correction Pipeline' },
  { id: 'pipeline', label: 'Ingestion' },
  { id: 'supplementals', label: 'Supplementals' },
  { id: 'search', label: 'Search' },
  { id: 'report', label: 'Report & Export' },
]

export default function App() {
  return (
    <ProjectProvider>
      <AppShell />
    </ProjectProvider>
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
  } = useProject()
  const [tab, setTab] = useState<Tab>('correction')
  const [inspecting, setInspecting] = useState<string | null>(null)

  const reload = () => activeId && void refresh(activeId)

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
          >
            {projects.length === 0 && <option value="">No projects yet</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
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
          Pick a <b>project</b> above, then use the tabs: Correction Pipeline for
          the worked reconciliation, or Ingestion to add your own documents.
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
        {TABS.map((t) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={tab === t.id ? `panel-${t.id}` : undefined}
            tabIndex={tab === t.id ? 0 : -1}
            className={`tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
              e.preventDefault()
              const i = TABS.findIndex((x) => x.id === t.id)
              const next =
                e.key === 'ArrowRight'
                  ? TABS[(i + 1) % TABS.length]
                  : TABS[(i - 1 + TABS.length) % TABS.length]
              setTab(next.id)
              document.getElementById(`tab-${next.id}`)?.focus()
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

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
        tab !== 'correction' && !error && (
          <div className="panel" role="status">
            <strong>No project selected</strong>
            <p className="small muted">
              Pick a project from the selector above (or create one on the
              Correction Pipeline tab) to use this view.
            </p>
          </div>
        )
      )}
    </div>
  )
}
