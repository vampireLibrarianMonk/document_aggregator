"""Approach C — simple correction pass (no manifest-driven extraction).

Convert the raw draft to sections, parse the emails into corrections, then apply
them directly: match a correction to a draft field/body by leaf name, set the
value if grounded in corpus/email, surface disagreements as conflict, and leave
required-but-absent units as needs_review. Emits the same CorrectedReport shape.

This is the pragmatic path: it does NOT try to reconstruct the manifest's
field-extraction queries or table spec; it corrects what the draft already has.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.convert import convert_document
from app.reconcile.models import (
    CorrectedField,
    CorrectedFurniture,
    CorrectedReport,
    CorrectedSection,
    DefectClass,
    Provenance,
    Status,
)

from .approach_a import _derive_template, _parse_email, _slug
from .harness import RawInputs

_VER = re.compile(r"\b(\d+\.\d+(?:\.\d+)?|[A-Z]\d+\.\d+)\b")
_DUR = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)[\s-]?hour", re.I)
_SEV = re.compile(r"\b(High|Medium|Low|Major|Minor|Moderate|Critical|Routine|Approved|Rejected)\b")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _grounded(value: str, corpus: str, email: str) -> bool:
    nv = _norm(value)
    return not nv or nv in _norm(corpus) or nv in _norm(email)


def _emails_to_targeted(emails: list[str]) -> list[dict]:
    """Each email -> {leaf, value, email, author} best-guess, keeping ALL emails
    so disagreements remain visible as separate proposals on the same leaf."""
    out = []
    for raw in emails:
        e = _parse_email(raw)
        low = e["body"].lower()
        leaf, val = None, None
        if any(w in low for w in ("version", "firmware", "software", "rate")):
            m = _VER.search(e["body"])
            if m:
                leaf, val = ("firmware" if "firmware" in low else
                             "signaling_rate" if "rate" in low else "software_version"), m.group(1)
        elif "severity" in low or "unacceptable" in low or "too low" in low:
            m = _SEV.search(e["body"])
            if m:
                leaf, val = "severity", m.group(1)
        elif "approve" in low or "approval" in low or "not ready" in low or "release" in low:
            m = _SEV.search(e["body"])
            if m:
                leaf, val = "approval_status", m.group(1)
        elif "hour" in low or "duration" in low or "window" in low:
            m = _DUR.search(e["body"])
            if m:
                leaf, val = "duration", m.group(0)
        elif "figure" in low or "chart" in low or "image" in low:
            m = re.search(r"\b([a-z0-9_]+\.png)\b", e["body"])
            if m:
                leaf, val = "graphic", m.group(1)
        if leaf and val:
            out.append({"leaf": leaf, "value": val, "email": e["body"], "author": e["author"]})
    return out


def run(raw: RawInputs) -> dict:
    template = _derive_template(raw.template_docx)
    draft_fa: dict = {"sections": []}
    if raw.draft_docx:
        try:
            res = convert_document(raw.draft_docx, "draft.docx", template,
                                   {"fields": [], "section_bodies": {}}, "draft")
            draft_fa = res.first_attempt
        except Exception:
            pass

    proposals = _emails_to_targeted(raw.corrections_emails)
    by_leaf: dict[str, list[dict]] = defaultdict(list)
    for p in proposals:
        by_leaf[p["leaf"]].append(p)

    corpus_blob = raw.corpus_blob
    sections: list[CorrectedSection] = []
    for sec in draft_fa.get("sections", []):
        skey = sec.get("key") or _slug(sec.get("heading", ""))
        cs = CorrectedSection(key=skey, heading=sec.get("heading", skey))
        fields = sec.get("fields") or {}
        if isinstance(fields, dict):
            for fkey, fval in fields.items():
                cs.fields.append(_resolve_leaf(skey, fkey, fval, by_leaf, corpus_blob))
        if sec.get("body") is not None:
            cs.fields.append(_resolve_body(skey, sec.get("body", ""), by_leaf, corpus_blob))
        sections.append(cs)

    report = CorrectedReport(
        mode="draft", title=draft_fa.get("title", template.get("title", "")),
        generated_at="", sections=sections,
        furniture=CorrectedFurniture(),
    )
    report.summary = _summarize(report)
    return report.model_dump()


def _resolve_leaf(skey: str, fkey: str, current, by_leaf, corpus) -> CorrectedField:
    props = by_leaf.get(fkey, [])
    key = f"{skey}.{fkey}"
    label = fkey.replace("_", " ").title()
    if not props:
        return CorrectedField(key=key, label=label, value=current,
                              status=Status.unchanged, defect_class=DefectClass.value)
    distinct = {_norm(p["value"]) for p in props}
    if len(distinct) > 1:   # genuine disagreement
        return CorrectedField(
            key=key, label=label, value=None, original_value=current,
            status=Status.conflict, defect_class=DefectClass.value,
            candidates=[{"value": p["value"], "author": p["author"]} for p in props])
    val = props[0]["value"]
    if not _grounded(val, corpus, props[0]["email"]):
        return CorrectedField(key=key, label=label, value=None, original_value=current,
                              status=Status.needs_review, defect_class=DefectClass.value)
    return CorrectedField(key=key, label=label, value=val, original_value=current,
                          status=Status.corrected, defect_class=DefectClass.value,
                          provenance=Provenance(corrections=[props[0]["author"]]))


def _resolve_body(skey: str, current: str, by_leaf, corpus) -> CorrectedField:
    key = f"{skey}.body"
    props = by_leaf.get("duration", []) if skey == "description" else []
    if props and _grounded(props[0]["value"], corpus, props[0]["email"]):
        return CorrectedField(key=key, label=f"{skey.title()} text", value=current,
                              status=Status.corrected, defect_class=DefectClass.value)
    status = Status.needs_review if not current.strip() else Status.unchanged
    return CorrectedField(key=key, label=f"{skey.title()} text", value=current,
                          status=status, defect_class=DefectClass.value)


def _summarize(report: CorrectedReport) -> dict:
    counts: dict[str, int] = defaultdict(int)
    total = 0
    for sec in report.sections:
        for f in sec.fields:
            counts[f.status.value] += 1
            total += 1
    counts["total_units"] = total
    return dict(counts)
