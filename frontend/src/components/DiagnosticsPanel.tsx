import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import type { Diagnostics, ServiceState } from '../api/types'

/** Human label + accessible word for each state. Color is NEVER the only
 *  signal: every indicator carries this text too. */
const STATE_META: Record<ServiceState, { label: string; cls: string }> = {
  ok: { label: 'OK', cls: 'ok' },
  degraded: { label: 'Degraded', cls: 'warn' },
  error: { label: 'Error', cls: 'err' },
  offline: { label: 'Offline', cls: 'muted' },
}

function Indicator({ state }: { state: ServiceState }) {
  const m = STATE_META[state]
  return (
    <span className={`status-tag ${m.cls}`} aria-label={`status: ${m.label}`}>
      {m.label}
    </span>
  )
}

/** Scrollable list box of approved foundation models, one per line, with the
 *  default/recommended ones tagged. Keyboard-focusable so the region is
 *  reachable and scrollable without a mouse. */
function ModelList({ models }: { models: { id: string; name: string; family: string; kind?: string; is_default: boolean; recommended?: boolean }[] }) {
  if (!models.length) {
    return <span className="muted">none (enable Bedrock to list approved models)</span>
  }
  return (
    <ul
      className="diag-model-list"
      tabIndex={0}
      aria-label={`${models.length} approved models`}
      style={{
        margin: 0,
        padding: '4px 0',
        listStyle: 'none',
        maxHeight: 160,
        overflowY: 'auto',
        border: '1px solid var(--border, #ccc)',
        borderRadius: 4,
      }}
    >
      {models.map((m) => (
        <li key={m.id} className="row" style={{ gap: 6, padding: '2px 8px', alignItems: 'baseline' }}>
          <span>{m.name}</span>
          {m.recommended && <span className="status-tag ok" aria-label="recommended">recommended</span>}
          {m.is_default && !m.recommended && <span className="status-tag muted">default</span>}
          {m.kind === 'inference_profile' && (
            <span className="muted small" title="cross-region inference profile">· profile</span>
          )}
        </li>
      ))}
    </ul>
  )
}

interface Row {
  key: string
  label: string
  /** Plain-text value (default). Ignored when `node` is provided. */
  value?: string
  /** Custom cell content (e.g. a list box) rendered instead of `value`. */
  node?: ReactNode
}

/**
 * Live service diagnostics. Shows, in plain terms, what the pipeline can
 * actually do right now so silent degradations are visible: which embedding
 * model is in use (real vs fallback), whether OCR and LibreOffice are present,
 * the Bedrock posture, versions, and the offline guards.
 */
export function DiagnosticsPanel() {
  const [diag, setDiag] = useState<Diagnostics | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setErr(null)
    api
      .getDiagnostics()
      .then(setDiag)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const svc = diag?.services

  const sections: { title: string; state: ServiceState; rows: Row[] }[] = svc
    ? [
        {
          title: 'Embeddings (semantic search)',
          state: svc.embedding.state,
          rows: [
            { key: 'provider', label: 'Provider', value: svc.embedding.is_real_model ? 'Real model' : svc.embedding.provider },
            { key: 'model', label: 'Model', value: svc.embedding.model ?? '—' },
            { key: 'dim', label: 'Dimensions', value: svc.embedding.dimensions?.toString() ?? '—' },
            { key: 'cfg', label: 'Configured backend', value: svc.embedding.configured_backend },
            ...(svc.embedding.detail ? [{ key: 'd', label: 'Detail', value: svc.embedding.detail }] : []),
          ],
        },
        {
          title: 'OCR (scanned PDFs and images)',
          state: svc.ocr.state,
          rows: [
            { key: 'en', label: 'Enabled', value: svc.ocr.enabled ? 'yes' : 'no' },
            { key: 'te', label: 'Text engine', value: svc.ocr.text_engine ?? 'none installed' },
            { key: 'ra', label: 'PDF rasterizer', value: svc.ocr.pdf_rasterizer ?? 'none installed' },
            { key: 'la', label: 'Language', value: svc.ocr.lang },
          ],
        },
        {
          title: 'Layout geometry tier (LibreOffice)',
          state: svc.geometry.state,
          rows: [
            { key: 'so', label: 'LibreOffice', value: svc.geometry.soffice_available ? 'present' : 'absent' },
            { key: 'no', label: 'Effect', value: svc.geometry.note },
          ],
        },
        {
          title: 'Bedrock (optional model tier)',
          state: svc.bedrock.state,
          rows: [
            { key: 'en', label: 'Enabled', value: svc.bedrock.enabled ? 'yes' : 'no (disabled by config)' },
            { key: 'av', label: 'Reachable', value: svc.bedrock.available ? 'yes' : 'no' },
            {
              key: 'us',
              label: 'Model tier in use',
              value: svc.bedrock.enabled && svc.bedrock.available
                ? 'yes (live Bedrock model)'
                : 'no (deterministic/offline tier)',
            },
            {
              key: 'orch',
              label: 'Orchestrators',
              // The coordinator (corrections) and governor (project generation)
              // run on EVERY request regardless of Bedrock; Bedrock only swaps
              // the model tier they drive. Make that explicit so a disabled
              // Bedrock never reads as "nothing is orchestrating".
              value: svc.bedrock.enabled && svc.bedrock.available
                ? 'Correction Orchestrator + governor active (Bedrock model tier)'
                : 'Correction Orchestrator + governor active (deterministic tier)',
            },
            { key: 'rg', label: 'Region', value: svc.bedrock.region },
            {
              key: 'mo',
              label: `Approved models${svc.bedrock.models.length ? ` (${svc.bedrock.models.length})` : ''}`,
              node: <ModelList models={svc.bedrock.models} />,
            },
          ],
        },
      ]
    : []

  return (
    <div className="panel">
      <div className="row">
        <strong>Diagnostics</strong>
        {diag && (
          <>
            <span className="muted small">overall</span>
            <Indicator state={diag.overall} />
          </>
        )}
        <div className="spacer" />
        <button className="btn secondary small" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <p className="small muted" style={{ marginTop: 4 }}>
        What the pipeline can actually do right now. This runs fully offline;
        anything marked Degraded still works but on a reduced path.
      </p>

      <div role="status" aria-live="polite">
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
        {!diag && !err && <p className="small muted">Loading diagnostics…</p>}
      </div>

      {sections.map((s) => (
        <div key={s.title} className="panel" style={{ marginTop: 10 }}>
          <div className="row">
            <strong className="small">{s.title}</strong>
            <div className="spacer" />
            <Indicator state={s.state} />
          </div>
          <dl className="diag-rows small" style={{ margin: '8px 0 0' }}>
            {s.rows.map((r) => (
              <div key={r.key} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                <dt className="muted" style={{ minWidth: 160 }}>{r.label}</dt>
                <dd style={{ margin: 0, flex: 1 }}>{r.node ?? r.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}

      {diag && (
        <div className="small muted" style={{ marginTop: 10 }}>
          Pipeline v{diag.versions.pipeline} · schema v{diag.versions.schema} ·
          offline guard: HF {diag.offline_guard.hf_hub_offline ? 'on' : 'off'},
          Transformers {diag.offline_guard.transformers_offline ? 'on' : 'off'} ·
          data dir <span className="mono">{diag.data_dir}</span>
        </div>
      )}
    </div>
  )
}
