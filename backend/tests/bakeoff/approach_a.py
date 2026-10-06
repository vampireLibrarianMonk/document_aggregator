"""Approach A — deterministic structure extraction.

Parse the RAW uploaded documents into the structured shapes the existing
deterministic reconcile() engine requires (manifest + template + first_attempt +
corrections), then run reconcile(). Pure offline, fully deterministic.

The hard part this exposes: reconcile() needs a project MANIFEST (fields,
section_bodies, table queries) and a structured TEMPLATE (required_sections,
table_specs). A raw template document gives headings and a table shell, but the
field-extraction QUERIES and the per-field section mapping must be inferred.
This approach infers them heuristically from the template + draft headings.
"""
from __future__ import annotations

import re

from app.convert import convert_document

# Reuse the production docx parser to get RawDocument blocks from raw bytes.
from app.convert.docx_in import extract_docx  # noqa: E402
from app.reconcile import reconcile

from .harness import RawInputs


def parse_docx(data: bytes):
    doc, _notes = extract_docx(data)
    return doc


def _slug(heading: str) -> str:
    """A section key from a heading like '4. Contributing Factors'."""
    h = re.sub(r"^\s*\d+[.)]\s*", "", heading or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_") or "section"


def _derive_template(template_bytes: bytes | None) -> dict:
    """Build a minimal structured template from the raw template document:
    required_sections from its headings, a table_spec if a table shell exists."""
    if not template_bytes:
        return {"title": "", "required_sections": [], "table_specs": {}}
    doc = parse_docx(template_bytes)
    sections = []
    for b in doc.blocks:
        if b.kind == "heading":
            key = _slug(b.text)
            sec = {"key": key, "heading": b.text}
            sections.append(sec)
    table_specs: dict = {}
    for b in doc.blocks:
        if b.kind == "table" and b.table and sections:
            # Attach the table to the last section seen before it.
            tkey = "the_table"
            table_specs[tkey] = {
                "key": tkey,
                "title": b.table.title or "Table {n}",
                "columns": b.table.columns or [],
                "font": b.table.font or "Arial",
                "header_style": b.table.header_style or "table-header",
            }
            break
    # The engine's template contract also requires a furniture block declaring
    # which page elements exist and whether cross-references must resolve.
    furniture = {
        "elements": [
            {"key": "furniture.header", "label": "Header", "required": True},
            {"key": "furniture.footer", "label": "Footer", "required": True},
            {"key": "furniture.page_numbers", "label": "Page numbers", "required": True},
            {"key": "furniture.classification", "label": "Classification", "required": True},
        ],
        "cross_references": {"must_resolve": True},
    }
    return {"title": doc.title or "", "required_sections": sections,
            "table_specs": table_specs, "furniture": furniture}


def _derive_manifest(template: dict, draft_fa: dict) -> dict:
    """Infer a manifest (fields + section_bodies + table) from the derived
    template and the converted draft. Each discrete field in the draft becomes a
    manifest field with a query built from its label; prose sections become
    section_bodies. This is the deterministic 'best guess' at the structure."""
    fields = []
    section_bodies: dict = {}
    table = None
    section_keys = {s["key"] for s in template.get("required_sections", [])}
    for sec in draft_fa.get("sections", []):
        skey = sec.get("key")
        if skey not in section_keys:
            continue
        f = sec.get("fields") or {}
        if isinstance(f, dict) and f:
            for fkey in f:
                fields.append({
                    "key": fkey, "section": skey,
                    "label": fkey.replace("_", " ").title(),
                    "query": f"{skey.replace('_', ' ')} {fkey.replace('_', ' ')}",
                    "extract": "line",
                })
        if sec.get("body") is not None:
            section_bodies[skey] = {
                "query": f"{skey.replace('_', ' ')}",
                "block_anchor": sec.get("heading", "").split(". ", 1)[-1],
            }
        if sec.get("table"):
            tspec = (template.get("table_specs") or {}).get("the_table", {})
            table = {
                "key": "the_table", "section": skey,
                "query": "corrective action owner due date",
                "row_marker": "Action:",
                "cell_queries": {c: c.lower() for c in tspec.get("columns", [])},
                "column_source": {c: c for c in tspec.get("columns", [])},
            }
    return {"title": template.get("title", ""), "fields": fields,
            "section_bodies": section_bodies, "table": table}


def _parse_email(text: str) -> dict:
    """Pull From/Subject/body out of a raw email."""
    frm = re.search(r"^From:\s*(.+)$", text, re.MULTILINE)
    subj = re.search(r"^Subject:\s*(.+)$", text, re.MULTILINE)
    body = re.sub(r"^(From|Subject):.*$", "", text, flags=re.MULTILINE).strip()
    return {"author": (frm.group(1).strip() if frm else "unknown"),
            "subject": (subj.group(1).strip() if subj else ""),
            "body": body}


_VER = re.compile(r"\b(\d+\.\d+(?:\.\d+)?|[A-Z]\d+\.\d+)\b")
_DUR = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)[\s-]?hour", re.I)


