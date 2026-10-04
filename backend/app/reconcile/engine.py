"""The reconciliation engine — generic and manifest-driven.

One engine for both modes (adversarial-review Loop 6): the template rubric
always runs; comment corrections are additive. The engine holds NO project
knowledge — section vocabulary, discrete fields, retrieval queries, and the
table spec all come from the project manifest. Values are located by semantic
retrieval over the indexed corpus (extract.py), never by project-specific
regex, and are always corpus- or correction-backed (never fabricated).

Pipeline order:
    1. Index corpus; build retrieval extractor
    2. Index corrections by target; detect conflicts (Loop 4)
    3. Build each required section from the template + manifest, filling/
       correcting from corpus + comments at smallest-unit granularity (Loop 5)
    4. Reconcile graphics (relabel / move / insert) by name (graphics = refs)
    5. Reconcile the table declared by the manifest against the template spec
    6. Reconcile furniture (header/footer/classification/page numbers)
    7. Derive figure/table numbering LAST from final reading order, then
       resolve cross-references against it (Loops 2 & 3)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .extract import RetrievalExtractor
from .models import (
    CorrectedField,
    CorrectedFurniture,
    CorrectedGraphic,
    CorrectedReport,
    CorrectedSection,
    CorrectedTable,
    DefectClass,
    Provenance,
    Status,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_corrections(corrections: list[dict]) -> dict[str, list[dict]]:
    by_target: dict[str, list[dict]] = defaultdict(list)
    for c in corrections:
        by_target[c["target"]].append(c)
    return by_target


def collapse_rounds(corrections: list[dict], up_to_round: int | None = None) -> list[dict]:
    """Last-good-wins across correction rounds.

    Corrections may carry an integer `round` (default 0). For each target, only
    the corrections from the LATEST round that touched it survive — a later
    round supersedes earlier rounds for that unit (the human revised their own
    decision). Within a single round, multiple differing values are kept so the
    engine still flags an in-round conflict.

    `up_to_round` limits the fold to rounds <= N (for per-round trajectories).
    """
    considered = [c for c in corrections
                  if up_to_round is None or c.get("round", 0) <= up_to_round]
    latest_round: dict[str, int] = {}
    for c in considered:
        t, r = c["target"], c.get("round", 0)
        if t not in latest_round or r > latest_round[t]:
            latest_round[t] = r
    return [c for c in considered if c.get("round", 0) == latest_round[c["target"]]]


def _resolve_value(
    key: str,
    label: str,
    defect_class: DefectClass,
    current: str,
    correct: str | None,
    correct_source: str | None,
    corrections: list[dict],
    template_rule: str | None = None,
) -> CorrectedField:
    """Core per-unit resolution: apply corpus truth + comment corrections,
    detect conflict, or flag needs_review. Never invents a value."""
    prov = Provenance(rule=template_rule)

    # Conflicting comment corrections on the same unit -> preserve all candidates.
    if len(corrections) > 1:
        distinct = {c.get("new_value") for c in corrections}
        if len(distinct) > 1:
            return CorrectedField(
                # No resolved value on a conflict: the corrections disagree and
                # a human must choose. Leaving the original (wrong) draft value
                # here read as a false "answer" (e.g. "Low" when the candidates
                # were High/Medium). The candidates + note carry everything the
                # reviewer needs; the original wrong value is kept in
                # `original_value` for audit, not presented as the answer.
                key=key, label=label, value=None,
                original_value=current or None,
                status=Status.conflict, defect_class=defect_class,
                provenance=Provenance(
                    corrections=[c["id"] for c in corrections], rule=template_rule,
                ),
                note="Corrections disagree; resolution deferred to a human.",
                candidates=[
                    {"value": c.get("new_value"), "source": c.get("author"), "correction_id": c["id"]}
                    for c in corrections
                ],
            )

    # Prefer a corpus-backed correct value when we have one.
    if correct is not None:
        if correct_source:
            prov.corpus.append(correct_source)
        for c in corrections:
            prov.corrections.append(c["id"])
        if not current:
            status = Status.filled
        elif current.strip().lower() != correct.strip().lower():
            status = Status.corrected
        else:
            status = Status.unchanged
        return CorrectedField(
            key=key, label=label, value=correct, status=status,
            defect_class=defect_class, provenance=prov,
        )

    # A single comment supplies a value but corpus has none: use the comment's value.
    if corrections:
        c = corrections[0]
        new_val = c.get("new_value")
        if new_val:
            prov.corrections.append(c["id"])
            status = Status.filled if not current else Status.corrected
            return CorrectedField(
                key=key, label=label, value=new_val, status=status,
                defect_class=defect_class, provenance=prov,
                note=f"Value supplied by correction {c['id']} (no corpus fact).",
            )

    # Flagged wrong/required but no correct value anywhere -> needs_review.
    if corrections:
        prov.corrections.extend(c["id"] for c in corrections)
    return CorrectedField(
        key=key, label=label, value=current or None,
        status=Status.needs_review, defect_class=defect_class, provenance=prov,
        note="Flagged or required, but no corpus value or corrected value available.",
    )


def _draft_field_value(draft: dict, fkey: str) -> str:
    """Generic lookup of a field's current value in the draft section."""
    return str((draft.get("fields") or {}).get(fkey, "") or "")


