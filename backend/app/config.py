"""Central configuration for the document aggregation platform.

Local-first defaults. Everything runs offline on CPU with no proprietary
services. Cloud swaps (S3 / OpenSearch / hosted embeddings) would be adapters
behind the same interfaces, but are intentionally not required here.
"""
from __future__ import annotations

import os
from pathlib import Path

# Fallback used only if the repo-root VERSION file is missing (should not happen
# in a correct build; the image copies it in). Keep in sync with VERSION as a
# last resort.
_VERSION_FALLBACK = "0.3.0"


def _read_version() -> str:
    """Single source of truth for the app version: the repo-root VERSION file.

    config.py lives at backend/app/config.py, so parents[2] is the repo root
    (and /app in the container image, where VERSION is copied alongside
    backend/). Falls back to a constant if the file is absent or unreadable so
    diagnostics never crashes on a malformed deployment.
    """
    try:
        text = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
        return text or _VERSION_FALLBACK
    except OSError:
        return _VERSION_FALLBACK


class Settings:
    APP_ENV: str = os.getenv("APP_ENV", "development")

    # Data root: immutable originals, canonical docs, artifacts, indexes.
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))

    # Feature flags mirroring the reference spec. Baseline works with all off.
    OCR_ENABLED: bool = os.getenv("OCR_ENABLED", "false").lower() == "true"
    # OCR language(s) for tesseract (e.g. "eng", "eng+fra"). Only used when OCR
    # is enabled AND an engine is installed; otherwise ignored.
    OCR_LANG: str = os.getenv("OCR_LANG", "eng")
    # Max PDF pages to OCR per document (bounds cost/time on huge scans).
    OCR_MAX_PAGES: int = int(os.getenv("OCR_MAX_PAGES", "20"))
    VISION_ENABLED: bool = os.getenv("VISION_ENABLED", "false").lower() == "true"
    LLM_ENABLED: bool = os.getenv("LLM_ENABLED", "false").lower() == "true"
    EMBEDDINGS_ENABLED: bool = os.getenv("EMBEDDINGS_ENABLED", "true").lower() == "true"
    # In-app sample-case instantiation. OFF by default: samples are meant to be
    # run from the repo per the user guide. When enabled, a separate Samples
    # page lets a user instantiate a bundled case; it never appears on the main
    # New Project page.
    SAMPLES_ENABLED: bool = os.getenv("SAMPLES_ENABLED", "false").lower() == "true"
    # In-app Diagnostics page. OFF by default. When enabled, a separate
    # /diagnostics page exposes live service status (embeddings, OCR,
    # LibreOffice, Bedrock, versions).
    DIAGNOSTICS_ENABLED: bool = os.getenv("DIAGNOSTICS_ENABLED", "false").lower() == "true"

    # Embedding backend selection. Both run locally / offline:
    #   "sentence-transformers" -> real open-weight model (default when installed)
    #   "hashing"               -> zero-dependency deterministic fallback
    #   "auto"                  -> sentence-transformers if importable, else hashing
    # Swappable behind the EmbeddingProvider interface; no model is load-bearing.
    EMBEDDING_BACKEND: str = os.getenv("EMBEDDING_BACKEND", "auto")

    # Real local model (Apache-2.0, CPU-friendly, air-gap after first pull).
    EMBEDDING_ST_MODEL: str = os.getenv("EMBEDDING_ST_MODEL", "all-MiniLM-L6-v2")

    # Hashing fallback identity.
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "local-hashing-embedder")
    EMBEDDING_REVISION: str = os.getenv("EMBEDDING_REVISION", "v1")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "256"))

    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))

    # Optional Bedrock interpreter: turns freeform feedback into constrained
    # correction operations via tool-use. Off by default; the pipeline works
    # fully without it (structured ops path). Never writes final state.
    BEDROCK_ENABLED: bool = os.getenv("BEDROCK_ENABLED", "false").lower() == "true"
    BEDROCK_REGION: str = os.getenv("BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    # Use a cross-region inference profile ID (required for on-demand newer models).
    BEDROCK_MODEL: str = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

    # Project-generation model: separate from the feedback-interpreter model so
    # the two roles can use different approved models. Must be on the allowlist.
    BEDROCK_SCENARIO_MODEL: str = os.getenv(
        "BEDROCK_SCENARIO_MODEL", os.getenv("BEDROCK_MODEL", ""))
    # Approved models for project generation (comma-separated model ids or
    # substrings). Only Nemotron and GPT-OSS families are approved by default;
    # the generator refuses any model id not matching the allowlist.
    BEDROCK_SCENARIO_MODEL_ALLOWLIST: str = os.getenv(
        "BEDROCK_SCENARIO_MODEL_ALLOWLIST", "nemotron,gpt-oss")

    # Tracks the repo-root VERSION file so the Diagnostics footer, the app
    # version, and the changelog can never disagree. SCHEMA_VERSION is the
    # reconcile-output contract version and is bumped independently.
    PIPELINE_VERSION: str = os.getenv("PIPELINE_VERSION", _read_version())
    SCHEMA_VERSION: str = "1.0"

    # Single source of truth for the project storage root. Previously this path
    # was recomputed independently in project.py, projectgen/persist.py, and
    # knowledge/factpool.py (with different parents[] indices), which risked
    # drift; they now all import this. Repo-relative (parents[2] = repo root
    # from backend/app/config.py).
    # Legacy bundle of the committed demo projects (read-only fallback).
    BUNDLED_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2] / "sample_docs" / "project"

    def template_dir(self, case_id: str) -> Path:
        """Source directory of a bundled sample case (read-only repo fixture)."""
        return self.BUNDLED_PROJECT_ROOT / case_id

    def project_dir(self, project_id: str) -> Path:
        return self.DATA_DIR / "projects" / project_id

    def project_data_dir(self, project_id: str) -> Path:
        """Where a project's correction data (corpus/template/draft/corrections)
        lives inside its project dir in the unified store."""
        return self.DATA_DIR / "projects" / project_id / "data"

    def resolve_project_data_dir(self, project_id: str) -> Path:
        """Locate a project's correction-data directory, preferring the unified
        store and falling back to the committed demo bundle, so the demo projects
        keep working. Returns the store path for a brand-new id so writers land
        in the new home."""
        store_dir = self.project_data_dir(project_id)
        if (store_dir / "project.json").exists():
            return store_dir
        legacy = self.BUNDLED_PROJECT_ROOT / project_id
        if (legacy / "project.json").exists():
            return legacy
        return store_dir  # new project -> write under the store

    def iter_project_data_dirs(self):
        """Yield (project_id, dir) for every project in either store, the unified
        store taking precedence over a demo bundle of the same id."""
        seen: set[str] = set()
        proj_root = self.DATA_DIR / "projects"
        if proj_root.exists():
            for pdir in sorted(proj_root.iterdir()):
                ddir = pdir / "data"
                if (ddir / "project.json").exists():
                    seen.add(pdir.name)
                    yield pdir.name, ddir
        if self.BUNDLED_PROJECT_ROOT.exists():
            for d in sorted(self.BUNDLED_PROJECT_ROOT.iterdir()):
                if d.name in seen:
                    continue
                if (d / "project.json").exists():
                    yield d.name, d

    def ensure_dirs(self) -> None:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (self.DATA_DIR / "projects").mkdir(parents=True, exist_ok=True)


settings = Settings()
