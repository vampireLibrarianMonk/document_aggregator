"""Adversarial Alpha Loop — a modular apparatus that fires JSON files at the
LIVE stack and red-teams the JSON->golden approval/rejection pipeline.

Public surface (import these to drive it in code or from a chat session):
    Controller          the orchestrator (step mode + run-to-break)
    Generator           the staged benign->hostile shape generator
    Decider             the spiral-out auto-decision policy under test
    CorrectnessAdversary / InvariantAdversary / PerformanceAdversary
                        the three red-team agents
    AlphaClient         the thin live-API client
    STAGES              the benign->hostile ladder

See README.md for the design and the spin-up commands.
"""
from __future__ import annotations

from .agents import (
    CorrectnessAdversary,
    InvariantAdversary,
    PerformanceAdversary,
    Verdict,
)
from .client import AlphaClient
from .controller import Controller, RoundReport
from .decider import Decider, Decision
from .generator import STAGES, Generator, ShapeSpec

__all__ = [
    "AlphaClient",
    "Controller",
    "CorrectnessAdversary",
    "Decider",
    "Decision",
    "Generator",
    "InvariantAdversary",
    "PerformanceAdversary",
    "RoundReport",
    "STAGES",
    "ShapeSpec",
    "Verdict",
]