def _field_query(fdef: dict) -> str:
    """The retrieval query for a field. A manifest `query` is an optional
    override; otherwise it is auto-derived from the field label + section, which
    matches hand-authored queries on nearly all fields (see try_hints.py). The
    `hint` still disambiguates competing values."""
    if fdef.get("query"):
        return fdef["query"]
    label = str(fdef.get("label", fdef["key"])).replace("_", " ")
    section = str(fdef.get("section", "")).replace("_", " ")
    return f"{label} {section}".strip()


def reconcile(
    first_attempt: dict,
    corpus: dict[str, str],
    graphics_manifest: list[dict],
    corrections: list[dict],
    template: dict,
    project: dict,
    up_to_round: int | None = None,
) -> CorrectedReport:
    mode = "template" if first_attempt.get("artifact_kind") == "template" else "draft"
    extractor = RetrievalExtractor(corpus)
    # Fold correction rounds with last-good-wins before resolution.
    effective = collapse_rounds(corrections, up_to_round)
    by_target = _index_corrections(effective)
    gfx_by_id = {g["graphic_id"]: g for g in graphics_manifest}

    # Manifest-declared discrete fields, grouped by their section.
    fields_by_section: dict[str, list[dict]] = defaultdict(list)
    for f in project.get("fields", []):
        fields_by_section[f["section"]].append(f)
    section_bodies = project.get("section_bodies", {})
    table_manifest = project.get("table")

    draft_sections = {s["key"]: s for s in first_attempt.get("sections", [])}

    report_sections: list[CorrectedSection] = []
    for spec in template["required_sections"]:
        skey = spec["key"]
        draft = draft_sections.get(skey, {})
        sec = CorrectedSection(key=skey, heading=spec["heading"])

        # --- discrete fields declared for this section by the manifest ---
        for fdef in fields_by_section.get(skey, []):
            fkey = fdef["key"]
            fact = extractor.field(_field_query(fdef), fdef.get("extract", "line"),
                                   fdef.get("hint"), fdef.get("source_doc"))
            corr = by_target.get(f"{skey}.{fkey}", [])
            current = _draft_field_value(draft, fkey)
            field = _resolve_value(
                key=f"{skey}.{fkey}", label=fdef.get("label", fkey.title()),
                defect_class=DefectClass.value,
                current=current,
                correct=fact.value if fact else None,
                correct_source=fact.source_doc if fact else None,
                corrections=corr,
                template_rule=f"field {skey}.{fkey}",
            )
            if fact is not None:
                field.provenance.rule = (
                    f"{field.provenance.rule} | {fact.method}:{fact.chunk_id}@{fact.score}"
                )
            sec.fields.append(field)

        # --- narrative body for prose sections declared in the manifest ---
        if skey in section_bodies:
            bdef = section_bodies[skey]
            body_fact = extractor.body(bdef["query"], bdef.get("block_anchor"),
                                       bdef.get("source_doc"))
            corr = by_target.get(f"{skey}.body", [])
            # Label the prose row after its section (e.g. "Description text")
            # instead of a bare, repeated "Body" so each is distinguishable.
            section_label = spec["heading"].split(". ", 1)[-1].strip() or skey.replace("_", " ").title()
            field = _resolve_value(
                key=f"{skey}.body", label=f"{section_label} text",
                defect_class=DefectClass.value,
                current=str(draft.get("body", "") or ""),
                correct=body_fact.value if body_fact else None,
                correct_source=body_fact.source_doc if body_fact else None,
                corrections=corr,
            )
            if body_fact is not None:
                field.provenance.rule = (
                    (field.provenance.rule or "")
                    + f" {body_fact.method}:{body_fact.chunk_id}@{body_fact.score}"
                ).strip()
            sec.fields.append(field)

        # --- table declared by the manifest for this section ---
        if table_manifest and table_manifest.get("section") == skey:
            sec.tables.append(_reconcile_table(draft, extractor, template, by_target, table_manifest))

        # --- graphics required by this section (from the template) ---
        if spec.get("requires_graphic"):
            sec.graphics.append(
                _reconcile_graphic(skey, spec["requires_graphic"], first_attempt, gfx_by_id, by_target)
            )

        report_sections.append(sec)

    furniture = _reconcile_furniture(first_attempt, by_target, template)

    # Numbering + cross-reference resolution LAST, over final reading order.
    _assign_numbering(report_sections)
    xrefs = _resolve_cross_references(first_attempt, report_sections, gfx_by_id, template)
    furniture.cross_references = xrefs

    # Build-discipline inspection: divine the standard from the template
    # document's own evidence (if provided) and inspect the draft against it.
    discipline_findings = _inspect_discipline(
        first_attempt, template, template_evidence=template.get("_evidence"))

    report = CorrectedReport(
        mode=mode,
        title=first_attempt.get("title", template.get("title", "")),
        generated_at=_utcnow(),
        sections=report_sections,
        furniture=furniture,
        discipline_findings=discipline_findings,
    )
    report.summary = _summarize(report)
    return report


