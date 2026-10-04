"""Corpus-grounded generation: faithful + deterministic (fully offline).

Proves the core claim: when the user's document IS the corpus, generation is
deterministic (same corpus -> identical project) and faithful (no fabrication;
every asserted value is grounded in the provided text). This closes the drift
the model path exhibits (see GOVERNOR_EVAL.md adversarial review).
"""
from __future__ import annotations

from app.projectgen.corpus_generator import CorpusProjectGenerator
from app.projectgen.corpus_intake import corpus_from_texts, corpus_from_upload
from app.projectgen.generator import ProjectBrief, get_generator_for_brief
from app.projectgen.schema import validate_spec

SAMPLE = """# Incident Summary
Site: West Campus Facility
Event Date: 2026-10-02
Firmware Version: 5.1.3
Severity: High

# Findings
The unit showed a measured deviation after a threshold alarm.

# Corrective Actions
Upgrade affected units to the validated firmware revision.
"""


def _brief(corpus) -> ProjectBrief:
    return ProjectBrief(domain="hardware reliability", title="Gateway Incident",
                         corpus=corpus)


def test_corpus_scenario_is_valid_and_grounded():
    corpus = corpus_from_texts([{"name": "incident.md", "text": SAMPLE}])
    spec = CorpusProjectGenerator().generate(_brief(corpus))
    assert validate_spec(spec) == []                         # persistable
    # the uploaded text is used verbatim as the ground truth
    assert any(d.text == SAMPLE for d in spec.corpus)
    # richness: a conflict and a needs-review unit
    by_target: dict[str, set] = {}
    for c in spec.corrections:
        by_target.setdefault(c.target, set()).add(str(c.new_value))
    assert any(len(v) > 1 for v in by_target.values())        # conflict
    assert any(f.extract == "none" for f in spec.fields)      # needs-review


def test_corpus_scenario_is_deterministic():
    corpus = corpus_from_texts([{"name": "incident.md", "text": SAMPLE}])
    a = CorpusProjectGenerator().generate(_brief(corpus)).model_dump_json()
    b = CorpusProjectGenerator().generate(_brief(corpus)).model_dump_json()
    assert a == b, "same corpus + brief must yield a byte-identical spec"


def test_no_fabrication_every_replacement_is_grounded():
    corpus = corpus_from_texts([{"name": "incident.md", "text": SAMPLE}])
    spec = CorpusProjectGenerator().generate(_brief(corpus))
    blob = " ".join(d.text for d in spec.corpus).lower()
    for c in spec.corrections:
        if c.operation == "replace" and c.new_value:
            nv = str(c.new_value).lower()
            # grounded either in the corpus text or stated in the correction body
            assert nv in blob or nv in (c.body or "").lower(), \
                f"correction {c.id} asserts an ungrounded value {c.new_value!r}"


def test_brief_with_corpus_routes_to_corpus_generator():
    corpus = corpus_from_texts([{"name": "incident.md", "text": SAMPLE}])
    assert get_generator_for_brief(_brief(corpus)).name == "corpus-grounded"
    # without a corpus, the normal (offline) generator is used
    assert get_generator_for_brief(ProjectBrief(domain="x")).name != "corpus-grounded"


def test_corpus_from_upload_parses_text_bytes():
    corpus = corpus_from_upload("incident.md", SAMPLE.encode("utf-8"))
    assert corpus and corpus[0].name.endswith(".md")
    assert "West Campus Facility" in corpus[0].text    # faithful extraction


def test_empty_document_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        corpus_from_upload("empty.txt", b"   ")


def test_thin_corpus_falls_back_safely():
    # No corpus at all -> the generator must not emit an invalid spec; it defers
    # to the deterministic rule generator.
    spec = CorpusProjectGenerator().generate(ProjectBrief(domain="x", title="y"))
    assert validate_spec(spec) == []
