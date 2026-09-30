"""Document converters: real DOCX/PPTX/PDF -> internal first_attempt JSON.

Clients submit actual documents, not hand-authored JSON. These converters are
the inverse of app.exporters: they extract a document's structure into the same
first_attempt shape the reconciliation engine consumes
({artifact_kind, title, sections[fields/body/table/graphics], furniture,
cross_references}).

Fidelity tiers (honest about extraction quality per format):
    docx -> high    (clean OOXML structure)
    pptx -> good    (slide/placeholder structure)
    pdf  -> partial (text + heuristics; lossy)

Incomplete input is normal: whatever the document does not contain is simply
absent from the first_attempt, and the engine flags it needs_review. A follow-on
"desired-content" corpus can fill those gaps later.
"""
from .base import ConversionResult, convert_document

__all__ = ["ConversionResult", "convert_document"]
