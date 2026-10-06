"""Per-value provenance for an aligned record.

Every produced target value carries a trace back to where it came from: the
source path it was read from, the transform op applied, the mapping method +
confidence that chose that source, and the resulting status. This is the
grounding evidence the validator checks — a value with no provenance pointing at
a real source cell is treated as fabrication and rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ValueProvenance:
    target: str
    status: str                     # filled|needs_review|conflict
    source_path: str | None = None
    operation: str = ""             # transform op (copy/cast_number/...)
    method: str = ""                # mapping method (exact/normalized/...)
    confidence: float = 0.0
    raw_value: Any = None           # value as read from the source
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "source_path": self.source_path,
            "operation": self.operation,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "raw_value": self.raw_value,
            "note": self.note,
        }


__all__ = ["ValueProvenance"]
