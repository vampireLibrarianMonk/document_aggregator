"""Governor state-machine + adjudicator tests (fully offline, no Bedrock)."""
from __future__ import annotations

from app.scenariogen.generator import ScenarioBrief
from app.scenariogen.governor import (
    ADJUDICATORS,
    DeterministicAdjudicator,
    make_adjudicator,
    run_governed,
)
from app.scenariogen.governor.adjudicators import grounded_value
from app.scenariogen.governor.core import (
    Decision,
    DecisionKind,
    VerdictKind,
)
from app.scenariogen.schema import validate_spec

BRIEF = ScenarioBrief(domain="avionics interface validation",
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
    from app.scenariogen.governor.adjudicators import DecisionLayerAdjudicator

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
