"""Shared test fixtures: make `app` importable and enumerate all scenarios."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import scenario as sc  # noqa: E402


def all_scenario_ids() -> list[str]:
    return [s["id"] for s in sc.list_scenarios()]


@pytest.fixture(params=all_scenario_ids())
def scenario_id(request) -> str:
    return request.param


@pytest.fixture
def scenario_module():
    return sc
