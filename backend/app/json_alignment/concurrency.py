"""Container-aware worker sizing for the batch pipeline.

The batch engine parallelizes independent clusters (and files within a
replay cluster) with a thread pool. Sizing that pool with `os.cpu_count()` is a
trap in Kubernetes: `os.cpu_count()` reports the NODE's cores, not the pod's CPU
LIMIT, so a pool sized to the node oversubscribes a limited pod and gets
CFS-throttled (the pod crawls while the node shows idle CPU).

`available_cores()` instead reads the cgroup CPU quota the container actually
has, falling back to `os.cpu_count()` only when no cgroup limit is set (bare
metal / dev laptop):

  cgroup v2:  /sys/fs/cgroup/cpu.max            -> "<quota> <period>" (or "max")
  cgroup v1:  /sys/fs/cgroup/cpu/cpu.cfs_quota_us + .../cpu.cfs_period_us
              cores = ceil(quota / period) when quota > 0

`worker_count()` applies the user policy of **total cores - 1** (leave one core
for the event loop / OS), floored at 1.

NOTE (Helm/k8s revisit): the correct knob at deploy time is the pod's CPU
*limit* (not request); a request becomes a CPU share and does NOT bound quota,
so only a limit shows up in cpu.max. If no limit is set, this falls back to the
node's cores and can oversubscribe -- set resources.limits.cpu in the Helm
values, or override via the JSON_ALIGNMENT_MAX_WORKERS env var. This module is
the single seam to adjust when that review happens.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

# Explicit override wins over any detection (set from Helm values if desired).
_ENV_OVERRIDE = "JSON_ALIGNMENT_MAX_WORKERS"

_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _cores_from_cgroup_v2() -> float | None:
    """cgroup v2 cpu.max is '<quota> <period>' in microseconds, or 'max' when
    unlimited. Returns quota/period cores, or None if unset/unlimited.

    Reads the module-level path at call time (not as a default arg) so tests can
    monkeypatch the path."""
    try:
        raw = _CGROUP_V2.read_text().strip()
    except OSError:
        return None
    parts = raw.split()
    if not parts or parts[0] == "max":
        return None
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100000
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def _cores_from_cgroup_v1() -> float | None:
    """cgroup v1 cpu.cfs_quota_us / cpu.cfs_period_us. A quota of -1 means
    unlimited. Returns quota/period cores, or None if unset/unlimited.

    Reads module-level paths at call time so tests can monkeypatch them."""
    quota = _read_int(_CGROUP_V1_QUOTA)
    period = _read_int(_CGROUP_V1_PERIOD)
    if quota is None or period is None or quota <= 0 or period <= 0:
        return None
    return quota / period


def available_cores() -> int:
    """Best estimate of the cores THIS process may actually use.

    Prefers the container's cgroup CPU quota (so a limited pod is respected),
    falling back to os.cpu_count() when no quota is set. Always >= 1 and rounded
    UP (a 1.5-core limit yields 2 schedulable workers; the -1 policy lives in
    worker_count, not here)."""
    quota_cores = _cores_from_cgroup_v2() or _cores_from_cgroup_v1()
    if quota_cores is not None:
        return max(1, math.ceil(quota_cores))
    return max(1, os.cpu_count() or 1)


def worker_count(reserve: int = 1, *, maximum: int | None = None) -> int:
    """How many worker threads the batch pool should use.

    Policy: available cores minus `reserve` (default 1, per the user's
    cores-minus-one rule), floored at 1. An explicit JSON_ALIGNMENT_MAX_WORKERS
    env var overrides detection entirely; an optional `maximum` caps the result
    (e.g. to avoid spinning more threads than clusters)."""
    override = os.getenv(_ENV_OVERRIDE)
    if override:
        try:
            n = int(override)
            if n >= 1:
                return min(n, maximum) if maximum else n
        except ValueError:
            pass
    n = max(1, available_cores() - max(0, reserve))
    if maximum is not None:
        n = min(n, max(1, maximum))
    return n


__all__ = ["available_cores", "worker_count"]