def _inspect_discipline(first_attempt: dict, template: dict,
                        template_evidence: dict | None = None) -> list:
    """Run document-inspection against the build discipline when the converter
    supplied observable evidence. Returns discipline findings (may be empty).

    The rubric is DIVINED from the template document's own evidence when
    available (a well-formed template establishes the standard: every figure has
    a centered title, tables have titles, etc.), falling back to the pinned
    profile only where the template is silent — no silent defaults. The DRAFT's
    evidence is then inspected against that learned-from-template rubric."""
    evidence = first_attempt.get("_evidence")
    if not evidence:
        return []
    from ..discipline import learn_discipline, load_discipline
    from ..discipline.inspect import inspect as inspect_discipline

    discipline = load_discipline(template)
    # First, divine the standard from the TEMPLATE document (if we have its
    # evidence); this makes the template docx authoritative for the rubric.
    if template_evidence:
        discipline = learn_discipline(discipline, template_evidence)
    # Then fill any still-undefined text rules from the presented document's own
    # style (declaration/template-learning wins over this).
    discipline = learn_discipline(discipline, evidence)
    return inspect_discipline(evidence, discipline)


def max_round(corrections: list[dict]) -> int:
    return max((c.get("round", 0) for c in corrections), default=0)


def converge(
    first_attempt: dict,
    corpus: dict[str, str],
    graphics_manifest: list[dict],
    corrections: list[dict],
    template: dict,
    project: dict,
) -> dict:
    """Run the correction rounds cumulatively and return the convergence
    trajectory. Each round produces a revision (base=1, round N -> revision N+1)
    with its own summary and an `unresolved` count (needs_review + conflict).

    Convergence = unresolved trending toward zero across rounds. Contradictory
    feedback keeps unresolved stuck (visible non-convergence)."""
    last = max_round(corrections)
    trajectory: list[dict] = []
    for r in range(last + 1):
        report = reconcile(first_attempt, corpus, graphics_manifest, corrections,
                           template, project, up_to_round=r)
        summary = report.summary
        unresolved = summary.get("needs_review", 0) + summary.get("conflict", 0)
        trajectory.append({
            "round": r,
            "revision": r + 1,
            "summary": summary,
            "unresolved": unresolved,
            "report": report.model_dump(),
        })
    final = trajectory[-1] if trajectory else None
    converged = bool(final) and final["unresolved"] == 0
    return {
        "rounds": last,
        "converged": converged,
        "final_unresolved": final["unresolved"] if final else None,
        "trajectory": trajectory,
    }


