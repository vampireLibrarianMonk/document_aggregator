# Software Bill of Materials (SBOM)

**Standard:** [CycloneDX 1.5](https://cyclonedx.org/) (OWASP). This document is
the authoritative, human-reviewed SBOM for the Document Aggregation Platform. It
enumerates the project's **direct** dependencies with their pinned versions,
licenses, and purpose.

**Machine-readable companion:** a complete CycloneDX 1.5 JSON SBOM — including
all transitive dependencies resolved in the current environment — is generated
on request:

```bash
python scripts/generate_sbom.py            # -> sbom.cdx.json  (CycloneDX 1.5 JSON)
python scripts/generate_sbom.py --print    # to stdout
```

The generated artifact reflects what is actually installed (backend via
`importlib.metadata`, frontend via `package-lock.json`); this document reflects
what the project **intentionally depends on**. When they disagree, the generated
file is the source of truth for transitive closure and this file is the source
of truth for intent and scope.

| Field | Value |
|-------|-------|
| Application | `document-aggregation-platform` |
| Version | see [`VERSION`](VERSION) (0.6.0 at last revision) |
| SBOM format | CycloneDX 1.5 |
| Primary language(s) | Python 3.10+ (backend), TypeScript/React (frontend) |
| License posture | permissive OSS only (MIT / BSD / Apache-2.0 / PSF / MPL-2.0); no copyleft runtime deps |
| Runtime network egress | none by default (offline/air-gap posture); Bedrock tier opt-in only |

---

## Backend — runtime (production) dependencies

Pinned in `backend/requirements-api.txt` (the api + worker share this set; the
worker Dockerfile additionally installs CPU `torch`).

| Component | Version | License | Purpose |
|-----------|---------|---------|---------|
| fastapi | 0.153.0 | MIT | HTTP API framework |
| starlette | 1.7.0 | BSD-3-Clause | ASGI toolkit (fastapi dep; pinned explicitly for CVE floor) |
| uvicorn[standard] | 0.30.6 | BSD-3-Clause | ASGI server |
| pydantic | 2.9.2 | MIT | models / validation |
| python-multipart | 0.0.32 | Apache-2.0 | multipart form parsing (uploads) |
| python-docx | 1.1.2 | MIT | DOCX parse/build |
| python-pptx | 1.0.2 | MIT | PPTX parse/build |
| pypdf | 6.21.1 | BSD-3-Clause | PDF parsing |
| reportlab | 4.2.2 | BSD-3-Clause | PDF export |
| Pillow | ≥10.4.0 | MIT-CMU (HPND) | image handling |
| pdfplumber | 0.11.10 | MIT | PDF geometry extraction (layout tier) |
| PyMuPDF | 1.24.10 | AGPL-3.0 / commercial | PDF rasterization for OCR — **see note** |
| pytesseract | 0.3.13 | Apache-2.0 | OCR wrapper (needs system `tesseract`) |
| sentence-transformers | ≥3.0,<7.0 | Apache-2.0 | local MiniLM embeddings |
| torch (worker image) | CPU build | BSD-3-Clause | sentence-transformers backend |
| boto3 | 1.43.104 | Apache-2.0 | optional AWS Bedrock tier (opt-in) |

> **PyMuPDF license note.** PyMuPDF is AGPL-3.0 (or a commercial license). It is
> used only for the **optional** OCR rasterization path (`OCR_ENABLED`), is
> gated at runtime, and the platform degrades gracefully without it. Deployments
> that cannot accept AGPL should disable OCR and may omit PyMuPDF; nothing in the
> core pipeline depends on it. This is the only non-permissive dependency and it
> is optional.

### Backend — optional / dev-only

| Component | Version | License | Scope |
|-----------|---------|---------|-------|
| rapidfuzz | 3.10.1 | MIT | **bench only** (`requirements-bench.txt`); C++/SIMD comparison variants in `alpha_loop/perf/`. Not imported by the app. |
| pytest | 8.3.5 | MIT | tests |
| ruff, mypy, pre-commit, gitleaks | (dev) | MIT / various permissive | lint / type / hooks |

## Frontend — runtime dependencies

From `frontend/package.json` (`dependencies`); resolved versions in
`package-lock.json`.

| Component | Version | License | Purpose |
|-----------|---------|---------|---------|
| react | 18.3.1 | MIT | UI |
| react-dom | 18.3.1 | MIT | UI DOM renderer |

### Frontend — dev / test-only (not shipped in the built app)

| Component | Version | License |
|-----------|---------|---------|
| vite | 5.4.x | MIT |
| typescript | 5.6.x | Apache-2.0 |
| @vitejs/plugin-react | 4.x | MIT |
| vitest | 2.1.9 | MIT |
| @testing-library/react, /dom | 16.1.0, 10.4.0 | MIT |
| @playwright/test, playwright | 1.63.0 | Apache-2.0 |
| @axe-core/playwright | 4.13.0 | MPL-2.0 (a11y audit, dev-only) |
| oxlint | 1.x | MIT |
| jsdom | 25.0.1 | MIT |
| @types/* | — | MIT |

## System / runtime (not Python/npm packages)

| Component | Where | License | Notes |
|-----------|-------|---------|-------|
| tesseract-ocr | worker image / optional local | Apache-2.0 | OCR engine; gated by `OCR_ENABLED` |
| LibreOffice (headless) | worker image / optional local | MPL-2.0 | DOCX→PDF for the geometry tier; gated on `soffice` presence |
| Liberation / Carlito / Caladea fonts | worker image | SIL OFL / Apache-2.0 | metric-compatible fonts for reproducible geometry |
| Red Hat UBI (python-312, nodejs-20, nginx-124) | enclave images | UBI EULA (freely redistributable) | base images — see `deploy/AIRGAP_RHEL.md` |

## Scope & provenance notes

- **No runtime network egress by default.** The stack runs fully offline; the
  only outbound path is the opt-in Bedrock tier (`BEDROCK_ENABLED`, off by
  default), which is a no-op when disabled.
- **Embedding weights** (MiniLM, Apache-2.0) are baked into the worker/api images
  at build time; no download at runtime.
- **Sample fact pools** (`corpus/fact_pool.json`) are paraphrased
  non-copyrightable facts from public-domain sources (NTSB/CISA/FDA/CPSC/NHTSA),
  committed as data and read offline.
- **MaDI-Bench** external benchmark data is **never** committed (see
  `.gitignore`); it is obtained separately by the developer.

## Regeneration & verification

```bash
# regenerate the machine-readable SBOM (deterministic; diff-friendly)
python scripts/generate_sbom.py
# the generated sbom.cdx.json validates against the CycloneDX 1.5 schema
```

## Vulnerability scanning

The pre-commit config (`.pre-commit-config.yaml`) scans these vectors on commit:

| Vector | Tool (hook) |
|--------|-------------|
| Secrets | gitleaks + detect-private-key |
| Python code security | bandit |
| Python dependency CVEs | pip-audit (`requirements-api.txt`) |
| Dockerfile hardening | hadolint |
| Frontend production-dep CVEs | `npm audit --omit=dev` |
| Lint / type / format | ruff, mypy, oxlint |

Run on demand (too heavy for a hook):

```bash
# container image CVEs (OS + baked packages)
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image --severity HIGH,CRITICAL document_aggregator-api:latest
```

Last full scan (0.6.0): production Python + frontend deps report **no known
vulnerabilities** after upgrading fastapi/starlette/pypdf/python-multipart off
CVE-affected versions; bandit reports **0 high/medium** (the sole high, an
MD5 bucket hash in the deterministic embedder, is marked `usedforsecurity=False`).
Dev-only frontend toolchain advisories (vite/vitest) do not reach the built app.

Review this document whenever a direct dependency is added, removed, or version-
bumped; regenerate `sbom.cdx.json` for the full transitive closure.
