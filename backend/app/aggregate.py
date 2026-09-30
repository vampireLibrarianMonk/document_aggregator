"""Report aggregation: assemble the intermediate canonical report JSON.

This is assembly and organization, not content generation. Words come straight
from the source documents' canonical blocks. Documents are grouped and ordered
by their resolved effective DTG, sections mirror the source structure, and
supplementals (comments, emails, corrections) are attached as annotations.

The output is the "final intermediate format" the frontend displays and the
exporters render into DOCX / PPTX / PDF.
"""
from __future__ import annotations

from .config import settings
from .models import utcnow
from .store import store


def _sentiment(text: str) -> str:
    negative = {"angry", "unacceptable", "wrong", "broken", "failed", "terrible",
                "furious", "disappointed", "escalate", "unhappy", "mistake", "error"}
    positive = {"great", "thanks", "excellent", "good", "approved", "correct", "perfect"}
    low = text.lower()
    neg = sum(1 for w in negative if w in low)
    pos = sum(1 for w in positive if w in low)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def build_report(project_id: str) -> dict:
    project = store.get_project(project_id)
    records = [r for r in store.list_records(project_id)
               if r.overall_status == "completed"]

    # Order documents by resolved effective DTG (fixed rule, not a guess).
    records.sort(key=lambda r: r.effective_dtg or r.ingested_at)

    supplementals = store.list_supplementals(project_id)

    sections = []
    for rec in records:
        canonical = store.get_canonical(project_id, rec.id)
        if not canonical:
            continue
        blocks = canonical.structure.get("blocks", [])
        rendered = []
        for b in blocks:
            entry = {"type": b.type, "text": b.text}
            if b.table:
                entry["table"] = b.table
            if b.page is not None:
                entry["page"] = b.page
            if b.slide is not None:
                entry["slide"] = b.slide
            rendered.append(entry)

        related = [s for s in supplementals if s.target_document_id == rec.id]
        sections.append({
            "document_id": rec.id,
            "title": rec.filename,
            "effective_dtg": rec.effective_dtg,
            "effective_dtg_source": rec.effective_dtg_source,
            "parser": canonical.provenance.parser,
            "block_count": rec.block_count,
            "artifact_count": rec.artifact_count,
            "content": rendered,
            "artifacts": [a.model_dump() for a in canonical.artifacts],
            "supplementals": [s.model_dump() for s in related],
            "provenance": {
                "sha256": rec.sha256,
                "parser": canonical.provenance.parser,
                "method": canonical.provenance.method,
            },
        })

    project_level_supps = [s.model_dump() for s in supplementals if not s.target_document_id]

    return {
        "schema_version": settings.SCHEMA_VERSION,
        "report_kind": "aggregated_corpus",
        "generated_at": utcnow(),
        "project": {"id": project_id, "name": project.name if project else project_id},
        "ordering": "chronological_by_effective_dtg",
        "summary": {
            "source_documents": len(sections),
            "supplementals_total": len(supplementals),
            "negative_supplementals": sum(1 for s in supplementals if s.sentiment == "negative"),
        },
        "sections": sections,
        "project_supplementals": project_level_supps,
        "note": "Assembly of source content ordered by resolved effective DTG. "
                "No content invented; words come from source documents.",
    }