def _reconcile_table(
    draft: dict, extractor: RetrievalExtractor, template: dict, by_target: dict, table_manifest: dict,
) -> CorrectedTable:
    tkey = table_manifest["key"]
    spec = template["table_specs"][tkey]
    draft_table = draft.get("table", {}) or {}
    formatting: dict[str, Any] = {}

    # Column order + missing columns (formatting defects).
    if draft_table.get("columns") and draft_table["columns"] != spec["columns"]:
        formatting["columns"] = {
            "was": draft_table.get("columns"), "now": spec["columns"],
            "rule": "table_spec.columns (order + required columns)",
        }
    if draft_table.get("font") and draft_table["font"] != spec["font"]:
        formatting["font"] = {"was": draft_table["font"], "now": spec["font"], "rule": "table_spec.font"}
    if draft_table.get("header_style") and draft_table["header_style"] != spec["header_style"]:
        formatting["header_style"] = {
            "was": draft_table["header_style"], "now": spec["header_style"],
            "rule": "table_spec.header_style",
        }

    # Retrieve rows generically; fill each cell ONLY from a corpus value. Any
    # column the corpus does not supply becomes [needs_review] per cell (Loop 5).
    prov = Provenance(rule=f"table_spec.{tkey}")
    corpus_rows = extractor.rows(
        table_manifest["query"], table_manifest["row_marker"], table_manifest.get("cell_queries", {}),
    )
    col_source = table_manifest.get("column_source", {})
    rows: list[list[str]] = []
    unsupported: set[str] = set()
    for cr in corpus_rows:
        prov.corpus.append(cr["source_doc"])
        cells = cr["cells"]
        row: list[str] = []
        for col in spec["columns"]:
            # Map the template column to its corpus label (manifest-declared).
            src_key = col_source.get(col, col)
            val = cells.get(src_key) or cells.get(col)
            if val:
                row.append(val)
            else:
                row.append("[needs_review]")
                unsupported.add(col)
        rows.append(row)
    for c in by_target.get(f"{table_manifest['section']}.table", []):
        prov.corrections.append(c["id"])

    status = Status.filled if not draft_table.get("rows") else Status.corrected
    note = "Cells filled only from corpus facts; "
    note += (f"columns with no corpus value: {sorted(unsupported)} (needs_review per cell)."
             if unsupported else "all columns corpus-backed.")
    return CorrectedTable(
        key=tkey,
        title=spec["title"].replace("{n}", "1"),
        columns=spec["columns"], rows=rows,
        font=spec["font"], header_style=spec["header_style"],
        status=status, provenance=prov, formatting=formatting, note=note,
    )


def _reconcile_graphic(
    section_key: str, graphic_id: str, first_attempt: dict, gfx_by_id: dict, by_target: dict,
) -> CorrectedGraphic:
    truth = gfx_by_id[graphic_id]
    prov = Provenance(corpus=[truth["source_doc"]], rule=f"section {section_key} requires {graphic_id}")

    placed_section = None
    placed_name = None
    for sec in first_attempt.get("sections", []):
        for g in sec.get("graphics", []) or []:
            if g.get("ref_name") == truth["name"]:
                placed_section, placed_name = sec["key"], g["ref_name"]

    relabel = None
    for c in by_target.get(f"{section_key}.graphic", []):
        if c.get("operation") == "relabel_graphic" and c.get("new_value") == truth["name"]:
            relabel = c

    if relabel:
        prov.corrections.append(relabel["id"])
        return CorrectedGraphic(
            graphic_id=graphic_id, name=truth["name"], caption=truth["caption"],
            section=section_key, status=Status.corrected, provenance=prov,
            note=f"Relabeled from '{relabel['old_value']}' to '{truth['name']}'.",
        )
    if placed_name is None:
        return CorrectedGraphic(
            graphic_id=graphic_id, name=truth["name"], caption=truth["caption"],
            section=section_key, status=Status.filled, provenance=prov,
            note="Graphic was missing; inserted into its required section.",
        )
    if placed_section != section_key:
        return CorrectedGraphic(
            graphic_id=graphic_id, name=truth["name"], caption=truth["caption"],
            section=section_key, status=Status.corrected, provenance=prov,
            note=f"Moved from '{placed_section}' to '{section_key}'.",
        )
    return CorrectedGraphic(
        graphic_id=graphic_id, name=truth["name"], caption=truth["caption"],
        section=section_key, status=Status.unchanged, provenance=prov,
    )


