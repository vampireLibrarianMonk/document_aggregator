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
import logging
import os
import threading
from pathlib import Path
from typing import Any

from ..config import settings
from .profile_store import (
    ProfileLibrary,
    compose_rejection_text,
    load_library,
    save_library,
    validate_rejection,
)
from .value_vocab import ValueVocabulary

_log = logging.getLogger(__name__)

_lock = threading.RLock()


def _alignment_dir(project_id: str) -> Path:
    return settings.project_dir(project_id) / "alignment"


def target_schema_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "target_schema.json"


def library_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "library.json"


def value_vocab_path(project_id: str) -> Path:
    return _alignment_dir(project_id) / "value_vocab.json"


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


# -- learned value vocabulary (per-project, per-field accepted values) --------

def load_value_vocab(project_id: str) -> ValueVocabulary:
    """Load the project's learned value vocabulary, or a fresh empty one."""
    p = value_vocab_path(project_id)
    if not p.exists():
        return ValueVocabulary()
    return ValueVocabulary.from_dict(json.loads(p.read_text(encoding="utf-8")))


def save_value_vocab(project_id: str, vocab: ValueVocabulary) -> Path:
    p = value_vocab_path(project_id)
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(vocab.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
    return p


def effective_target_schema(project_id: str) -> dict[str, Any] | None:
    """The golden schema a batch run should MATCH against: the stored schema
    with the project's human-accepted value vocabulary merged into each field's
    taxonomy. Returns None if no schema is saved. This is the single seam that
    teaches the matcher the learned values — callers that align/convert for a
    project should load the schema through here, not raw load_target_schema."""
    schema = load_target_schema(project_id)
    if schema is None:
        return None
    from .value_vocab import merge_vocab_into_schema
    return merge_vocab_into_schema(schema, load_value_vocab(project_id))


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
                    # Stale prior-batch file we could not remove (locked /
                    # permission). Non-fatal — the new batch still writes — but
                    # unexpected, so WARNING with the trace.
                    _log.warning("could not remove prior batch output %s", old,
                                 exc_info=True)
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


def _unique_stem(doc_id: str, used: set[str]) -> str:
    """A filesystem-safe stem for `doc_id` that does not collide with any stem in
    `used` (suffixing _2, _3, … on a clash), matching save_batch_outputs."""
    stem = _safe_stem(doc_id)
    candidate, n = stem, 1
    while candidate in used:
        n += 1
        candidate = f"{stem}_{n}"
    return candidate


def _merge_batch_outputs_locked(project_id: str,
                                conformed_by_doc: dict[str, list[dict[str, Any]]],
                                *, pathway: str = "replay_clean") -> dict[str, Any]:
    """Lock-free inner writer; caller MUST already hold `_lock`.

    Overwrite-by-`doc_id` merge (design §2.3, Finding 7): an incoming doc_id that
    already has a manifest entry overwrites its stored file IN PLACE (no new stem,
    no `_2` duplicate); a genuinely new doc_id gets a fresh collision-free stem.
    Additive — never clears prior output. Idempotent on a repeated same-doc_id
    merge. tmp/replace atomic."""
    d = outputs_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    manifest = load_batch_manifest(project_id) or {"outputs": [], "doc_count": 0}
    entries: list[dict[str, Any]] = list(manifest.get("outputs", []))
    by_doc = {e.get("doc_id"): e for e in entries}
    used_stems = {e.get("stem") for e in entries if e.get("stem")}

    for doc_id, records in conformed_by_doc.items():
        existing = by_doc.get(doc_id)
        if existing is not None:
            stem = existing["stem"]
            fname = existing.get("stored_file") or f"{stem}.records.json"
            (d / fname).write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            existing["stored_file"] = fname
            existing["record_count"] = len(records)
            existing["pathway"] = pathway
        else:
            stem = _unique_stem(doc_id, used_stems)
            used_stems.add(stem)
            fname = f"{stem}.records.json"
            (d / fname).write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            entry = {
                "doc_id": doc_id,
                "stem": stem,
                "stored_file": fname,
                "record_count": len(records),
                "pathway": pathway,
            }
            entries.append(entry)
            by_doc[doc_id] = entry

    merged = {"outputs": entries, "doc_count": len({e.get("doc_id") for e in entries})}
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "manifest.json")
    return merged


