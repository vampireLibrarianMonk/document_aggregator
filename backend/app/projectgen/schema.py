"""The ProjectSpec: a single structured description of a complete project.

This is the contract every generator (deterministic or LLM) must satisfy, and
the gate before anything is written to disk. If a spec validates here, it is
guaranteed to produce a project the engine can reconcile and that the
invariant suite will accept.

A ProjectSpec maps 1:1 onto the four on-disk components:
  - manifest  (project.json): fields, section_bodies, table
  - template  (incident_report_template.json): required_sections, table_specs,
              furniture, build_discipline
  - draft     (incident_report_draft.json): per-section values + furniture +
              cross_references + seeded-defect inventory
  - corpus    (corpus/*.txt + graphics.json)
  - corrections (corrections/comments.json)

Design rule mirrored from the rest of the platform: NOTHING is fabricated. Every
value a correction or the corpus asserts must be present in the corpus text the
spec itself carries, so a generated project is as traceable as a hand-authored
one. The validator enforces this (see _check_corpus_grounding).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Field extraction kinds the engine's RetrievalExtractor supports.
EXTRACT_KINDS = {"token", "line", "version", "date", "duration", "none"}
# Correction operations the engine understands (native dict form).
CORR_OPS = {"replace", "relabel_graphic", "flag"}


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

class CorpusDoc(BaseModel):
    """A ground-truth source document (plain text / markdown body)."""
    name: str                       # e.g. "field_report_2026-03-02.txt"
    text: str                       # the full document body (the ground truth)

    @field_validator("name")
    @classmethod
    def _ext(cls, v: str) -> str:
        if not (v.endswith(".txt") or v.endswith(".md")):
            raise ValueError("corpus doc name must end in .txt or .md")
        return v


class GraphicSpec(BaseModel):
    graphic_id: str                 # e.g. "gfx_topology"
    name: str                       # managed filename, e.g. "site_network_topology.png"
    caption: str = ""
    source_doc: str                 # which corpus doc it belongs to (provenance)
    belongs_in_section: str         # the section key it must appear in

    @field_validator("name")
    @classmethod
    def _png(cls, v: str) -> str:
        if not v.endswith(".png"):
            raise ValueError("graphic name must end in .png")
        return v


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

class FieldSpec(BaseModel):
    key: str
    label: str
    section: str
    extract: str = "line"
    hint: str | None = None
    query: str | None = None
    source_doc: str | None = None   # scope retrieval to the doc the value lives in

    @field_validator("extract")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in EXTRACT_KINDS:
            raise ValueError(f"extract must be one of {sorted(EXTRACT_KINDS)}")
        return v


class SectionBodySpec(BaseModel):
    section: str                    # section key this prose body belongs to
    query: str
    block_anchor: str | None = None


class TableColumnSpec(BaseModel):
    name: str                       # template column name, e.g. "Action"
    corpus_label: str | None = None  # corpus cell label if different (column_source)
    cell_query: str | None = None   # retrieval query for this cell


class TableSpec(BaseModel):
    key: str
    section: str
    title: str                      # e.g. "Table {n}: Corrective Actions"
    columns: list[TableColumnSpec]
    font: str = "Arial"
    header_style: str = "table-header"
    query: str = ""
    row_marker: str = ""


# --------------------------------------------------------------------------
# Template (rubric)
# --------------------------------------------------------------------------

class SectionSpec(BaseModel):
    model_config = {"coerce_numbers_to_str": True}

    key: str
    heading: str                    # e.g. "1. Identifiers"
    requires_graphic: str | None = None   # a graphic_id (the first/primary one)
    requires_graphics: list[str] = Field(default_factory=list)  # all graphic_ids in order
    requires_table: str | None = None     # a table key

    @field_validator("requires_graphic", "requires_table", mode="before")
    @classmethod
    def _drop_falsey(cls, v: Any) -> Any:
        # Models sometimes emit false/"" instead of omitting an optional ref.
        return None if (v is False or v == "" or v is True) else v


# --------------------------------------------------------------------------
# Draft (the flawed first attempt) + corrections
# --------------------------------------------------------------------------

class DraftSection(BaseModel):
    # The draft is the flawed first attempt; be tolerant of model shape quirks
    # here (None bodies, list-shaped fields/graphics) since the exact draft
    # shape is less load-bearing than the template/corpus/corrections.
    model_config = {"coerce_numbers_to_str": True}

    key: str
    heading: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)   # fkey -> (often wrong) value
    body: str = ""
    graphics: list[dict[str, Any]] = Field(default_factory=list)
    table: dict[str, Any] | None = None

    @field_validator("body", mode="before")
    @classmethod
    def _body_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("fields", mode="before")
    @classmethod
    def _fields_dict(cls, v: Any) -> dict:
        if v is None:
            return {}
        if isinstance(v, list):  # [{key,value}] -> {key: value}
            return {i.get("key"): i.get("value") for i in v
                    if isinstance(i, dict) and i.get("key") is not None}
        return v

    @field_validator("graphics", mode="before")
    @classmethod
    def _graphics_list(cls, v: Any) -> list:
        if not v:
            return []
        out = []
        for g in v:
            if isinstance(g, dict):
                out.append(g)
            elif isinstance(g, str):  # a bare ref name/id
                out.append({"ref_name": g})
        return out

    @field_validator("table", mode="before")
    @classmethod
    def _table_dict(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):  # a bare table-key ref -> minimal placeholder dict
            return {"columns": [], "rows": []}
        return v


class CrossRef(BaseModel):
    model_config = {"coerce_numbers_to_str": True}

    id: str
    in_section: str
    text: str                       # e.g. "Figure 2"
    points_to_graphic: str | None = None

    @field_validator("points_to_graphic", mode="before")
    @classmethod
    def _ptr(cls, v: Any) -> Any:
        return None if isinstance(v, bool) or v == "" else v


class CorrectionSpec(BaseModel):
    id: str
    kind: str = "comment"           # comment | email | correction | interview_note
    author: str = "reviewer"
    subject: str = ""
    target: str                     # "section.field" | "section.graphic" | "furniture.x"
    operation: str = "replace"
    old_value: Any = None
    new_value: Any = None
    body: str = ""
    round: int = 0

    @field_validator("operation")
    @classmethod
    def _op(cls, v: str) -> str:
        if v not in CORR_OPS:
            raise ValueError(f"operation must be one of {sorted(CORR_OPS)}")
        return v


# --------------------------------------------------------------------------
# The full spec
# --------------------------------------------------------------------------

class ProjectSpec(BaseModel):
    title: str
    domain: str = ""
    # Template structure
    required_sections: list[SectionSpec]
    fields: list[FieldSpec] = Field(default_factory=list)
    section_bodies: list[SectionBodySpec] = Field(default_factory=list)
    table: TableSpec | None = None
    build_discipline_profile: str = "gov_standard"
    # Ground truth
    corpus: list[CorpusDoc]
    graphics: list[GraphicSpec] = Field(default_factory=list)
    # The flawed first attempt
    draft_title: str = ""
    draft_sections: list[DraftSection] = Field(default_factory=list)
    draft_header: str = ""
    draft_footer: str = ""
    draft_page_numbers: bool = False
    draft_classification: str = ""
    cross_references: list[CrossRef] = Field(default_factory=list)
    # Human feedback + the known defect inventory (documentation only)
    corrections: list[CorrectionSpec] = Field(default_factory=list)
    seeded_defects: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Validation: enforce engine-consumability + no fabrication
# --------------------------------------------------------------------------

def salvage_spec(spec: ProjectSpec) -> ProjectSpec:
    """Drop individually-invalid items so a mostly-good generated spec is kept
    rather than rejected wholesale: corrections targeting non-resolvable units
    or asserting ungrounded values are removed, and graphics with a bad
    source_doc are repointed to the first corpus doc. Returns a new spec; run
    validate_spec again after. This keeps an LLM's good work while refusing to
    persist anything that would violate the engine contract or fabricate."""
    resolvable = _resolvable_targets(spec)
    corpus_names = {d.name for d in spec.corpus}
    corpus_blob = _norm(" \n ".join(d.text for d in spec.corpus))
    first_doc = spec.corpus[0].name if spec.corpus else ""

    # Repoint graphics whose source_doc isn't a real corpus name.
    for g in spec.graphics:
        if g.source_doc not in corpus_names and first_doc:
            g.source_doc = first_doc

    kept = []
    dropped = 0
    fabrication = 0
    for c in spec.corrections:
        base = c.target.split("[", 1)[0]
        if c.target not in resolvable and base not in resolvable:
            dropped += 1
            continue  # drop non-resolvable target
        if c.operation == "replace" and c.new_value not in (None, ""):
            nv = _norm(str(c.new_value))
            if nv and nv not in corpus_blob and nv not in _norm(c.body):
                dropped += 1
                fabrication += 1
                continue  # drop ungrounded (possible fabrication)
        kept.append(c)
    spec.corrections = kept
    # Stash counts for the metrics layer (ignored by every other consumer).
    spec._salvage_dropped = dropped          # type: ignore[attr-defined]
    spec._salvage_fabrication = fabrication  # type: ignore[attr-defined]
    return spec


def validate_spec(spec: ProjectSpec) -> list[str]:
    """Return a list of human-readable problems. Empty list == valid and safe to
    persist. These checks mirror exactly what reconcile()/load_* require, plus
    the no-fabrication rule that corrections and corpus-backed values must be
    grounded in the corpus text the spec carries."""
    problems: list[str] = []
    section_keys = {s.key for s in spec.required_sections}

    if not spec.required_sections:
        problems.append("required_sections is empty")
    if not spec.corpus:
        problems.append("corpus is empty (there must be ground-truth source docs)")

    # Every section referenced by a field/body/table must exist.
    for f in spec.fields:
        if f.section not in section_keys:
            problems.append(f"field '{f.key}' references unknown section '{f.section}'")
    for sb in spec.section_bodies:
        if sb.section not in section_keys:
            problems.append(f"section_body references unknown section '{sb.section}'")
    if spec.table and spec.table.section not in section_keys:
        problems.append(f"table references unknown section '{spec.table.section}'")

    # requires_graphic / requires_table must resolve.
    graphic_ids = {g.graphic_id for g in spec.graphics}
    for s in spec.required_sections:
        if s.requires_graphic and s.requires_graphic not in graphic_ids:
            problems.append(f"section '{s.key}' requires unknown graphic '{s.requires_graphic}'")
        if s.requires_table and (not spec.table or s.requires_table != spec.table.key):
            problems.append(f"section '{s.key}' requires unknown table '{s.requires_table}'")

    # Graphics must reference a real corpus doc and a real section.
    corpus_names = {d.name for d in spec.corpus}
    for g in spec.graphics:
        if g.source_doc not in corpus_names:
            problems.append(f"graphic '{g.graphic_id}' source_doc '{g.source_doc}' not in corpus")
        if g.belongs_in_section not in section_keys:
            problems.append(f"graphic '{g.graphic_id}' belongs_in_section '{g.belongs_in_section}' unknown")

    # Correction targets must be resolvable units.
    resolvable = _resolvable_targets(spec)
    for c in spec.corrections:
        base = c.target.split("[", 1)[0]
        if c.target not in resolvable and base not in resolvable:
            problems.append(f"correction '{c.id}' targets non-resolvable unit '{c.target}'")

    # No fabrication: a replace correction's new_value and a corpus-backed field
    # must be groundable in the corpus text (or in the correction body itself).
    problems += _check_corpus_grounding(spec)

    # Draft sections should only reference known section keys/fields.
    for ds in spec.draft_sections:
        if ds.key not in section_keys:
            problems.append(f"draft section '{ds.key}' is not a required section")

    return problems


def _resolvable_targets(spec: ProjectSpec) -> set[str]:
    targets: set[str] = set()
    for f in spec.fields:
        targets.add(f"{f.section}.{f.key}")
    for sb in spec.section_bodies:
        targets.add(f"{sb.section}.body")
    for s in spec.required_sections:
        if s.requires_graphic:
            targets.add(f"{s.key}.graphic")
    if spec.table:
        targets.add(f"{spec.table.section}.table")
    targets.update({"furniture.classification", "furniture.footer", "furniture.header"})
    return targets


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _check_corpus_grounding(spec: ProjectSpec) -> list[str]:
    """A replace correction asserts a corrected value. To honor 'no fabrication',
    that value should appear either in the corpus text (the ground truth) or be
    explicitly stated in the correction's own body (a reviewer-supplied value is
    allowed, but must be stated, not invented silently). This catches an LLM that
    hallucinates a corrected value with no basis anywhere.

    Graphic relabels are exempt (they assert a filename, matched structurally).
    """
    problems: list[str] = []
    corpus_blob = _norm(" \n ".join(d.text for d in spec.corpus))
    for c in spec.corrections:
        if c.operation != "replace" or c.new_value in (None, ""):
            continue
        nv = _norm(str(c.new_value))
        if not nv:
            continue
        in_corpus = nv in corpus_blob
        in_body = nv in _norm(c.body)
        if not in_corpus and not in_body:
            problems.append(
                f"correction '{c.id}' new_value {c.new_value!r} is not grounded in the "
                "corpus or stated in the correction body (possible fabrication)"
            )
    return problems