def _reconcile_furniture(first_attempt: dict, by_target: dict,
                         template: dict | None = None) -> CorrectedFurniture:
    template = template or {}
    draft_f = first_attempt.get("furniture", {}) or {}

    # The template declares WHICH page elements this document type has. Default
    # to the government incident set so existing projects are unchanged.
    declared = _declared_page_elements(template)

    built: dict[str, CorrectedField] = {}
    for name in declared:
        builder = _PAGE_ELEMENT_BUILDERS.get(name)
        if builder is None:
            continue  # unknown element name in a template -> skip, no crash
        built[name] = builder(first_attempt, draft_f, by_target)

    elements = [built[n] for n in declared if n in built]
    return CorrectedFurniture(
        elements=elements,
        header=built.get("header"),
        footer=built.get("footer"),
        classification=built.get("classification"),
        page_numbers=built.get("page_numbers"),
    )


# Default page-element set: the government incident report furniture. A template
# may override via build_discipline/furniture "page_elements": [...].
_DEFAULT_PAGE_ELEMENTS = ["header", "footer", "page_numbers", "classification"]


def _declared_page_elements(template: dict) -> list[str]:
    furn = template.get("furniture") or {}
    declared = furn.get("page_elements")
    if isinstance(declared, list) and declared:
        return [str(x) for x in declared]
    return list(_DEFAULT_PAGE_ELEMENTS)


# --- Page-element builders (one per known element) ---------------------------
# Each takes (first_attempt, draft_furniture, by_target) -> CorrectedField, so
# a template can mix and match them per document type.

