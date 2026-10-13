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


# -- conformed batch output (per source file) --------------------------------
#
# A batch run's conformed golden records, partitioned back to the SOURCE file
# they came from, are persisted under alignment/outputs/ so the Report & Export
# tab can offer a per-file "<name>_conformed_<DTG>.json" download after the
# async job finishes. Only producing pathways (replay/novel/drift) write output;
# review/reject docs produce nothing and are recorded in the manifest as such.

def outputs_dir(project_id: str) -> Path:
    return _alignment_dir(project_id) / "outputs"


def _safe_stem(doc_id: str) -> str:
    """A filesystem-safe stem derived from a source doc id/filename (its own
    extension dropped; path separators and oddities neutralized)."""
    base = os.path.basename(doc_id or "doc")
    if base.lower().endswith(".json"):
        base = base[:-5]
    safe = "".join(c if (c.isalnum() or c in "-_. ") else "_" for c in base).strip()
    return safe or "doc"


def save_batch_outputs(project_id: str, conformed_by_doc: dict[str, list[dict[str, Any]]],
                       *, pathways_by_doc: dict[str, str] | None = None) -> dict[str, Any]:
    """Persist each source file's conformed records as its own JSON file and
    write a manifest. Replaces any prior batch output for the project (the latest
    batch is what the Report tab shows). Returns the manifest dict."""
    pathways_by_doc = pathways_by_doc or {}
    with _lock:
        d = outputs_dir(project_id)
        # Clear prior output so the Report tab reflects only the latest batch.
        if d.exists():
            for old in d.glob("*.json"):
                try:
                    old.unlink()
                except OSError:
                    pass
        d.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        used: set[str] = set()
        for doc_id, records in conformed_by_doc.items():
            stem = _safe_stem(doc_id)
            # de-dupe stems from distinct doc_ids that normalize the same
            candidate, n = stem, 1
            while candidate in used:
                n += 1
                candidate = f"{stem}_{n}"
            used.add(candidate)
            fname = f"{candidate}.records.json"
            (d / fname).write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            entries.append({
                "doc_id": doc_id,
                "stem": candidate,
                "stored_file": fname,
                "record_count": len(records),
                "pathway": pathways_by_doc.get(doc_id, ""),
            })
        manifest = {"outputs": entries, "doc_count": len(entries)}
        (d / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest


def _shape_records_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "shape_records.json"


# Cap how many source records we retain per shape so the "thousands of files"
# case doesn't bloat the store; the retained sample is what a re-run replays and
# reports propagation over, with the true total kept alongside.
_SHAPE_RECORDS_CAP = 200


def save_shape_records(project_id: str, records_by_profile: dict[str, list[dict[str, Any]]],
                       ) -> None:
    """Persist a capped SAMPLE of the raw source records per learned shape
    (keyed by profile_id), so a later human field-override can be re-applied over
    the shape's actual data and report how many records the fix now completes.
    Replaces any prior snapshot (the latest batch is what a re-run reflects)."""
    with _lock:
        payload = {
            pid: {"records": recs[:_SHAPE_RECORDS_CAP], "total": len(recs)}
            for pid, recs in records_by_profile.items() if recs
        }
        p = _shape_records_path(project_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_shape_records(project_id: str, profile_id: str,
                       ) -> tuple[list[dict[str, Any]], int]:
    """Return (sample_records, total_count) retained for a shape, or ([], 0)."""
    p = _shape_records_path(project_id)
    if not p.exists():
        return [], 0
    data = json.loads(p.read_text(encoding="utf-8"))
    entry = data.get(profile_id)
    if not entry:
        return [], 0
    return list(entry.get("records", [])), int(entry.get("total", 0))


def apply_profile_override(project_id: str, profile_id: str, target: str,
                           source_path: str | None) -> dict[str, Any]:
    """Record a human field-by-field correspondence on a learned profile and
    persist it. `source_path` is a real source field path to map `target` to, or
    None to explicitly leave `target` unmapped. Returns a small status dict.

    Guard rails:
      - the profile must exist;
      - `target` must be a golden field the profile knows;
      - a non-null `source_path` must be one the shape actually exposes
        (meta['source_paths']) — a human points at a REAL source field, never an
        invented one.
    Re-approval is the caller's concern (an override typically re-opens the
    shape for sign-off); this only updates the stored mapping."""
    with _lock:
        lib = load_project_library(project_id)
        entry = lib.get(profile_id)
        if entry is None:
            return {"ok": False, "error": f"no such profile: {profile_id}"}
        prof = entry.profile
        known_targets = {m.target for m in prof.mapping.fields}
        if target not in known_targets:
            return {"ok": False, "error": f"unknown golden field: {target}"}
        if source_path is not None:
            source_paths = {sp.get("path") for sp in prof.meta.get("source_paths", [])}
            # source_paths may be empty for legacy profiles; fall back to the
            # mapping's own candidate paths so an override is still possible.
            if source_paths and source_path not in source_paths:
                return {"ok": False,
                        "error": f"source path not in this shape: {source_path}"}
        prof.mapping = prof.mapping.with_override(target, source_path)
        lib.register(profile_id, entry.signature, prof, state=entry.state)
        save_library(lib, library_path(project_id))
        return {"ok": True, "profile_id": profile_id, "target": target,
                "source_path": source_path}


def load_batch_manifest(project_id: str) -> dict[str, Any] | None:
    p = outputs_dir(project_id) / "manifest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_batch_output_records(project_id: str, stem: str) -> list[dict[str, Any]] | None:
    """Read one source file's conformed records by its manifest stem."""
    manifest = load_batch_manifest(project_id)
    if not manifest:
        return None
    entry = next((e for e in manifest.get("outputs", []) if e.get("stem") == stem), None)
    if entry is None:
        return None
    p = outputs_dir(project_id) / entry["stored_file"]
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


__all__ = [
    "library_path",
    "load_batch_manifest",
    "load_batch_output_records",
    "load_project_library",
    "load_target_schema",
    "outputs_dir",
    "save_batch_outputs",
    "save_project_library",
    "save_target_schema",
    "target_schema_path",
]
