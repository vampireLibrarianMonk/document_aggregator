"""On-demand model-sweep safety test.

The sweep harness (tests/command_center/model_sweep.py) runs the winning
bake-off configuration across multiple approved models. This test is the
CI-visible guard rail on top of it: when run live (BEDROCK_ENABLED=true with
creds), it asserts the HARD SAFETY INVARIANTS hold for every model that actually
ran — zero fabrications and the deliberate conflict preserved. It deliberately
does NOT assert accuracy numbers, which are model-dependent; the point is that
no model, however weak, is allowed to push an ungrounded value through or
silently collapse a conflict.

Offline (the default, and all of CI) this test SKIPS, matching the repo
convention for live-Bedrock tests (see test_interpreter.py / the conftest
autouse offline pin). Model calls are cached on disk, so a warm re-run is free.
"""
from __future__ import annotations

import os

import pytest

_LIVE = os.getenv("BEDROCK_ENABLED", "false").lower() == "true"


@pytest.mark.skipif(
    not _LIVE,
    reason="Bedrock disabled; set BEDROCK_ENABLED=true with creds to run the model sweep",
)
def test_model_sweep_invariants_hold_for_every_model_that_ran():
    """Live: sweep a small subset and assert the safety invariants per model.

    Uses an explicit small subset (the two gpt-oss sizes) rather than the full
    catalog so the test is bounded and cache-friendly; the standalone harness
    sweeps everything. Any model that actually produced output must show zero
    fabrications and must have preserved every expected conflict."""
    from tests.command_center.model_sweep import sweep

    report = sweep(["gpt-oss-120b", "gpt-oss-20b"])

    assert report["config"]["pathway"] == "draft"
    assert report["config"]["agent"] == "model"
    assert isinstance(report["models"], list) and report["models"], "no models resolved"

    ran_any = False
    for m in report["models"]:
        # A model that could not be reached is not a failure of this test; it is
        # simply not exercised. Only assert on models that actually produced runs.
        if not m["available"] or m["projects_run"] == 0:
            continue
        ran_any = True
        assert m["total_fabrications"] == 0, (
            f"{m['model_id']} fabricated {m['total_fabrications']} ungrounded "
            f"value(s) — the no-fabrication guarantee was broken"
        )
        assert m["conflicts_preserved"] == m["conflicts_expected"], (
            f"{m['model_id']} collapsed a conflict: preserved "
            f"{m['conflicts_preserved']}/{m['conflicts_expected']}"
        )
        assert m["invariants_held"], f"{m['model_id']} broke a hard invariant"

    assert ran_any, "no model in the subset was reachable; cannot assert invariants"


@pytest.mark.skipif(
    not _LIVE,
    reason="Bedrock disabled; set BEDROCK_ENABLED=true with creds to run the model sweep",
)
def test_model_sweep_report_is_well_formed():
    """Live: the sweep report carries the fields the write-up/UI depend on."""
    from tests.command_center.model_sweep import sweep

    report = sweep(["gpt-oss-120b"])
    assert set(report) >= {"config", "bedrock_available", "allowlist", "models"}
    for m in report["models"]:
        assert set(m) >= {
            "model_id", "available", "projects_run", "avg_value_pct",
            "avg_status_pct", "total_fabrications", "conflicts_preserved",
            "conflicts_expected", "invariants_held", "est_usd", "per_project",
        }
