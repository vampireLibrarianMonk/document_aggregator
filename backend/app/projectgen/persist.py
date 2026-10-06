"""Persist a validated ProjectSpec to the on-disk project tree.

Deterministic: the same spec always writes the same files. After writing the
JSON + corpus, it generates the real PNG figures and the template/draft DOCX so
the project is immediately usable in every source-fidelity mode.

The write is staged in a temp dir and swapped into place so a half-written
project never appears on disk.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..config import settings
from .schema import ProjectSpec, validate_spec

# Single source of truth lives in config (previously duplicated here).
BUNDLED_PROJECT_ROOT = settings.BUNDLED_PROJECT_ROOT

# These filenames are what the loaders expect (kept for compatibility).
TEMPLATE_FILE = "incident_report_template.json"
DRAFT_FILE = "incident_report_draft.json"


def next_project_id() -> str:
    """The next free numeric project id, considering BOTH the legacy bundle and
    the unified project store so a generated project never collides with a demo
    one. (Ids stay numeric for now; the proj_* id model is a later phase.)"""
    nums = [int(sid) for sid, _ in settings.iter_project_data_dirs() if sid.isdigit()]
    return str((max(nums) + 1) if nums else 1)


def _manifest_json(spec: ProjectSpec, project_id: str) -> dict:
    fields = []
    for f in spec.fields:
        fd = {"key": f.key, "label": f.label, "section": f.section, "extract": f.extract}
        if f.hint:
            fd["hint"] = f.hint
        if f.query:
            fd["query"] = f.query
        fields.append(fd)

    section_bodies = {}
    for sb in spec.section_bodies:
        entry = {"query": sb.query}
        if sb.block_anchor:
            entry["block_anchor"] = sb.block_anchor
        section_bodies[sb.section] = entry

    manifest = {
        "id": project_id,
        "title": spec.title,
        "domain": spec.domain,
        "note": "Generated project. Everything project-specific lives here so the "
                "reconciliation engine stays generic.",
        "corpus_docs": [d.name for d in spec.corpus],
        "fields": fields,
        "section_bodies": section_bodies,
    }
    if spec.table:
        column_source = {}
        cell_queries = {}
        for col in spec.table.columns:
            if col.corpus_label and col.corpus_label != col.name:
                column_source[col.name] = col.corpus_label
            else:
                column_source[col.name] = col.name
            if col.cell_query:
                cell_queries[col.corpus_label or col.name] = col.cell_query
        manifest["table"] = {
            "key": spec.table.key,
            "section": spec.table.section,
            "query": spec.table.query,
            "row_marker": spec.table.row_marker,
            "cell_queries": cell_queries,
            "column_source": column_source,
        }
    return manifest


def _template_json(spec: ProjectSpec) -> dict:
    required = []
    for s in spec.required_sections:
        entry: dict[str, Any] = {"key": s.key, "heading": s.heading}
        if s.requires_graphic:
            entry["requires_graphic"] = s.requires_graphic
        if s.requires_table:
            entry["requires_table"] = s.requires_table
        # Informational: declared field keys for this section.
        fkeys = [f.key for f in spec.fields if f.section == s.key]
        if fkeys:
            entry["fields"] = fkeys
        required.append(entry)

    tmpl = {
        "artifact_kind": "template",
        "title": f"{spec.title} Template",
        "note": "Generated template rubric: the pipeline fills it from the corpus and "
                "judges structure, formatting, and page elements against it.",
        "build_discipline": {"profile": spec.build_discipline_profile},
        "required_sections": required,
        "furniture": {
            "header": {"required": True, "must_contain": ["report_title"]},
            "footer": {"required": True, "must_contain": ["page_number", "classification"]},
            "figures": {"caption_required": True, "numbering": "sequential",
                        "caption_format": "Figure {n}: {caption}"},
            "tables": {"title_required": True, "title_position": "above", "numbering": "sequential"},
            "cross_references": {"must_resolve": True},
        },
    }
    if spec.table:
        tmpl["table_specs"] = {
            spec.table.key: {
                "title_required": True,
                "title": spec.table.title,
                "columns": [c.name for c in spec.table.columns],
                "font": spec.table.font,
                "header_style": spec.table.header_style,
            }
        }
    return tmpl


def _draft_json(spec: ProjectSpec) -> dict:
    sections = []
    for ds in spec.draft_sections:
        entry: dict = {"key": ds.key, "heading": ds.heading}
        if ds.fields:
            entry["fields"] = ds.fields
        if ds.body:
            entry["body"] = ds.body
        if ds.graphics:
            entry["graphics"] = ds.graphics
        if ds.table is not None:
            entry["table"] = ds.table
        sections.append(entry)
    return {
        "artifact_kind": "draft",
        "title": spec.draft_title or spec.title,
        "note": "Generated first attempt: looks finished but carries seeded defects. "
                "Judged against the template rules plus reviewer comments.",
        "sections": sections,
        "furniture": {
            "header": {"text": spec.draft_header},
            "footer": {"text": spec.draft_footer},
            "page_numbers": spec.draft_page_numbers,
            "classification": spec.draft_classification,
        },
        "cross_references": [c.model_dump() for c in spec.cross_references],
        "_seeded_defects": spec.seeded_defects,
    }


def _graphics_json(spec: ProjectSpec) -> dict:
    return {
        "note": "Graphic references for this project; real PNGs are generated under "
                "corpus/figures/ with managed titles and sizes.",
        "graphics": [
            {
                "graphic_id": g.graphic_id, "name": g.name, "caption": g.caption,
                "source_doc": g.source_doc, "belongs_in_section": g.belongs_in_section,
            }
            for g in spec.graphics
        ],
    }


def _corrections_json(spec: ProjectSpec) -> dict:
    return {
        "note": "Generated reviewer comments/emails. In draft mode these judge value "
                "defects. Disagreeing corrections on one unit become a conflict.",
        "corrections": [c.model_dump() for c in spec.corrections],
    }


def persist_spec(spec: ProjectSpec, project_id: str | None = None,
                 build_assets: bool = True) -> str:
    """Validate + write a project to disk and return its id. Raises ValueError
    with all problems if the spec is invalid (nothing is written)."""
    problems = validate_spec(spec)
    if problems:
        raise ValueError("invalid project spec:\n  - " + "\n  - ".join(problems))

    sid = project_id or next_project_id()
    # Write into the UNIFIED project store: the correction data (corpus/template/
    # draft/corrections) lives at DATA_DIR/projects/<sid>/data (matching
    # settings.project_data_dir). Staging is a sibling under the same project dir
    # so the rename swap stays atomic (same filesystem). A minimal project.json
    # is written so the store recognizes it.
    project_root = settings.project_dir(sid)
    project_root.mkdir(parents=True, exist_ok=True)
    dest = settings.project_data_dir(sid)
    staging = project_root / ".data.staging"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "corpus").mkdir(parents=True)
    (staging / "first_attempt").mkdir(parents=True)
    (staging / "corrections").mkdir(parents=True)

    def _w(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    _w(staging / "project.json", _manifest_json(spec, sid))
    _w(staging / "first_attempt" / TEMPLATE_FILE, _template_json(spec))
    _w(staging / "first_attempt" / DRAFT_FILE, _draft_json(spec))
    _w(staging / "corpus" / "graphics.json", _graphics_json(spec))
    _w(staging / "corrections" / "comments.json", _corrections_json(spec))
    for doc in spec.corpus:
        (staging / "corpus" / doc.name).write_text(doc.text, encoding="utf-8")

    # Swap staging -> final atomically.
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)

    # Register the directory as a project so the five tabs can scope to it. Done
    # best-effort; the project payload above is the authoritative artifact.
    _ensure_project_record(sid, spec)

    if build_assets:
        _build_assets(sid)
    return sid


def _ensure_project_record(project_id: str, spec: ProjectSpec) -> None:
    """Write a minimal project.json + the standard project subdirs so the unified
    store treats this project directory as a real project the five tabs scope to.
    Best-effort: the project payload is authoritative regardless."""
    try:
        from ..models import Project
        from ..store import store
        if store.get_project(project_id) is None:
            store.create_project(Project(id=project_id, name=spec.title or project_id))
    except Exception:
        pass


def _build_assets(project_id: str) -> None:
    """Generate the real PNG figures and the template/draft DOCX/PPTX/PDF for a
    freshly-written project, reusing the existing build scripts. Resolves the
    project directory via the shared resolver (unified project store first)."""
    import sys
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    sdir = settings.resolve_project_data_dir(project_id)
    try:
        import build_project_graphics as bpg
        bpg.build_project(project_id)
    except Exception:
        pass  # figures are best-effort; project still reconciles in JSON mode
    try:
        import json as _json

        import build_sample_docs as bsd
        scn = _json.loads((sdir / "project.json").read_text(encoding="utf-8"))
        out = sdir / "first_attempt" / "generated"
        for kind, jname in (("draft", DRAFT_FILE), ("template", TEMPLATE_FILE)):
            src = _json.loads((sdir / "first_attempt" / jname).read_text(encoding="utf-8"))
            bsd.build_docx(src, scn, out / f"{kind}.docx")
            bsd.build_pptx(src, scn, out / f"{kind}.pptx")
            bsd.build_pdf(src, scn, out / f"{kind}.pdf")
    except Exception:
        pass  # docx generation best-effort; JSON baseline always works
