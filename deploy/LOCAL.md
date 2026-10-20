# Local deployment (no Docker)

Run the full stack directly on your host with a Python virtualenv and the Vite
dev server. This is the fastest path for development and a capable offline run;
for the containerized stack see [`DOCKER.md`](DOCKER.md), and for the enclave see
[`AIRGAP_RHEL.md`](AIRGAP_RHEL.md).

Three processes make up a local run: the **API** (FastAPI/uvicorn), one or more
**workers** (the async job loop), and the **frontend** (Vite dev server, which
proxies `/api` to the API).

## Prerequisites

- Python 3.10+ and `pip`
- Node 18+ and `npm`
- (optional) `tesseract` + `libreoffice` on `PATH` for OCR and the DOCX→PDF
  geometry tier; both degrade gracefully when absent.

## 1. Backend dependency sets

Pick the set that matches what you need (all are in `backend/`):

| File | Contents | Use when |
|------|----------|----------|
| `requirements-api.txt` | FastAPI, parsers, exporters, embeddings, OCR | the standard full-capability local run |
| `requirements-worker.txt` | = api set (worker adds CPU torch via its Dockerfile) | parity with the worker image |
| `requirements.txt` | the full dev set (= worker set) | development + tests |
| `requirements-bench.txt` | `rapidfuzz` (optional) | only the perf bench's C++/SIMD variants |

```bash
# from the repo root
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-api.txt
# dev + tests:   -r backend/requirements.txt
# optional bench: -r backend/requirements-bench.txt   (NOT needed by the app)
```

## 2. Start the API

```bash
DATA_DIR="$PWD/.local_data" \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --app-dir backend
```

- `DATA_DIR` is the throwaway local store (jobs DB, projects, indexes). It is
  git-ignored (`.local_data/`).
- `GET http://localhost:8000/health` → `{"status":"ok"}`; `GET /projects` is
  empty on a fresh install.

### Port already in use?

If something else occupies `8000`, run the API on another port (e.g. `8001`)
and point the frontend proxy at it (see step 4).

## 3. Start a worker (required for batch / async jobs)

Heavy work (batch JSON→golden alignment, reconcile, geometry) runs as async
jobs that **only a worker drains**. Without a worker, a submitted batch stays at
"processing…" forever (the Workers & queue panel in the UI will say so).

```bash
# single worker, 1 thread (default)
DATA_DIR="$PWD/.local_data" .venv/bin/python backend/worker_main.py
```

Two independent scaling axes (measured — see the async regime notes):

| Env var | Meaning | Use for |
|---------|---------|---------|
| `WORKER_PROCESSES` | N worker **processes** (int, or `auto` = cgroup cores−1) | **CPU-bound** ops (reconcile/align) — scales across cores |
| `WORKER_CONCURRENCY` | threads **per** process | **I/O-bound** ops (GIL is released on I/O) |

```bash
# CPU throughput: 4 processes
DATA_DIR="$PWD/.local_data" WORKER_PROCESSES=4 .venv/bin/python backend/worker_main.py
# auto-size to the machine (cores − 1)
DATA_DIR="$PWD/.local_data" WORKER_PROCESSES=auto .venv/bin/python backend/worker_main.py
# I/O concurrency: 1 process, 4 threads
DATA_DIR="$PWD/.local_data" WORKER_CONCURRENCY=4 .venv/bin/python backend/worker_main.py
```

Rule of thumb: **CPU-bound → `WORKER_PROCESSES`; I/O-bound → `WORKER_CONCURRENCY`;
tiny jobs → batch them, don't fan out** (coordination overhead can exceed the
work). The worker prints its mode and the registered ops on start.

The API and worker must share the **same `DATA_DIR`** (they coordinate through
the SQLite job queue there).

## 4. Start the frontend

```bash
cd frontend
npm install
npm run dev            # Vite dev server on http://localhost:5173
```

Open `http://localhost:5173`. The dev server proxies `/api` → `http://localhost:8000`
by default. If the API is on a different port, set the proxy target:

```bash
VITE_API_TARGET="http://localhost:8001" npm run dev
```

## Configuration

Copy `.env.example` to `.env` and adjust. Notable flags (all optional; safe
offline defaults):

- `EMBEDDING_BACKEND` = `auto` | `sentence-transformers` | `hashing`
  (`hashing` is the zero-dependency fallback; `auto` uses MiniLM if available).
- `EMBEDDINGS_ENABLED`, `OCR_ENABLED`, `VISION_ENABLED`, `LLM_ENABLED`.
- `DIAGNOSTICS_ENABLED=true` exposes the `/diagnostics` page.
- `BEDROCK_ENABLED` (default off) — the optional model tier; offline it is a
  no-op and the deterministic result stands.

## Tests

```bash
# backend (from repo root)
.venv/bin/python -m pytest backend/tests -q
# opt-in gates (slow; alpha-loop ladder + async regimes)
.venv/bin/python -m pytest backend/tests -m opt_in

# frontend (from frontend/)
npm run test        # vitest unit
npm run build       # tsc + vite build
npm run audit:a11y  # on-demand accessibility gate (needs a built app)
npm run e2e         # on-demand Playwright suite (needs the Docker stack; see DOCKER.md)
```

## Stopping

The API and worker are foreground processes — `Ctrl-C` each. If you backgrounded
them, find and kill by name (`pkill -f "uvicorn app.main"`, `pkill -f worker_main`).
The local store persists in `DATA_DIR`; delete `.local_data/` to reset.
