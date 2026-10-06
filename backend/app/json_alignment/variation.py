"""Deterministic 1:1 variation generator for tuning + regression.

The real workload is "thousands of JSONs across teams, each a VARIATION of the
same information, all converging on one golden schema." To tune and regression-
test the deterministic matcher against THAT shape (not a cross-source fusion
benchmark), we generate our own variations: start from clean records whose field
names equal the golden target names, then apply bounded 1:1 mutations, carrying
the gold `(mutated_source_path -> target_field)` correspondence as we mutate.

Scope: 1:1 fields only (one source field feeds one target field). We never
split or merge, matching the confirmed workload. Each knob is one of the brief's
adversarial levels minus split/merge:

    rename            replace the key with a declared synonym
    case              CamelCase / UPPER / Title the key
    delimiter         snake_case <-> kebab-case
    affix             add a prefix/suffix to the key
    nest              move a top-level field under a wrapper object
    array_wrap        wrap a scalar as a single-element array (singleton->array)
    reorder           shuffle key order (no semantic change; determinism check)
    irrelevant        inject extra fields with no target
    null              set some values to null
    omit_optional     drop a non-required field entirely
    date_format       re-render a date into a different textual format
    numeric_string    render a number as a string (or vice versa)
    boolean_variant   render a bool as yes/no / 1/0 / true/false strings
    opaque            replace the key with an opaque code + a description sidecar

Everything is deterministic given a seed (uses random.Random(seed), never the
global RNG), so fixtures and tests are reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Declared synonyms per golden field name, used by the `rename`/`opaque` knobs.
# Only the generator knows these; the matcher must rediscover them (that's the
# point). Opaque codes come paired with a human description (honest signal).
_SYNONYMS: dict[str, list[str]] = {
    "name": ["title", "game_title", "prod_title", "label"],
    "releaseYear": ["year", "year_published", "launch_yr", "release_year"],
    "developer": ["studio", "made_by", "dev", "creator"],
    "publisher": ["dist", "distributor", "published_by", "pub"],
    "platform": ["console", "system", "hw", "device"],
    "genres": ["genre", "categories", "tags"],
    "criticScore": ["press_rating", "metascore", "critic_rating", "score"],
    "ESRB": ["age_rating", "age_classification", "rating", "esrb_rating"],
}

# Opaque codes + a description that still carries the semantics (the metadata
# sidecar a real source would ship). Keyed by golden field name.
_OPAQUE: dict[str, tuple[str, str]] = {
    "name": ("f01", "Official title of the game"),
    "releaseYear": ("f02", "Year the game was published"),
    "developer": ("f03", "Studio that developed the game"),
    "publisher": ("f04", "Company that published the game"),
    "platform": ("f05", "Console or hardware platform"),
    "genres": ("f06", "List of genres"),
    "criticScore": ("f07", "Critic press rating out of 100"),
    "ESRB": ("f08", "ESRB age rating"),
}

ALL_KNOBS = (
    "rename", "case", "delimiter", "affix", "nest", "array_wrap", "reorder",
    "irrelevant", "null", "omit_optional", "date_format", "numeric_string",
    "boolean_variant", "opaque",
)

# Difficulty ladder (brief s24, minus split/merge). Each level is cumulative-ish
# but we keep them explicit so scoring can report per-level behavior.
DIFFICULTY_LEVELS: dict[int, tuple[str, ...]] = {
    1: ("rename",),
    2: ("rename", "numeric_string", "boolean_variant"),
    3: ("rename", "nest"),
    4: ("rename", "nest", "array_wrap"),
    6: ("rename", "irrelevant"),
    7: ("rename", "omit_optional", "null"),
    9: ("opaque",),
    10: ("rename", "case", "delimiter", "affix", "nest", "array_wrap",
         "irrelevant", "null", "date_format", "numeric_string",
         "boolean_variant", "reorder"),
}


@dataclass
class Variation:
    """One generated source variation + the gold mapping it was built from."""
    name: str
    level: int
    knobs: tuple[str, ...]
    records: list[dict[str, Any]]
    # gold correspondences: mutated source path -> golden target field
    gold_mapping: dict[str, str] = field(default_factory=dict)
    # optional per-path source descriptions (populated by the `opaque` knob)
    descriptions: dict[str, str] = field(default_factory=dict)
    # golden target fields that were intentionally dropped (omit_optional) so a
    # scorer can tell an honest-missing from a matcher miss
    dropped_targets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "knobs": list(self.knobs),
            "records": self.records,
            "gold_mapping": self.gold_mapping,
            "descriptions": self.descriptions,
            "dropped_targets": list(self.dropped_targets),
        }


def _retype_key(key: str, knob: str) -> str:
    if knob == "case":
        parts = key.replace("-", "_").split("_")
        return parts[0] + "".join(p.capitalize() for p in parts[1:])  # camelCase
    if knob == "delimiter":
        return key.replace("_", "-")
    if knob == "affix":
        return f"src_{key}_v2"
    return key


def _reformat_date(value: Any, rng: random.Random) -> Any:
    """Re-render an ISO 'YYYY-MM-DD' or 'YYYY' into another textual format."""
    if not isinstance(value, str):
        return value
    for fmt in ("%Y-%m-%d", "%Y"):
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        out_fmt = rng.choice(("%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%d-%m-%Y"))
        if fmt == "%Y":
            # year-only: expand to Jan 1 so reformatting is well-defined
            dt = datetime(dt.year, 1, 1)
        return dt.strftime(out_fmt)
    return value


def _boolean_variant(value: Any, rng: random.Random) -> Any:
    if not isinstance(value, bool):
        return value
    style = rng.choice(("yesno", "onezero", "truefalse"))
    if style == "yesno":
        return "yes" if value else "no"
    if style == "onezero":
        return 1 if value else 0
    return "true" if value else "false"


@dataclass
class _KeyPlan:
    """The FIXED, per-variation decision for one target field: what key it uses,
    where it sits, and whether its value gets array-wrapped. Chosen once so every
    record in the variation looks like the same team's export (consistent)."""
    target: str
    key: str
    nested: bool
    array_wrap: bool
    description: str = ""

    def path(self, nest_wrapper: str, is_list: bool) -> str:
        base = f"{nest_wrapper}.{self.key}" if self.nested else self.key
        return f"{base}[]" if is_list else base


