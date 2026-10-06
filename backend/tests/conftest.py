"""Shared test fixtures: make `app` importable and enumerate all projects."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import project as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_by_default(monkeypatch):
    """Keep the suite offline + deterministic by default: pin Bedrock OFF so
    reconciliation never makes a live model call (slow + non-reproducible).

    Respect an explicit opt-in: when BEDROCK_ENABLED=true is set in the
    environment, leave the setting alone so the dedicated live-Bedrock tests
    (which skipif on the same env var) can run against the model."""
    import os

    if os.getenv("BEDROCK_ENABLED", "false").lower() == "true":
        return
    from app.config import settings

    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False, raising=False)


def all_project_ids() -> list[str]:
    return [s["id"] for s in sc.list_project_cases()]


@pytest.fixture(params=all_project_ids())
def project_id(request) -> str:
    return request.param


@pytest.fixture
def project_module():
    return sc
