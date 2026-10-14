"""Conformance-frontier alpha loop (research harness, not wired into the app).

Pushes each malformation AXIS to increasing SEVERITY until the deterministic
JSON->golden matcher can no longer recover a shape, binary-searches that break
point, and reports the resulting conformance frontier — measuring BOTH recall
(fields recovered) and precision (false matches) with an unrelated-junk control.

Everything is deterministic + offline. Ground truth for every mutated field is
MECHANICAL (derived from the mutation applied), never read back from the engine,
so the harness can never grade the engine against the engine.
"""
