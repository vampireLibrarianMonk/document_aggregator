"""DOCX -> RawDocument (high fidelity).

Reads heading styles, paragraphs, tables (with the header row's font + style),
inline image references (by drawing relationship), and the section header/footer
including page-number fields and any classification marking.
"""
from __future__ import annotations

import re

from .base import RawBlock, RawDocument, RawTable


def extract_docx(data: bytes) -> tuple[RawDocument, list[str]]:
    import io

    import docx

    notes: list[str] = []
    document = docx.Document(io.BytesIO(data))
    raw = RawDocument()

    # Title = document core property or first Title/Heading-1 paragraph.
    raw.title = (document.core_properties.title or "").strip()

    # python-docx exposes paragraphs and tables separately; reconstruct order
    # by walking the body's child elements directly.
    from docx.oxml.ns import qn

    parent_elm = document.element.body
    tbl_iter = iter(document.tables)
    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            para = _para_for_element(document, child)
            if para is None:
                continue
            text = para.text.strip()
            # Check for an embedded image FIRST — an image-only paragraph has no
            # text, so the empty-text skip below would otherwise drop it.
            image_name = _first_image_name(para)
            is_marker = text.upper().startswith("[FIGURE:")
            if image_name or is_marker:
                font, size_pt, weight = _para_format(para)
                w_px, h_px = _image_size_px(para)
                align = _para_align(para)
                raw.blocks.append(RawBlock(
                    kind="image", image_name=image_name or (text if is_marker else ""),
                    font=font, size_pt=size_pt, weight=weight,
                    image_width=w_px, image_height=h_px, image_align=align,
                ))
                continue
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style else ""
            font, size_pt, weight = _para_format(para)
            casing = _casing(text)
            palign = _para_align(para)
            if "heading" in style or "title" in style:
                if not raw.title and "title" in style:
                    raw.title = text
                raw.blocks.append(RawBlock(kind="heading", text=text, level=1,
                                           font=font, size_pt=size_pt, weight=weight,
                                           casing=casing, align=palign))
            else:
                raw.blocks.append(RawBlock(kind="paragraph", text=text,
                                           font=font, size_pt=size_pt, weight=weight,
                                           casing=casing, align=palign))
        elif child.tag == qn("w:tbl"):
            try:
                tbl = next(tbl_iter)
            except StopIteration:
                continue
            raw.blocks.append(_table_block(tbl))

    _assign_caption_positions(raw)

    # Header / footer / page numbers / classification from the first section.
    if document.sections:
        sec = document.sections[0]
        raw.header_text = _hf_text(sec.header)
        raw.footer_text = _hf_text(sec.footer)
        raw.header_style = _hf_style(sec.header)
        raw.footer_style = _hf_style(sec.footer)
        if _has_page_number(sec.footer):
            raw.has_page_numbers, raw.page_number_position = True, "footer"
        elif _has_page_number(sec.header):
            raw.has_page_numbers, raw.page_number_position = True, "header"
        raw.classification = _find_classification(raw.header_text, raw.footer_text)

    if not raw.blocks:
        notes.append("no extractable blocks found in DOCX")
    return raw, notes


def _para_for_element(document, elm):
    from docx.text.paragraph import Paragraph

    return Paragraph(elm, document)


def _para_format(para) -> tuple[str, int | None, str]:
    """Effective font name, size (pt), and weight of a paragraph, resolved by
    walking the OOXML style chain the way Word renders it:
        run direct props -> paragraph style -> based-on ancestors -> docDefaults.
    Returns the effective values so inheritance-based formatting is observed."""
    font: str | None = None
    size_pt: int | None = None
    weight: str | None = None

    # 1. Direct run formatting (highest precedence).
    for run in para.runs:
        if run.font is not None:
            if font is None and run.font.name:
                font = run.font.name
            if size_pt is None and run.font.size is not None:
                try:
                    size_pt = int(run.font.size.pt)
                except (AttributeError, ValueError):
                    pass
            if weight is None and run.font.bold is not None:
                weight = "bold" if run.font.bold else "normal"
        if font and size_pt is not None and weight:
            break

    # 2. Paragraph style + its based-on ancestry.
    style = para.style
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        sf = getattr(style, "font", None)
        if sf is not None:
            if font is None and sf.name:
                font = sf.name
            if size_pt is None and sf.size is not None:
                try:
                    size_pt = int(sf.size.pt)
                except (AttributeError, ValueError):
                    pass
            if weight is None and sf.bold is not None:
                weight = "bold" if sf.bold else "normal"
        style = getattr(style, "base_style", None)

    # 3. Document defaults (docDefaults / theme fallback).
    if font is None or size_pt is None:
        d_font, d_size = _doc_default_font(para.part.document)
        font = font or d_font
        size_pt = size_pt if size_pt is not None else d_size

    return font or "", size_pt, weight or ""


