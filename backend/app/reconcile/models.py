"""Data models for the reconciliation engine.

The corrected intermediate JSON is output-neutral: it carries the *correct*
content plus, for every asserted unit, a status and provenance so any fix is
defensible and any downstream renderer (DOCX/PPTX/PDF) can project it later.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    unchanged = "unchanged"        # matched the correct value already
    filled = "filled"              # was empty, populated from corpus
    corrected = "corrected"        # was wrong, replaced with corpus/comment value
    needs_review = "needs_review"  # flagged wrong/required but no correct value available
    conflict = "conflict"          # multiple corrections disagree; all candidates preserved


class DefectClass(str, Enum):
    value = "value"
    graphic = "graphic"
    table = "table"
    furniture = "furniture"
    discipline = "discipline"      # build-discipline placement/format violation


class Provenance(BaseModel):
    """Where a corrected unit's value came from. Smallest-unit granularity."""
    corpus: list[str] = Field(default_factory=list)       # e.g. "field_report_2026-03-02.txt"
    corrections: list[str] = Field(default_factory=list)  # correction ids that acted on this unit
    rule: str | None = None                            # template rule that judged it


class CorrectedField(BaseModel):
    key: str
    label: str
    value: Any = None
    # On a conflict the resolved `value` is None (no answer); this keeps the
    # original draft value for audit/trace without presenting it as the answer.
    original_value: Any = None
    status: Status
    defect_class: DefectClass
    provenance: Provenance = Field(default_factory=Provenance)
    note: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)  # for conflict


class CorrectedGraphic(BaseModel):
    graphic_id: str
    name: str
    caption: str = ""
    figure_number: int | None = None
    section: str = ""
    status: Status
    provenance: Provenance = Field(default_factory=Provenance)
    note: str = ""


class CorrectedTable(BaseModel):
    key: str
    title: str = ""
    table_number: int | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    font: str = ""
    header_style: str = ""
    status: Status
    provenance: Provenance = Field(default_factory=Provenance)
    formatting: dict[str, Any] = Field(default_factory=dict)  # what was normalized + against which rule
    note: str = ""


class CorrectedSection(BaseModel):
    key: str
    heading: str
    fields: list[CorrectedField] = Field(default_factory=list)
    graphics: list[CorrectedGraphic] = Field(default_factory=list)
    tables: list[CorrectedTable] = Field(default_factory=list)


class CorrectedFurniture(BaseModel):
    header: CorrectedField
    footer: CorrectedField
    classification: CorrectedField
    page_numbers: CorrectedField
    cross_references: list[CorrectedField] = Field(default_factory=list)


class CorrectedReport(BaseModel):
    schema_version: str = "1.0"
    kind: str = "corrected_intermediate_report"
    mode: str = "draft"  # draft | template
    title: str = ""
    generated_at: str = ""
    sections: list[CorrectedSection] = Field(default_factory=list)
    furniture: CorrectedFurniture
    discipline_findings: list[CorrectedField] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    note: str = (
        "Output-neutral corrected representation. Every unit carries a status and "
        "provenance. Values come from the corpus or explicit corrections; nothing is "
        "invented. Gaps are needs_review; disagreements are conflict."
    )
