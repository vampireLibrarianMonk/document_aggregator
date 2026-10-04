"""Randomized-draft test.

Instead of asserting fixed answers, this synthesizes a draft by mutating a
project's template with a seeded RNG: it injects known defects (blank fields,
corrupted values, shuffled/moved/dropped graphics) and then asserts the engine
(a) resolves or flags every unit legally, (b) fabricates nothing, and (c) does
not silently keep an injected-wrong value as `unchanged`.

Because the defects are random per seed, the test cannot be satisfied by
hardcoding — it exercises the generic pipeline, not one example.
"""
from __future__ import annotations

import random

import pytest

ALLOWED = {"unchanged", "filled", "corrected", "needs_review", "conflict"}
RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _all_fields(report: dict) -> list[dict]:
    out: list[dict] = []
    for sec in report["sections"]:
        out.extend(sec["fields"])
    fu = report["furniture"]
    # Page elements are a dynamic, template-declared list now.
    out.extend(fu.get("elements", []))
    out.extend(fu["cross_references"])
    return out


def _has_provenance(unit: dict) -> bool:
    p = unit.get("provenance", {})
    return bool(p.get("corpus") or p.get("corrections") or p.get("rule"))


def synthesize_draft(template: dict, graphics: list[dict], rng: random.Random) -> tuple[dict, dict]:
    """Build a draft from the template with random injected defects.
    Returns (draft, injected) where injected records what we corrupted."""
    draft = {
        "artifact_kind": "draft",
        "title": template.get("title", "Draft"),
        "sections": [],
        "furniture": {"header": {"text": ""}, "footer": {"text": ""},
                      "page_numbers": False, "classification": ""},
        "cross_references": [],
    }
    injected: dict = {"blanked": [], "corrupted": [], "graphic_ops": []}

    gfx_names = [g["name"] for g in graphics]
    gfx_by_section = {g["belongs_in_section"]: g for g in graphics}

    for spec in template["required_sections"]:
        skey = spec["key"]
        sec = {"key": skey, "heading": spec["heading"], "fields": {}, "graphics": []}

        for fkey in spec.get("fields", []):
            roll = rng.random()
            if roll < 0.4:
                sec["fields"][fkey] = ""  # blank it
                injected["blanked"].append(f"{skey}.{fkey}")
            elif roll < 0.7:
                sec["fields"][fkey] = "WRONG_" + rng.choice(["x", "y", "z"])
                injected["corrupted"].append(f"{skey}.{fkey}")
            # else leave absent (also effectively blank)

        # Graphic handling: randomly drop, misplace, or mislabel the required graphic.
        req = spec.get("requires_graphic")
        if req:
            truth = next((g for g in graphics if g["graphic_id"] == req), None)
            if truth:
                op = rng.choice(["drop", "misplace", "mislabel", "correct"])
                if op == "correct":
                    sec["graphics"].append({"ref_name": truth["name"], "caption": truth["caption"]})
                elif op == "mislabel":
                    sec["graphics"].append({"ref_name": "wrong_" + truth["name"], "caption": ""})
                    injected["graphic_ops"].append((truth["graphic_id"], "mislabel"))
                elif op == "misplace":
                    injected["graphic_ops"].append((truth["graphic_id"], "misplace"))
                else:
                    injected["graphic_ops"].append((truth["graphic_id"], "drop"))

        draft["sections"].append(sec)

    # For "misplace", drop the graphic into a random OTHER section.
    for gid, op in injected["graphic_ops"]:
        if op == "misplace":
            truth = next(g for g in graphics if g["graphic_id"] == gid)
            others = [s for s in draft["sections"] if s["key"] != truth["belongs_in_section"]]
            if others:
                rng.choice(others)["graphics"].append(
                    {"ref_name": truth["name"], "caption": truth["caption"]}
                )

    _ = (gfx_names, gfx_by_section)  # available for future defect types
    return draft, injected


@pytest.mark.parametrize("seed", list(range(10)))
def test_randomized_draft_no_fabrication_and_catches_defects(scenario_module, project_id, seed):
    from app.reconcile import reconcile

    rng = random.Random(seed)
    template = scenario_module.load_template(project_id)
    corpus = scenario_module.load_corpus(project_id)
    graphics = scenario_module.load_graphics(project_id)
    corrections = scenario_module.load_corrections(project_id)
    manifest = scenario_module.load_manifest(project_id)

    draft, injected = synthesize_draft(template, graphics, rng)

    report = reconcile(
        first_attempt=draft, corpus=corpus, graphics_manifest=graphics,
        corrections=corrections, template=template, project=manifest,
    ).model_dump()

    fields = {f["key"]: f for f in _all_fields(report)}

    # (a) every unit has a legal status
    for f in fields.values():
        assert f["status"] in ALLOWED

    # (b) no fabrication: resolved values are provenance-backed
    for f in fields.values():
        if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
            assert _has_provenance(f), f"fabricated {f['key']}={f['value']!r} (seed {seed})"

    # (c) an injected WRONG_ value must never survive as `unchanged`
    for key in injected["corrupted"]:
        f = fields.get(key)
        if f and isinstance(f["value"], str):
            assert not (f["status"] == "unchanged" and f["value"].startswith("WRONG_")), (
                f"engine kept corrupted value on {key} (seed {seed})"
            )

    # (d) dropped/mislabeled graphics are resolved (filled or corrected), never left wrong
    gfx = {g["graphic_id"]: g for sec in report["sections"] for g in sec["graphics"]}
    for gid, op in injected["graphic_ops"]:
        g = gfx.get(gid)
        assert g is not None, f"graphic {gid} missing from output (seed {seed})"
        assert g["status"] in ALLOWED
        # Its final name must be the corpus-true name, not a mislabel.
        truth = next(t for t in graphics if t["graphic_id"] == gid)
        assert g["name"] == truth["name"], f"graphic {gid} not renamed to truth (seed {seed})"
