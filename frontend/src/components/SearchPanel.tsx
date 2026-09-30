import { useState } from 'react'
import { api } from '../api/client'
import type { SearchResponse } from '../api/types'

/** Hybrid (lexical + vector) search over the project index, with provenance. */
export function SearchPanel({ projectId }: { projectId: string }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    if (!query.trim()) return
    setBusy(true)
    setErr(null)
    try {
      setResults(await api.search(projectId, query))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="row">
        <input
          style={{ flex: 1 }}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void run()}
          placeholder="Search the source documents by meaning or keyword…"
        />
        <button className="btn" disabled={busy} onClick={() => void run()}>
          {busy ? 'Searching…' : 'Search'}
        </button>
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>
        Combines keyword and meaning-based matching. Results trace back to the
        source document and section.
      </div>
      <div role="status" aria-live="polite">
        {busy && <span className="sr-only">Searching…</span>}
        {err && <p className="small" style={{ color: 'var(--err)' }}>{err}</p>}
        {results && results.hits.hits.length === 0 && (
          <p className="small muted" style={{ marginTop: 8 }}>No matches found.</p>
        )}
      </div>
      {results && results.hits.hits.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="muted small">{results.hits.total.value} results</div>
          {results.hits.hits.map((h) => (
            <div key={h._id} className="hit">
              {/* Lead with the matched text + where it's from. */}
              <div className="small">{h._source.text}</div>
              <div className="row small muted" style={{ marginTop: 2 }}>
                <span>{h._source.document_id}</span>
                {h._source.section_path.length > 0 && (
                  <span>· {h._source.section_path.join(' › ')}</span>
                )}
                <div className="spacer" />
                <span title={`match score ${h._score.toFixed(4)} (vector ${h._source.vector_score}, keyword ${h._source.lexical_score})`}>
                  relevance {(h._score * 100).toFixed(0)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