def _plan_keys(target_fields: list[str], knobs: tuple[str, ...],
               rng: random.Random) -> dict[str, _KeyPlan]:
    """Decide, once per variation, the source key/placement for each target."""
    nested = "nest" in knobs
    plans: dict[str, _KeyPlan] = {}
    for tgt in target_fields:
        key = tgt
        desc = ""
        if "opaque" in knobs and tgt in _OPAQUE:
            key, desc = _OPAQUE[tgt]
        elif "rename" in knobs and tgt in _SYNONYMS:
            key = rng.choice(_SYNONYMS[tgt])
        if "case" in knobs:
            key = _retype_key(key, "case")
        if "delimiter" in knobs:
            key = _retype_key(key, "delimiter")
        if "affix" in knobs:
            key = _retype_key(key, "affix")
        aw = "array_wrap" in knobs and rng.random() < 0.5
        plans[tgt] = _KeyPlan(target=tgt, key=key, nested=nested,
                              array_wrap=aw, description=desc)
    return plans


def _mutate_value(tgt_field: str, value: Any, knobs: tuple[str, ...],
                  array_wrap: bool, rng: random.Random) -> Any:
    """Apply value-level 1:1 mutations (never changes which target it feeds).

    When a field is array-wrapped (a per-variation decision), it is ALWAYS a
    one-element list so the profiler sees a consistent '[]' path across records;
    the null knob is skipped for such fields to avoid a mixed scalar/list path."""
    if array_wrap:
        value = _scalar_mutations(tgt_field, value, knobs, rng)
        return [value] if not isinstance(value, list) else value
    if "null" in knobs and rng.random() < 0.25:
        return None
    return _scalar_mutations(tgt_field, value, knobs, rng)


