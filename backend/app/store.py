"""File-backed authoritative store.

Records live as JSON on disk under the project directory. This stands in for
PostgreSQL in the barebones build; the access pattern (get/put/list) is narrow
enough that a real DB adapter could replace it without touching call sites.

Layout:
    data/projects/<project_id>/
        project.json
        source/<document_id>__<filename>      # immutable original bytes
        documents/<document_id>.record.json    # DocumentRecord
        documents/<document_id>.canonical.json # CanonicalDocument
        supplementals/<supplemental_id>.json
        index/manifest.json
        index/vectors.json                     # {chunk_id: [floats]}
        index/chunks.json                      # {chunk_id: Chunk}
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from .config import settings
from .models import (
    CanonicalDocument,
    DocumentRecord,
    Project,
    Supplemental,
)

_lock = threading.RLock()


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # atomic swap


class Store:
    def __init__(self) -> None:
        settings.ensure_dirs()

    # ----- projects -----
    def create_project(self, project: Project) -> Project:
        with _lock:
            pdir = settings.project_dir(project.id)
            for sub in ("source", "documents", "supplementals", "index"):
                (pdir / sub).mkdir(parents=True, exist_ok=True)
            _write_json(pdir / "project.json", project.model_dump())
        return project

    def get_project(self, project_id: str) -> Project | None:
        data = _read_json(settings.project_dir(project_id) / "project.json")
        return Project(**data) if data else None

    def update_project(self, project: Project) -> Project:
        """Persist changes to an existing project record (overwrites its
        project.json). Used by PATCH to update tunables like reject_below
        without touching the project's data subdirs."""
        with _lock:
            _write_json(settings.project_dir(project.id) / "project.json",
                        project.model_dump())
        return project

    def delete_project(self, project_id: str) -> bool:
        """Permanently delete a stored project and all its data. Returns False
        if it does not exist. Only ever removes a directory UNDER the projects
        root (never the bundle, never anything outside DATA_DIR)."""
        with _lock:
            root = (settings.DATA_DIR / "projects").resolve()
            pdir = settings.project_dir(project_id).resolve()
            # Guard: refuse anything that is not a direct child of the store root
            # (defends against traversal via a crafted id).
            if pdir.parent != root or not pdir.is_dir():
                return False
            shutil.rmtree(pdir)
            return True

    def list_projects(self) -> list[Project]:
        root = settings.DATA_DIR / "projects"
        out: list[Project] = []
        if not root.exists():
            return out
        for pdir in sorted(root.iterdir()):
            data = _read_json(pdir / "project.json")
            if data:
                out.append(Project(**data))
        return out

    # ----- source bytes (immutable) -----
    def save_source_bytes(self, project_id: str, document_id: str, filename: str, data: bytes) -> Path:
        safe = filename.replace("/", "_").replace("\\", "_")
        path = settings.project_dir(project_id) / "source" / f"{document_id}__{safe}"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(data)
        return path

    def source_path(self, project_id: str, document_id: str, filename: str) -> Path:
        safe = filename.replace("/", "_").replace("\\", "_")
        return settings.project_dir(project_id) / "source" / f"{document_id}__{safe}"

    # ----- document records -----
    def save_record(self, rec: DocumentRecord) -> None:
        with _lock:
            path = settings.project_dir(rec.project_id) / "documents" / f"{rec.id}.record.json"
            _write_json(path, rec.model_dump())

    def get_record(self, project_id: str, document_id: str) -> DocumentRecord | None:
        data = _read_json(settings.project_dir(project_id) / "documents" / f"{document_id}.record.json")
        return DocumentRecord(**data) if data else None

    def list_records(self, project_id: str) -> list[DocumentRecord]:
        ddir = settings.project_dir(project_id) / "documents"
        out: list[DocumentRecord] = []
        if not ddir.exists():
            return out
        for f in sorted(ddir.glob("*.record.json")):
            data = _read_json(f)
            if data:
                out.append(DocumentRecord(**data))
        return out

    def find_record_by_hash(self, project_id: str, sha256: str) -> DocumentRecord | None:
        for rec in self.list_records(project_id):
            if rec.sha256 == sha256:
                return rec
        return None

    # ----- canonical docs -----
    def save_canonical(self, project_id: str, document_id: str, doc: CanonicalDocument) -> None:
        path = settings.project_dir(project_id) / "documents" / f"{document_id}.canonical.json"
        _write_json(path, doc.model_dump())

    def get_canonical(self, project_id: str, document_id: str) -> CanonicalDocument | None:
        data = _read_json(settings.project_dir(project_id) / "documents" / f"{document_id}.canonical.json")
        return CanonicalDocument(**data) if data else None

    def read_source_bytes(self, project_id: str, document_id: str, filename: str) -> bytes | None:
        p = self.source_path(project_id, document_id, filename)
        return p.read_bytes() if p.exists() else None

    def delete_document(self, project_id: str, document_id: str) -> bool:
        """Remove a document from a project: its record, canonical JSON, source
        bytes, and any of its chunks/vectors from the search index. Returns
        False if the document does not exist."""
        with _lock:
            rec = self.get_record(project_id, document_id)
            if rec is None:
                return False
            pdir = settings.project_dir(project_id)
            # record + canonical
            (pdir / "documents" / f"{document_id}.record.json").unlink(missing_ok=True)
            (pdir / "documents" / f"{document_id}.canonical.json").unlink(missing_ok=True)
            # source bytes (filename is embedded in the stored name)
            sp = self.source_path(project_id, document_id, rec.filename)
            sp.unlink(missing_ok=True)
            # any cached preview PDFs for this document
            pcache = pdir / "preview_cache"
            if pcache.is_dir():
                for f in pcache.glob(f"{document_id}.*.pdf"):
                    f.unlink(missing_ok=True)
            # prune the index: drop every chunk/vector belonging to this doc
            vectors, chunks, manifest = self.load_index(project_id)
            drop = {cid for cid, ch in chunks.items()
                    if (ch or {}).get("document_id") == document_id}
            if drop:
                for cid in drop:
                    chunks.pop(cid, None)
                    vectors.pop(cid, None)
                manifest["document_count"] = max(0, int(manifest.get("document_count", 1)) - 1)
                manifest["chunk_count"] = len(chunks)
                self.save_index(project_id, vectors, chunks, manifest)
            return True

    # ----- supplementals -----
    def save_supplemental(self, project_id: str, supp: Supplemental) -> None:
        path = settings.project_dir(project_id) / "supplementals" / f"{supp.id}.json"
        _write_json(path, supp.model_dump())

    def list_supplementals(self, project_id: str) -> list[Supplemental]:
        sdir = settings.project_dir(project_id) / "supplementals"
        out: list[Supplemental] = []
        if not sdir.exists():
            return out
        for f in sorted(sdir.glob("*.json")):
            data = _read_json(f)
            if data:
                out.append(Supplemental(**data))
        return out

    # ----- index (vectors + chunks + manifest) -----
    def index_dir(self, project_id: str) -> Path:
        d = settings.project_dir(project_id) / "index"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_index(self, project_id: str) -> tuple[dict, dict, dict]:
        idir = self.index_dir(project_id)
        vectors = _read_json(idir / "vectors.json") or {}
        chunks = _read_json(idir / "chunks.json") or {}
        manifest = _read_json(idir / "manifest.json") or {}
        return vectors, chunks, manifest

    def save_index(self, project_id: str, vectors: dict, chunks: dict, manifest: dict) -> None:
        idir = self.index_dir(project_id)
        _write_json(idir / "vectors.json", vectors)
        _write_json(idir / "chunks.json", chunks)
        _write_json(idir / "manifest.json", manifest)


store = Store()
