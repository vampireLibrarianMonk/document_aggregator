"""Precision-correction scoring against the growth dataset's edit ledger.

A technique is handed the DRAFT units + the correction intents and returns an
EDITED unit map. We score that output on five axes, three of which are the
scaling early-warning signals the user asked for:

  recall              of the units that SHOULD have changed (the ledger's
                      targets), how many did we actually change to the right
                      value? (did we fix everything?)
  precision           of the units we DID change, how many were supposed to
                      change? (did we only touch what we should?)
  unintended_changes  COUNT of units that changed but were NOT targeted — the
                      key scaling signal: a technique that rewrites a target
                      paragraph but drifts a neighbouring one shows up here, and
                      this count is expected to climb for sloppy techniques as
                      the document (and so the number of untouched units) grows.
  fabrications        changed values not grounded in corpus/corrections/gold.
  conflict_preserved  a genuine-conflict target was NOT silently resolved.

Ground truth = the generator's edit ledger (kind/target/draft_value/
correct_value/intent). `correct_value is None` marks a genuine conflict: the
right behaviour is to leave it unresolved (changing it to a single value is a
precision failure, not a fix).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


@dataclass
class EditScore:
    technique: str
    project_id: str
    size: int
    # ground-truth sizes
    total_units: int = 0            # all editable units in the doc
    targeted: int = 0               # units the ledger says should change
    conflicts: int = 0              # genuine-conflict targets (should stay open)
    # outcomes
    correct_edits: int = 0          # targeted units changed to the right value
    changed_units: int = 0          # units whose text actually changed
    off_target_changes: int = 0     # changed units that were NOT targeted
    fabrications: int = 0           # changed values not grounded
    conflicts_resolved: int = 0     # genuine conflicts wrongly collapsed
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        base = self.targeted - self.conflicts   # conflicts aren't "fixable"
        return (100.0 * self.correct_edits / base) if base else 100.0

    @property
    def precision(self) -> float:
        return (100.0 * self.correct_edits / self.changed_units) if self.changed_units else 100.0

    @property
    def unintended_changes(self) -> int:
        return self.off_target_changes

    @property
    def conflict_preserved(self) -> bool:
        return self.conflicts_resolved == 0

    def as_dict(self) -> dict:
        return {
            "technique": self.technique, "project_id": self.project_id, "size": self.size,
            "total_units": self.total_units, "targeted": self.targeted,
            "conflicts": self.conflicts, "correct_edits": self.correct_edits,
            "changed_units": self.changed_units,
            "recall_pct": round(self.recall, 1), "precision_pct": round(self.precision, 1),
            "unintended_changes": self.unintended_changes,
            "fabrications": self.fabrications,
            "conflict_preserved": self.conflict_preserved, "error": self.error,
        }


def score_edits(technique: str, project_id: str, size: int, *,
                draft_units: dict[str, str], produced_units: dict[str, str],
                ledger: list[dict], grounding_text: str) -> EditScore:
    """Compare a technique's produced unit map against the draft (before) and
    the ledger (ground truth of what should change)."""
    s = EditScore(technique=technique, project_id=project_id, size=size)
    s.total_units = len(draft_units)

    # Index the ledger by target.
    want: dict[str, dict] = {e["target"]: e for e in ledger}
    s.targeted = len(want)
    s.conflicts = sum(1 for e in ledger if e.get("correct_value") in (None, "None"))

    ground = _norm(grounding_text)

    for key, before in draft_units.items():
        after = produced_units.get(key, before)
        changed = _norm(after) != _norm(before)
        if changed:
            s.changed_units += 1
        e = want.get(key)
        if e is not None:
            cv = e.get("correct_value")
            is_conflict = cv in (None, "None")
            if is_conflict:
                # Should stay unresolved: any change to a single value is wrong.
                if changed:
                    s.conflicts_resolved += 1
            elif changed and _value_matches(after, cv):
                s.correct_edits += 1
                # Fabrication = NEW content the technique introduced that is
                # neither grounded nor already in the draft unit. Preserving
                # the draft's surrounding sentences is NOT fabrication, so we
                # check only the tokens that are new vs the original `before`.
                if _introduced_ungrounded(before, after, ground):
                    s.fabrications += 1
        elif changed:
            # Not a targeted unit; any change here is UNINTENDED.
            s.off_target_changes += 1
            if _introduced_ungrounded(before, after, ground):
                s.fabrications += 1
    return s


def _introduced_ungrounded(before: str, after: str, ground: str) -> bool:
    """True if the produced text introduced content tokens that are neither in
    the grounding corpus/corrections NOR in the original draft unit. New words
    that come from the draft (preserved context) are legitimate; only genuinely
    invented words count as fabrication."""
    import re as _re
    before_toks = set(_re.split(r"[^a-z0-9]+", _norm(before)))
    new_toks = [t for t in _re.split(r"[^a-z0-9]+", _norm(after))
                if t and len(t) > 2 and t not in before_toks]
    if not new_toks:
        return False
    # Fabrication only if a NEW, non-trivial token appears nowhere in grounding.
    return any(t not in ground for t in new_toks)


def _value_matches(produced: str, correct: str) -> bool:
    """A unit is 'correctly edited' if the correct value is present in the
    produced text (exact for fields; substring for prose/relabel, since a body
    edit embeds the corrected value in a sentence)."""
    p, c = _norm(produced), _norm(correct)
    return p == c or c in p


def aggregate(scores: list[EditScore]) -> dict:
    """Aggregate per (technique) across projects+sizes for the scorecard."""
    by_tech: dict[str, list[EditScore]] = {}
    for sc in scores:
        if sc.error:
            continue
        by_tech.setdefault(sc.technique, []).append(sc)
    out = {}
    for tech, lst in sorted(by_tech.items()):
        n = len(lst)
        out[tech] = {
            "runs": n,
            "avg_recall": round(sum(x.recall for x in lst) / n, 1),
            "avg_precision": round(sum(x.precision for x in lst) / n, 1),
            "total_unintended": sum(x.unintended_changes for x in lst),
            "total_fabrications": sum(x.fabrications for x in lst),
            "conflict_preserved_all": all(x.conflict_preserved for x in lst),
        }
    return out
