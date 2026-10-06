"""Flatten a generated project's draft into a unit map the techniques edit, and
turn its corrections into edit INTENTS.

A unit key is `<section>.<field>` for discrete fields, `<section>.body` for the
prose paragraph, and `<section>.graphic` for a figure reference. The map's
values are the current (draft) text/value of each unit. A precision-editing
technique receives the whole map + the intents and returns a NEW map; scoring
then compares before/after to find correct edits AND off-target drift.

Keeping the whole document in the map is deliberate: a technique that over-edits
(e.g. regenerates a neighbouring paragraph) reveals itself as an off-target
change, which is the scaling signal we are measuring.
"""
from __future__ import annotations


def flatten_draft(draft: dict) -> dict[str, str]:
    units: dict[str, str] = {}
    for sec in draft.get("sections", []):
        skey = sec.get("key")
        for fk, fv in (sec.get("fields") or {}).items():
            units[f"{skey}.{fk}"] = "" if fv is None else str(fv)
        if "body" in sec:
            units[f"{skey}.body"] = sec.get("body") or ""
        for g in sec.get("graphics", []) or []:
            if g.get("ref_name"):
                units[f"{skey}.graphic"] = g["ref_name"]
    return units


def intents_from_corrections(corrections: list[dict]) -> list[dict]:
    """Group corrections by target. A target with two conflicting replace values
    is a genuine conflict (the technique should NOT pick one)."""
    by_target: dict[str, list[dict]] = {}
    for c in corrections:
        by_target.setdefault(c["target"], []).append(c)
    intents: list[dict] = []
    for target, cs in by_target.items():
        values = {str(c.get("new_value")) for c in cs if c.get("new_value") is not None}
        intents.append({
            "target": target,
            "operation": cs[0].get("operation", "replace"),
            "new_value": cs[0].get("new_value"),
            "old_value": cs[0].get("old_value"),
            "conflict": len(values) > 1,          # disagreeing reviewers
            "bodies": [c.get("body", "") for c in cs],
        })
    return intents


def editable_ledger(edits: list[dict], draft_units: dict[str, str]) -> list[dict]:
    """The subset of the edit ledger that is a span-editable UNIT (field / body
    / graphic). Table-fill is a structured-retrieval operation, not a span edit,
    and is validated separately in the datagen table test — exclude it here so
    the precision-editing recall isn't dragged down by an un-flattenable target.
    """
    return [e for e in edits
            if e.get("kind") != "table_fill" and e.get("target") in draft_units]


def grounding_text(gp) -> str:
    """Everything a correct value may legitimately come from: the corpus, the
    graphics manifest names/captions, and the correction emails."""
    parts = list(gp.corpus.values())
    for g in gp.graphics:
        parts += [g.get("name", ""), g.get("caption", ""), g.get("title", "")]
    for c in gp.corrections:
        parts += [str(c.get("new_value", "")), c.get("body", "")]
    return "\n".join(parts)