def _emails_to_corrections(emails: list[str], manifest: dict, template: dict) -> list[dict]:
    """Deterministically parse reviewer emails into structured corrections.
    Heuristic: look for version fixes, duration fixes, severity escalations
    (conflict), and figure relabels, mapping to the manifest's targets."""
    field_targets = {f"{f['section']}.{f['key']}": f for f in manifest.get("fields", [])}
    body_targets = {f"{s}.body" for s in manifest.get("section_bodies", {})}
    corrections: list[dict] = []
    cid = 0
    for raw in emails:
        e = _parse_email(raw)
        low = e["body"].lower()
        cid += 1
        made = False
        # version fix -> a field whose leaf mentions version/firmware/software
        if any(w in low for w in ("version", "firmware", "software", "rate", "kbps")):
            ver = _VER.search(e["body"])
            tgt = next((t for t in field_targets
                        if any(w in t for w in ("firmware", "version", "rate", "software"))), None)
            if tgt and ver:
                corrections.append(_corr(cid, tgt, ver.group(1), e))
                made = True
        # duration fix -> description.body or a duration field
        if not made and ("duration" in low or "hour" in low or "window" in low):
            dur = _DUR.search(e["body"])
            tgt = next((t for t in body_targets if "description" in t), None) \
                or next(iter(body_targets), None)
            if tgt and dur:
                corrections.append(_corr(cid, tgt, dur.group(0), e))
                made = True
        # severity escalation / assessment -> identifiers.severity (conflict pair)
        if "severity" in low or "unacceptable" in low or "too low" in low:
            tgt = next((t for t in field_targets if t.endswith(".severity")), None)
            # grab a capitalized severity word
            sev = re.search(r"\b(High|Medium|Low|Major|Minor|Moderate|Critical|Routine)\b", e["body"])
            if tgt and sev:
                corrections.append(_corr(cid, tgt, sev.group(1), e))
                made = True
        # figure relabel
        if not made and ("figure" in low or "chart" in low or "image" in low):
            png = re.search(r"\b([a-z0-9_]+\.png)\b", e["body"])
            tgt = next((f"{s['key']}.graphic" for s in template.get("required_sections", [])
                        if "timeline" in s["key"] or "data" in s["key"]), None)
            if tgt and png:
                corrections.append({
                    "id": f"corr_{cid}", "round": 0, "kind": "email",
                    "author": e["author"], "subject": e["subject"],
                    "target": tgt, "operation": "relabel_graphic",
                    "new_value": png.group(1), "body": e["body"]})
                made = True
    return corrections


def _corr(cid: int, target: str, new_value: str, e: dict) -> dict:
    return {"id": f"corr_{cid}", "round": 0, "kind": "email",
            "author": e["author"], "subject": e["subject"], "target": target,
            "operation": "replace", "new_value": new_value, "body": e["body"]}


def run(raw: RawInputs) -> dict:
    """Produce a corrected report from raw inputs via deterministic extraction."""
    template = _derive_template(raw.template_docx)
    # Convert the raw draft against the derived template to get first_attempt.
    draft_fa: dict = {"artifact_kind": "draft", "sections": []}
    if raw.draft_docx:
        try:
            res = convert_document(raw.draft_docx, "draft.docx", template,
                                   {"fields": [], "section_bodies": {}}, "draft")
            draft_fa = res.first_attempt
        except Exception:
            pass
    manifest = _derive_manifest(template, draft_fa)
    corrections = _emails_to_corrections(raw.corrections_emails, manifest, template)
    report = reconcile(
        first_attempt=draft_fa,
        corpus=raw.corpus,
        graphics_manifest=[],
        corrections=corrections,
        template=template,
        project=manifest,
    )
    return report.model_dump()