def _scalar_mutations(tgt_field: str, value: Any, knobs: tuple[str, ...],
                      rng: random.Random) -> Any:
    if "date_format" in knobs and tgt_field == "releaseYear":
        value = _reformat_date(value, rng)
    if "numeric_string" in knobs and isinstance(value, int | float) \
            and not isinstance(value, bool):
        value = str(value)
    if "boolean_variant" in knobs:
        value = _boolean_variant(value, rng)
    return value


def generate_variation(golden_records: list[dict[str, Any]],
                       target_fields: list[str], required: list[str],
                       knobs: tuple[str, ...], *, name: str, level: int,
                       seed: int) -> Variation:
    """Generate one Variation from clean golden records under a set of knobs.

    `golden_records` are records whose keys ARE the golden target field names
    (the canonical form). The key/placement decision is made ONCE per variation
    (so all records share one consistent shape, like one team's export); only
    values vary per record. We mutate copies; the originals are untouched."""
    rng = random.Random(seed)
    req = set(required)
    nest_wrapper = "payload"
    plans = _plan_keys(target_fields, knobs, rng)

    # omit_optional: decide once per variation which non-required targets to drop
    dropped: set[str] = set()
    if "omit_optional" in knobs:
        for tgt in target_fields:
            if tgt not in req and rng.random() < 0.4:
                dropped.add(tgt)

    recs: list[dict[str, Any]] = []
    gold: dict[str, str] = {}
    descriptions: dict[str, str] = {}

    for rec in golden_records:
        out: dict[str, Any] = {}
        order = [t for t in target_fields if t in rec and t not in dropped]
        if "reorder" in knobs:
            rng.shuffle(order)
        for tgt in order:
            plan = plans[tgt]
            value = _mutate_value(tgt, rec[tgt], knobs, plan.array_wrap, rng)
            is_list = isinstance(value, list)
            if plan.nested:
                out.setdefault(nest_wrapper, {})[plan.key] = value
            else:
                out[plan.key] = value
            gpath = plan.path(nest_wrapper, is_list)
            gold[gpath] = tgt
            if plan.description:
                descriptions[gpath] = plan.description
        if "irrelevant" in knobs:
            out["internal_id"] = f"rec-{rng.randint(1000, 9999)}"
            out["_scraped_at"] = "2024-01-01T00:00:00Z"
        recs.append(out)

    return Variation(
        name=name, level=level, knobs=knobs, records=recs,
        gold_mapping=gold, descriptions=descriptions,
        dropped_targets=tuple(sorted(dropped)),
    )


def generate_variations(golden_records: list[dict[str, Any]],
                        target_schema: dict[str, Any], *, seed: int = 0,
                        levels: tuple[int, ...] | None = None) -> list[Variation]:
    """Generate one Variation per requested difficulty level.

    `target_schema` is the golden JSON Schema; its top-level properties define
    the valid target fields and `required` the required ones. Deterministic:
    each level gets its own derived seed so re-running yields identical output."""
    props = list((target_schema.get("properties") or {}).keys())
    required = list(target_schema.get("required", []) or [])
    want = levels if levels is not None else tuple(sorted(DIFFICULTY_LEVELS))
    out: list[Variation] = []
    for lvl in want:
        knobs = DIFFICULTY_LEVELS[lvl]
        out.append(generate_variation(
            golden_records, props, required, knobs,
            name=f"level_{lvl:02d}", level=lvl, seed=seed + lvl,
        ))
    return out


def score_variation(variation: Variation, target_schema: dict[str, Any]):
    """Run the deterministic matcher over a variation and score vs its gold.

    Returns a SchemaMatchScore. The variation's `descriptions` sidecar (populated
    by the opaque knob) is fed as the honest source-side signal, mirroring how a
    real source metadata document would help."""
    from .pipeline import run_alignment
    from .scoring import mapping_to_correspondences, schema_match_score

    res = run_alignment(variation.records, target_schema,
                        source_descriptions=variation.descriptions or None)
    predicted = mapping_to_correspondences(res.mapping)
    gold = {(p, t) for p, t in variation.gold_mapping.items()}
    return schema_match_score(predicted, gold)


__all__ = [
    "ALL_KNOBS",
    "DIFFICULTY_LEVELS",
    "Variation",
    "generate_variation",
    "generate_variations",
    "score_variation",
]
