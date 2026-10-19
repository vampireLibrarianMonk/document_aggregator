"""The Decider: the spiral-out auto-decision policy — the thing UNDER TEST.

For each cluster outcome the batch produced, the Decider chooses one action:

  approve   the provisional shape clears the conformity bar -> sign it off so it
            replays automatically next round (this is how the corpus "expands")
  remap     a needs-review field has a known gold correspondence the matcher
            missed -> point it at the true source path (a reorg, never a value
            rewrite), then re-evaluate
  reject    the shape is genuinely wrong/irrelevant -> decline with a reason
  hold      not confident enough and no safe remap -> leave for the next spiral

"Spiral out" = escalate difficulty/volume while decisions keep clearing the bar;
contract to the minimal reproducing shape when a red-team agent FAILS. The bar
is conformity-based (per your call: fires + conformity drive the strategy):

    approve when  conformed/produced >= approve_conformity  AND needs_review==0
    reject  when  the cluster quarantined (irrelevant) OR produced nothing and
                  cannot be remapped

The Decider is deliberately simple and separate from the red team, so the red
team genuinely checks it rather than agreeing with itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .generator import FiredDoc


@dataclass
class Decision:
    profile_id: str | None
    action: str                 # approve | remap | reject | hold
    reason: str
    remaps: list[tuple[str, str]] = None  # (target, source_path) applied
    conformity: float = 0.0


class Decider:
    def __init__(self, approve_conformity: float = 0.90) -> None:
        self.approve_conformity = approve_conformity

    def decide(self, outcome: dict[str, Any], entry: dict[str, Any] | None,
               doc: FiredDoc) -> Decision:
        """Decide an action for one cluster outcome + its library entry."""
        pathway = outcome.get("pathway", "")
        produced = outcome.get("produced", 0)
        conformed = outcome.get("conformed", 0)
        pid = outcome.get("provisional_profile") or (
            entry.get("profile_id") if entry else None)
        conformity = (conformed / produced) if produced else 0.0

        # Quarantined / irrelevant -> reject as not-our-data.
        if pathway == "reject_irrelevant" or outcome.get("quarantined"):
            return Decision(pid, "reject", "quarantined as unrelated",
                            conformity=conformity)

        # Already replaying (approved shape re-fired) -> nothing to decide.
        if pathway == "replay_clean":
            return Decision(pid, "hold", "already approved; replayed clean",
                            conformity=conformity)

        if pid is None:
            return Decision(None, "hold", "no provisional profile to act on",
                            conformity=conformity)

        needs_review = entry.get("needs_review", []) if entry else []

        # Try safe remaps for needs-review fields with a KNOWN gold key the
        # matcher abstained on (a reorg, never a value rewrite).
        remaps = self._safe_remaps(doc, entry) if entry else []

        if conformity >= self.approve_conformity and not needs_review:
            return Decision(pid, "approve",
                            f"conformity {conformity:.0%} >= "
                            f"{self.approve_conformity:.0%}, no needs_review",
                            conformity=conformity)

        if remaps:
            return Decision(pid, "remap",
                            f"remap {len(remaps)} field(s) from gold truth",
                            remaps=remaps, conformity=conformity)

        # Can't clear the bar and nothing safe to remap -> hold for next spiral.
        return Decision(pid, "hold",
                        f"conformity {conformity:.0%} < bar and no safe remap "
                        f"(needs_review={needs_review})", conformity=conformity)

    def _safe_remaps(self, doc: FiredDoc,
                     entry: dict[str, Any]) -> list[tuple[str, str]]:
        """For each needs-review golden field, if the doc's ground truth names a
        real source path, propose that remap. Only real source paths (the
        override endpoint re-validates); never a value change."""
        needs_review = entry.get("needs_review", [])
        if not needs_review:
            return []
        # Invert truth: golden -> source key(s) that truly are it.
        truth_inv: dict[str, list[str]] = {}
        for k, g in doc.truth.items():
            if g is not None:
                truth_inv.setdefault(g, []).append(k)
        # Candidate real source paths the shape exposes (from the entry).
        source_paths = {sp.get("path") for sp in entry.get("source_paths", [])}
        out: list[tuple[str, str]] = []
        for g in needs_review:
            for key in truth_inv.get(g, []):
                # Prefer an exact exposed path; fall back to the leaf key.
                if key in source_paths:
                    out.append((g, key))
                    break
                match = next((p for p in source_paths
                              if p and p.split(".")[-1].replace("[]", "") == key), None)
                if match:
                    out.append((g, match))
                    break
        return out