def _doc_default_font(document) -> tuple[str | None, int | None]:
    """Resolve the document default run font/size from styles.xml docDefaults,
    falling back to the theme's minor (body) font."""
    from docx.oxml.ns import qn

    styles_el = document.styles.element
    font_name = None
    size_pt = None
    dd = styles_el.find(qn("w:docDefaults"))
    if dd is not None:
        rpr = dd.find(qn("w:rPrDefault") + "/" + qn("w:rPr"))
        if rpr is not None:
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is not None:
                font_name = (rfonts.get(qn("w:ascii")) or rfonts.get(qn("w:hAnsi"))
                             or rfonts.get(qn("w:asciiTheme")))
            sz = rpr.find(qn("w:sz"))
            if sz is not None and sz.get(qn("w:val")):
                try:
                    size_pt = int(int(sz.get(qn("w:val"))) / 2)  # half-points
                except ValueError:
                    pass
    # Theme minor font if docDefaults referenced a theme token or was absent.
    if not font_name or "minorHAnsi" in str(font_name) or "Theme" in str(font_name):
        theme_font = _theme_minor_font(document)
        font_name = theme_font or font_name
    return font_name, size_pt


def _theme_minor_font(document) -> str | None:
    """Read the theme's minor (body) typeface from the theme part's raw XML.
    python-docx stores the theme as a generic part, so parse its blob."""
    from lxml import etree

    ns_a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    try:
        for rel in document.part.rels.values():
            if "theme" in rel.reltype:
                blob = rel.target_part.blob
                root = etree.fromstring(blob)
                latin = root.find(
                    f"{ns_a}themeElements/{ns_a}fontScheme/{ns_a}minorFont/{ns_a}latin")
                if latin is not None and latin.get("typeface"):
                    return latin.get("typeface")
    except (AttributeError, KeyError, etree.XMLSyntaxError):
        pass
    return None


