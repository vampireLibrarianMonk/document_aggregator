# Diagnostics page — audit

**Audited:** 2026-10-07, against the live Docker stack (api + worker images with
MiniLM, tesseract, pymupdf, and LibreOffice baked in).

Scope: confirm each service row on the Diagnostics page is (1) actually probed
from a real capability check, (2) rendered, and (3) accurate in the running
container. Compare `backend/app/diagnostics.py` `collect()` and
`frontend/src/components/DiagnosticsPanel.tsx` to the live `/diagnostics` output.

## Live snapshot (container)

```
overall: ok
embedding: ok    SentenceTransformerProvider · all-MiniLM-L6-v2 · 384 dims  (real)
ocr:       ok    tesseract · pymupdf · eng
geometry:  ok    LibreOffice present
bedrock:   ok    enabled=false · available=true · region us-east-1 · 8 models
versions:  pipeline 0.1.0 · schema 1.0
offline_guard: HF on, Transformers on
data_dir: /data
```

## Findings

### Accurate and well-probed (no change needed)

- **Embeddings** — real probe: `make_embedder()` is constructed and `dim`/`model`
  are read, so a missing/unloadable model would surface as `error`/`degraded`
  rather than silently falling back at ingest. Live: real MiniLM, 384 dims. ✅
- **OCR** — probes `ocr_status()` for the text engine (tesseract) + PDF
  rasterizer (pymupdf) + language. Live: both present. ✅
- **Geometry** — probes `soffice_available()`. Live: LibreOffice present → full
  geometry tier. ✅
- **Versions / offline-guard / data-dir footer** — all rendered and accurate. ✅
- **Refresh** — re-calls `/diagnostics`. ✅

### Finding D1 (bug): Bedrock `state` ignores `enabled`

`_bedrock_status()` sets `state = "ok" if available else "offline"`, where
`available` means "Bedrock was reachable and returned approved models." In the
container, the host `~/.aws` is mounted read-only, so even with
**`BEDROCK_ENABLED=false`** the probe reaches Bedrock, lists models, and reports
**`state: "ok"`**.

That is misleading: the operator explicitly turned Bedrock **off**, yet the
Diagnostics page shows it green/"OK" as though it were in use. The panel's
"Enabled: no" row is the only hint, and the top-line indicator contradicts it.

**Expected:** when `BEDROCK_ENABLED` is false, the state should read **offline**
(the normal air-gap posture), regardless of whether creds happen to be present —
because a disabled integration is not "OK and operating." The state should be
`ok` only when it is both **enabled AND available**.

### Finding D2 (minor): no explicit "disabled vs offline vs unavailable" wording

Tied to D1: the UI maps `offline` to the muted word "Offline", which is right,
but there is no distinction surfaced between "turned off by config" and "on but
unreachable." Low priority; the Enabled/Available rows already carry the detail.
Fix D1 and the top-line indicator becomes truthful; the row detail covers the
nuance.

### Finding D3 (cosmetic): empty default model

`bedrock.default` is `""` (no `BEDROCK_SCENARIO_MODEL` set). The panel lists the
approved models fine and does not misreport, so no change required; noted for
completeness.

## Outfitting decision

Fix **D1** in `diagnostics.py`: a disabled Bedrock reports `state: "offline"`
even when creds are present, so the page tells the truth about what is actually
operating. D2 is resolved by D1; D3 needs no change. The embeddings/OCR/geometry
probes and the footer are already correct and complete — no stubbed
functionality was found there.
