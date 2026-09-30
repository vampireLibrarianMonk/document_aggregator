"""Converter dispatch + shared mapping helpers.

A converter extracts raw structural elements from a document (headings,
paragraphs, tables, image refs, header/footer). base.py then maps those onto the
scenario's section vocabulary using the template's section headings as the guide
— so the converter needs no per-scenario code, only the template it is judged
against (which the client would supply alongside the document).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawTable:
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    font: str = ""
    header_style: str = ""
    title: str = ""
    title_position: str = ""          # observed: above | below | "" (missing)


@dataclass
class RawBlock:
    """A structural element extracted from a document, before section mapping.
    Carries observable formatting evidence used for discipline inspection."""
    kind: str  # heading | paragraph | table | image | header | footer | pagenum | classification
    text: str = ""
    level: int = 0
    table: RawTable | None = None
    image_name: str | None = None
    image_caption: str = ""
    caption_position: str = ""        # observed: below | above | "" (none)
    # Observed image geometry/title (what the document actually used).
    image_title: str = ""             # centered title text found on/with the image
    image_width: int | None = None    # rendered width in EMUs->px (best effort)
    image_height: int | None = None
    image_align: str = ""             # left | center | right | "" (unknown)
    image_title_align: str = ""       # alignment of the title paragraph
    align: str = ""                   # paragraph alignment: left | center | right
    # Observed text formatting of this block (what the document actually used).
    font: str = ""
    size_pt: int | None = None
    weight: str = ""                  # normal | bold | ""
    casing: str = ""                  # title | upper | sentence | ""


@dataclass
class RawDocument:
    title: str = ""
    blocks: list[RawBlock] = field(default_factory=list)
    header_text: str = ""
    footer_text: str = ""
    header_style: str = ""            # observed header text style (e.g. bold)
    footer_style: str = ""
    has_page_numbers: bool = False
    page_number_position: str = ""    # footer | header | ""
    classification: str = ""


@dataclass
class ConversionResult:
    first_attempt: dict[str, Any]
    fidelity: str
    source_format: str
    notes: list[str] = field(default_factory=list)


_HEADING_NUM = re.compile(r"^\s*\d+[.)]\s*")
_FIGURE_MARKER = re.compile(r"^\[FIGURE:\s*(.+?)\]$", re.IGNORECASE)


def _norm(text: str) -> str:
    return _HEADING_NUM.sub("", text or "").strip().lower()


def _match_section(heading_text: str, template_sections: list[dict]) -> str | None:
    """Map a document heading to a template section key by fuzzy heading match."""
    target = _norm(heading_text)
    if not target:
        return None
    best_key, best_score = None, 0.0
    for spec in template_sections:
        cand = _norm(spec["heading"])
        # Compare against both the numbered heading and the bare key words.
        score = max(
            difflib.SequenceMatcher(None, target, cand).ratio(),
            difflib.SequenceMatcher(None, target, spec["key"].replace("_", " ")).ratio(),
        )
        if score > best_score:
            best_key, best_score = spec["key"], score
    return best_key if best_score >= 0.6 else None


def map_to_first_attempt(
    raw: RawDocument, template: dict, scenario: dict, artifact_kind: str,
) -> dict[str, Any]:
    """Fold raw blocks onto the scenario's section vocabulary.

    Fields declared in the manifest are pulled from the mapped section's text by
    "Label: value" parsing; a prose section's body is the concatenated non-field
    paragraphs; tables and image refs attach to their section. Anything the
    document lacks is simply omitted (engine -> needs_review)."""
    template_sections = template["required_sections"]
    # manifest fields grouped by section
    fields_by_section: dict[str, list[dict]] = {}
    for f in scenario.get("fields", []):
        fields_by_section.setdefault(f["section"], []).append(f)

    # Initialize a section shell for every template section (order preserved).
    sections: dict[str, dict] = {
        s["key"]: {"key": s["key"], "heading": s["heading"], "fields": {},
                   "body": "", "graphics": []}
        for s in template_sections
    }

    current_key: str | None = None
    body_acc: dict[str, list[str]] = {k: [] for k in sections}

    for blk in raw.blocks:
        if blk.kind == "heading":
            mapped = _match_section(blk.text, template_sections)
            if mapped:
                current_key = mapped
            continue
        if current_key is None:
            continue
        sec = sections[current_key]
        if blk.kind == "paragraph":
            # A "[FIGURE: name]" marker is a graphic reference (we track graphics
            # as named refs, not pixels), regardless of source format.
            fig = _FIGURE_MARKER.match(blk.text.strip())
            if fig:
                sec["graphics"].append({"ref_name": fig.group(1).strip(), "caption": ""})
                continue
            # Try "Label: value" against this section's declared fields.
            consumed = _absorb_fields(blk.text, fields_by_section.get(current_key, []), sec["fields"])
            if not consumed:
                body_acc[current_key].append(blk.text)
        elif blk.kind == "table":
            sec["table"] = {
                "title": blk.table.title, "font": blk.table.font,
                "header_style": blk.table.header_style,
                "columns": blk.table.columns, "rows": blk.table.rows,
            }
        elif blk.kind == "image":
            sec["graphics"].append({
                "ref_name": blk.image_name or "", "caption": blk.image_caption or "",
                "caption_position": blk.caption_position,
                "title": blk.image_title,
                "width": blk.image_width, "height": blk.image_height,
                "align": blk.image_align,
            })

    for k in sections:
        sections[k]["body"] = " ".join(body_acc[k]).strip()

    furniture = {
        "header": {"text": raw.header_text},
        "footer": {"text": raw.footer_text},
        "page_numbers": raw.has_page_numbers,
        "classification": raw.classification,
    }

    return {
        "artifact_kind": artifact_kind,
        "title": raw.title or template.get("title", ""),
        "sections": list(sections.values()),
        "furniture": furniture,
        "cross_references": _extract_cross_refs(sections),
        "_evidence": _collect_evidence(raw),
    }


def _collect_evidence(raw: RawDocument) -> dict[str, Any]:
    """Observable placement/format evidence for discipline inspection. Only what
    the document structure actually reveals; missing signals are recorded as
    empty so the inspector can flag them."""
    images = []
    tables = []
    text_samples: dict[str, list[dict]] = {}
    for blk in raw.blocks:
        if blk.kind == "image":
            images.append({
                "name": blk.image_name or "",
                "has_caption": bool(blk.image_caption),
                "caption_position": blk.caption_position,
                "title": blk.image_title,
                "has_title": bool(blk.image_title),
                "title_align": blk.image_title_align,
                "title_centered": blk.image_title_align == "center",
                "width": blk.image_width,
                "height": blk.image_height,
                "align": blk.image_align,
            })
        elif blk.kind == "table" and blk.table:
            tables.append({
                "has_title": bool(blk.table.title),
                "title_position": blk.table.title_position,
                "columns": blk.table.columns,
                "header_style": blk.table.header_style,
                "font": blk.table.font,
            })
        # Record EVERY observed formatting sample per element type, so learning
        # sees the full distribution (dominant value) and drift is detectable.
        etype = {"heading": "heading", "paragraph": "body"}.get(blk.kind)
        if etype and (blk.font or blk.weight or blk.casing or blk.size_pt):
            text_samples.setdefault(etype, []).append(
                {"font": blk.font, "size_pt": blk.size_pt,
                 "weight": blk.weight, "casing": blk.casing}
            )
    return {
        "images": images,
        "tables": tables,
        "header": {"text": raw.header_text, "style": raw.header_style},
        "footer": {"text": raw.footer_text, "style": raw.footer_style},
        "page_numbers": {"present": raw.has_page_numbers, "position": raw.page_number_position},
        "classification": raw.classification,
        "text_format_samples": text_samples,
    }


_LABEL_VALUE = re.compile(r"^([A-Za-z][\w /-]{1,40}?)\s*:\s*(.*)$")


def _absorb_fields(text: str, field_defs: list[dict], out: dict) -> bool:
    """If a paragraph is a "Label: value" line matching a declared field label,
    store it. A label with an empty value is still consumed (a blank template
    field) but only recorded when non-empty, so the engine flags it needs_review.
    Returns True if the line was consumed as a field label."""
    m = _LABEL_VALUE.match(text.strip())
    if not m:
        return False
    label, value = m.group(1).strip().lower(), m.group(2).strip()
    for fd in field_defs:
        flabel = fd.get("label", fd["key"]).lower()
        fkey = fd["key"].replace("_", " ").lower()
        if label in (flabel, fkey) or label.replace(" ", "") in (flabel.replace(" ", ""), fkey.replace(" ", "")):
            if value:
                out[fd["key"]] = value
            return True  # consumed the label line either way
    return False


_FIG_REF = re.compile(r"\b(figure|fig\.?)\s*(\d+)\b", re.IGNORECASE)


def _extract_cross_refs(sections: dict[str, dict]) -> list[dict]:
    """Detect 'see Figure N' references in body text so the engine can resolve
    them against final numbering. We record the textual reference; the engine
    re-resolves it. (Target graphic id is left None; engine flags if dangling.)"""
    refs: list[dict] = []
    i = 0
    for key, sec in sections.items():
        for m in _FIG_REF.finditer(sec.get("body", "")):
            i += 1
            refs.append({
                "id": f"xref_{i}", "in_section": key,
                "text": f"Figure {m.group(2)}", "points_to_graphic": None,
            })
    return refs


def convert_document(data: bytes, filename: str, template: dict, scenario: dict,
                     artifact_kind: str) -> ConversionResult:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "docx":
        from .docx_in import extract_docx
        raw, notes = extract_docx(data)
        fidelity, fmt = "high", "docx"
    elif ext == "pptx":
        from .pptx_in import extract_pptx
        raw, notes = extract_pptx(data)
        fidelity, fmt = "good", "pptx"
    elif ext == "pdf":
        from .pdf_in import extract_pdf
        raw, notes = extract_pdf(data)
        fidelity, fmt = "partial", "pdf"
    else:
        raise ValueError(f"unsupported document format: {ext}")

    first_attempt = map_to_first_attempt(raw, template, scenario, artifact_kind)
    return ConversionResult(first_attempt=first_attempt, fidelity=fidelity,
                            source_format=fmt, notes=notes)
