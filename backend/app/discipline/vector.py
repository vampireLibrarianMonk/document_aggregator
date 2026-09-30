"""Vector-layout inspection orchestration (gated on LibreOffice).

Bridges the discipline engine to the layout tier: given the original document
bytes and the resolved discipline, render to PDF (if the source is DOCX and
LibreOffice is present), extract element geometry, and run the geometric
inspector. Returns (findings, ran) so the caller knows whether the tier
executed or the pipeline degraded to structural inspection only.
"""
from __future__ import annotations

from ..reconcile.models import CorrectedField
from .spec import BuildDiscipline


def inspect_vector_layout(
    source_bytes: bytes | None, source_format: str, discipline: BuildDiscipline,
) -> tuple[list[CorrectedField], bool]:
    """Run the vector tier when possible. Returns (findings, ran)."""
    if not source_bytes:
        return [], False

    from ..layout.geometry import extract_layout
    from ..layout.inspect_layout import inspect_layout
    from ..layout.render import docx_to_pdf_bytes

    pdf_bytes: bytes | None = None
    if source_format == "pdf":
        pdf_bytes = source_bytes
    elif source_format == "docx":
        pdf_bytes = docx_to_pdf_bytes(source_bytes)  # None if soffice absent

    if not pdf_bytes:
        return [], False

    try:
        pages = extract_layout(pdf_bytes)
    except Exception:
        return [], False
    return inspect_layout(pages, discipline.layout), True
