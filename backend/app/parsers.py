"""Parser registry / router.

Each parser converts source bytes into a list of canonical Blocks plus a list
of Artifacts. Optional libraries (python-docx, python-pptx, pypdf, Pillow) are
imported lazily so the pipeline degrades gracefully: if a parser's dependency
is missing, it reports that clearly instead of crashing the service.

File-type routing is by MIME/extension; a real build would sniff content.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field

from .models import Artifact, Block, Provenance


@dataclass
class ParseResult:
    blocks: list[Block] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    parser: str = "unknown"
    parser_version: str = ""
    method: str = "native"
    notes: str = ""


def _bid() -> str:
    return "block_" + uuid.uuid4().hex[:12]


def _aid() -> str:
    return "artifact_" + uuid.uuid4().hex[:12]


def _prov(parser: str, version: str, method: str, doc_id: str) -> Provenance:
    return Provenance(parser=parser, parser_version=version, method=method, source_document_id=doc_id)


# --------------------------------------------------------------------------
# TXT / Markdown
# --------------------------------------------------------------------------

def parse_text(data: bytes, doc_id: str, is_markdown: bool) -> ParseResult:
    text = data.decode("utf-8", errors="replace")
    parser = "markdown_native" if is_markdown else "text_native"
    prov = _prov(parser, "1", "native", doc_id)
    blocks: list[Block] = []
    order = 0
    section_path: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        btype = "paragraph"
        if is_markdown and line.lstrip().startswith("#"):
            btype = "heading"
            section_path = [line.lstrip("# ").strip()]
        elif is_markdown and line.lstrip()[:2] in ("- ", "* "):
            btype = "list_item"
        blocks.append(Block(
            id=_bid(), type=btype, text=line.strip(),
            reading_order=order, section_path=list(section_path), provenance=prov,
        ))
        order += 1
    return ParseResult(blocks=blocks, parser=parser, parser_version="1", notes="")


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------

def parse_docx(data: bytes, doc_id: str) -> ParseResult:
    try:
        import docx  # python-docx
    except ImportError:
        return ParseResult(parser="docx", notes="python-docx not installed; DOCX parsing skipped")

    document = docx.Document(io.BytesIO(data))
    prov = _prov("python-docx", getattr(docx, "__version__", "?"), "native", doc_id)
    blocks: list[Block] = []
    order = 0
    section_path: list[str] = []
    for para in document.paragraphs:
        txt = para.text.strip()
        if not txt:
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if "heading" in style or "title" in style:
            btype = "heading"
            section_path = [txt]
        elif "list" in style:
            btype = "list_item"
        else:
            btype = "paragraph"
        blocks.append(Block(id=_bid(), type=btype, text=txt, reading_order=order,
                            section_path=list(section_path), provenance=prov))
        order += 1

    for table in document.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        flat = " | ".join(" ".join(r) for r in rows)
        blocks.append(Block(id=_bid(), type="table", text=flat, reading_order=order,
                            section_path=list(section_path), provenance=prov, table=rows))
        order += 1

    return ParseResult(blocks=blocks, parser="python-docx",
                       parser_version=getattr(docx, "__version__", "?"))


# --------------------------------------------------------------------------
# PPTX
# --------------------------------------------------------------------------

def parse_pptx(data: bytes, doc_id: str) -> ParseResult:
    try:
        from pptx import Presentation
    except ImportError:
        return ParseResult(parser="pptx", notes="python-pptx not installed; PPTX parsing skipped")

    prs = Presentation(io.BytesIO(data))
    prov = _prov("python-pptx", "1", "native", doc_id)
    blocks: list[Block] = []
    artifacts: list[Artifact] = []
    order = 0
    for sidx, slide in enumerate(prs.slides, start=1):
        section_path = [f"Slide {sidx}"]
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                txt = shape.text_frame.text.strip()
                is_title = bool(getattr(shape, "is_placeholder", False)) and order == 0
                blocks.append(Block(id=_bid(), type="slide_title" if is_title else "paragraph",
                                    text=txt, slide=sidx, reading_order=order,
                                    section_path=list(section_path), provenance=prov))
                order += 1
            if shape.shape_type == 13:  # PICTURE
                artifacts.append(Artifact(id=_aid(), type="image", slide=sidx,
                                          classification="slide_image"))
        # speaker notes
        if slide.has_notes_slide:
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                blocks.append(Block(id=_bid(), type="notes", text=note, slide=sidx,
                                    reading_order=order, section_path=list(section_path),
                                    provenance=prov))
                order += 1
    return ParseResult(blocks=blocks, artifacts=artifacts, parser="python-pptx", parser_version="1")


# --------------------------------------------------------------------------
# PDF (born-digital)
# --------------------------------------------------------------------------

def parse_pdf(data: bytes, doc_id: str) -> ParseResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ParseResult(parser="pdf", notes="pypdf not installed; PDF parsing skipped")

    reader = PdfReader(io.BytesIO(data))
    prov = _prov("pypdf", "1", "native", doc_id)
    blocks: list[Block] = []
    order = 0
    total_text = 0
    for pidx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        total_text += len(text)
        for para in [p.strip() for p in text.split("\n") if p.strip()]:
            blocks.append(Block(id=_bid(), type="paragraph", text=para, page=pidx,
                                reading_order=order, provenance=prov))
            order += 1
    method = "native_pdf"
    notes = ""
    if total_text < 20:
        # No usable text -> would route to OCR escalation (OCR_ENABLED flag).
        method = "needs_ocr"
        notes = "Little/no native text extracted; scanned PDF -> OCR escalation required (OCR disabled)."
    return ParseResult(blocks=blocks, parser="pypdf", parser_version="1", method=method, notes=notes)


# --------------------------------------------------------------------------
# Images (PNG/JPEG/TIFF) -> artifact + EXIF timestamp evidence
# --------------------------------------------------------------------------

def parse_image(data: bytes, doc_id: str, mime: str) -> ParseResult:
    art = Artifact(id=_aid(), type="image", classification="standalone_image",
                   content_hash=None, metadata={"mime": mime})
    notes = "Image ingested as artifact. Baseline keeps location/relationship; OCR/VLM optional."
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        img = Image.open(io.BytesIO(data))
        art.metadata["width"], art.metadata["height"] = img.size
        exif = getattr(img, "_getexif", lambda: None)() or {}
        for tag_id, val in exif.items():
            name = TAGS.get(tag_id, str(tag_id))
            if name in ("DateTimeOriginal", "DateTime"):
                art.metadata["exif_datetime_original"] = str(val)
    except ImportError:
        notes += " (Pillow not installed; no dimensions/EXIF)"
    except Exception as exc:  # malformed image should not crash the worker
        notes += f" (image inspect failed: {exc})"
    return ParseResult(artifacts=[art], parser="image_native", parser_version="1", notes=notes)


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

def route_and_parse(filename: str, mime: str, data: bytes, doc_id: str) -> ParseResult:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("txt",) or mime == "text/plain":
        return parse_text(data, doc_id, is_markdown=False)
    if ext in ("md", "markdown"):
        return parse_text(data, doc_id, is_markdown=True)
    if ext == "docx" or "wordprocessingml" in mime:
        return parse_docx(data, doc_id)
    if ext == "pptx" or "presentationml" in mime:
        return parse_pptx(data, doc_id)
    if ext == "pdf" or mime == "application/pdf":
        return parse_pdf(data, doc_id)
    if ext in ("png", "jpg", "jpeg", "tif", "tiff") or mime.startswith("image/"):
        return parse_image(data, doc_id, mime)
    # Fallback: try decoding as text (Tika would sit here in a full build).
    return parse_text(data, doc_id, is_markdown=False)
