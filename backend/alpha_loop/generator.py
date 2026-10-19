"""Generator / Protagonist agent: turns the proven alpha_frontier mutation axes
into fireable live-API batches, carrying ground truth for the red team.

Design decision (do not reinvent the oracle): the repo already ships a battle-
tested, adversarially-reviewed malformation generator + threshold-independent
truth model in `backend/tests/alpha_frontier/`. We REUSE it verbatim as the
mutation engine and ground-truth source. This module only adapts those axes
into (a) batch documents the live `/alignment/batch` endpoint accepts and (b) a
per-doc gold correspondence the correctness adversary scores against.

A "shape" here is one (axis, severity) pick applied to the clean game-record
pool: every record in the emitted doc shares one consistent mutated shape (one
team's export), so its signature is stable and replay/cascade is testable.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make the on-disk alpha_frontier harness importable (it lives under tests/, is
# deliberately not a package on the install path). We reuse its axes + truth.
_FRONTIER_DIR = Path(__file__).resolve().parents[1] / "tests"
if str(_FRONTIER_DIR) not in sys.path:
    sys.path.insert(0, str(_FRONTIER_DIR))

from alpha_frontier.mutations import (  # noqa: E402
    AXES,
    GOLDEN_FIELDS,
    REQUIRED_FIELDS,
    MutationResult,
)

# The clean game-record pool the frontier harness mutates. Imported so the loop
# and the harness agree on the base data exactly.
from alpha_frontier.harness import CLEAN_POOL  # noqa: E402


# --------------------------------------------------------------------------
# Spiral-out stages: a benign -> hostile ladder expressed as (axis, severity)
# picks over the proven axes. Each STAGE is a rung; the controller escalates one
# rung at a time (and the Generator can mix shapes within a rung for cascade
# tests). Severities stay at or below each axis's monotonic break ceiling so a
# rung is a deliberate, known difficulty — not random nastiness.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ShapeSpec:
    """One fireable shape: an axis + severity, optionally tagged for clarity."""
    axis: str
    severity: int
    tag: str = ""

    @property
    def label(self) -> str:
        return self.tag or f"{self.axis}@s{self.severity}"


# The staged ladder. Benign (cosmetic / low severity) first; each later stage
# introduces a harder axis or a higher severity. Ordered benign -> hostile.
STAGES: list[tuple[str, list[ShapeSpec]]] = [
    ("benign_clean", [ShapeSpec("rename", 0, "clean")]),
    ("benign_rename_shared", [ShapeSpec("rename", 1, "shared-token rename")]),
    ("rename_synonym", [ShapeSpec("rename", 2, "synonym rename")]),
    ("nesting", [ShapeSpec("nesting", 2, "nested under wrapper")]),
    ("misspell", [ShapeSpec("misspell", 3, "typo'd keys")]),
    ("typecoerce", [ShapeSpec("typecoerce", 2, "types coerced")]),
    ("noise_decoys", [ShapeSpec("noise", 4, "decoy fields injected")]),
    ("rename_abbrev", [ShapeSpec("rename", 3, "abbreviated keys")]),
    ("conflict", [ShapeSpec("conflict", 2, "ambiguous ties")]),
    ("hostile_opaque", [ShapeSpec("rename", 4, "opaque keys")]),
    ("hostile_split_merge", [ShapeSpec("split_merge", 2, "split/merged fields")]),
    ("hostile_junk_leak", [ShapeSpec("junk_leak", 3, "junk precision trap")]),
]


@dataclass
class FiredDoc:
    """One document fired at the batch endpoint, plus its carried ground truth.

    `truth` is source_key -> golden_field|None (None = decoy). `descriptions`
    carries the honest metadata sidecar (opaque fields) the endpoint accepts.
    """
    doc_id: str
    records: list[dict[str, Any]]
    truth: dict[str, str | None]
    descriptions: dict[str, str] = field(default_factory=dict)
    shape_label: str = ""

    def as_batch_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"doc_id": self.doc_id, "records": self.records}
        if self.descriptions:
            doc["descriptions"] = self.descriptions
        return doc

    @property
    def present_golden(self) -> set[str]:
        return {g for g in self.truth.values() if g is not None}


class Generator:
    """Emits fireable batches from the staged ladder, deterministically."""

    def __init__(self, seed: int = 1337) -> None:
        self.seed = seed
        self.golden_fields = list(GOLDEN_FIELDS)
        self.required_fields = set(REQUIRED_FIELDS)

    def stage_names(self) -> list[str]:
        return [name for name, _ in STAGES]

    def _one_shape(self, spec: ShapeSpec, *, n: int, round_id: int) -> FiredDoc:
        """Build one FiredDoc for an (axis, severity): mutate `n` clean records
        under ONE fixed shape decision so the whole doc is one coherent shape.

        The frontier axis is value-independent on the KEY decision but uses the
        rng for value-level choices, so we seed per (round, shape) for a stable
        shape whose signature is reproducible across rounds (the replay test)."""
        axis = AXES[spec.axis]
        # One rng seeded by the SHAPE (not the round) so re-firing the same shape
        # in a later round yields the SAME field layout -> same signature -> the
        # replay/cascade path. Values still vary per record via the pool.
        key_rng = random.Random(f"{self.seed}:{spec.axis}:{spec.severity}")
        records: list[dict[str, Any]] = []
        truth: dict[str, str | None] = {}
        descriptions: dict[str, str] = {}
        pool = CLEAN_POOL
        for i in range(n):
            base = dict(pool[i % len(pool)])
            # Re-seed the per-record value rng from the stable key_rng state plus
            # the record index, so the KEY layout is identical across records
            # (one shape) while values differ. The axis fn decides keys from the
            # rng first call; to keep keys stable we pass a fresh rng seeded the
            # same way each record for key decisions, accepting identical value
            # mutations per pooled base (values still differ across the 3 bases).
            rng = random.Random(f"{self.seed}:{spec.axis}:{spec.severity}:{i % len(pool)}")
            mr: MutationResult = axis.fn(base, spec.severity, rng)
            records.append(mr.record)
            # Merge truth; a consistent shape yields the same keys each record.
            for k, g in mr.truth.items():
                truth.setdefault(k, g)
        doc_id = f"r{round_id}_{spec.label.replace(' ', '_')}_{n}recs.json"
        return FiredDoc(doc_id=doc_id, records=records, truth=truth,
                        descriptions=descriptions, shape_label=spec.label)

    def emit_stage(self, stage_index: int, *, n_per_shape: int = 3,
                   round_id: int = 0) -> list[FiredDoc]:
        """Emit all shapes for a stage rung as fireable docs."""
        _, specs = STAGES[stage_index]
        return [self._one_shape(s, n=n_per_shape, round_id=round_id) for s in specs]

    def emit_shape(self, spec: ShapeSpec, *, n_per_shape: int = 3,
                   round_id: int = 0) -> FiredDoc:
        """Emit one explicit shape (used for cascade re-fire + ad-hoc firing)."""
        return self._one_shape(spec, n=n_per_shape, round_id=round_id)
