"""Government-standard reproducibility tests.

The pinned gov_standard profile + metric-compatible font substitution is the
reproducibility anchor. These assert:
  - the profile loads fully-declared (no learning/defaulting needed);
  - proprietary fonts conform to their metric-compatible required family
    (a document specifying Arial is NOT flagged against a Liberation Sans rule);
  - a genuinely wrong font (not metric-compatible) IS flagged;
  - geometry extraction from a fixed PDF is deterministic across runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.discipline import (  # noqa: E402
    fonts_equivalent,
    load_discipline,
    load_profile,
    normalize_font,
)
from app.discipline.inspect import inspect as inspect_discipline  # noqa: E402
from app.layout.geometry import extract_layout  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def test_profile_loads_fully_declared():
    d = load_profile("gov_standard")
    assert d.text_format["body"].font == "Liberation Serif"
    assert d.text_format["body"].size_pt == 12
    assert all(tf.source == "declared" for tf in d.text_format.values())
    assert d.rule_sources.get("profile") == "gov_standard"


def test_profile_selectable_via_template_ref():
    d = load_discipline({"build_discipline": {"profile": "gov_standard"}})
    assert d.text_format["heading"].font == "Liberation Sans"
    assert d.layout.page_number_region == "footer-center"


def test_metric_compatible_font_map():
    assert normalize_font("Arial") == "Liberation Sans"
    assert normalize_font("Times New Roman") == "Liberation Serif"
    assert normalize_font("Courier New") == "Liberation Mono"
    assert normalize_font("Calibri") == "Carlito"
    assert normalize_font("Cambria") == "Caladea"
    assert fonts_equivalent("Arial", "Liberation Sans")
    assert not fonts_equivalent("Arial", "Liberation Serif")


def test_proprietary_font_conforms_to_metric_compatible_rule():
    """A document body observed as 'Arial' must NOT be flagged against a
    gov_standard rule requiring 'Liberation Sans' — they are metric-compatible,
    so rendering geometry is identical. This is the false-positive guard."""
    d = load_profile("gov_standard")
    # heading rule requires Liberation Sans; observe Arial (its proprietary twin)
    evidence = {"text_format_samples": {"heading": [
        {"font": "Arial", "size_pt": 14, "weight": "bold", "casing": "title"}]}}
    findings = inspect_discipline(evidence, d)
    font_flags = [f for f in findings
                  if f.key.startswith("text_format.heading") and "font" in f.key
                  and f.status == "corrected"]
    assert not font_flags, "metric-compatible font should not be flagged"


def test_non_compatible_font_is_flagged():
    d = load_profile("gov_standard")
    evidence = {"text_format_samples": {"heading": [
        {"font": "Comic Sans MS", "size_pt": 14, "weight": "bold", "casing": "title"}]}}
    findings = inspect_discipline(evidence, d)
    font_flags = [f for f in findings
                  if f.key.startswith("text_format.heading") and "font" in f.key
                  and f.status == "corrected"]
    assert font_flags, "a non-metric-compatible font must be flagged"


def test_geometry_extraction_deterministic():
    """Same PDF -> same element geometry on every extraction (reproducibility)."""
    data = (FIX / "compliant.pdf").read_bytes()
    a = extract_layout(data)
    b = extract_layout(data)
    boxes_a = [(box.role, box.as_tuple()) for p in a for box in p.boxes]
    boxes_b = [(box.role, box.as_tuple()) for p in b for box in p.boxes]
    assert boxes_a == boxes_b
