"""Centralized logging configuration.

One place to configure how the platform logs, used by every entry point (the API
and the worker). Module code never configures logging itself — it only does
`log = logging.getLogger(__name__)` and emits; the format, level, and handlers
are owned here so output is consistent and controllable.

Policy (the project's standard): **exceptions are never swallowed silently.** A
handler either re-raises, or logs at an appropriate level:
  - `log.exception(...)` / `log.error(...)` for a genuine failure;
  - `log.warning(...)` for a recoverable/degraded path (e.g. an optional
    dependency missing);
  - `log.debug(...)` for an expected, high-frequency skip (e.g. a parse that
    legitimately means "skip this element").

Level is controlled by the `LOG_LEVEL` env var (default INFO). Idempotent:
calling `configure_logging()` more than once is safe.
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str | None = None) -> None:
    """Install a single stream handler + format on the root logger. Idempotent.

    `level` overrides the `LOG_LEVEL` env var (default INFO). Call once at
    process start (API startup, worker main)."""
    global _CONFIGURED
    lvl_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    root = logging.getLogger()
    if not _CONFIGURED:
        # Respect an already-installed handler set (e.g. uvicorn's) by only
        # adding ours when the root has none, so we don't double-emit.
        if not root.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
            root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(lvl)
    # Keep the app's own loggers at the configured level explicitly.
    logging.getLogger("app").setLevel(lvl)


def get_logger(name: str) -> logging.Logger:
    """Convenience accessor; equivalent to logging.getLogger(name)."""
    return logging.getLogger(name)
