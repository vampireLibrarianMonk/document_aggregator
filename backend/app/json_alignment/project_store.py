"""Per-project persistence for the batch JSON-alignment feature.

Each project that uses batch conversion gets an `alignment/` subdir in its store
directory holding two artifacts:

    alignment/target_schema.json   the GOLDEN target JSON Schema everything in
                                   this project converts to.
    alignment/library.json         the ProfileLibrary: the learned shapes (one
                                   per source team/shape), with their approval
                                   state, all pointing at that one target.

Both are plain JSON so they are inspectable and portable. Access is lock-guarded
and atomic (tmp + replace) to match the rest of the store. These are the two
things a batch run needs: WHAT to conform to (target schema) and WHAT we've
already learned (the library we replay / grow).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..config import settings
from .profile_store import ProfileLibrary, load_library, save_library

_lock = threading.RLock()


def _alignment_dir(project_id: str) -> Path:
    return settings.project_dir(project_id) / "alignment"


def target_schema_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "target_schema.json"


def library_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "library.json"


# -- golden target schema ----------------------------------------------------

def save_target_schema(project_id: str, schema: dict[str, Any]) -> Path:
    p = target_schema_path(project_id)
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(schema, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
    return p


def load_target_schema(project_id: str) -> dict[str, Any] | None:
    p = target_schema_path(project_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# -- profile library ---------------------------------------------------------

def load_project_library(project_id: str, *, target_title: str = "") -> ProfileLibrary:
    """Load the project's library, or return a fresh empty one if none exists."""
    p = library_path(project_id)
    if p.exists():
        return load_library(p)
    return ProfileLibrary(target_title=target_title)


def save_project_library(project_id: str, library: ProfileLibrary) -> Path:
    with _lock:
        return save_library(library, library_path(project_id))


__all__ = [
    "library_path",
    "load_project_library",
    "load_target_schema",
    "save_project_library",
    "save_target_schema",
    "target_schema_path",
]
