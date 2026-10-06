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
from .source_profile import SourceProfile
from .target_profile import TargetSchema

PROFILE_VERSION = 1


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


__all__ = [
    "ConversionProfile",
    "PROFILE_VERSION",
    "build_profile",
    "load_profile",
    "save_profile",
]
