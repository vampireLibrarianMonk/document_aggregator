"""Shared test fixtures: make `app` importable and enumerate all projects."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import project as sc  # noqa: E402


def all_project_ids() -> list[str]:
    return [s["id"] for s in sc.list_project_cases()]


@pytest.fixture(params=all_project_ids())
def project_id(request) -> str:
    return request.param


@pytest.fixture
def project_module():
    return sc