def _pe_header(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    header_text = draft_f.get("header", {}).get("text", "")
    return CorrectedField(
        key="furniture.header", label="Header",
        value=header_text or first_attempt.get("title", ""),
        status=Status.unchanged if header_text else Status.filled,
        defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.header.must_contain=report_title"),
    )


def _pe_page_numbers(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    pn_on = bool(draft_f.get("page_numbers"))
    return CorrectedField(
        key="furniture.page_numbers", label="Page Numbers", value=True,
        status=Status.unchanged if pn_on else Status.corrected,
        defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.footer.must_contain=page_number"),
    )


def _pe_classification(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    classif_corr = by_target.get("furniture.classification", [])
    if classif_corr:
        return _resolve_value(
            key="furniture.classification", label="Classification Marking",
            defect_class=DefectClass.furniture, current=draft_f.get("classification", ""),
            correct=None, correct_source=None, corrections=classif_corr,
            template_rule="furniture.footer.must_contain=classification",
        )
    return CorrectedField(
        key="furniture.classification", label="Classification Marking",
        value=draft_f.get("classification") or None,
        status=Status.needs_review, defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.footer.must_contain=classification"),
        note="Required by template footer rule; no corpus source for a marking.",
    )


def _pe_footer(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    footer_ok = bool(draft_f.get("footer", {}).get("text"))
    return CorrectedField(
        key="furniture.footer", label="Footer",
        value="Page number (added) + classification marking (still needed)",
        status=Status.corrected if not footer_ok else Status.unchanged,
        defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.footer.required"),
        note="Page numbering was added to the footer. The classification marking "
             "still needs a human — see the Classification Marking row above.",
    )


def _pe_revision_history(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    """Revision/version block common to engineering docs (ICDs, specs)."""
    rev_corr = by_target.get("furniture.revision_history", [])
    if rev_corr:
        return _resolve_value(
            key="furniture.revision_history", label="Revision History",
            defect_class=DefectClass.furniture, current=str(draft_f.get("revision_history", "") or ""),
            correct=None, correct_source=None, corrections=rev_corr,
            template_rule="furniture.revision_history.required",
        )
    cur = draft_f.get("revision_history")
    return CorrectedField(
        key="furniture.revision_history", label="Revision History",
        value=cur or None,
        status=Status.unchanged if cur else Status.needs_review,
        defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.revision_history.required"),
        note="" if cur else "Required by the template; no revision entry supplied.",
    )


def _pe_approval_block(first_attempt: dict, draft_f: dict, by_target: dict) -> CorrectedField:
    """Signature/approval block (approver + date), e.g. for ICDs/specs."""
    appr_corr = by_target.get("furniture.approval_block", [])
    if appr_corr:
        return _resolve_value(
            key="furniture.approval_block", label="Approval Block",
            defect_class=DefectClass.furniture, current=str(draft_f.get("approval_block", "") or ""),
            correct=None, correct_source=None, corrections=appr_corr,
            template_rule="furniture.approval_block.required",
        )
    cur = draft_f.get("approval_block")
    return CorrectedField(
        key="furniture.approval_block", label="Approval Block",
        value=cur or None,
        status=Status.unchanged if cur else Status.needs_review,
        defect_class=DefectClass.furniture,
        provenance=Provenance(rule="furniture.approval_block.required"),
        note="" if cur else "Required by the template; no approver recorded.",
    )


_PAGE_ELEMENT_BUILDERS = {
    "header": _pe_header,
    "footer": _pe_footer,
    "page_numbers": _pe_page_numbers,
    "classification": _pe_classification,
    "revision_history": _pe_revision_history,
    "approval_block": _pe_approval_block,
}


def _assign_numbering(sections: list[CorrectedSection]) -> None:
    """Figure/table numbers derived from final reading order (Loop 2)."""
    fig_n = 0
    tbl_n = 0
    for sec in sections:
        for g in sec.graphics:
            fig_n += 1
            g.figure_number = fig_n
        for t in sec.tables:
            tbl_n += 1
            t.table_number = tbl_n
            t.title = t.title.replace("Table 1", f"Table {tbl_n}")


def _resolve_cross_references(
    first_attempt: dict, sections: list[CorrectedSection], gfx_by_id: dict, template: dict,
) -> list[CorrectedField]:
    """Re-resolve each cross-reference against final figure numbering (Loops 2 & 3)."""
    if not template["furniture"].get("cross_references", {}).get("must_resolve"):
        return []
    fig_num = {g.graphic_id: g.figure_number for sec in sections for g in sec.graphics}
    out: list[CorrectedField] = []
    for xref in first_attempt.get("cross_references", []):
        target_gid = xref.get("points_to_graphic")
        n = fig_num.get(target_gid)
        prov = Provenance(rule="furniture.cross_references.must_resolve")
        if n is None:
            out.append(CorrectedField(
                key=f"xref.{xref['id']}", label=f"Cross-reference ({xref['text']})",
                value=xref["text"], status=Status.needs_review,
                defect_class=DefectClass.furniture, provenance=prov,
                note="Cross-reference target is not placed in the report.",
            ))
        else:
            resolved = f"Figure {n}"
            out.append(CorrectedField(
                key=f"xref.{xref['id']}", label=f"Cross-reference ({xref['text']})",
                value=resolved,
                status=Status.corrected if resolved != xref["text"] else Status.unchanged,
                defect_class=DefectClass.furniture, provenance=prov,
                note=f"Re-resolved from '{xref['text']}' to '{resolved}' after numbering.",
            ))
    return out


def _summarize(report: CorrectedReport) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    units = 0

    def tally(status: Status) -> None:
        nonlocal units
        counts[status.value] += 1
        units += 1

    for sec in report.sections:
        for f in sec.fields:
            tally(f.status)
        for g in sec.graphics:
            tally(g.status)
        for t in sec.tables:
            tally(t.status)
    for f in report.furniture.elements:
        tally(f.status)
    for x in report.furniture.cross_references:
        tally(x.status)
    for f in report.discipline_findings:
        tally(f.status)

    counts["total_units"] = units
    return dict(counts)
