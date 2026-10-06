"""Scaling precision-correction alpha loop.

A harness that pits precision-EDITING techniques against each other on the
synthetic growth dataset and measures how each behaves AS THE DOCUMENT GROWS.
The question it answers is the one the user raised: our discrete-field accuracy
is strong on small docs, but does precision hold as documents scale, especially
for prose edits where a technique could "fix" the target sentence while
silently drifting the surrounding text?

Modules:
  metric      precision / recall / unintended-change / no-fabrication / conflict
  techniques  T1 anchored span-replace, T2 diff-constrained, T3 model, T4 micro
  run         sweep techniques x sizes, score, persist, scorecard + recommendation
"""
