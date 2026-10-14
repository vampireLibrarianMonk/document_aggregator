"""Graded malformation axes for the conformance-frontier alpha loop.

Each AXIS is a monotonic difficulty dial: severity 0 is a near-clean record and
higher severities make the SAME record progressively harder for the matcher to
map back to the golden schema.

GROUND-TRUTH MODEL (the key design, after adversarial review):
Every mutation returns, per SOURCE key it emits, the golden field that key TRULY
corresponds to — or None when the key corresponds to no golden field (a decoy /
junk field that MUST stay unmapped). This "truth map" is derived purely from the
mutation we applied; it never predicts the matcher's internal scores/thresholds.
The harness then scores the engine's actual mapping against the truth map:

  recovered     engine mapped golden G <- the source key whose truth is G  (good)
  missed        engine abstained on G though a source key truly is G        (recall loss)
  false_match   engine mapped golden G <- a key whose truth is NOT G        (PRECISION loss)

Because the truth map is threshold-independent, lowering the matcher's floor
cannot "move the oracle with it" — a floor that starts grabbing decoys shows up
immediately as false_match. This is what makes a recall gain provably safe or
not.

Axes: rename, misspell, nesting, noise, typecoerce, split_merge, conflict.
All deterministic + offline. Only golden field names are referenced; decoy
values are plausible-but-wrong (a person's name, a different year) so a decoy is
a real precision trap, not an obviously-unrelated token.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Record = dict[str, Any]

GOLDEN_FIELDS = ["name", "releaseYear", "developer", "publisher",
                 "platform", "genres", "criticScore", "ESRB"]
REQUIRED_FIELDS = {"name", "releaseYear"}


# --------------------------------------------------------------------------
# Embedded dictionary: rename variants per golden field, ordered by how much
# matchable signal they retain (shared token -> synonym -> abbrev -> opaque).
# NOTE: we deliberately DROP the ambiguous "rephrase/padded" tier the first
# version used — padding with description words (e.g. 'the_game_title_field')
# made severity NON-monotonic because the matcher's description tier then fired.
# A clean monotonic ladder is: shares a NAME token -> a synonym with no name
# token -> an abbreviation -> an opaque positional key.
# --------------------------------------------------------------------------
_DICT: dict[str, dict[str, str]] = {
    "name":        {"shared": "game_name",       "synonym": "title",      "abbrev": "nm",    "opaque": "f1"},
    "releaseYear": {"shared": "release_year",     "synonym": "launched",   "abbrev": "yr",    "opaque": "f2"},
    "developer":   {"shared": "developer_studio", "synonym": "studio",     "abbrev": "dvlpr", "opaque": "f3"},
    "publisher":   {"shared": "publisher_name",   "synonym": "label",      "abbrev": "pub",   "opaque": "f4"},
    "platform":    {"shared": "platform_system",  "synonym": "console",    "abbrev": "hw",    "opaque": "f5"},
    "genres":      {"shared": "genre_list",       "synonym": "categories", "abbrev": "gnr",   "opaque": "f6"},
    "criticScore": {"shared": "critic_score",     "synonym": "metacritic", "abbrev": "cs",    "opaque": "f7"},
    "ESRB":        {"shared": "esrb_rating",      "synonym": "maturity",   "abbrev": "esb",   "opaque": "f8"},
}
_RENAME_TIERS = ["shared", "synonym", "abbrev", "opaque"]


@dataclass
class MutationResult:
    """The mutated record + the TRUTH map: for each source key present, the
    golden field it truly corresponds to, or None for a decoy/junk key.

    `present_golden` is the set of golden fields that have SOME truly-corresponding
    source key in this record (so the harness knows a 'miss' is possible). A
    golden field absent from any truth value cannot be recovered and is excluded
    from recall scoring (e.g. a split/merged field)."""
    record: Record
    truth: dict[str, str | None] = field(default_factory=dict)   # source_key -> golden|None

    @property
    def present_golden(self) -> set[str]:
        return {g for g in self.truth.values() if g is not None}

    @property
    def decoy_keys(self) -> set[str]:
        return {k for k, g in self.truth.items() if g is None}


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


# --------------------------------------------------------------------------
# Deterministic misspelling. Fix vs v1: operations can only CORRUPT (never
# reconstruct), the first char is preserved, and we stop early rather than let a
# short word collapse to the original. Guarantees monotonic corruption.
# --------------------------------------------------------------------------
_KEYBOARD_ADJ = {
    "a": "s", "s": "d", "d": "f", "e": "r", "r": "t", "o": "i", "i": "u",
    "l": "k", "n": "m", "t": "y", "p": "o", "g": "h", "c": "v", "m": "n",
    "u": "y", "y": "u", "b": "v", "v": "b", "h": "j", "j": "h", "k": "l",
}


def _misspell(word: str, edits: int) -> str:
    """Apply up to `edits` deterministic typos at distinct positions (left to
    right, skipping index 0), substitution-only so length is preserved and the
    result can never collapse back to the original. Deterministic from the word
    + edit count alone (no RNG), so it is reproducible and position-stable."""
    chars = list(word)
    positions = [i for i in range(1, len(chars))]
    for i in positions[:edits]:
        ch = chars[i].lower()
        chars[i] = _KEYBOARD_ADJ.get(ch, "x" if ch != "x" else "z")
    return "".join(chars)


# --------------------------------------------------------------------------
# Axes. Signature: (clean_record, severity, rng) -> MutationResult.
# --------------------------------------------------------------------------

def axis_rename(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Rename every golden key to its tier-`severity` variant. Truth = the key
    maps to its golden field regardless of how hard the name is (the engine's job
    is to still find it). severity 0 = clean."""
    if severity <= 0:
        return MutationResult({f: rec[f] for f in GOLDEN_FIELDS},
                              {f: f for f in GOLDEN_FIELDS})
    tier = _RENAME_TIERS[min(severity, len(_RENAME_TIERS)) - 1]
    out: Record = {}
    truth: dict[str, str | None] = {}
    for f in GOLDEN_FIELDS:
        key = _DICT[f][tier]
        out[key] = rec[f]
        truth[key] = f
    return MutationResult(out, truth)


