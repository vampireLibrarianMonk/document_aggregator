"""Read-only document preview: classify a document for in-browser rendering and
convert Office files (DOCX/PPTX) to PDF for viewing.

Security + posture:
  - READ-ONLY. Nothing here mutates a stored document. The Office->PDF converter
    only ever reads the immutable source bytes and writes to an isolated temp
    directory; the resulting PDF is cached under the project's own dir.
  - Conversion runs LibreOffice headless in a sandboxed temp dir with a hard
    timeout, and degrades to None (caller returns a clear message) when
    LibreOffice is absent or conversion fails. No network, air-gap safe.
  - Callers are responsible for path-guarding (serving only a document that
    belongs to the project) and size limits.

Preview kinds (what the frontend renders):
  text        decodable text -> show as text
  image       png/jpg/gif/webp -> <img> from the original bytes
  pdf         application/pdf -> <iframe>/<embed> on the original bytes
  office-pdf  docx/pptx -> convert to PDF, then <iframe> on the converted bytes
  unsupported anything else -> metadata note only
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import settings

# Browser-native image types we stream as-is.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
# Office types we convert to PDF for preview.
_OFFICE_EXT = {".docx", ".pptx", ".doc", ".ppt", ".odt", ".odp", ".xlsx", ".xls"}
# Text-ish types we show as text (kept in sync with the raw text decode path).
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml", ".html"}

# Conversion limits.
_CONVERT_TIMEOUT_S = 120
_MAX_CONVERT_BYTES = 50 * 1024 * 1024   # refuse to convert absurdly large inputs


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def classify(filename: str, mime_type: str) -> str:
    """Return the preview kind for a document. Prefers extension (stable across
    the pipeline), falls back to the mime type."""
    ext = _ext(filename)
    if ext in _IMAGE_EXT or (mime_type or "").startswith("image/"):
        return "image"
    if ext == ".pdf" or mime_type == "application/pdf":
        return "pdf"
    if ext in _OFFICE_EXT:
        return "office-pdf"
    if ext in _TEXT_EXT or (mime_type or "").startswith("text/"):
        return "text"
    return "unsupported"


def image_media_type(filename: str, mime_type: str) -> str:
    return _IMAGE_MIME.get(_ext(filename), mime_type or "application/octet-stream")


def soffice_bin() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def office_to_pdf(data: bytes, ext: str) -> bytes | None:
    """Convert Office bytes (docx/pptx/...) to PDF via LibreOffice headless.
    Returns None if LibreOffice is unavailable, the input is too large, or the
    conversion fails. Runs entirely in an isolated temp dir; reads nothing but
    the bytes passed in."""
    if not data or len(data) > _MAX_CONVERT_BYTES:
        return None
    soffice = soffice_bin()
    if not soffice:
        return None
    safe_ext = ext if ext.startswith(".") else f".{ext}"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{safe_ext}"
        src.write_bytes(data)
        try:
            # Dedicated user-installation keeps concurrent conversions from
            # colliding on the default LibreOffice profile.
            subprocess.run(
                [soffice, "--headless",
                 f"-env:UserInstallation=file://{tmp}/profile",
                 "--convert-to", "pdf", "--outdir", tmp, str(src)],
                check=True, capture_output=True, timeout=_CONVERT_TIMEOUT_S,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        pdf = Path(tmp) / "in.pdf"
        return pdf.read_bytes() if pdf.exists() else None


def cached_office_pdf(project_id: str, document_id: str, data: bytes, ext: str) -> bytes | None:
    """office_to_pdf with a per-document cache so repeat views do not re-convert.
    The cache key includes a hash of the source bytes, so it is invalidated if
    the (immutable) source ever changes. Cache lives under the project dir;
    a failure to cache never fails the request."""
    digest = hashlib.sha256(data).hexdigest()[:16]
    cache_dir = settings.project_dir(project_id) / "preview_cache"
    cache_file = cache_dir / f"{document_id}.{digest}.pdf"
    if cache_file.exists():
        try:
            return cache_file.read_bytes()
        except OSError:
            pass
    pdf = office_to_pdf(data, ext)
    if pdf is None:
        return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".pdf.tmp")
        tmp.write_bytes(pdf)
        tmp.replace(cache_file)
    except OSError:
        pass  # caching is best-effort; still return the PDF
    return pdf


def office_ext(filename: str) -> str:
    return _ext(filename)
