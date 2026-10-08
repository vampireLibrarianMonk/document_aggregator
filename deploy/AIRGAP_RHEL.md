# Air-gapped RHEL/OpenShift deployment notes

The platform is designed to run fully offline in a government enclave on a RHEL
base. Nothing phones home at runtime. This note covers the pieces that need to
be mirrored/baked in ahead of time.

## Base image

Use the Red Hat Universal Base Image (UBI) — freely redistributable and
mirror-able into an internal registry without a RHEL subscription:

```
registry.access.redhat.com/ubi9/python-312   # api + worker
registry.access.redhat.com/ubi9/nodejs-20    # frontend build stage
registry.access.redhat.com/ubi9/nginx-124    # frontend runtime
```

Mirror these into the enclave's internal registry (e.g. Quay/Harbor) and
reference the internal paths in the Dockerfiles.

### Enclave build is not a limitation

The three images build against **stock public UBI** on any dev box (proven
locally), and against the **mirrored UBI** in the enclave. The open-source
binaries the worker wants (LibreOffice, the full font set) are approved OSS that
the enclave mirrors into its RHEL AppStream. So moving to the enclave *adds*
capability (LibreOffice becomes available); it removes nothing. Nothing about
the build is enclave-only.

### Modular images (one concern each)

- **api** (`Dockerfile.api`) — lean FastAPI. No LibreOffice, no embedding stack.
  ~1.7GB. Serves HTTP and enqueues jobs.
- **worker** (`Dockerfile.rhel`) — the only image carrying the heavy processing
  stack (LibreOffice + fonts + CPU-only torch/sentence-transformers). Runs the
  templated worker loop against the shared job queue.
- **frontend** (`Dockerfile.frontend`) — two-stage: nodejs-20 builds the React
  app, nginx-124 serves it and proxies `/api` to the api service.

Build with BuildKit (parallel stages, better caching):

```
DOCKER_BUILDKIT=1 docker compose build
```

## LibreOffice (vector-layout tier)

The DOCX -> PDF -> geometry inspection tier renders with LibreOffice headless.

- Installed via `dnf` (RHEL), not `apt`: `libreoffice-headless` + `writer`/`calc`.
- Runs headless; no display server required.
- Fully offline after install; LGPL/MPL licensing is enterprise/government-safe.
- **Optional at runtime**: if LibreOffice is absent, the pipeline degrades to
  structural inspection and simply skips the geometric tier. It is never a hard
  dependency.

**Build behavior (important):** stock public UBI's repos do NOT carry
LibreOffice, so the worker Dockerfile installs it **best-effort** — it attempts
the install and continues if the repo lacks it (the worker still runs, geometry
tier gated off). In the enclave, point `dnf` at the internal RPM mirror (full
RHEL AppStream) and LibreOffice installs normally, activating the geometry tier.
To require it (fail the build if `soffice` is missing — recommended for the
enclave image), build with `--build-arg REQUIRE_LIBREOFFICE=1`.

## Fonts (determinism)

LibreOffice renders using whatever fonts are installed and **substitutes
silently** when a referenced font is absent — which would make geometry
non-deterministic across environments. The pinned standard
(`app/discipline/profiles/gov_standard.json`) uses metric-compatible fonts so a
document written in a proprietary font renders to the SAME geometry. Bake in
EXACTLY this set:

- `liberation-sans-fonts` / `liberation-serif-fonts` / `liberation-mono-fonts`
  — metric-compatible with Arial / Times New Roman / Courier New (SIL OFL,
  redistributable)
- `google-carlito-fonts` — Carlito, metric-compatible with Calibri
- `google-crosextra-caladea-fonts` — Caladea, metric-compatible with Cambria

**Build behavior:** stock public UBI AppStream carries only a subset
(`liberation-mono-fonts`, `liberation-fonts-common`). The full Liberation set +
Carlito/Caladea come from the RHEL AppStream that the enclave mirrors. The
worker Dockerfile installs the available subset and treats the rest as
enclave-provided (best-effort, does not fail the build on stock UBI). Do not
rely on the proprietary Office fonts (they are not redistributable); the
metric-compatible set is what guarantees reproducible geometry. Mirror any
additional client-required font RPMs into the enclave. When
`REQUIRE_LIBREOFFICE=1` and the enclave mirror is in place, the full set installs
and geometry is deterministic.

## Python dependencies

Dependencies are split by concern so each image carries only what it needs:

- `backend/requirements-api.txt` — lean api set (FastAPI, doc parsers, layout).
  No torch, no sentence-transformers.
- `backend/requirements-worker.txt` — `-r requirements-api.txt` **plus**
  `sentence-transformers` (the embedding stack). Only the worker carries it.
- `backend/requirements.txt` — `-r requirements-worker.txt` (the full set for
  local dev/tests).

Vendor a wheelhouse and install offline (per image):

```
pip download -r backend/requirements-api.txt    -d wheels/   # api
pip download -r backend/requirements-worker.txt -d wheels/   # worker
# in the image:
pip install --no-index --find-links=/wheels -r backend/requirements-<api|worker>.txt
```

**CPU-only torch (worker):** the default `torch` wheel drags in ~2.5GB of
CUDA/cuDNN this CPU/air-gap worker never uses. The worker Dockerfile installs
the CPU wheel first:

```
pip install --index-url https://download.pytorch.org/whl/cpu torch
```

In the enclave, mirror the CPU torch wheels into the wheelhouse and install with
`--no-index --find-links=/wheels`.

## Local models

- Embeddings: the `sentence-transformers` model (`all-MiniLM-L6-v2`) must be
  pre-downloaded and baked into the image cache (or mounted), or the pipeline
  falls back to the zero-dependency hashing embedder. **No runtime model
  download** — the compose services set `HF_HUB_OFFLINE=1` and
  `TRANSFORMERS_OFFLINE=1` as a hard network guard, and default
  `EMBEDDING_BACKEND=hashing`. To use the real model in the enclave, bake/mount
  the weights and set `EMBEDDING_BACKEND=sentence-transformers`.
## Bedrock model tier (enclave-dependent)

Bedrock is an **optional refinement tier**, never required: the correction
coordinator and the project-generation governor always run on the deterministic
engine (the authoritative, reproducible, no-fabrication floor). Enabling Bedrock
only sharpens the natural-language steps and never writes final state — every
value it proposes passes a grounding gate first.

Two valid enclave postures:

- **Enclave WITHOUT Bedrock (strict offline):** leave `BEDROCK_ENABLED=false`
  (the default). The deterministic path is the whole system; nothing calls out.
- **Enclave WITH an in-enclave Bedrock endpoint:** set `BEDROCK_ENABLED=true`
  (plus `BEDROCK_REGION` and the mounted AWS config — see `.env.example`). The
  HF/Transformers offline guards stay on; the only outbound calls are to the
  enclave's own Bedrock service. Both the api and the worker honor the toggle
  (the worker runs the async reconcile/converge/batch path, so it must agree).
  Models are allowlist-gated to `nemotron,gpt-oss` by default; the tool-use and
  generation tiers both default to an allowlisted `gpt-oss` id so "enabled" is
  coherent out of the box.

Either way the deterministic result is always available as the floor, so a
Bedrock outage degrades cleanly rather than failing.

## OpenShift notes

- Runs as a non-root, arbitrary UID (group 0) — OpenShift-compatible.
- `DATA_DIR` should be a PersistentVolume; the container writable layer is not
  authoritative.
- No cluster-admin required; readiness/liveness on `/ready` and `/health`.