def merge_batch_outputs(project_id: str,
                        conformed_by_doc: dict[str, list[dict[str, Any]]],
                        *, pathway: str = "replay_clean") -> dict[str, Any]:
    """Merge conformed records into the project's batch outputs, overwriting by
    doc_id (never minting a `_2` duplicate for the same doc_id). Additive;
    lock-guarded + tmp/replace atomic. See `_merge_batch_outputs_locked`."""
    with _lock:
        return _merge_batch_outputs_locked(project_id, conformed_by_doc,
                                           pathway=pathway)


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


def apply_rejection(project_id: str, profile_id: str, category: str,
                    fields: list[str] | None, note: str | None,
                    rejected_by: str) -> dict[str, Any]:
    """Reject a learned shape with a SERVER-VALIDATED structured reason,
    lock-guarded + atomic. Validation runs fully before any mutation, so a
    hollow reason (422) never persists. Returns a status dict carrying an HTTP
    `status` the endpoint maps to an HTTPException on failure."""
    with _lock:
        lib = load_project_library(project_id)
        entry = lib.get(profile_id)
        if entry is None:
            return {"ok": False, "error": f"no such profile: {profile_id}",
                    "status": 404}
        known = _schema_field_names(project_id)
        ok, err = validate_rejection(category, fields, note or "",
                                     known_fields=known)
        if not ok:
            return {"ok": False, "error": err, "status": 422}
        composed = compose_rejection_text(category, fields, note or "")
        lib.reject(profile_id, category=category, fields=fields, note=note or "",
                   composed_text=composed, rejected_by=rejected_by)
        save_project_library(project_id, lib)
        return {"ok": True, "profile_id": profile_id, "state": "rejected",
                "rejection": entry.meta["rejection"]}


def apply_restore(project_id: str, profile_id: str) -> dict[str, Any]:
    """Un-reject a shape, returning it to its pre-reject state (approved or
    provisional), lock-guarded + atomic. 404 if unknown, 409 if not currently
    rejected. Returns the RESTORED state."""
    with _lock:
        lib = load_project_library(project_id)
        entry = lib.get(profile_id)
        if entry is None:
            return {"ok": False, "error": f"no such profile: {profile_id}",
                    "status": 404}
        try:
            lib.restore(profile_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "status": 409}
        save_project_library(project_id, lib)
        return {"ok": True, "profile_id": profile_id, "state": entry.state}


def _schema_field_names(project_id: str) -> set[str]:
    schema = load_target_schema(project_id)
    if not schema:
        return set()
    props = schema.get("properties")
    return set(props.keys()) if isinstance(props, dict) else set()


def _builtin_values(schema: dict[str, Any] | None, target: str) -> list[str]:
    """The field's BUILT-IN accepted values, mirroring get_value_vocab's
    precedence EXACTLY: node.enum or items.enum or x-pydi-taxonomy."""
    if not schema:
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    node = props.get(target)
    if not isinstance(node, dict):
        return []
    items = node.get("items") if isinstance(node.get("items"), dict) else {}
    return list(
        node.get("enum")
        or (items.get("enum") if isinstance(items, dict) else None)
        or node.get("x-pydi-taxonomy")
        or []
    )


