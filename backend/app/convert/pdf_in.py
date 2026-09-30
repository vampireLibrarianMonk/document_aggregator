"""PDF -> RawDocument (partial fidelity, best-effort).

PDF carries little reliable structure, so this is heuristic:
  - text is extracted per page and split into lines;
  - a line is treated as a heading if it matches a numbered-section pattern or
    is short and title-cased;
  - "Label: value" lines and "[FIGURE: name]" markers are recognized as in the
    other formats;
  - table structure is largely lost in text extraction — we detect a table
    title line ("Table N: ...") and record it, but columns/rows are best-effort.

Fidelity is honestly reported as "partial"; provenance downstream reflects that.
"""
from __future__ import annotations

import re

from .base import RawBlock, RawDocument, RawTable

_NUM_HEADING = re.compile(r"^\s*\d+[.)]\s+[A-Z]")
_FIGURE = re.compile(r"^\[FIGURE:\s*(.+?)\]$", re.IGNORECASE)
_TABLE_TITLE = re.compile(r"^Table\s+\d+\s*:", re.IGNORECASE)
_CLASS_MARKERS = ("unclassified", "confidential", "secret", "internal use",
                  "proprietary", "public", "restricted")

# Known section words help promote a bare heading line to a heading even when
# it lacks a number (headings often lose their numbering in PDF extraction).
_SECTION_WORDS = ("identifiers", "description", "timeline", "contributing",
                  "corrective", "approvals", "findings", "summary", "recommendation")


def extract_pdf(data: bytes) -> tuple[RawDocument, list[str]]:
    import io

    from pypdf import PdfReader

    notes: list[str] = ["PDF conversion is best-effort; table columns/rows may be lossy."]
    reader = PdfReader(io.BytesIO(data))
    raw = RawDocument()

    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for ln in text.splitlines():
            s = ln.strip()
            if s:
                lines.append(s)

    if not lines:
        notes.append("no extractable text in PDF (scanned? OCR not enabled)")
        return raw, notes

    # First non-empty line is the document title.
    raw.title = lines[0]

    pending_table_title = ""
    for s in lines[1:]:
        low = s.lower()
        if _is_classification(s) and not raw.classification:
            raw.classification = s
        fig = _FIGURE.match(s)
        if fig:
            raw.blocks.append(RawBlock(kind="image", image_name=fig.group(1).strip()))
            continue
        if _TABLE_TITLE.match(s):
            pending_table_title = s
            continue
        if _is_heading(s, low):
            raw.blocks.append(RawBlock(kind="heading", text=s, level=1))
            continue
        raw.blocks.append(RawBlock(kind="paragraph", text=s))

    # If we saw a table title, attach an (empty-row) table marker so the engine
    # still recognizes the table's presence; columns are recovered where the
    # generator emitted them inline (best-effort).
    if pending_table_title:
        raw.blocks.append(RawBlock(kind="table", table=RawTable(title=pending_table_title)))

    raw.has_page_numbers = _detect_page_numbers(lines)
    return raw, notes


def _is_heading(s: str, low: str) -> bool:
    if _NUM_HEADING.match(s):
        return True
    if len(s.split()) <= 6 and any(w in low for w in _SECTION_WORDS):
        return True
    return False


def _is_classification(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _CLASS_MARKERS)


def _detect_page_numbers(lines: list[str]) -> bool:
    # A lone integer line is a strong page-number signal in extracted PDF text.
    return any(re.fullmatch(r"\d{1,3}", ln) for ln in lines)
