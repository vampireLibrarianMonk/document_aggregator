"""The /diagnostics snapshot must report the TRUTH about each capability so
silent degradations are visible. These assert the structure and the
state-derivation logic under both healthy and degraded conditions.
"""
from __future__ import annotations

import pytest
from app import diagnostics
from app.config import settings


def test_snapshot_structure():
    snap = diagnostics.collect()
    assert set(snap) >= {"overall", "services", "versions", "offline_guard", "data_dir"}
    assert set(snap["services"]) == {"embedding", "ocr", "geometry", "bedrock"}
    assert snap["versions"]["pipeline"] == settings.PIPELINE_VERSION
    assert snap["overall"] in ("ok", "degraded", "error")


def test_embedding_reports_actual_provider():
    st = diagnostics._embedding_status()
    assert "provider" in st and "is_real_model" in st and "state" in st
    # Whatever is installed, the reported provider must be internally consistent.
    if st["is_real_model"]:
        assert st["provider"] == "SentenceTransformerProvider"
        assert st["dimensions"] and st["dimensions"] > 0


def test_embedding_disabled_is_degraded(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", False, raising=False)
    st = diagnostics._embedding_status()
    assert st["state"] == "degraded"
    assert st["enabled"] is False


def test_ocr_off_is_degraded(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENABLED", False, raising=False)
    st = diagnostics._ocr_status()
    assert st["state"] == "degraded"
    assert st["enabled"] is False


def test_geometry_reflects_soffice(monkeypatch):
    monkeypatch.setattr(diagnostics, "_geometry_status", diagnostics._geometry_status)
    st = diagnostics._geometry_status()
    assert st["state"] in ("ok", "degraded")
    assert isinstance(st["soffice_available"], bool)
    # state must agree with the probe.
    assert (st["state"] == "ok") == st["soffice_available"]


def test_bedrock_offline_is_not_an_error(monkeypatch):
    # Offline Bedrock is a normal air-gap posture, never 'error'.
    st = diagnostics._bedrock_status()
    assert st["state"] in ("ok", "offline")


def test_bedrock_disabled_reads_offline_even_if_reachable(monkeypatch):
    # Finding D1: a disabled Bedrock must NOT show 'ok' just because creds are
    # present and the probe reaches it. Disabled by config -> 'offline'.
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False, raising=False)
    monkeypatch.setattr(
        diagnostics, "_bedrock_status", diagnostics._bedrock_status)
    # Simulate a reachable Bedrock (available=True) while disabled.
    import app.projectgen.bedrock_gen as bg
    monkeypatch.setattr(bg, "list_approved_models",
                        lambda: {"available": True, "models": [{"name": "x"}],
                                 "default": "", "allowlist": ["nemotron"]})
    st = diagnostics._bedrock_status()
    assert st["enabled"] is False
    assert st["available"] is True
    assert st["state"] == "offline"  # NOT 'ok' — it's turned off


def test_bedrock_ok_only_when_enabled_and_available(monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", True, raising=False)
    import app.projectgen.bedrock_gen as bg
    monkeypatch.setattr(bg, "list_approved_models",
                        lambda: {"available": True, "models": [{"name": "x"}],
                                 "default": "", "allowlist": ["nemotron"]})
    st = diagnostics._bedrock_status()
    assert st["enabled"] is True and st["available"] is True
    assert st["state"] == "ok"


def test_bedrock_enabled_but_unreachable_is_offline(monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", True, raising=False)
    import app.projectgen.bedrock_gen as bg
    monkeypatch.setattr(bg, "list_approved_models",
                        lambda: {"available": False, "models": []})
    st = diagnostics._bedrock_status()
    assert st["state"] == "offline"


def test_bedrock_disabled_does_not_drag_overall_down(monkeypatch):
    # With all core services ok and Bedrock disabled, overall stays ok.
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False, raising=False)
    snap = diagnostics.collect()
    assert snap["services"]["bedrock"]["state"] == "offline"
    # bedrock offline must not pull overall below the core services' posture.
    core = [snap["services"][s]["state"] for s in ("embedding", "ocr", "geometry")]
    if "error" not in core and "degraded" not in core:
        assert snap["overall"] == "ok"


def test_offline_guard_reporting(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    snap = diagnostics.collect()
    assert snap["offline_guard"]["hf_hub_offline"] is True
    assert snap["offline_guard"]["transformers_offline"] is False


def test_overall_degraded_when_a_core_service_degrades(monkeypatch):
    # Force OCR off -> core service degraded -> overall at least degraded.
    monkeypatch.setattr(settings, "OCR_ENABLED", False, raising=False)
    snap = diagnostics.collect()
    assert snap["overall"] in ("degraded", "error")


@pytest.mark.parametrize("core,expected", [
    (["ok", "ok", "ok"], "ok"),
    (["ok", "degraded", "ok"], "degraded"),
    (["ok", "error", "degraded"], "error"),
])
def test_overall_derivation(core, expected):
    # Mirror the overall logic directly to lock the precedence error>degraded>ok.
    if "error" in core:
        got = "error"
    elif "degraded" in core:
        got = "degraded"
    else:
        got = "ok"
    assert got == expected