def apply_vocab_change(project_id: str, action: str, target: str, *,
                       value: str = "", new_value: str | None = None,
                       alias: str | None = ...,  # type: ignore[assignment]
                       added_by: str = "",
                       note: str | None = ...,     # type: ignore[assignment]
                       example: str | None = ...,  # type: ignore[assignment]
                       free_form: bool = False,
                       ) -> dict[str, Any]:
    """CRUD on the per-project learned value vocabulary, lock-guarded + atomic.

    action: 'add' | 'update' | 'remove' | 'set_free_form' | 'clear_free_form' |
    'add_alias' | 'remove_alias'. `target` must be a golden field the project's
    schema actually declares (a value can only be taught for a REAL field —
    never an invented one, mirroring the override guard). Returns a small status
    dict; the change is persisted before returning so a subsequent batch run
    (which loads via effective_target_schema) sees it.

    `alias`/`note`/`example` use the Ellipsis sentinel on UPDATE: omit to leave a
    field unchanged (so a partial edit never silently wipes provenance). On ADD,
    an omitted note/example simply defaults to empty.

    For `add_alias`/`remove_alias` the alias conflict rules (design §4) run here
    — server-authoritative, exact + case-sensitive membership — and a rejected
    alias carries an explicit `status` (422/404) the endpoint maps to an
    HTTPException. `add_alias` uses `value` (canonical) + `alias` (raw);
    `remove_alias` uses `alias` (raw). Removing a learned value cascades: every
    dependent alias on that field is dropped and reported."""
    with _lock:
        if target not in _schema_field_names(project_id):
            return {"ok": False, "error": f"unknown golden field: {target}"}
        vocab = load_value_vocab(project_id)
        try:
            if action == "add":
                entry = vocab.add(
                    target, value,
                    alias=(alias if isinstance(alias, str) else None),
                    added_by=added_by,
                    note=(note if isinstance(note, str) else ""),
                    example=(example if isinstance(example, str) else ""))
                result = entry.to_dict()
            elif action == "update":
                entry = vocab.update(target, value, new_value=new_value,
                                     alias=alias, note=note, example=example)
                result = entry.to_dict()
            elif action == "remove":
                removed = vocab.remove(target, value)
                if not removed:
                    return {"ok": False,
                            "error": f"{target} has no learned value {value!r}"}
                # Cascade: a dangling alias would rewrite a raw spelling to a
                # value the gate no longer admits (silent breakage), so drop
                # every dependent alias and report it (auditable, not silent).
                dropped = vocab.remove_aliases_for_value(target, value)
                result = {"removed": value,
                          "dropped_aliases": [a.alias for a in dropped]}
            elif action == "add_alias":
                schema = load_target_schema(project_id)
                builtin = _builtin_values(schema, target)
                learned = [e.value for e in vocab.list_field(target)]
                accepted = set(builtin) | set(learned)
                current = {a.alias: a.value for a in vocab.list_field_aliases(target)}
                raw = (alias if isinstance(alias, str) else "").strip()
                canonical = value.strip()
                if not raw or not canonical:
                    return {"ok": False,
                            "error": "alias and canonical value are both required",
                            "status": 422}
                if canonical not in accepted:
                    return {"ok": False,
                            "error": (f"'{canonical}' is not an accepted value of "
                                      f"'{target}'. Add it as an accepted value "
                                      "first, or pick an existing one."),
                            "status": 422}
                if raw in accepted:
                    return {"ok": False,
                            "error": (f"'{raw}' is already an accepted value of "
                                      f"'{target}'; an alias can't shadow a real "
                                      "value."),
                            "status": 422}
                if raw in current:
                    if current[raw] == canonical:
                        # Idempotent same-pair: no mutation, return existing.
                        existing = next(a for a in vocab.list_field_aliases(target)
                                        if a.alias == raw)
                        return {"ok": True, "action": action, "target": target,
                                "entry": existing.to_dict()}
                    return {"ok": False,
                            "error": (f"'{raw}' already maps to '{current[raw]}' on "
                                      f"'{target}'. Remove that alias before "
                                      f"remapping it to '{canonical}'."),
                            "status": 422}
                entry = vocab.add_alias(
                    target, raw, canonical,
                    added_by=added_by,
                    note=(note if isinstance(note, str) else ""))
                result = entry.to_dict()
            elif action == "remove_alias":
                raw = (alias if isinstance(alias, str) else "").strip()
                if not vocab.remove_alias(target, raw):
                    return {"ok": False,
                            "error": f"'{target}' has no alias '{raw}'",
                            "status": 404}
                result = {"removed": raw}
            elif action == "set_free_form":
                vocab.set_free_form(target, bool(free_form))
                result = {"target": target, "free_form": vocab.is_free_form(target)}
            elif action == "clear_free_form":
                vocab.set_free_form(target, False)
                result = {"target": target, "free_form": False}
            else:
                return {"ok": False, "error": f"unknown action: {action}"}
        except (ValueError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}
        save_value_vocab(project_id, vocab)
        return {"ok": True, "action": action, "target": target, "entry": result}


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
    "apply_rejection",
    "apply_restore",
    "apply_vocab_change",
    "effective_target_schema",
    "library_path",
    "load_batch_manifest",
    "load_batch_output_records",
    "load_project_library",
    "load_target_schema",
    "load_value_vocab",
    "merge_batch_outputs",
    "outputs_dir",
    "save_batch_outputs",
    "save_project_library",
    "save_target_schema",
    "save_value_vocab",
    "target_schema_path",
    "value_vocab_path",
]
