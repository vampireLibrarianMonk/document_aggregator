"""Tests for the integrated command-center winner (app/corrections/refine.py).

Covers the figure-relabel corpus grounding and the Option-B model-refinement
orchestration as wired into run_reconciliation. The model path is only
exercised structurally (offline), so these run with no network and no Bedrock:
the hard invariants (no fabrication, conflict preservation, determinism) are
what we assert, not a model's specific output.
"""

from __future__ import annotations

import json

import pytest
from app import project as sc
from app.corrections.refine import ground_graphic_relabels, refine_corrections

# --------------------------------------------------------------------------
# Figure-relabel grounding
# --------------------------------------------------------------------------


def _ctx(pid: str = "1"):
    return {
        "graphics": sc.load_graphics(pid),
        "template": sc.load_template(pid),
        "manifest": sc.load_manifest(pid),
        "corpus": sc.load_corpus(pid),
    }


def test_grounding_resolves_prose_to_exact_filename():
    """A prose figure reference with NO filename resolves to the exact corpus
    graphic name, scoped to the referenced section."""
    c = _ctx("1")
    prose = [
        "From: qa\nSubject: wrong figure\n\nThe chart in the Timeline "
        "section is wrong. It should be the packet-loss-versus-temperature "
        "figure."
    ]
    out = ground_graphic_relabels(
        [], graphics=c["graphics"], template=c["template"], feedback_texts=prose
    )
    assert len(out) == 1
    op = out[0]
    assert op["operation"] == "relabel_graphic"
    assert op["target"] == "timeline.graphic"
    assert op["new_value"] == "packet_loss_vs_temp.png"
    # The filename came verbatim from the corpus manifest (not invented).
    names = {g["name"] for g in c["graphics"]}
    assert op["new_value"] in names


def test_grounding_vague_prose_is_a_noop():
    """No fabrication: vague feedback with no distinctive match emits nothing."""
    c = _ctx("1")
    vague = [
        "From: x\nSubject: hmm\n\nThe figure seems a little off, please "
        "double-check it when you get a chance."
    ]
    out = ground_graphic_relabels(
        [], graphics=c["graphics"], template=c["template"], feedback_texts=vague
    )
    assert out == []


def test_grounding_is_idempotent_with_existing_relabel():
    """If a structured relabel already names the file, grounding does not add a
    duplicate for that target."""
    c = _ctx("1")
    existing = [
        {
            "id": "x",
            "operation": "relabel_graphic",
            "target": "timeline.graphic",
            "new_value": "packet_loss_vs_temp.png",
        }
    ]
    prose = ["The Timeline chart is wrong; it should be the packet loss versus temperature figure."]
    out = ground_graphic_relabels(
        existing, graphics=c["graphics"], template=c["template"], feedback_texts=prose
    )
    relabels = [o for o in out if o["target"] == "timeline.graphic"]
    assert len(relabels) == 1  # no duplicate added


def test_grounding_ignores_feedback_with_literal_filename():
    """When the feedback already names a .png, the structured path owns it and
    grounding stays out of the way."""
    c = _ctx("1")
    withfile = ["The timeline figure should be packet_loss_vs_temp.png."]
    out = ground_graphic_relabels(
        [], graphics=c["graphics"], template=c["template"], feedback_texts=withfile
    )
    assert out == []


# --------------------------------------------------------------------------
# refine_corrections orchestration (offline)
# --------------------------------------------------------------------------


def test_refine_offline_is_grounding_only_and_passthrough(monkeypatch):
    """With Bedrock disabled, refine returns the structured corrections plus any
    grounded relabels, and never drops or rewrites an existing correction."""
    from app.config import settings

    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False)
    c = _ctx("1")
    base = sc.load_corrections("1")
    out = refine_corrections(
        base,
        manifest=c["manifest"],
        template=c["template"],
        graphics=c["graphics"],
        feedback_texts=sc.load_correction_feedback("1"),
        corpus=c["corpus"],
    )
    # Every original correction survives (augment-only).
    for b in base:
        assert b in out


def test_refine_preserves_conflict(monkeypatch):
    """Both disagreeing severity corrections survive refinement (the engine must
    still see the conflict)."""
    from app.config import settings

    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False)
    c = _ctx("1")
    base = sc.load_corrections("1")
    out = refine_corrections(
        base,
        manifest=c["manifest"],
        template=c["template"],
        graphics=c["graphics"],
        feedback_texts=[],
        corpus=c["corpus"],
    )
    sev = [o for o in out if o.get("target") == "identifiers.severity"]
    values = {o.get("new_value") for o in sev}
    assert len(sev) >= 2 and len(values) >= 2  # conflict intact


# --------------------------------------------------------------------------
# End-to-end through run_reconciliation
# --------------------------------------------------------------------------


@pytest.fixture
def bedrock_off(monkeypatch):
    """Pin the deterministic (offline) path. The model refinement is, by design,
    not bit-reproducible; the determinism guarantee is for the deterministic
    baseline, so these end-to-end assertions pin Bedrock off."""
    from app.config import settings

    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False)


@pytest.mark.parametrize("mode", ["draft", "template"])
def test_run_reconciliation_still_works_both_modes(bedrock_off, project_module, project_id, mode):
    """The refinement layer does not break reconciliation on any sample project
    in either pathway; a report with sections is produced."""
    report = project_module.run_reconciliation(mode, project_id)
    assert report["sections"]
    assert "summary" in report


def test_run_reconciliation_is_deterministic(bedrock_off):
    """Two runs produce an identical report once the timestamp is stripped
    (deterministic baseline; the model path is intentionally not covered here)."""
    a = sc.run_reconciliation("draft", "1", "json")
    b = sc.run_reconciliation("draft", "1", "json")
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_sample_figure_is_corrected(bedrock_off):
    """Sample project 1's timeline figure resolves to 'corrected' (the grounding
    + structured relabel agree)."""
    report = sc.run_reconciliation("draft", "1", "json")
    statuses = {}
    for s in report["sections"]:
        for g in s.get("graphics", []):
            statuses[g["name"]] = g["status"]
    assert statuses.get("packet_loss_vs_temp.png") == "corrected"
