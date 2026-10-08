"""Correction Orchestrator (a.k.a. the command center): the JEV-style
orchestration layer, promoted to production.

PRODUCT NAME: when this orchestration runs the document-correction pipeline it is
the **Correction Orchestrator** — that is the canonical, user-facing name (and
the `engine="orchestrator"` API value). The underlying `Coordinator` class and
this package are deliberately GENERIC: the same queue + deterministic assembler +
convergence machinery also drives JSON-alignment and the mass batch pathway, so
the class/package keep their workflow-agnostic names while the correction-facing
product name is "Correction Orchestrator".

A Coordinator decomposes work into a bounded sub-task DAG, dispatches each task
to the sub-agent that handles its kind through a parallel queue with a
deterministic order-preserving assembler, and iterates to convergence
(needs_review -> 0, genuine conflicts preserved, capped rounds).

This is the orchestration layer that was proven in the command-center bake-off
(see docs/testing/bakeoff-command-center.md) and is now wired into the
Correction Pipeline. It is deliberately generic: the correction flow is one
`SubAgent` implementation, and future workflows (e.g. JSON schema alignment)
plug in as additional agents without a second orchestration framework.

Public surface:
  SubTask / TaskResult / SubAgent      the task + agent contracts
  QueueAssembler                       parallel-capable, deterministic assembly
  RoundState / assess / count_states   convergence detection
  Coordinator / RunRecord              the orchestrator + its observable output
  CorrectionAgent                      the production correction sub-agent
  run_correction                       convenience entry for a project correction
"""
from __future__ import annotations

from .agents import CorrectionAgent
from .convergence import RoundState, assess, count_states
from .coordinator import Coordinator, RunRecord, run_correction
from .core import QueueAssembler, SubAgent, SubTask, TaskResult

__all__ = [
    "CorrectionAgent",
    "Coordinator",
    "QueueAssembler",
    "RoundState",
    "RunRecord",
    "SubAgent",
    "SubTask",
    "TaskResult",
    "assess",
    "count_states",
    "run_correction",
]
