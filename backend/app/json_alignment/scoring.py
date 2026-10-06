"""Our own benchmark scorers for JSON schema-alignment.

Reimplemented from the public metric DEFINITIONS (not from any benchmark's code
or data): standard set-based precision/recall/F1 for schema matching, and
cell-level accuracy for value normalization. No third-party benchmark software
is imported or vendored.

schema_match_score:
    Compare our inferred correspondences (target_field <- source_path) against a
    gold set of correspondences. A correspondence is correct when both endpoints
    match gold. precision = TP/(TP+FP), recall = TP/(TP+FN), F1 harmonic mean.
    Abstentions (no prediction) count as misses (lower recall), never as wrong
    guesses (precision is protected) — this is the no-fabrication posture.

normalization_score:
    Over a set of (raw_value -> expected_value) cases per field, accuracy is the
    fraction where our transform produced exactly the expected canonical value.
    Cases we decline (fail-closed) count as incorrect, not as fabricated hits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mapping import STATUS_MAPPED, Mapping

# A gold/predicted correspondence identified by (source_path, target_field).
Correspondence = tuple[str, str]


@dataclass
class SchemaMatchScore:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int
    tp_pairs: list[Correspondence] = field(default_factory=list)
    fp_pairs: list[Correspondence] = field(default_factory=list)
    fn_pairs: list[Correspondence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
        }


def mapping_to_correspondences(mapping: Mapping) -> set[Correspondence]:
    """Confidently-mapped (source_path, target) pairs only; abstentions excluded."""
    out: set[Correspondence] = set()
    for fm in mapping.fields:
        if fm.status == STATUS_MAPPED and fm.source_path is not None:
            out.add((fm.source_path, fm.target))
    return out


def schema_match_score(predicted: set[Correspondence],
                       gold: set[Correspondence]) -> SchemaMatchScore:
    """Set-based P/R/F1 over correspondences."""
    tp = predicted & gold
    fp = predicted - gold
    fn = gold - predicted
    n_tp, n_fp, n_fn = len(tp), len(fp), len(fn)
    precision = n_tp / (n_tp + n_fp) if (n_tp + n_fp) else 0.0
    recall = n_tp / (n_tp + n_fn) if (n_tp + n_fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)
    return SchemaMatchScore(
        precision=precision, recall=recall, f1=f1,
        true_positives=n_tp, false_positives=n_fp, false_negatives=n_fn,
        tp_pairs=sorted(tp), fp_pairs=sorted(fp), fn_pairs=sorted(fn),
    )


@dataclass
class NormalizationCase:
    """One normalization expectation: raw -> expected for a target field."""
    target_field: str
    raw_value: Any
    expected_value: Any
    category: str = "normalization"       # identity|normalization|knowledge


@dataclass
class NormalizationScore:
    total: int
    correct: int
    by_category: dict[str, tuple[int, int]] = field(default_factory=dict)  # cat->(correct,total)
    misses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": round(self.accuracy, 4),
            "correct": self.correct,
            "total": self.total,
            "by_category": {
                c: {"correct": cor, "total": tot, "accuracy": round(cor / tot, 4) if tot else 0.0}
                for c, (cor, tot) in sorted(self.by_category.items())
            },
        }


def normalization_score(cases: list[NormalizationCase],
                        produced: list[Any]) -> NormalizationScore:
    """Compare produced normalized values against expected, index-aligned.

    `produced[i]` is what our transform produced for `cases[i]` (or a sentinel
    like None when we declined). An exact match counts; a decline counts as a
    miss (we never fabricate to inflate the score)."""
    if len(produced) != len(cases):
        raise ValueError("produced and cases must be the same length")
    correct = 0
    by_cat: dict[str, list[int]] = {}
    misses: list[dict[str, Any]] = []
    for case, got in zip(cases, produced):
        cat = case.category or "normalization"
        bucket = by_cat.setdefault(cat, [0, 0])
        bucket[1] += 1
        if got == case.expected_value and got is not None:
            correct += 1
            bucket[0] += 1
        else:
            misses.append({
                "target_field": case.target_field,
                "raw_value": case.raw_value,
                "expected": case.expected_value,
                "got": got,
                "category": cat,
            })
    return NormalizationScore(
        total=len(cases), correct=correct,
        by_category={c: (v[0], v[1]) for c, v in by_cat.items()},
        misses=misses,
    )


__all__ = [
    "Correspondence",
    "NormalizationCase",
    "NormalizationScore",
    "SchemaMatchScore",
    "mapping_to_correspondences",
    "normalization_score",
    "schema_match_score",
]
