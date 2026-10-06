"""JSON schema-alignment: learn how a class of source JSON maps to a canonical
target schema, save it as a reusable Conversion Profile, and execute future
conversions deterministically.

Design mirrors the repo's precision-first philosophy (see the correction
engine): deterministic code is authoritative for mapping, transformation,
validation, and provenance; nothing is fabricated; ambiguity surfaces as
`needs_review` / `conflict` rather than a guess. A model may later PROPOSE
semantic mappings, but every proposal must pass the same deterministic
validation + grounding gate before it is accepted — not implemented in this
deterministic-first pass.

Pipeline (orchestrated by the command center):
    profile_source   inventory the incoming records (paths, types, examples)
    extract_target   parse the target JSON Schema into a field inventory
    infer_mapping    map source fields -> target fields (exact/normalized/alias/
                     description), abstaining when unsure (no fabrication)
    execute          apply the mapping + bounded transforms to each record
    validate         JSON Schema structural + type + grounding checks

The learned relationship is a ConversionProfile that reproduces the mapping
deterministically for future records.

Public surface:
  profile_source / SourceProfile
  extract_target / TargetSchema / TargetField
  infer_mapping / Mapping / FieldMapping
  execute_mapping
  validate_record
  ConversionProfile / save_profile / load_profile
  run_alignment            convenience entry (profiles + maps + executes a batch)
"""
from __future__ import annotations

from .executor import execute_mapping
from .mapping import FieldMapping, Mapping, infer_mapping
from .profile_store import ConversionProfile, load_profile, save_profile
from .source_profile import SourceProfile, profile_source
from .target_profile import TargetField, TargetSchema, extract_target
from .validator import validate_record

__all__ = [
    "ConversionProfile",
    "FieldMapping",
    "Mapping",
    "SourceProfile",
    "TargetField",
    "TargetSchema",
    "execute_mapping",
    "extract_target",
    "infer_mapping",
    "load_profile",
    "profile_source",
    "save_profile",
    "validate_record",
]
