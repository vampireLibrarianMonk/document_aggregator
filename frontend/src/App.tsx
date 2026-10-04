import { useState } from 'react'
import { CanonicalViewer } from './components/CanonicalViewer'
import { CorrectionPipeline } from './components/CorrectionPipeline'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { GenerateProject } from './components/GenerateProject'
import { PipelineBoard } from './components/PipelineBoard'
import { ReportPanel } from './components/ReportPanel'
import { SearchPanel } from './components/SearchPanel'
import { SupplementalsPanel } from './components/SupplementalsPanel'
import { TemplatePicker } from './components/TemplatePicker'
import { ProjectProvider, useProject } from './hooks/useProject'

type Tab = 'correction' | 'pipeline' | 'supplementals' | 'search' | 'report' | 'diagnostics'

const TABS: { id: Tab; label: string }[] = [
  { id: 'correction', label: 'Correction Pipeline' },
  { id: 'pipeline', label: 'Ingestion' },
  { id: 'supplementals', label: 'Supplementals' },
  { id: 'search', label: 'Search' },
  { id: 'report', label: 'Report & Export' },
  { id: 'diagnostics', label: 'Diagnostics' },
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
  const [showTemplates, setShowTemplates] = useState(false)
  const [showGenerate, setShowGenerate] = useState(false)

  const reload = () => activeId && void refresh(activeId)
  const isEmpty = projects.length === 0

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
            className="btn secondary small"
            aria-expanded={showTemplates}
            onClick={() => { setShowTemplates((v) => !v); setShowGenerate(false) }}
          >
            {showTemplates ? 'Hide samples' : 'Start from a sample'}
          </button>
          <button
            className="btn secondary small"
            aria-expanded={showGenerate}
            onClick={() => { setShowGenerate((v) => !v); setShowTemplates(false) }}
          >
            {showGenerate ? 'Hide generator' : 'New project'}
          </button>
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
          The app starts empty. Create a project three ways:{' '}
          <b>Start from a sample</b> to instantiate a complete worked case,{' '}
          <b>New project</b> to generate one from a brief or an uploaded
          document, or open <b>Ingestion</b> to upload documents into a project.
          Everything you see after that is a project you created.
        </p>
      </div>

      {/* The app starts empty: show the sample picker prominently when there
          are no projects (unless the user opened the generator instead), or on
          demand via the header toggles. */}
      {(showTemplates || (isEmpty && !showGenerate)) && (
        <TemplatePicker
          onInstantiated={() => {
            // instantiateTemplate already refreshed the list and selected the
            // new project; just close the picker and land on the pipeline.
            setShowTemplates(false)
            setTab('correction')
          }}
        />
      )}

      {showGenerate && (
        <GenerateProject
          onGenerated={async (id) => {
            await refreshProjects()   // pull the new project into the selector
            setActiveId(id)
            setShowGenerate(false)
            setTab('correction')
          }}
        />
      )}

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

      {tab === 'diagnostics' && (
        <div id="panel-diagnostics" role="tabpanel" aria-labelledby="tab-diagnostics">
          <DiagnosticsPanel />
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
        tab !== 'correction' && tab !== 'diagnostics' && !error && (
          <div className="panel" role="status">
            <strong>{isEmpty ? 'No projects yet' : 'No project selected'}</strong>
            <p className="small muted">
              {isEmpty
                ? 'Use "Start from a sample" above to instantiate a worked case, or open the Ingestion tab to upload your own documents.'
                : 'Pick a project from the selector above to use this view.'}
            </p>
          </div>
        )
      )}
    </div>
  )
}
