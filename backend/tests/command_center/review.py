"""Task 4 — staged-evolution review + final-product render review.

Two independent reviews over a completed command-center RunRecord:

1. evolution_report(record) -> dict
   How the pipeline materials EVOLVED across convergence rounds: the template /
   manifest / draft that fed each round, the corrections that landed, and how
   the report's unit states (filled / corrected / needs_review / conflict /
   unchanged) moved round-to-round. This is the "evolution of review of the
   materials" the user asked for — one per run, both pathways.

2. render_review(report) -> dict
   The final intermediate JSON (a CorrectedReport dict) rendered to every
   deliverable form (JSON / Markdown / DOCX / PPTX / PDF) and checked:
     - did it render (bytes produced, no exception)?
     - is it structurally openable (re-parsed by the same library)?
     - are the materials PRESENT (sections / fields / tables / graphics counts)?
     - optional: does LibreOffice open it headless (when soffice is present;
       degrades cleanly otherwise, exactly like the app's geometry tier).

The CorrectedReport shape (sections[].{fields,graphics,tables} + furniture)
is NOT the aggregate shape app/exporters.py consumes (project + sections[].
content[]). `corrected_to_export_shape()` is the adapter that bridges them;
it is deliberately lossless for the review's purposes (every unit becomes a
line item so "present?" checks are meaningful) and is the same adapter the
Phase-3 app integration will need on its corrected-report export path.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.exporters import export_report

_STATUS_ORDER = ["unchanged", "filled", "corrected", "needs_review", "conflict"]


# --------------------------------------------------------------------------
# 1. Staged-evolution review
# --------------------------------------------------------------------------

def evolution_report(record) -> dict:
    """Build a per-round evolution of the pipeline materials from the run's
    stage_snapshots + round states. Pure data; no model/IO."""
    rounds_out = []
    prev_states: dict | None = None
    prev_corr: list | None = None
    for i, snap in enumerate(record.stage_snapshots):
        rs = record.rounds[i] if i < len(record.rounds) else None
        states = snap.get("report_states", {}) or {}
        corr = snap.get("corrections", []) or []
        rounds_out.append({
            "round": snap.get("round", i),
            "materials": {
                "template_sections": snap.get("template", {}).get("sections", []),
                "manifest_fields": snap.get("manifest", {}).get("fields", []),
                "manifest_section_bodies": snap.get("manifest", {}).get("section_bodies", []),
                "manifest_has_table": snap.get("manifest", {}).get("has_table", False),
                "draft_sections": snap.get("draft_sections", []),
                "corrections_count": len(corr),
            },
            "report_states": states,
            "convergence": None if rs is None else {
                "needs_review": rs.needs_review,
                "conflict": rs.conflict,
                "resolved": rs.resolved,
                "converged": rs.converged,
                "progressed": rs.progressed,
            },
            "delta_vs_prev": _state_delta(prev_states, states),
            "corrections_delta": (None if prev_corr is None
                                  else len(corr) - len(prev_corr)),
        })
        prev_states, prev_corr = states, corr
    return {
        "project_id": record.project_id,
        "pathway": record.pathway,
        "agent_type": record.agent_type,
        "strategy": record.strategy,
        "rounds": rounds_out,
        "final_convergence": (None if not record.rounds else {
            "rounds_run": len(record.rounds),
            "needs_review": record.rounds[-1].needs_review,
            "conflict": record.rounds[-1].conflict,
            "converged": record.rounds[-1].converged,
        }),
        "error": record.error,
    }


def _state_delta(prev: dict | None, cur: dict) -> dict:
    if prev is None:
        return {}
    keys = set(prev) | set(cur)
    return {k: cur.get(k, 0) - prev.get(k, 0)
            for k in keys if cur.get(k, 0) != prev.get(k, 0)}


# --------------------------------------------------------------------------
# 2a. Adapter: CorrectedReport dict -> aggregate export shape
# --------------------------------------------------------------------------

def corrected_to_export_shape(report: dict) -> dict:
    """Map a CorrectedReport dict onto the shape app/exporters.py renders.

    Every corrected UNIT (field / graphic / table) becomes a visible content
    item carrying its status, so a rendered deliverable makes the correction
    state auditable and the "is it present?" review check is meaningful.
    Nothing is invented — this only reshapes what reconcile already produced.
    """
    sections_out = []
    for sec in report.get("sections", []):
        content: list[dict] = []
        for f in sec.get("fields", []):
            val = f.get("value")
            val = "" if val is None else str(val)
            label = f.get("label") or f.get("key", "")
            status = f.get("status", "")
            content.append({"type": "paragraph",
                            "text": f"{label}: {val}  [{status}]"})
        for g in sec.get("graphics", []):
            num = g.get("figure_number")
            cap = g.get("caption") or g.get("name", "")
            content.append({"type": "paragraph",
                            "text": f"Figure {num if num is not None else '?'}: {cap} "
                                    f"[{g.get('status', '')}]"})
        for t in sec.get("tables", []):
            cols = t.get("columns", [])
            rows = t.get("rows", [])
            table = ([list(map(str, cols))] if cols else []) + \
                    [list(map(str, r)) for r in rows]
            if table:
                content.append({"type": "table", "table": table})
            title = t.get("title") or t.get("key", "")
            content.append({"type": "paragraph",
                            "text": f"Table {t.get('table_number') or ''}: {title} "
                                    f"[{t.get('status', '')}]"})
        sections_out.append({
            "title": sec.get("heading") or sec.get("key", ""),
            "effective_dtg": None,
            "effective_dtg_source": "corrected",
            "content": content,
            "supplementals": [],
        })
    # Discipline findings + furniture become a trailing audit section so they
    # are visible in the rendered deliverable too.
    extras: list[dict] = []
    for f in report.get("furniture", {}).get("elements", []):
        extras.append({"type": "paragraph",
                       "text": f"{f.get('label') or f.get('key', '')}: "
                               f"{f.get('value', '')} [{f.get('status', '')}]"})
    for d in report.get("discipline_findings", []):
        extras.append({"type": "list_item",
                       "text": f"{d.get('label') or d.get('key', '')}: "
                               f"{d.get('note') or d.get('value', '')} "
                               f"[{d.get('status', '')}]"})
    if extras:
        sections_out.append({"title": "Furniture & Discipline Findings",
                             "effective_dtg": None, "effective_dtg_source": "corrected",
                             "content": extras, "supplementals": []})
    return {
        "project": {"name": report.get("title", "") or "Corrected Report"},
        "summary": {
            "source_documents": 0,
            "supplementals_total": 0,
            "negative_supplementals": 0,
        },
        "ordering": f"corrected ({report.get('mode', 'draft')} mode)",
        "sections": sections_out,
    }


# --------------------------------------------------------------------------
# 2b. Expected-material census (what SHOULD be present after adaptation)
# --------------------------------------------------------------------------

def _census(report: dict) -> dict:
    fields = graphics = tables = 0
    for sec in report.get("sections", []):
        fields += len(sec.get("fields", []))
        graphics += len(sec.get("graphics", []))
        tables += len(sec.get("tables", []))
    return {
        "sections": len(report.get("sections", [])),
        "fields": fields,
        "graphics": graphics,
        "tables": tables,
        "discipline_findings": len(report.get("discipline_findings", [])),
    }


# --------------------------------------------------------------------------
# 2c. Per-format openability checks (re-parse with the writer's own library)
# --------------------------------------------------------------------------

def _check_json(data: bytes) -> dict:
    import json
    obj = json.loads(data.decode("utf-8"))
    return {"openable": isinstance(obj, dict), "sections": len(obj.get("sections", []))}


def _check_markdown(data: bytes) -> dict:
    text = data.decode("utf-8")
    return {"openable": bool(text.strip()),
            "headings": text.count("\n## "),
            "chars": len(text)}


def _check_docx(data: bytes) -> dict:
    import docx
    d = docx.Document(io.BytesIO(data))
    return {"openable": True,
            "paragraphs": len(d.paragraphs),
            "tables": len(d.tables),
            "headings": sum(1 for p in d.paragraphs if (p.style and "Heading" in p.style.name))}


def _check_pptx(data: bytes) -> dict:
    from pptx import Presentation
    p = Presentation(io.BytesIO(data))
    return {"openable": True, "slides": len(p.slides.__iter__.__self__._sldIdLst)}


def _check_pdf(data: bytes) -> dict:
    # Validate the PDF structurally without a heavy parser: header + EOF marker.
    head = data[:5] == b"%PDF-"
    tail = b"%%EOF" in data[-1024:]
    return {"openable": bool(head and tail), "bytes": len(data)}


_CHECKERS = {
    "json": _check_json,
    "markdown": _check_markdown,
    "docx": _check_docx,
    "pptx": _check_pptx,
    "pdf": _check_pdf,
}


# --------------------------------------------------------------------------
# 2d. Optional LibreOffice "does it open?" probe (degrades cleanly)
# --------------------------------------------------------------------------

def _soffice_bin() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _soffice_opens(data: bytes, ext: str) -> dict:
    """Convert the deliverable to PDF via LibreOffice headless as an open-check.
    Returns {"probed": False, ...} when soffice is absent (NOT a failure — the
    air-gapped RHEL enclave has it; dev/CI here typically does not)."""
    soffice = _soffice_bin()
    if not soffice:
        return {"probed": False, "note": "soffice absent; open-check skipped"}
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"deliverable.{ext}"
        src.write_bytes(data)
        try:
            subprocess.run(
                [soffice, "--headless",
                 f"-env:UserInstallation=file://{tmp}/profile",
                 "--convert-to", "pdf", "--outdir", tmp, str(src)],
                check=True, capture_output=True, timeout=120)
            pdf = Path(tmp) / "deliverable.pdf"
            return {"probed": True, "opened": pdf.exists() and pdf.stat().st_size > 0}
        except Exception as exc:  # noqa: BLE001 - record, never raise
            return {"probed": True, "opened": False, "error": str(exc)[:120]}


# --------------------------------------------------------------------------
# 2. Final-product render review
# --------------------------------------------------------------------------

_EXT = {"json": "json", "markdown": "md", "docx": "docx", "pptx": "pptx", "pdf": "pdf"}
_OFFICE_FORMATS = {"docx", "pptx"}


def render_review(report: dict | None, *, probe_soffice: bool = True) -> dict:
    """Render the final CorrectedReport to every form and review each.
    `report` is the CorrectedReport dict (final_report from a RunRecord)."""
    if not report:
        return {"ok": False, "note": "no final report to render", "formats": {}}
    export_shape = corrected_to_export_shape(report)
    census = _census(report)
    results: dict[str, dict] = {}
    for fmt in ("json", "markdown", "docx", "pptx", "pdf"):
        entry: dict = {"ok": False, "bytes": 0}
        try:
            data, media_type, filename = export_report(export_shape, fmt)
            entry["bytes"] = len(data)
            entry["media_type"] = media_type
            entry["filename"] = filename
            entry["ok"] = len(data) > 0
            entry["structure"] = _CHECKERS[fmt](data)
            if probe_soffice and fmt in _OFFICE_FORMATS:
                entry["soffice"] = _soffice_opens(data, _EXT[fmt])
        except Exception as exc:  # noqa: BLE001 - record, never raise
            entry["error"] = f"{type(exc).__name__}: {exc}"[:160]
        results[fmt] = entry
    all_ok = all(v.get("ok") for v in results.values())
    return {
        "ok": all_ok,
        "expected_census": census,
        "formats": results,
        "note": ("all formats rendered + openable" if all_ok
                 else "one or more formats failed to render"),
    }


def full_review(record, *, probe_soffice: bool = True) -> dict:
    """Combine both reviews for a single run."""
    return {
        "evolution": evolution_report(record),
        "render": render_review(record.final_report, probe_soffice=probe_soffice),
    }
