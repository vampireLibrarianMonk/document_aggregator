"""PPTX -> RawDocument (good fidelity).

Each slide becomes a section: the slide title is the heading, and body text-frame
paragraphs become field lines / body / figure markers. Picture shapes and
"[FIGURE: name]" text markers become image references. A table shape (or a
"TABLE COLUMNS: ..." speaker note) yields the table's columns. The title slide's
subtitle is treated as a classification marking if it matches a known marker.
"""
from __future__ import annotations

from .base import RawBlock, RawDocument, RawTable

_CLASS_MARKERS = ("unclassified", "confidential", "secret", "internal use",
                  "proprietary", "public", "restricted")


def extract_pptx(data: bytes) -> tuple[RawDocument, list[str]]:
    import io

    from pptx import Presentation

    notes: list[str] = []
    prs = Presentation(io.BytesIO(data))
    raw = RawDocument()

    for idx, slide in enumerate(prs.slides):
        title = _slide_title(slide)
        if idx == 0:
            # Title slide: document title + optional classification subtitle.
            raw.title = title
            for shape in slide.shapes:
                if shape.has_text_frame:
                    txt = shape.text_frame.text.strip()
                    if txt and txt != title and _is_classification(txt):
                        raw.classification = txt
            continue

        if title:
            raw.blocks.append(RawBlock(kind="heading", text=title, level=1))

        # Body placeholders: each paragraph is a candidate field/body/figure line.
        for shape in slide.shapes:
            if shape.has_text_frame and shape != _title_shape(slide):
                for para in shape.text_frame.paragraphs:
                    line = para.text.strip()
                    if line:
                        raw.blocks.append(RawBlock(kind="paragraph", text=line))
            if shape.shape_type == 13:  # PICTURE
                name = _picture_name(shape)
                raw.blocks.append(RawBlock(kind="image", image_name=name))

        # Table: real table shape, else a "TABLE COLUMNS:" speaker note.
        tbl_block = _slide_table(slide)
        if tbl_block:
            raw.blocks.append(tbl_block)

    if not raw.blocks:
        notes.append("no extractable slides found in PPTX")
    return raw, notes


def _title_shape(slide):
    try:
        return slide.shapes.title
    except (AttributeError, KeyError):
        return None


def _slide_title(slide) -> str:
    ts = _title_shape(slide)
    if ts is not None and ts.has_text_frame:
        return ts.text_frame.text.strip()
    return ""


def _is_classification(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _CLASS_MARKERS)


def _picture_name(shape) -> str:
    # python-pptx exposes the shape name; the image part name is the fallback.
    name = getattr(shape, "name", "") or ""
    try:
        img = shape.image
        if img and img.filename:
            return img.filename.rsplit("/", 1)[-1]
    except (AttributeError, ValueError):
        pass
    return name


def _slide_table(slide) -> RawBlock | None:
    for shape in slide.shapes:
        if shape.has_table:
            tb = shape.table
            rows = [[cell.text.strip() for cell in row.cells] for row in tb.rows]
            columns = rows[0] if rows else []
            body = rows[1:] if len(rows) > 1 else []
            return RawBlock(kind="table", table=RawTable(columns=columns, rows=body))
    # Fallback: a speaker note declaring the table columns.
    if slide.has_notes_slide:
        note = slide.notes_slide.notes_text_frame.text.strip()
        if note.upper().startswith("TABLE COLUMNS:"):
            cols = [c.strip() for c in note.split(":", 1)[1].split("|") if c.strip()]
            if cols:
                return RawBlock(kind="table", table=RawTable(columns=cols, rows=[]))
    return None
