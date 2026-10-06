"""Synthetic growth-dataset generator for the precision-correction alpha loop.

The six bundled sample projects are small by design. To study whether the
correction pipeline's accuracy HOLDS as documents grow, we need progressively
larger documents with KNOWN localized edits and a KNOWN gold result, so we can
measure precision (did we change ONLY the target span) and recall (did we
change EVERYTHING we should) and, crucially, an UNINTENDED-CHANGE count (edits
that touched text outside their target) as size scales.

Why generated, not sourced: public edit corpora (IteraTeR, WikiAtomicEdits,
NewsEdits, GEC sets) are shaped right but fail our constraints — they load over
the network (we are air-gapped), carry mixed/unclear licenses (share-alike
Wikipedia, non-exclusive arXiv), and are general prose with no corpus to ground
against, no discrete fields, and no fabrication/conflict semantics. A
self-generated, domain-matched corpus is air-gap clean (no external
provenance), deterministic (seeded), and exercises the exact pipeline we ship:
manifest fields + section bodies + graphics + grounding + no-fabrication +
conflict preservation. The edit-type taxonomy borrows IteraTeR's labels
(value / clarity / fluency / meaning-changed) for familiarity.
"""
from tests.command_center.datagen.generator import (
    GrowthSpec,
    generate_project,
    write_project,
)

__all__ = ["GrowthSpec", "generate_project", "write_project"]