def _casing(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return ""
    if text.isupper():
        return "upper"
    words = [w for w in text.split() if w and w[0].isalpha()]
    if words and all(w[0].isupper() for w in words):
        return "title"
    return "sentence"


def _assign_caption_positions(raw) -> None:
    """Attach a caption and/or a title to each image block from adjacent
    paragraphs. A caption is a "Figure N: ..." line (below/above). A title is a
    short, centered/bold heading-like line immediately above the image (e.g.
    "Packet Loss vs Cabinet Temperature") that is NOT a Figure caption. Real
    figures often carry both; the pipeline tracks the title's presence/centering
    even though it may also be baked into the image pixels."""
    blocks = raw.blocks
    for i, blk in enumerate(blocks):
        if blk.kind != "image":
            continue
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        prv = blocks[i - 1] if i - 1 >= 0 else None
        if nxt and nxt.kind == "paragraph" and _looks_like_caption(nxt.text):
            blk.image_caption = nxt.text
            blk.caption_position = "below"
        elif prv and prv.kind == "paragraph" and _looks_like_caption(prv.text):
            blk.image_caption = prv.text
            blk.caption_position = "above"

        # Title: a short line just above the image that isn't a caption. Its
        # own alignment tells us whether the title is centered.
        if prv and prv.kind in ("paragraph", "heading") and prv.text \
                and not _looks_like_caption(prv.text) and len(prv.text) <= 80:
            blk.image_title = prv.text.strip()
            blk.image_title_align = prv.align


def _looks_like_caption(text: str) -> bool:
    return bool(re.match(r"^\s*(figure|fig\.?)\s*\d+", text or "", re.IGNORECASE))


def _first_image_name(paragraph) -> str | None:
    """Return the LOGICAL image reference for a paragraph that embeds a drawing.
    Prefers the docPr @name/@descr (where we stash the managed logical name like
    'site_network_topology.png'), because the media part name ('image1.png') is
    an opaque zip-internal name that carries no meaning for graphic matching.
    Falls back to the media part name only when no logical name is declared."""
    from docx.oxml.ns import qn

    has_drawing = bool(paragraph._p.findall(".//" + qn("a:blip")))

    # A declared logical name on the drawing wins (this is our managed name).
    for d in paragraph._p.findall(".//" + qn("wp:docPr")):
        name = d.get("name") or d.get("descr")
        if name and name.strip():
            return name.strip()

    if not has_drawing:
        return None

    # Fallback: the media part filename.
    part = paragraph.part
    for blip in paragraph._p.findall(".//" + qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if rid and rid in part.rels:
            target = part.rels[rid].target_ref  # e.g. media/image1.png
            return target.rsplit("/", 1)[-1]
    return None


def _image_size_px(paragraph) -> tuple[int | None, int | None]:
    """Rendered image size from the drawing's extent (EMUs -> px at 96 dpi).
    Returns (width_px, height_px) or (None, None) if no sized drawing found."""
    from docx.oxml.ns import qn

    exts = paragraph._p.findall(".//" + qn("wp:extent"))
    for ext in exts:
        cx, cy = ext.get("cx"), ext.get("cy")
        if cx and cy:
            try:
                # 914400 EMUs per inch; 96 px per inch.
                return int(int(cx) / 914400 * 96), int(int(cy) / 914400 * 96)
            except ValueError:
                return None, None
    return None, None


def _para_align(paragraph) -> str:
    """Paragraph horizontal alignment, normalized to left|center|right."""
    try:
        a = paragraph.alignment
    except (AttributeError, ValueError):
        return ""
    if a is None:
        return ""
    name = str(a).lower()
    if "center" in name:
        return "center"
    if "right" in name:
        return "right"
    if "left" in name or "justify" in name:
        return "left"
    return ""


def _table_block(tbl) -> RawBlock:
    rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
    columns = rows[0] if rows else []
    body = rows[1:] if len(rows) > 1 else []
    font, style = _table_header_style(tbl)
    title = _table_title(tbl)
    return RawBlock(kind="table", table=RawTable(
        columns=columns, rows=body, font=font, header_style=style, title=title,
        title_position="above" if title else "",
    ))


def _table_header_style(tbl) -> tuple[str, str]:
    """Sniff the header row's run font and paragraph style."""
    if not tbl.rows:
        return "", ""
    font = ""
    style = ""
    hdr = tbl.rows[0]
    for cell in hdr.cells:
        for para in cell.paragraphs:
            if para.style and para.style.name:
                style = para.style.name.lower().replace(" ", "-")
            for run in para.runs:
                if run.font and run.font.name:
                    font = run.font.name
                    break
            if font:
                break
        if font:
            break
    return font, style


def _table_title(tbl) -> str:
    """A table title is often the paragraph immediately preceding the table."""
    prev = tbl._tbl.getprevious()
    from docx.oxml.ns import qn

    if prev is not None and prev.tag == qn("w:p"):
        text = "".join(t.text or "" for t in prev.findall(".//" + qn("w:t"))).strip()
        if text.lower().startswith("table"):
            return text
    return ""


def _hf_text(hf) -> str:
    if hf is None:
        return ""
    parts = [p.text.strip() for p in hf.paragraphs if p.text and p.text.strip()]
    return " ".join(parts)


def _hf_style(hf) -> str:
    """Observed weight of the header/footer's first run (bold vs normal)."""
    if hf is None:
        return ""
    for p in hf.paragraphs:
        for run in p.runs:
            if run.font is not None and run.font.bold is not None:
                return "bold" if run.font.bold else "normal"
    return ""


def _has_page_number(hf) -> bool:
    from docx.oxml.ns import qn

    if hf is None:
        return False
    for p in hf.paragraphs:
        for fld in p._p.findall(".//" + qn("w:instrText")):
            if fld.text and "PAGE" in fld.text.upper():
                return True
        for fld in p._p.findall(".//" + qn("w:fldSimple")):
            instr = fld.get(qn("w:instr")) or ""
            if "PAGE" in instr.upper():
                return True
    return False


def _find_classification(*texts: str) -> str:
    markers = ("unclassified", "confidential", "secret", "internal use",
               "proprietary", "public", "restricted")
    for t in texts:
        low = (t or "").lower()
        for m in markers:
            if m in low:
                return t.strip()
    return ""
