"""Optional OCR adapter for scanned/flattened PDFs and images.

Air-gap and offline posture first:
  - OCR is OFF by default (settings.OCR_ENABLED) and never required.
  - All engine imports are LAZY, so the service runs with no OCR libraries
    installed; `ocr_available()` reports the truth and callers degrade to the
    existing "needs_ocr" behavior.
  - No network. tesseract runs locally; model data ships with the engine.

Engine selection (first available wins), kept behind a tiny seam so an enclave
can bake in a different OCR tool without touching the parsers:
  - text engine:   pytesseract + a local `tesseract` binary.
  - PDF rasterizer: PyMuPDF (fitz) preferred (pure-wheel, no system poppler),
    else pdf2image + poppler.

Everything returns empty / reports unavailable rather than raising, so one bad
page or a missing dependency never breaks ingestion.
"""
from __future__ import annotations

import importlib.util
import io

from .config import settings


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _tesseract_ok() -> bool:
    """pytesseract importable AND a tesseract binary reachable."""
    if not _has("pytesseract"):
        return False
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def text_engine() -> str | None:
    """Name of the available text-OCR engine, or None."""
    if _tesseract_ok():
        return "tesseract"
    return None


def pdf_rasterizer() -> str | None:
    """Name of the available PDF->image rasterizer, or None."""
    if _has("fitz"):
        return "pymupdf"
    if _has("pdf2image"):
        return "pdf2image"
    return None


def ocr_available() -> bool:
    """True only when OCR is enabled AND a text engine is installed."""
    return bool(settings.OCR_ENABLED) and text_engine() is not None


def ocr_status() -> dict:
    """Introspection for diagnostics / the API."""
    return {
        "enabled": bool(settings.OCR_ENABLED),
        "text_engine": text_engine(),
        "pdf_rasterizer": pdf_rasterizer(),
        "available": ocr_available(),
        "lang": settings.OCR_LANG,
    }


def ocr_image_bytes(data: bytes) -> str:
    """OCR a single image's bytes to text. Returns '' if OCR is unavailable or
    the image can't be read. Never raises."""
    if not ocr_available():
        return ""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return (pytesseract.image_to_string(img, lang=settings.OCR_LANG) or "").strip()
    except Exception:
        return ""


def _render_pdf_pages(data: bytes, max_pages: int):
    """Yield PIL Images for the first max_pages pages, using whatever rasterizer
    is available. Yields nothing if none is."""
    r = pdf_rasterizer()
    if r == "pymupdf":
        try:
            import fitz  # PyMuPDF
            from PIL import Image
            doc = fitz.open(stream=data, filetype="pdf")
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                # 200 DPI zoom (2.78x of the 72dpi base) is a good OCR default.
                pix = page.get_pixmap(matrix=fitz.Matrix(2.78, 2.78))
                yield Image.open(io.BytesIO(pix.tobytes("png")))
        except Exception:
            return
    elif r == "pdf2image":
        try:
            from pdf2image import convert_from_bytes
            yield from convert_from_bytes(data, dpi=200, first_page=1,
                                          last_page=max_pages)
        except Exception:
            return


def ocr_pdf_bytes(data: bytes) -> list[str]:
    """OCR a scanned/flattened PDF, returning per-page text. Returns [] if OCR
    or a rasterizer is unavailable. Never raises."""
    if not ocr_available() or pdf_rasterizer() is None:
        return []
    try:
        import pytesseract
    except Exception:
        return []
    pages: list[str] = []
    for img in _render_pdf_pages(data, settings.OCR_MAX_PAGES):
        try:
            pages.append((pytesseract.image_to_string(img, lang=settings.OCR_LANG) or "").strip())
        except Exception:
            pages.append("")
    return pages