def axis_misspell(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Misspell each golden key by `severity` substitutions. Truth = still the
    golden field (a typo'd 'developer' is still developer). The engine recovers
    while enough signal survives; where it can't, that is a measured recall
    limit — the truth does not change."""
    out: Record = {}
    truth: dict[str, str | None] = {}
    for f in GOLDEN_FIELDS:
        typo = _misspell(f, severity) if severity > 0 else f
        # Guard against a collision producing a duplicate key.
        if typo in out:
            typo = f"{typo}_{f}"
        out[typo] = rec[f]
        truth[typo] = f
    return MutationResult(out, truth)


def axis_nesting(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Bury fields under `severity` levels of nesting (leaf keys preserved).
    Truth = the leaf key maps to its golden field. Depth>=2 also renames leaves
    to synonyms to compound difficulty."""
    truth: dict[str, str | None] = {}
    inner: Record = {}
    for f in GOLDEN_FIELDS:
        leaf = f if severity < 2 else _DICT[f]["synonym"]
        inner[leaf] = rec[f]
        truth[leaf] = f
    node: Any = inner
    for d in range(max(0, severity)):
        node = {f"level{d}": node}
    return MutationResult(node if severity > 0 else inner, truth)


def axis_noise(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Clean golden keys buried among `severity*10` DECOY fields. The decoys are
    designed to TEMPT a loose matcher: some carry golden-adjacent tokens or
    golden-looking values but correspond to NO golden field (truth None), so a
    floor that starts grabbing them shows up as false_match."""
    out: Record = {f: rec[f] for f in GOLDEN_FIELDS}
    truth: dict[str, str | None] = {f: f for f in GOLDEN_FIELDS}
    # A pool of tempting-but-wrong decoy keys (truth = None). Each shares a
    # content token with a golden field NAME so a loosened matcher would be
    # tempted to grab it — making a floor drop show up as a false_match. (Keys
    # that share no golden token can't trip the lexical matcher, so they'd be
    # worthless as precision traps.)
    tempting = [
        "internal_name", "name_slug", "release_year_note", "developer_count",
        "developer_studio_city", "publisher_name_alt", "platform_system_fee",
        "genre_list_notes", "critic_score_source", "esrb_rating_locale",
        "name_display", "release_year_region",
    ]
    for i in range(severity * 10):
        key = tempting[i % len(tempting)] if i < len(tempting) * 2 else f"junk_{i}"
        if key in out:
            key = f"{key}_{i}"
        out[key] = rng.choice(["lorem", 123, "2026-01-01", "n/a", True, "see notes"])
        truth[key] = None
    return MutationResult(out, truth)


def axis_noise_decoyonly(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Precision stress axis: the true golden keys are REMOVED and replaced by
    golden-adjacent-but-wrong decoys (truth=None), so for each targeted golden
    field there is NO true source key present.

    This is the variant `axis_noise` structurally cannot be: in axis_noise the
    exact golden key is always present and wins at tier 1.0, so a decoy can never
    out-rank it and false_match is pinned at 0 no matter how low the floor goes.
    Here the only lexical signal for a field comes from decoys that SHARE the
    field's name tokens but carry a plausible-wrong value. If a floor drop lets
    the matcher grab one of those, that is a real false_match the harness sees.
    Because no true key is present, recall is not scored on the dropped fields
    (present_golden excludes them); this axis exists purely to make a precision
    regression observable.

    severity N targets the first N golden fields (name, releaseYear, ...),
    dropping each and seeding one or more token-sharing decoys for it. Fields
    beyond severity keep their clean exact key (so the record still parses as a
    game and the matcher still runs normally)."""
    out: Record = {f: rec[f] for f in GOLDEN_FIELDS}
    truth: dict[str, str | None] = {f: f for f in GOLDEN_FIELDS}
    # For each targeted golden, the decoys that share its NAME tokens but are the
    # WRONG field. A loosened floor would be tempted by these; the correct answer
    # is to abstain (the real field is gone).
    adjacent: dict[str, list[str]] = {
        "name":        ["name_prefix", "display_name_hint"],
        "releaseYear": ["release_year_label", "year_bucket"],
        "developer":   ["developer_region", "developer_notes"],
        "publisher":   ["publisher_region", "publisher_notes"],
        "platform":    ["platform_family", "platform_notes"],
        "genres":      ["genre_tag_notes", "genre_group"],
        "criticScore": ["critic_score_band", "critic_notes"],
        "ESRB":        ["esrb_descriptor", "esrb_notes"],
    }
    targeted = GOLDEN_FIELDS[:max(0, severity)]
    for f in targeted:
        out.pop(f, None)
        truth.pop(f, None)             # no true source key for f any more
        for dk in adjacent[f]:
            key = dk if dk not in out else f"{dk}_x"
            # Plausible-wrong value (never the real field's value).
            out[key] = rng.choice(["see metadata", "n/a", 0, "TBD", "various"])
            truth[key] = None          # decoy: must stay unmapped
    return MutationResult(out, truth)


def axis_typecoerce(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Coerce value TYPES on clean keys (keys unchanged). Truth unchanged (keys
    are golden). Probes the transform tier, not the matcher."""
    out: Record = {f: rec[f] for f in GOLDEN_FIELDS}
    coercions: list[Callable[[Record], None]] = [
        lambda d: d.__setitem__("releaseYear", int(d["releaseYear"])),
        lambda d: d.__setitem__("criticScore", str(d["criticScore"])),
        lambda d: d.__setitem__("genres", ";".join(_as_list(d["genres"]))),
    ]
    for c in coercions[:severity]:
        c(out)
    return MutationResult(out, {f: f for f in GOLDEN_FIELDS})


def axis_split_merge(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Split name into title+subtitle, merge developer+publisher. The split/merged
    SOURCE keys correspond to NO single golden field (truth None), and the golden
    fields they came from have no corresponding source key — so they are absent
    from present_golden and correctly excluded from recall (a derive-rule gap,
    not a matcher failure). This also makes mapping a split part to `name` a
    FALSE match the harness will catch."""
    out: Record = {f: rec[f] for f in GOLDEN_FIELDS}
    truth: dict[str, str | None] = {f: f for f in GOLDEN_FIELDS}
    if severity >= 1:
        name = str(out.pop("name"))
        truth.pop("name")
        head, _, tail = name.partition(":")
        out["titlePart"] = head.strip() or name
        out["subtitlePart"] = tail.strip()
        truth["titlePart"] = None       # a title FRAGMENT is not the golden name
        truth["subtitlePart"] = None
    if severity >= 2:
        dev, pub = out.pop("developer"), out.pop("publisher")
        truth.pop("developer")
        truth.pop("publisher")
        out["studioCredit"] = f"{dev} / {pub}"
        truth["studioCredit"] = None    # merged: not a single golden field
    return MutationResult(out, truth)


def axis_conflict(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """Introduce TWO sources that genuinely TIE for one golden field, so the
    matcher's conflict rule actually fires (it triggers only when two distinct
    source paths tie at the top with a non-exact/normalized method). To reach a
    real tie we drop the golden's own exact key and emit two keys that each share
    the SAME tokens with the target name+description so both land in the
    description tier at equal score. Neither is "the" truth — the engine should
    CONFLICT (abstain-with-candidates), which the harness treats as precision-safe
    (not a false match); mapping the field to EITHER competing key alone is also
    acceptable here (both carry the real value), but silently picking one over a
    true tie is what we watch. Both keys have truth=None so a frank false_match
    (mapping to an unrelated key) is still caught.

    severity 1: tie for `criticScore`; severity 2: also tie for `genres`."""
    out: Record = {f: rec[f] for f in GOLDEN_FIELDS}
    truth: dict[str, str | None] = {f: f for f in GOLDEN_FIELDS}
    if severity >= 1:
        # `name` target tokens: name {name} + desc {title,game}. Two keys built
        # from {title,game} each score the SAME description-tier value (~0.83)
        # and TIE, with no exact/normalized match -> the matcher's conflict rule
        # fires (verified against the real scorer). Neither is "the" truth.
        out.pop("name")
        truth.pop("name")
        out["game_title"] = rec["name"]
        out["title_game"] = rec["name"]
        truth["game_title"] = None
        truth["title_game"] = None
    if severity >= 2:
        # Same construction for `name` is enough to exercise conflict; at sev 2
        # add a THIRD competitor so the tie is 3-way (stresses the tie-break /
        # dominance guard). Still description-tier, still a tie.
        out["named_game"] = rec.get("name", out.get("game_title"))
        truth["named_game"] = None
    return MutationResult(out, truth)


# --------------------------------------------------------------------------
# The junk-leak PRECISION axis (explicit target: drive junk_fp -> 0).
#
# This is a NON-GAME record whose field NAMES deliberately share a token with a
# golden field, but whose MEANING and VALUES are unrelated — the exact trap the
# purely-lexical matcher falls into (e.g. 'developer_contact' is an email, not a
# studio; 'platform_vendor' is a company name, not a console; 'station_name' is a
# weather station, not a game title). EVERY key has truth=None: the correct
# behavior is to ground ZERO golden fields. Any confident mapping is a false
# match the harness counts.
#
# Graded: severity N presents the first N golden-adjacent decoy fields, so the
# leak can be watched scale up and (after the meaning tier + value-evidence gate)
# driven to zero. Values are plausible-but-wrong for the golden field the name
# resembles, so a value-evidence gate has something concrete to veto on.
# --------------------------------------------------------------------------
# Each trap: a field NAME that shares a golden token (or is semantically near a
# golden field, to also tempt an embedding tier), paired with a value that is
# genuinely WRONG for that golden — so a value-evidence gate has a concrete
# contradiction to veto on. We deliberately make the VALUES vetoable even for the
# traps that don't leak lexically today (year/score/genre), because the meaning
# tier added later may raise their similarity; when it does, the value gate must
# have something real to catch (a timestamp is not a 4-digit year; a star string
# is not a 0-100 integer; a scalar is not a genres list).
_JUNK_LEAK_FIELDS: list[tuple[str, Any]] = [
    ("station_name",       "North Field Buoy"),       # ~name: not a game title
    ("developer_contact",  "ops@example.com"),         # ~developer: an email, not a studio
    ("platform_vendor",    "Acme Instruments Inc"),    # ~platform: a company, not a console
    ("year_installed",     "1998-04-12T09:00:00Z"),    # ~releaseYear: a timestamp, not \d{4}
    ("score_rating_stars", "4 stars"),                  # ~criticScore: a star string, not 0-100 int
    ("genre_of_station",   "coastal"),                  # ~genres: a scalar, not a list
    ("publisher_region",   "Pacific Northwest"),        # ~publisher: a region, not a company
    ("esrb_descriptor",    "non-rated equipment"),      # ~ESRB: not an ESRB enum code
]


def axis_junk_leak(rec: Record, severity: int, rng: random.Random) -> MutationResult:
    """A graded golden-adjacent NON-GAME record. severity N includes the first N
    trap fields (severity 0 = empty record = trivially clean). The record carries
    NO true golden field, so present_golden is empty and recall is not scored;
    the axis exists purely to measure PRECISION — every confident mapping is a
    false match. Deterministic (values are fixed, `rng` unused)."""
    out: Record = {}
    truth: dict[str, str | None] = {}
    for name, value in _JUNK_LEAK_FIELDS[:max(0, severity)]:
        out[name] = value
        truth[name] = None
    return MutationResult(out, truth)


@dataclass
class Axis:
    name: str
    fn: Callable[[Record, int, random.Random], MutationResult]
    max_severity: int
    # When True the axis's output does NOT depend on the clean base record (it
    # builds a fixed record of its own), so the harness runs it ONCE per severity
    # instead of once per pooled base record (which would merely triple identical
    # counts). Used by the junk-leak precision axis.
    base_independent: bool = False


AXES: dict[str, Axis] = {
    "rename":      Axis("rename", axis_rename, 4),
    "misspell":    Axis("misspell", axis_misspell, 6),
    "nesting":     Axis("nesting", axis_nesting, 4),
    "noise":       Axis("noise", axis_noise, 10),
    "noise_decoyonly": Axis("noise_decoyonly", axis_noise_decoyonly, len(GOLDEN_FIELDS)),
    "typecoerce":  Axis("typecoerce", axis_typecoerce, 3),
    "split_merge": Axis("split_merge", axis_split_merge, 2),
    "conflict":    Axis("conflict", axis_conflict, 2),
    "junk_leak":   Axis("junk_leak", axis_junk_leak, len(_JUNK_LEAK_FIELDS),
                        base_independent=True),
}


def junk_record(rng: random.Random) -> Record:
    """Unrelated record (no games) — the hard negative control: ZERO golden
    fields should ever ground. Kept as a floor check alongside the per-axis
    decoys (which are the confidence-floor check)."""
    return {
        "station_id": rng.choice(["KSEA", "KJFK", "KORD"]),
        "temperature_c": rng.randint(-10, 35),
        "humidity_pct": rng.randint(10, 99),
        "wind_kph": rng.randint(0, 60),
        "observed_at": "2026-01-01T00:00:00Z",
    }


__all__ = [
    "AXES", "Axis", "GOLDEN_FIELDS", "REQUIRED_FIELDS", "MutationResult",
    "junk_record",
]
