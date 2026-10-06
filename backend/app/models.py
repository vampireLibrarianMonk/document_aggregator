"""Pydantic models: canonical document model + pipeline tracking models.

These are the stable internal contracts. Parser-specific representations are
converted into these. JSON is a serialization of this model, not the model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Pipeline stage tracking (what the frontend board renders)
# --------------------------------------------------------------------------

class StageStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class DocClass(str, Enum):
    source_document = "SOURCE_DOCUMENT"
    correction_document = "CORRECTION_DOCUMENT"
    configuration_document = "CONFIGURATION_DOCUMENT"


class DocKind(str, Enum):
    """Which of the four Ingestion areas a document was uploaded into. The area
    the user chooses tags the document; the correction engine reads these to
    decide what role each document plays."""
    corpus = "corpus"            # original source material (ground truth)
    template = "template"        # the required structure/rubric (may be absent)
    corrections = "corrections"  # reviewer feedback (comments / emails)
    first_draft = "first_draft"  # the completed-but-flawed attempt


class Stage(BaseModel):
    name: str
    status: StageStatus = StageStatus.pending
    detail: str = ""
    started_at: str | None = None
    completed_at: str | None = None


class TimestampEvidence(BaseModel):
    """Time is modeled as evidence, never a single collapsed value."""
    effective_dtg: str | None = None
    effective_dtg_source: str | None = None
    created: str | None = None
    modified: str | None = None
    filesystem_mtime: str | None = None
    ingested_at: str = Field(default_factory=utcnow)


class Provenance(BaseModel):
    method: str = "native"
    parser: str = "unknown"
    parser_version: str = ""
    source_document_id: str = ""


class Artifact(BaseModel):
    id: str
    type: str  # image, chart, table, attachment
    page: int | None = None
    slide: int | None = None
    content_hash: str | None = None
    classification: str | None = None
    description: str | None = None
    anchor_block_id: str | None = None
    nearby_block_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Block(BaseModel):
    id: str
    type: str  # heading, paragraph, list_item, table, slide_title, caption ...
    text: str = ""
    page: int | None = None
    slide: int | None = None
    reading_order: int = 0
    parent_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    table: list[list[str]] | None = None  # rows of cells, when type == table


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    revision: int = 1
    text: str
    block_ids: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    section_path: list[str] = Field(default_factory=list)
    hash: str = ""
    embedding_model: str = ""


class CanonicalDocument(BaseModel):
    schema_version: str = "1.0"
    document: dict[str, Any] = Field(default_factory=dict)
    timestamps: TimestampEvidence = Field(default_factory=TimestampEvidence)
    metadata: dict[str, Any] = Field(default_factory=dict)
    structure: dict[str, list[Block]] = Field(default_factory=lambda: {"blocks": []})
    artifacts: list[Artifact] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    extraction: dict[str, Any] = Field(default_factory=dict)
    revisions: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Records the API + frontend work against
# --------------------------------------------------------------------------

class SupplementalKind(str, Enum):
    comment = "comment"
    email = "email"
    correction = "correction"
    interview_note = "interview_note"


class Supplemental(BaseModel):
    id: str
    kind: SupplementalKind
    author: str = "unknown"
    subject: str = ""
    body: str = ""
    sentiment: str = "neutral"  # naive: neutral / negative / positive
    target_document_id: str | None = None
    created_at: str = Field(default_factory=utcnow)


class DocumentRecord(BaseModel):
    id: str
    project_id: str
    filename: str
    mime_type: str
    sha256: str
    byte_size: int
    doc_class: DocClass = DocClass.source_document
    kind: DocKind = DocKind.corpus
    ingested_at: str = Field(default_factory=utcnow)

    stages: list[Stage] = Field(default_factory=list)
    error: str | None = None

    # Derived stats (filled by the pipeline) for the board view.
    block_count: int = 0
    chunk_count: int = 0
    artifact_count: int = 0
    effective_dtg: str | None = None
    effective_dtg_source: str | None = None

    def stage(self, name: str) -> Stage | None:
        return next((s for s in self.stages if s.name == name), None)

    @property
    def overall_status(self) -> str:
        if self.error or any(s.status == StageStatus.failed for s in self.stages):
            return "failed"
        if all(s.status in (StageStatus.completed, StageStatus.skipped) for s in self.stages) and self.stages:
            return "completed"
        if any(s.status == StageStatus.processing for s in self.stages):
            return "processing"
        return "pending"


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str = Field(default_factory=utcnow)


class ExportProfile(BaseModel):
    format: str = "json"  # json, docx, pptx, pdf, markdown
    include_supplementals: bool = True
    include_provenance: bool = True
