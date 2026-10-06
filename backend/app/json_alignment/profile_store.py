"""ConversionProfile: a learned, reusable source->target alignment, persisted
as plain JSON so future batches convert deterministically without re-inference.

A profile captures the frozen decision for a class of source documents:
  - the target schema title it aligns to
  - the resolved field mappings (target <- source_path, method, confidence)
  - the fields left for human review / in conflict (carried, not hidden)
  - lightweight metadata (version, source field count)

Reusing a profile skips inference entirely: `Mapping` is rebuilt from the stored
field decisions and handed straight to the executor. This is the "learn the
relationship once, replay it" capability.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .mapping import FieldMapping, Mapping
from .signature import Signature
from .source_profile import SourceProfile
from .target_profile import TargetSchema

PROFILE_VERSION = 1
LIBRARY_VERSION = 1

# Approval states for a learned profile in a library.
STATE_PROVISIONAL = "provisional"   # inferred, awaiting one-time human approval
STATE_APPROVED = "approved"         # a human signed off; replays automatically
STATE_RETIRED = "retired"           # superseded (e.g. after drift) but kept for audit


@dataclass
class ConversionProfile:
    """A persisted alignment between a source shape and a target schema."""
    name: str
    target_title: str
    mapping: Mapping
    source_field_count: int = 0
    version: int = PROFILE_VERSION
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "target_title": self.target_title,
            "source_field_count": self.source_field_count,
            "meta": self.meta,
            "mapping": self.mapping.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversionProfile:
        fields = [
            FieldMapping(
                target=f["target"],
                source_path=f.get("source_path"),
                method=f.get("method", ""),
                confidence=float(f.get("confidence", 0.0)),
                status=f.get("status", "needs_review"),
                candidates=tuple(
                    (c[0], float(c[1])) for c in f.get("candidates", [])
                ),
                note=f.get("note", ""),
            )
            for f in data.get("mapping", {}).get("fields", [])
        ]
        return cls(
            name=data.get("name", ""),
            target_title=data.get("target_title", ""),
            mapping=Mapping(fields=fields),
            source_field_count=int(data.get("source_field_count", 0)),
            version=int(data.get("version", PROFILE_VERSION)),
            meta=dict(data.get("meta", {})),
        )


def build_profile(name: str, source: SourceProfile, target: TargetSchema,
                  mapping: Mapping, *, meta: dict[str, Any] | None = None,
                  ) -> ConversionProfile:
    return ConversionProfile(
        name=name,
        target_title=target.title,
        mapping=mapping,
        source_field_count=len(source.fields),
        meta=dict(meta or {}),
    )


def save_profile(profile: ConversionProfile, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True,
                            ensure_ascii=False), encoding="utf-8")
    return p


def load_profile(path: str | Path) -> ConversionProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ConversionProfile.from_dict(data)


@dataclass
class LibraryEntry:
    """One learned shape in a ProfileLibrary.

    Pairs a ConversionProfile with the source SHAPE it was learned for (its
    Signature, used to route incoming documents) and an approval state. A novel
    shape is registered PROVISIONAL (inferred, needs one human sign-off); once
    approved it replays automatically. Drift can RETIRE an entry in favour of a
    new version while keeping the old one for audit."""
    profile_id: str
    signature: Signature
    profile: ConversionProfile
    state: str = STATE_PROVISIONAL
    version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.state == STATE_APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "signature": self.signature.to_dict(),
            "profile": self.profile.to_dict(),
            "state": self.state,
            "version": self.version,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LibraryEntry:
        return cls(
            profile_id=data["profile_id"],
            signature=Signature.from_dict(data.get("signature", {})),
            profile=ConversionProfile.from_dict(data.get("profile", {})),
            state=data.get("state", STATE_PROVISIONAL),
            version=int(data.get("version", 1)),
            meta=dict(data.get("meta", {})),
        )


class ProfileLibrary:
    """A catalog of learned shapes that all convert to ONE shared target schema.

    This is the "many variations -> one golden" store: each team shape becomes a
    LibraryEntry keyed by a stable profile id. Incoming documents are routed to
    an entry by signature (see signature.best_match); approved entries replay
    deterministically, provisional ones await human approval, and drift can
    supersede an entry with a new version.

    The whole library persists as one JSON document so it can be reviewed in a
    PR (approved shapes are code-reviewable artifacts) or stored as runtime
    state, as the deployment prefers."""

    def __init__(self, target_title: str = "", *,
                 entries: dict[str, LibraryEntry] | None = None,
                 meta: dict[str, Any] | None = None) -> None:
        self.target_title = target_title
        self.entries: dict[str, LibraryEntry] = entries or {}
        self.meta: dict[str, Any] = dict(meta or {})

    # -- registration / lifecycle ------------------------------------------

    def register(self, profile_id: str, signature: Signature,
                 profile: ConversionProfile, *, state: str = STATE_PROVISIONAL,
                 ) -> LibraryEntry:
        """Add (or supersede) a learned shape.

        Re-registering an existing id bumps its version (used when drift forces a
        re-learn); the superseded entry's data is replaced in place but version
        increments so an audit can see the shape changed."""
        prev = self.entries.get(profile_id)
        version = (prev.version + 1) if prev else 1
        entry = LibraryEntry(profile_id=profile_id, signature=signature,
                             profile=profile, state=state, version=version)
        self.entries[profile_id] = entry
        return entry

    def approve(self, profile_id: str) -> LibraryEntry:
        """Mark a provisional entry approved so it replays automatically."""
        entry = self.entries[profile_id]
        entry.state = STATE_APPROVED
        return entry

    def retire(self, profile_id: str) -> LibraryEntry:
        entry = self.entries[profile_id]
        entry.state = STATE_RETIRED
        return entry

    # -- lookup -------------------------------------------------------------

    def get(self, profile_id: str) -> LibraryEntry | None:
        return self.entries.get(profile_id)

    def signatures(self, *, approved_only: bool = False) -> dict[str, Signature]:
        """profile_id -> Signature, for routing (optionally approved shapes only)."""
        return {
            pid: e.signature for pid, e in self.entries.items()
            if e.state != STATE_RETIRED and (not approved_only or e.is_approved)
        }

    def approved_ids(self) -> list[str]:
        return sorted(pid for pid, e in self.entries.items() if e.is_approved)

    def provisional_ids(self) -> list[str]:
        return sorted(pid for pid, e in self.entries.items()
                      if e.state == STATE_PROVISIONAL)

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "library_version": LIBRARY_VERSION,
            "target_title": self.target_title,
            "meta": self.meta,
            "entries": [self.entries[pid].to_dict() for pid in sorted(self.entries)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileLibrary:
        entries = {
            e["profile_id"]: LibraryEntry.from_dict(e)
            for e in data.get("entries", [])
        }
        return cls(
            target_title=data.get("target_title", ""),
            entries=entries,
            meta=dict(data.get("meta", {})),
        )


def save_library(library: ProfileLibrary, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(library.to_dict(), indent=2, sort_keys=True,
                            ensure_ascii=False), encoding="utf-8")
    return p


def load_library(path: str | Path) -> ProfileLibrary:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProfileLibrary.from_dict(data)


__all__ = [
    "LIBRARY_VERSION",
    "PROFILE_VERSION",
    "STATE_APPROVED",
    "STATE_PROVISIONAL",
    "STATE_RETIRED",
    "ConversionProfile",
    "LibraryEntry",
    "ProfileLibrary",
    "build_profile",
    "load_library",
    "load_profile",
    "save_library",
    "save_profile",
]
