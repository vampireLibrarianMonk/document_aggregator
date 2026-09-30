import { useState } from 'react'
import { CanonicalViewer } from './components/CanonicalViewer'
import { CorrectionPipeline } from './components/CorrectionPipeline'
import { PipelineBoard } from './components/PipelineBoard'
import { ReportPanel } from './components/ReportPanel'
import { SearchPanel } from './components/SearchPanel'
import { SupplementalsPanel } from './components/SupplementalsPanel'
import { useProject } from './hooks/useProject'

type Tab = 'correction' | 'pipeline' | 'supplementals' | 'search' | 'report'

const TABS: { id: Tab; label: string }[] = [
  { id: 'correction', label: 'Correction Pipeline' },
  { id: 'pipeline', label: 'Ingestion' },
  { id: 'supplementals', label: 'Supplementals' },
  { id: 'search', label: 'Search' },
  { id: 'report', label: 'Report & Export' },
]

export default function App() {
  const { project, documents, supplementals, error, refresh } = useProject()
  const [tab, setTab] = useState<Tab>('correction')
  const [inspecting, setInspecting] = useState<string | null>(null)

  const reload = () => project && void refresh(project.id)

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Document Aggregation Pipeline</h1>
          <div className="subtitle">
            Turn a messy first-draft report into a clean, corrected report —
            and flag where it doesn&apos;t follow the rules.
          </div>
        </div>
        <div className="small muted">
          {project ? (
            <>
              <div>{project.name}</div>
              <div className="mono">{project.id}</div>
            </>
          ) : (
            'Loading…'
          )}
        </div>
      </header>

      <div className="panel intro">
        <strong>What this does</strong>
        <p className="small">
          You give it the source material (the <b>corpus</b>) plus a first
          attempt at a report and any reviewer comments. It produces a{' '}
          <b>corrected report</b> — fixing wrong values, filling gaps, and
          flagging anything it can&apos;t resolve or that breaks the formatting
          rules. Nothing is invented: every change traces back to a source.
        </p>
        <p className="small muted">
          New here? Start on <b>Correction Pipeline</b> to see a worked example,
          or open <b>Ingestion</b> to upload your own documents.
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
            // Only reference the panel when it is actually in the DOM: a panel
            // renders only for the active tab, and the project-gated tabs
            // render nothing until a project resolves. A dangling aria-controls
            // is itself an a11y violation.
            aria-controls={
              tab === t.id && (t.id === 'correction' || project)
                ? `panel-${t.id}`
                : undefined
            }
            tabIndex={tab === t.id ? 0 : -1}
            className={`tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
              e.preventDefault()
              // Move relative to THIS tab (the focused one), not the selected
              // tab — the ARIA tabs pattern is roving focus. Using the focused
              // tab's own index keeps arrow-nav correct even when focus and
              // selection differ.
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
          <CorrectionPipeline />
        </div>
      )}

      {project && (
        <>
          {tab === 'pipeline' && (
            <div id="panel-pipeline" role="tabpanel" aria-labelledby="tab-pipeline">
              <PipelineBoard
                projectId={project.id}
                documents={documents}
                onChange={reload}
                onInspect={(id) => {
                  setInspecting(id)
                }}
              />
              <CanonicalViewer projectId={project.id} documentId={inspecting} />
            </div>
          )}

          {tab === 'supplementals' && (
            <div id="panel-supplementals" role="tabpanel" aria-labelledby="tab-supplementals">
              <SupplementalsPanel
                projectId={project.id}
                supplementals={supplementals}
                documents={documents}
                onChange={reload}
              />
            </div>
          )}

          {tab === 'search' && (
            <div id="panel-search" role="tabpanel" aria-labelledby="tab-search">
              <SearchPanel projectId={project.id} />
            </div>
          )}

          {tab === 'report' && (
            <div id="panel-report" role="tabpanel" aria-labelledby="tab-report">
              <ReportPanel projectId={project.id} />
            </div>
          )}
        </>
      )}

      {/* Avoid a blank dead-end: these four tabs need a project. If none has
          resolved yet (and no error is already shown), say so instead of
          rendering nothing. */}
      {!project && tab !== 'correction' && !error && (
        <div className="panel" role="status">
          <strong>Connecting to your project…</strong>
          <p className="small muted">
            This view needs a project. If this doesn&apos;t clear, the backend
            may be unreachable, or you may need to seed the demo data
            (<span className="mono">python backend/seed.py</span>). The{' '}
            <b>Correction Pipeline</b> tab works without a project.
          </p>
        </div>
      )}
    </div>
  )
}
