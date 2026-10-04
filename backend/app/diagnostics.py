"""Service diagnostics: a single honest snapshot of what the pipeline can
actually do right now, so silent degradations become visible.

Each probe reports the TRUTH about a capability (is the engine installed, does
the model actually load) rather than just what is configured. The frontend
Diagnostics page renders this with clear OK / degraded indicators.
"""
from __future__ import annotations

from .config import settings


def _embedding_status() -> dict:
    """What embedding backend is actually in use, and does it load? Probes the
    real provider so a configured-but-unloadable model shows as degraded rather
    than silently falling back at ingest time."""
    from .embeddings import HashingEmbeddingProvider, make_embedder

    configured = settings.EMBEDDING_BACKEND
    enabled = bool(settings.EMBEDDINGS_ENABLED)
    try:
        provider = make_embedder()
        kind = type(provider).__name__
        is_real = kind == "SentenceTransformerProvider"
        # Force a load so a missing/broken model surfaces here, not mid-ingest.
        dim = provider.dim
        model = provider.model
        loaded = True
        detail = ""
    except Exception as exc:  # model missing / offline cache absent / etc.
        provider = HashingEmbeddingProvider()
        kind = "unavailable"
        is_real = False
        dim = None
        model = None
        loaded = False
        detail = str(exc)[:200]

    # "ok" when embeddings are enabled and the real model loads; "degraded" when
    # running on the hashing fallback or disabled; "error" when nothing loads.
    if not enabled:
        state = "degraded"
    elif not loaded:
        state = "error"
    elif is_real:
        state = "ok"
    else:
        state = "degraded"  # hashing fallback is functional but weak on meaning

    return {
        "state": state,
        "enabled": enabled,
        "configured_backend": configured,
        "provider": kind,
        "is_real_model": is_real,
        "model": model,
        "dimensions": dim,
        "detail": detail,
    }


def _ocr_status() -> dict:
    from .ocr import ocr_status

    st = ocr_status()
    if not st["enabled"]:
        state = "degraded"       # off by config
    elif st["available"]:
        state = "ok"
    else:
        state = "error"          # enabled but no engine installed
    st["state"] = state
    return st


def _geometry_status() -> dict:
    """The DOCX->PDF vector-layout / discipline-geometry tier needs LibreOffice.
    Without it the pipeline still runs (structural inspection only)."""
    from .layout.render import soffice_available

    ok = soffice_available()
    return {
        "state": "ok" if ok else "degraded",
        "soffice_available": ok,
        "note": ("LibreOffice present: full geometry/layout checks run."
                 if ok else
                 "LibreOffice absent: structural checks only (geometry tier skipped)."),
    }


def _bedrock_status() -> dict:
    """Optional Bedrock project-generation path. Off by default (air-gap);
    offline generation always works, so 'unavailable' is NOT an error."""
    try:
        from .projectgen.bedrock_gen import list_approved_models

        info = list_approved_models()
    except Exception as exc:
        info = {"available": False, "models": [], "detail": str(exc)[:200]}

    available = bool(info.get("available"))
    return {
        # Bedrock being off is a normal air-gap posture, not a fault.
        "state": "ok" if available else "offline",
        "enabled": bool(settings.BEDROCK_ENABLED),
        "available": available,
        "region": settings.BEDROCK_REGION,
        "models": info.get("models", []),
        "default": info.get("default"),
        "allowlist": info.get("allowlist"),
    }


def collect() -> dict:
    """Full diagnostics snapshot."""
    embedding = _embedding_status()
    ocr = _ocr_status()
    geometry = _geometry_status()
    bedrock = _bedrock_status()

    services = {
        "embedding": embedding,
        "ocr": ocr,
        "geometry": geometry,
        "bedrock": bedrock,
    }
    # Overall posture: worst non-informational state across the core services
    # (bedrock 'offline' is intentional, so it does not drag the overall down).
    core = [embedding["state"], ocr["state"], geometry["state"]]
    if "error" in core:
        overall = "error"
    elif "degraded" in core:
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "overall": overall,
        "services": services,
        "versions": {
            "pipeline": settings.PIPELINE_VERSION,
            "schema": settings.SCHEMA_VERSION,
        },
        "offline_guard": {
            "hf_hub_offline": _env_truthy("HF_HUB_OFFLINE"),
            "transformers_offline": _env_truthy("TRANSFORMERS_OFFLINE"),
        },
        "data_dir": str(settings.DATA_DIR),
    }


def _env_truthy(name: str) -> bool:
    import os

    return os.getenv(name, "").lower() in ("1", "true", "yes")
