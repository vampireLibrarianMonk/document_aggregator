"""Governor state-machine + adjudicator tests (fully offline, no Bedrock)."""
from __future__ import annotations

from app.projectgen.generator import ProjectBrief
from app.projectgen.governor import (
    ADJUDICATORS,
    DeterministicAdjudicator,
    make_adjudicator,
    run_governed,
)
from app.projectgen.governor.adjudicators import grounded_value
from app.projectgen.governor.core import (
    Decision,
    DecisionKind,
    VerdictKind,
)
from app.projectgen.schema import validate_spec

BRIEF = ProjectBrief(domain="avionics interface validation",
                      doc_type="interface control document", title="Nav Bus ICD")


def test_governed_run_produces_valid_spec_offline():
    res = run_governed(BRIEF)
    assert res.spec is not None
    assert validate_spec(res.spec) == []        # persistable
    assert res.sections_planned > 0
    assert res.sections_filled > 0
    assert res.decisions_total > 0


def test_all_adjudicators_run_offline_and_degrade():
    # With no client, the model-based adjudicators degrade to deterministic.
    for name in ADJUDICATORS:
        adj = make_adjudicator(name)  # no client/adapter
        res = run_governed(BRIEF, adjudicator=adj)
        assert res.adjudicator == "deterministic"
        assert validate_spec(res.spec) == []


def test_events_are_structured_and_ordered():
    res = run_governed(BRIEF)
    assert res.events, "governor must emit progress events"
    first = res.events[0]
    assert first.step == "plan" and first.status == "start"
    assert res.events[-1].step == "reconcile"
    # every event serializes cleanly for the live stream
    for e in res.events:
        d = e.as_dict()
        assert set(["step", "status", "ts"]).issubset(d)


def test_decision_quality_tracks_agreement_with_truth():
    res = run_governed(BRIEF)
    # Deterministic adjudicator always agrees with the deterministic truth.
    assert res.decisions_total == res.decisions_agree_truth
    assert res.summary()["decision_agreement"] == 1.0


def test_per_section_authoring_offline():
    from app.projectgen.governor import make_section_author
    sa = make_section_author()  # offline deterministic section author
    res = run_governed(BRIEF, section_author=sa)
    s = res.summary()
    assert s["authoring_mode"] == "per_section"
    assert s["sections_planned"] > 0
    assert s["sections_filled"] > 0
    assert validate_spec(res.spec) == []           # still persistable
    # per-section mode must still emit the ordered fill/proofread events
    steps = [e.step for e in res.events]
    assert any(st.startswith("fill:") for st in steps)
    assert any(st.startswith("proofread:") for st in steps)


def test_section_author_assembles_same_contract():
    from app.projectgen.governor import DeterministicSectionAuthor
    sa = DeterministicSectionAuthor()
    plan = sa.plan(BRIEF)
    assert plan and all("key" in s for s in plan)
    spec = sa.assemble()
    assert validate_spec(spec) == []
    # authoring one section returns a bounded payload for that key
    one = sa.author_section(plan[0]["key"])
    assert one["key"] == plan[0]["key"]


def test_recommend_model_is_earned_and_overridable():
    from app.projectgen.model_profiles import recommend_model
    full = ["openai.gpt-oss-20b-1:0", "nvidia.nemotron-super-3-120b",
            "openai.gpt-oss-120b-1:0"]
    r = recommend_model(full)
    assert "gpt-oss-120b" in r["model"]        # earned winner from the eval
    assert r["reason"] and r["basis"]           # transparent
    # falls through the ranking when the top pick is absent
    assert "nemotron-super" in recommend_model(full[:2])["model"]
    # offline / empty -> no model (the UI offers the deterministic generator)
    assert recommend_model([])["model"] == ""


def test_generate_document_op_offline():
    """The async governor op runs through the job queue and stores the event log
    + summary as the job result (fully offline, no Bedrock)."""
    import tempfile
    from pathlib import Path

    from app.jobs import ops  # noqa: F401  (registers generate_document)
    from app.jobs.queue import SqliteJobQueue
    from app.jobs.worker import Worker, registered_ops

    assert "generate_document" in registered_ops()
    q = SqliteJobQueue(Path(tempfile.mkdtemp()) / "jobs.db")
    jid = q.enqueue("generate_document", {
        "domain": "avionics interface validation",
        "doc_type": "interface control document", "per_section": True})
    assert Worker(q, "w1").run_once() is True
    job = q.get(jid)
    assert job.state.value == "completed"
    assert job.result["summary"]["authoring_mode"] == "per_section"
    assert job.result["summary"]["sections_filled"] > 0
    assert len(job.result["events"]) > 0        # the full progress trail is stored


def test_grounded_value_rule():
    corpus = "the measured flow rate was 4.2 L/min at the inlet"
    assert grounded_value("4.2 L/min", corpus)
    assert grounded_value("", corpus)                  # nothing asserted
    assert not grounded_value("9.9 L/min", corpus)     # fabrication
    assert grounded_value("supported in body", "", body="supported in body")


def test_deterministic_adjudicator_verdicts():
    adj = DeterministicAdjudicator()
    # ungrounded asserted value -> reject (fabrication)
    d = Decision(kind=DecisionKind.fill, unit_id="s1",
                 payload={"asserted_values": ["banana 7x"], "body": ""},
                 corpus="nothing relevant here")
    assert adj.judge(d).kind == VerdictKind.reject
    # grounded value -> accept
    d2 = Decision(kind=DecisionKind.fill, unit_id="s2",
                  payload={"asserted_values": ["4.2 L/min"], "body": ""},
                  corpus="measured 4.2 L/min")
    assert adj.judge(d2).kind == VerdictKind.accept
    # required but absent -> needs_review
    d3 = Decision(kind=DecisionKind.fill, unit_id="s3",
                  payload={"asserted_values": [], "required_but_absent": True},
                  corpus="")
    assert adj.judge(d3).kind == VerdictKind.needs_review


def test_decision_layer_escalates_on_low_confidence(monkeypatch):
    # Build a decision_layer with a fake adapter that returns low-confidence
    # garbage; it must escalate to the deterministic truth.
    from app.projectgen.governor.adjudicators import DecisionLayerAdjudicator

    class FakeAdapter:
        def complete_with_usage(self, client, system, user, max_tokens):
            class U:
                input_tokens = output_tokens = latency_ms = 0
            return '{"verdict":"accept","confidence":0.1,"reason":"unsure"}', U()

    adj = DecisionLayerAdjudicator("m", client=object(), adapter=FakeAdapter(),
                                   confidence_threshold=0.6)
    # ungrounded -> truth says reject; low-confidence 'accept' must be overridden
    d = Decision(kind=DecisionKind.fill, unit_id="s1",
                 payload={"asserted_values": ["banana 7x"], "body": ""},
                 corpus="nothing relevant")
    v = adj.judge(d)
    assert v.kind == VerdictKind.reject
    assert "escalated" in v.source
