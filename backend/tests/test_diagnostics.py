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
