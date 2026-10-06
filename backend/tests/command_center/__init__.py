"""Command-center bake-off: a JEV-style Coordinator that decomposes correction
work into bounded sub-tasks, dispatches them to swappable sub-agents (in-process
deterministic + model-backed Bedrock), runs a parallel queue with a
deterministic order-preserving assembler, and iterates to convergence.

Not part of the app. Harness only; the winning architecture is promoted into
the Phase 3 engine later.

Entry points:
    python -m tests.command_center.efficiency   # Task 1: model-efficiency study
    python -m tests.command_center.run           # Task 5: full matrix bake-off
"""
