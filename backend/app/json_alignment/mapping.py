"""Mapping inference: decide which source field (if any) feeds each target
field, deterministically and with explicit abstention.

Matching cascade (highest-confidence tier wins), per target field:
  1. exact           normalized source leaf name == normalized target name
  2. normalized key  token-set equality after name normalization
  3. alias           source name tokens match a known alias of the target
  4. description      Jaccard token overlap between source name and target
                     name+description, above a floor
  5. abstain         no tier clears the floor -> status 'needs_review'
                     (NO fabrication: we never invent a correspondence)

Conflict: if two source fields tie for the top score on a required target with
no deterministic tie-break, the field is marked 'conflict' and left unresolved
(mirrors the correction engine's two-humans-disagree rule).

Type compatibility is scored as a secondary signal, never used to fabricate a
match. Everything here is pure and reproducible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .evidence import evidence_string
from .source_profile import FieldStat, SourceProfile, name_tokens
from .target_profile import TargetField, TargetSchema

# Confidence floors per tier. A match below EXACT/NORMALIZED is "proposed".
_EXACT = 1.0
_NORMALIZED = 0.95
_ALIAS = 0.9
_SYNONYM = 0.88           # curated field-name synonym (below alias, above fuzzy)
_FUZZY_MAX = 0.86         # ceiling for a near-miss (typo) match
_FUZZY_MIN = 0.80         # floor confidence for an accepted typo match
_DESC_FLOOR = 0.34        # Jaccard overlap required to propose a description match
# Embeddings are NOT a grounding tier (measured: on thin data cosine can't
# separate real synonyms from in-domain junk). They are used only as a display-
# order booster in infer_mapping via _cosine_to_target — re-ranking candidates a
# lexical tier already proposed, never selecting the grounded path.
# Positive value-corroboration boost: a small confidence bump applied to a
# sub-authoritative match whose VALUES positively fit the target's shape, so a
# value-backed candidate outranks a merely-lexical one for the same target. Kept
# small and capped per-tier so it never promotes a candidate past the next tier
# (a boosted description match stays below alias; it only breaks ties / reorders
# within the sub-authoritative band).
_VALUE_BOOST = 0.04

STATUS_MAPPED = "mapped"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_CONFLICT = "conflict"

# --------------------------------------------------------------------------
# Embedded field-name synonym dictionary (the "offline semantic/dictionary
# tier"). Maps a target field NAME to known synonymous source field names whose
# tokens carry no lexical overlap with the target name — so the token-overlap
# tier cannot reach them (a single synonym token dilutes below _DESC_FLOOR).
# This is a CLOSED, curated vocabulary: it only ever proposes a candidate when a
# source leaf's normalized token-set EXACTLY equals one of these synonyms. It
# never fuzzy-expands, never fabricates, and is scored strictly below the alias
# tier so a real alias/exact always dominates. Deterministic and offline.
#
# Why this is precision-safe: matching is exact token-set equality against a
# finite hand-authored list, identical in mechanism to the alias tier. A decoy
# like 'developer_contact' tokenizes to {developer, contact} which equals no
# synonym set, so it is NOT grabbed; 'studio' tokenizes to {studio} which DOES
# equal developer's synonym, which is the intended recovery.
# --------------------------------------------------------------------------
# IMPORTANT precision rule: only UNAMBIGUOUS synonyms belong here. A word that
# can denote more than one golden field (e.g. 'label' -> publisher OR a title's
# label; 'rating' -> ESRB OR criticScore; 'score' -> criticScore OR a generic
# score) is DELIBERATELY excluded, because asserting one meaning would fabricate
# a correspondence on genuinely ambiguous data. Ambiguous names are left to
# abstain (and, if needed, to the human/LLM tier). This keeps the dictionary a
# high-precision recall extender, not a guesser.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "name":        ("title",),
    "releaseYear": ("launched", "launch", "debut"),
    "developer":   ("studio", "creator", "maker"),
    "publisher":   ("distributor",),
    "platform":    ("console", "system", "hardware"),
    "genres":      ("categories", "category", "genre", "tags"),
    "criticScore": ("metacritic", "metascore"),
    "ESRB":        ("maturity",),
}


@dataclass
class FieldMapping:
    """One target field's resolution against the source."""
    target: str
    source_path: str | None          # None when abstained
    method: str                      # exact|normalized|alias|description|abstain
    confidence: float
    status: str                      # mapped|needs_review|conflict
    candidates: tuple[tuple[str, float], ...] = ()  # (source_path, score) ranked
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "source_path": self.source_path,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "status": self.status,
            "candidates": [[p, round(s, 4)] for p, s in self.candidates],
            "note": self.note,
        }


@dataclass
class Mapping:
    """The full source->target mapping with per-field provenance of the choice."""
    fields: list[FieldMapping] = field(default_factory=list)

    def mapped(self) -> dict[str, str]:
        """target -> source_path for confidently-mapped fields only."""
        return {m.target: m.source_path for m in self.fields
                if m.status == STATUS_MAPPED and m.source_path is not None}

    def needs_review(self) -> list[str]:
        return [m.target for m in self.fields if m.status == STATUS_NEEDS_REVIEW]

    def conflicts(self) -> list[str]:
        return [m.target for m in self.fields if m.status == STATUS_CONFLICT]

    def with_override(self, target: str, source_path: str | None) -> Mapping:
        """Return a NEW Mapping with a human decision applied to one target:

          source_path = "<path>"  -> map the target to that source path (a human
                                     correspondence decision; method='human',
                                     confidence 1.0, status mapped).
          source_path = None      -> explicitly leave the target UNMAPPED (the
                                     human judged there is no viable source);
                                     status needs_review with a human note so it
                                     is not re-offered as an open question.

        The chosen source_path is NOT trusted blindly downstream: execution still
        reads + transforms it and will defer the field if the value can't be
        produced for a given record (type mismatch etc.). This only records the
        correspondence; grounding/validation remain authoritative."""
        new_fields: list[FieldMapping] = []
        for m in self.fields:
            if m.target != target:
                new_fields.append(m)
                continue
            if source_path:
                new_fields.append(FieldMapping(
                    target=target, source_path=source_path, method="human",
                    confidence=1.0, status=STATUS_MAPPED,
                    candidates=m.candidates,
                    note="human-mapped (field-by-field override)"))
            else:
                new_fields.append(FieldMapping(
                    target=target, source_path=None, method="human",
                    confidence=0.0, status=STATUS_NEEDS_REVIEW,
                    candidates=m.candidates,
                    note="human left unmapped (no viable source)"))
        return Mapping(fields=new_fields)

    def candidates_for_unmapped(self) -> dict[str, list[tuple[str, float]]]:
        """For every target field that is NOT confidently mapped (needs_review or
        conflict), the ranked source-path candidates the matcher SAW but did not
        confidently pick — so a human can choose among real options instead of
        staring at a blank. Keyed by target field name; empty list means the
        matcher saw no plausible source at all (truly nothing to offer).

        These are real source paths from the data (never invented), carried on
        each FieldMapping as `candidates`; this just surfaces them for the
        unmapped fields the user is asked to resolve."""
        out: dict[str, list[tuple[str, float]]] = {}
        for m in self.fields:
            if m.status in (STATUS_NEEDS_REVIEW, STATUS_CONFLICT):
                out[m.target] = [(p, s) for p, s in m.candidates]
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [m.to_dict() for m in self.fields]}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _levenshtein(a: str, b: str) -> int:
    """Classic edit distance (insert/delete/substitute). Pure, O(len(a)*len(b)),
    used only on short field names so cost is trivial. Deterministic."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # deletion
                cur[j - 1] + 1,       # insertion
                prev[j - 1] + (ca != cb),  # substitution
            ))
        prev = cur
    return prev[-1]


# Common standalone English words that happen to sit within one edit of a SHORT
# golden name (e.g. 'game'/'fame'/'tame' vs 'name'; 'date'/'rate' vs ...). A real
# source shipping such a bare field is almost never a typo of the golden name, so
# the fuzzy tier must NOT claim it (that would be a fabrication). This is a
# precision guard the adversarial review flagged as a latent surface; it is
# deliberately tiny and explicit — a true typo of a golden name will not be one
# of these dictionary words.
_FUZZY_WORD_BLOCKLIST = frozenset({
    "game", "fame", "tame", "same", "lame", "dame", "came", "mane", "nave",
    "date", "rate", "late", "gate", "mate", "fate", "hate", "time", "type",
    "note", "code", "node", "mode", "role", "line", "list", "size", "year",
})


def _synonym_match(s_name: set[str], tgt: TargetField) -> bool:
    """True when the source's name token-set EXACTLY equals one of the target's
    curated field-name synonyms. Closed vocabulary, exact set equality — the
    same precision posture as the alias tier."""
    syns = _SYNONYMS.get(tgt.name)
    if not syns:
        return False
    return any({syn} == s_name for syn in syns)


def _fuzzy_score(s_name: set[str], tgt: TargetField) -> float:
    """A bounded typo-tolerance score for a near-miss on the WHOLE normalized
    name, so only a genuine misspelling of the target name — not an unrelated
    short word — can clear it.

    Precision design (two independent guards, both must pass):
      - LENGTH guard: a true typo preserves length within 2 chars. This alone
        rejects multi-token decoys (e.g. 'name_prefix' vs 'name') and anything
        structurally different.
      - EDIT-BUDGET guard: the allowed number of edits scales with target
        length — 1 edit per 4 chars (so a 4-7 char name tolerates 1 edit, 8-11
        tolerates 2, ...). An absolute budget is far stricter than a flat ratio
        for short names: a 4-char name accepts ONLY a single-character typo, not
        a 25%-different unrelated word. A decoy would have to be literally one
        keystroke from the target name (and type-compatible, and not already
        caught by a higher tier) to slip through.

    Returns a confidence in [_FUZZY_MIN, _FUZZY_MAX] scaled by how clean the
    match is (fewer edits -> higher), else 0.0. Requires >=1 real token per side
    and a target name of at least 3 chars (a 2-char target like an abbreviation
    is too short to typo-match safely)."""
    if not s_name or not tgt.tokens:
        return 0.0
    # Precision guard: a lone common dictionary word that merely sits near a
    # short golden name is not a typo of it. Reject when the source is a single
    # blocklisted word that is not itself a real token of the target.
    if len(s_name) == 1:
        (only,) = tuple(s_name)
        if only in _FUZZY_WORD_BLOCKLIST and only not in set(tgt.tokens):
            return 0.0
    s_joined = "".join(sorted(s_name))
    t_joined = "".join(sorted(tgt.tokens))
    if len(t_joined) < 3:
        return 0.0
    if abs(len(s_joined) - len(t_joined)) > 2:
        return 0.0
    budget = max(1, len(t_joined) // 4)
    dist = _levenshtein(s_joined, t_joined)
    if dist == 0 or dist > budget:
        # dist==0 is an exact/normalized match already handled upstream; a true
        # 0 here would mean the higher tiers didn't fire, so leave it to overlap.
        return 0.0
    # Scale: a 1-edit match scores near the ceiling; a budget-exhausting match
    # scores near the floor. Deterministic.
    cleanliness = 1.0 - (dist - 1) / budget
    return _FUZZY_MIN + cleanliness * (_FUZZY_MAX - _FUZZY_MIN)


def _type_compatible(src_type: str, tgt: TargetField) -> bool:
    """Soft compatibility: strings cast to many targets, so this is permissive;
    used only to break near-ties, never to create a match."""
    if not tgt.types:
        return True
    if src_type in tgt.types:
        return True
    # strings are castable to numeric/date targets; numerics interchange
    if src_type == "string":
        return True
    if src_type in ("integer", "number") and (
        "integer" in tgt.types or "number" in tgt.types
    ):
        return True
    return False


# --------------------------------------------------------------------------
# Value-evidence precision gate.
#
# A field matched only by a SUB-AUTHORITATIVE tier (description/synonym/fuzzy —
# NOT exact/normalized/alias, which are name-identity and self-evident) is a
# PROPOSAL. Before trusting it, we corroborate it against the field's actual
# VALUES: if the values positively CONTRADICT what the target field must hold,
# we veto the match (it falls back to abstain / needs_review). This is what
# separates a real field from a golden-ADJACENT junk field whose NAME overlaps
# but whose VALUES are unrelated (e.g. developer_contact is an email, not a
# studio; platform_vendor is a company, not a console; year_installed is a
# timestamp, not a 4-digit year).
#
# Design rules (precision without hurting recall):
#   - Only vetoes on a POSITIVE contradiction. "Unknown / no examples" never
#     vetoes (we abstain from vetoing, not from matching).
#   - Constraints come straight from the target SCHEMA (pattern, numeric range,
#     enum/taxonomy) — never invented. A value is wrong only against the
#     schema's own declared shape.
#   - Shape heuristics (email/url/timestamp) only veto a FREE-TEXT, human-name-
#     like target (name/developer/publisher), where such a value is self-
#     evidently the wrong kind of thing. They never veto a constrained field.
#
# KNOWN RECALL-DEBT (tracked for the richer-real-data pass; adversarial review):
#   The veto is strict against the SCHEMA's declared shape, so a GENUINE field
#   whose values are UNIFORMLY in an alternate-but-legitimate encoding can be
#   wrongly vetoed — e.g. a releaseYear synonym carrying a full ISO date vs the
#   schema's ^\d{4}$, a criticScore encoded "9/10", or a platform/ESRB value
#   that is valid in the world but absent from a small taxonomy ("Linux", "RP").
#   This is contained today (only on sub-authoritative tiers, and only when
#   EVERY sampled example is malformed, so one well-formed example disarms the
#   veto) and regresses no current test, but it is a real ceiling on messy
#   real-world data. When richer source values arrive, loosen to "extract a
#   plausible value" (year-within-a-date, scale-aware score parsing) and treat
#   small taxonomies as open rather than closed. Precision must stay the gate.
#   (3) The region-value veto (below) could wrongly veto a GENUINE studio/
#   publisher literally named a bare region word (e.g. a studio called
#   "Oceania") reached via a sub-authoritative tier. Contained: name-identity
#   tiers (exact/normalized/alias) are immune, multi-word company names escape,
#   and one non-region example disarms the veto. Track for the rich-data pass.
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(https?://|www\.)", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
# A clock component (HH:MM) marks a value as a TIMESTAMP rather than a plain
# date. Used to keep a timestamp-valued junk field (e.g. an install log
# 'year_installed' = '1998-04-12T09:00:00Z') vetoed even when we loosen a
# year-style pattern to accept a genuine release DATE like '2007-05-01'.
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}")
# Tokens of a value, split on whitespace and common date/text delimiters, so a
# pattern-conforming part (e.g. the '2007' in '2007-05-01' or 'May 1, 2007') can
# be recognized without matching the whole string.
_VALUE_TOKEN_RE = re.compile(r"[^\s,/\-:]+")

# A small, explicit set of geographic REGION / TERRITORY / COUNTRY values. A
# field whose values are drawn ENTIRELY from this set denotes a location, not a
# company or person — so it is self-evidently NOT a publisher/developer name.
# This closes the publisher_region/owner_studio_country class of hard negatives
# that no schema constraint can catch (the target is free-text). Deliberately
# conservative: a real publisher/studio is a company name, never a bare region.
_REGION_VALUES = frozenset({
    # sales regions / macro-territories
    "emea", "apac", "amer", "latam", "anz", "mena", "na", "eu", "us", "uk",
    "north america", "south america", "latin america", "central america",
    "asia pacific", "asia-pacific", "europe", "middle east", "oceania",
    "africa", "asia", "worldwide", "global", "international", "domestic",
    # cardinal/relative regions
    "north", "south", "east", "west", "northwest", "northeast", "southwest",
    "southeast", "pacific northwest", "midwest",
    # a handful of common country names (studios' HQ countries in reports)
    "united states", "usa", "canada", "australia", "japan", "china", "germany",
    "france", "united kingdom", "sweden", "finland", "poland", "south korea",
    "new zealand", "brazil", "mexico", "spain", "italy", "netherlands",
})


def _looks_like_region(value: Any) -> bool:
    """True when a string value is a bare geographic region / territory / country
    (not a company or person name)."""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _REGION_VALUES
# Generic, low-information name tokens. Sharing ONLY one of these with the target
# is weak evidence (every entity has a 'name'/'id'); when the source ALSO carries
# a foreign specific qualifier (station_name, record_id), that qualifier usually
# re-points the field at a DIFFERENT entity. Note the asymmetry this captures:
# 'station_name' shares only the GENERIC 'name' (weak) whereas 'publisher_name'
# shares the SPECIFIC 'publisher' and merely adds the generic 'name' (strong) —
# so the former is demoted and the latter is kept.
_GENERIC_TOKENS = frozenset({"name", "id", "code", "type", "value", "key", "label"})


_RATIO_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")


def _coerce_number(v: Any) -> float | None:
    """Parse a value to a float, accepting common WHOLE-STRING numeric encodings.

    Recognized string forms (the ENTIRE string must be the number — a bare
    number embedded in prose like '4 stars' is deliberately NOT accepted, so a
    junk field whose value merely contains a digit stays vetoed):
      - plain int/float ('90', '88.5')
      - trailing percent ('90%')
      - a simple ratio ('9/10' -> 0.9), the common 'score out of N' encoding
    Returns None when the whole string is not one of these."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        # A ratio like "9/10" or "4.5/5" — a whole-string "out of N" score.
        m = _RATIO_RE.match(s)
        if m:
            num, den = float(m.group(1)), float(m.group(2))
            return num / den if den != 0 else None
        # A trailing percent is an unambiguous numeric encoding (e.g. "90%").
        if s.endswith("%"):
            s = s[:-1].strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


# Words allowed in a plausible DATE value (so the pattern token-recovery path
# fires for a real date like 'May 1, 2007' but not for prose like
# 'copyright 2019 acme'). Month names (full + 3-letter abbrev) and ordinal/era
# words. Lower-cased; any alphabetic token outside this set means "not a date".
_DATE_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
    "st", "nd", "rd", "th",            # ordinal suffixes (1st, 2nd, 3rd, 4th)
    "of", "the",                        # harmless connectors ("the 1st of May")
})
_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _looks_like_date(s: str) -> bool:
    """True when `s` is a plausible date: it contains at least one digit and
    every ALPHABETIC token is a month name / ordinal / connector (not arbitrary
    prose). '2007-05-01' and 'May 1, 2007' pass; 'copyright 2019 acme' and
    'page 1998 of 2000' do not (copyright/acme/page are not date words)."""
    if not any(ch.isdigit() for ch in s):
        return False
    alpha = _ALPHA_TOKEN_RE.findall(s)
    return all(tok.lower() in _DATE_WORDS for tok in alpha)


def _satisfies_pattern(value: Any, pat: re.Pattern[str]) -> bool:
    """True when `value` satisfies the target's pattern constraint, tolerating a
    genuine alternate encoding that CONTAINS a conforming token.

    - A whole-string match passes (the strict original behavior).
    - Otherwise, if the value is NOT a timestamp (no HH:MM clock), a value TOKEN
      (split on whitespace / , / - / : /) that fully matches the pattern passes.
      This recovers a real release DATE ('2007-05-01', 'May 1, 2007') for a
      '^\\d{4}$' year field, while a full TIMESTAMP ('1998-04-12T09:00:00Z', an
      install-log value) is excluded by the clock guard and stays vetoed.
    The executor/transform layer extracts the conforming part downstream; the
    gate's job is only to not veto a value that genuinely contains it."""
    s = str(value).strip()
    if pat.search(s):
        return True
    if _CLOCK_RE.search(s):
        return False  # a timestamp is not a plain date -> no token recovery
    # Token recovery is allowed ONLY when the value is a PLAUSIBLE DATE, not
    # arbitrary prose that happens to contain a conforming token. Otherwise a
    # junk value like 'copyright 2019 acme' or 'page 1998 of 2000' would clear a
    # '^\d{4}$' year field via a stray year token (adversarial review finding).
    # A plausible date = every alphabetic token is a month name or an ordinal
    # word; numbers/separators are free. '2007-05-01' and 'May 1, 2007' pass;
    # 'copyright 2019 acme' is rejected (copyright/acme are not date words).
    if not _looks_like_date(s):
        return False
    return any(pat.search(tok) for tok in _VALUE_TOKEN_RE.findall(s))


def _enum_vocab(tgt: TargetField) -> set[str]:
    """Lower-cased accepted values for an enum/taxonomy target, incl. alias keys
    and their canonical values. Empty when the target has no controlled vocab."""
    vocab: set[str] = set()
    for e in tgt.enum:
        vocab.add(str(e).strip().lower())
    for k, val in tgt.aliases.items():
        vocab.add(str(k).strip().lower())
        vocab.add(str(val).strip().lower())
    return vocab


def _value_contradicts(sf: FieldStat, tgt: TargetField) -> bool:
    """True when the source field's example VALUES positively contradict the
    target's declared shape. Conservative: returns False (no veto) whenever
    there is no evidence either way."""
    examples = [e for e in sf.examples if e is not None]
    if not examples:
        return False  # no values seen -> cannot contradict

    # 1. Pattern constraint (e.g. releaseYear ^\d{4}$): veto if NO example
    #    satisfies it. "Satisfies" tolerates a genuine alternate encoding that
    #    CONTAINS a conforming token (a real date for a year field) but still
    #    excludes a timestamp (install-log junk) via the clock guard.
    if tgt.pattern:
        try:
            pat = re.compile(tgt.pattern)
        except re.error:
            pat = None
        if pat is not None and not any(
                _satisfies_pattern(e, pat) for e in examples):
            return True

    # 2. Numeric range (e.g. criticScore 0..100 integer): veto if NO example is
    #    a number within [min, max].
    if (tgt.minimum is not None or tgt.maximum is not None) and \
            ("integer" in tgt.types or "number" in tgt.types):
        lo = tgt.minimum if tgt.minimum is not None else float("-inf")
        hi = tgt.maximum if tgt.maximum is not None else float("inf")
        nums = [n for n in (_coerce_number(e) for e in examples) if n is not None]
        if not nums or not any(lo <= n <= hi for n in nums):
            return True

    # 3. Controlled vocabulary (enum/taxonomy, e.g. platform / ESRB): veto if NO
    #    example is an accepted value (after alias + case normalization).
    vocab = _enum_vocab(tgt)
    if vocab:
        if not any(isinstance(e, str) and e.strip().lower() in vocab
                   for e in examples):
            return True

    # 4. Shape heuristic for a FREE-TEXT human-name-like target: a value that is
    #    self-evidently the WRONG KIND of thing for a game name / studio /
    #    publisher is a contradiction. Two shapes:
    #      (a) email / url / timestamp — not a name;
    #      (b) a bare geographic region / country — a location, not a company
    #          (closes publisher_region->publisher, owner_studio_country->developer).
    #    Only vetoes when EVERY example is such a shape (one real name disarms it).
    name_like = (tgt.name in ("name", "developer", "publisher")
                 and not tgt.enum and not tgt.pattern)
    if name_like:
        strs = [e for e in examples if isinstance(e, str)]
        if strs and all(
                _EMAIL_RE.match(e.strip()) or _URL_RE.match(e.strip())
                or _TIMESTAMP_RE.match(e.strip())
                for e in strs):
            return True
        if strs and all(_looks_like_region(e) for e in strs):
            return True

    return False


def _value_corroborates(sf: FieldStat, tgt: TargetField) -> bool:
    """True when the source values POSITIVELY fit the target's declared shape —
    the complement of _value_contradicts. Used only as a small confidence BOOST
    on an already-fired sub-authoritative match (it never creates a match, and a
    value-contradicted candidate is vetoed upstream before this is consulted), so
    a value-backed candidate outranks a merely-lexical one on the same target.

    Conservative: requires POSITIVE evidence (an example that actually fits), so
    'no values' or 'free-text target with no constraint' does not corroborate."""
    examples = [e for e in sf.examples if e is not None]
    if not examples:
        return False

    # Pattern fit (e.g. a 4-digit year, or a date containing one, for releaseYear).
    if tgt.pattern:
        try:
            pat = re.compile(tgt.pattern)
        except re.error:
            pat = None
        if pat is not None and any(_satisfies_pattern(e, pat) for e in examples):
            return True

    # Numeric-range fit (e.g. a 0..100 integer for criticScore).
    if (tgt.minimum is not None or tgt.maximum is not None) and \
            ("integer" in tgt.types or "number" in tgt.types):
        lo = tgt.minimum if tgt.minimum is not None else float("-inf")
        hi = tgt.maximum if tgt.maximum is not None else float("inf")
        nums = [n for n in (_coerce_number(e) for e in examples) if n is not None]
        if any(lo <= n <= hi for n in nums):
            return True

    # Controlled-vocabulary fit (an example is an accepted enum/taxonomy value).
    vocab = _enum_vocab(tgt)
    if vocab and any(isinstance(e, str) and e.strip().lower() in vocab
                     for e in examples):
        return True

    # Array-shape fit: the target is an array and the source actually carries a
    # list (genres/playerModes), a positive structural signal.
    if "array" in tgt.types and "array" in sf.types:
        return True

    return False


def _weak_generic_overlap(sf: FieldStat, tgt: TargetField) -> bool:
    """True when a description-tier match on a value-UNCONSTRAINED free-text
    target rests on a WEAK name-structure signal and should be demoted to
    needs_review (no fabrication) rather than confidently grounded.

    The discriminating asymmetry (both conditions required):
      - the match rests ONLY on a shared GENERIC token (name/id/code/...), i.e.
        every shared token is low-information; AND
      - the source carries an extra qualifier token that is FOREIGN to the
        target's name+description vocabulary (a real qualifier pointing
        elsewhere).
    So 'station_name' (shares only generic 'name', adds foreign 'station') is
    demoted, while 'publisher_name' (shares the SPECIFIC 'publisher', merely adds
    generic 'name') is KEPT — the shared token is not generic there. 'game_name'
    adds 'game', which is in name's description vocabulary, so it is not foreign
    and the match stands. Never applied to value-CONSTRAINED targets (their value
    gate decides)."""
    if tgt.enum or tgt.pattern or tgt.minimum is not None or tgt.maximum is not None:
        return False  # constrained targets are judged by value evidence instead
    s_name = set(sf.tokens)
    t_name = set(tgt.tokens)
    shared = s_name & t_name
    extra = s_name - t_name
    if not shared or not extra:
        return False  # no overlap, or a clean subset -> not this gate's concern
    # Only weak when the shared evidence is purely generic (low information).
    if not (shared <= _GENERIC_TOKENS):
        return False
    explained = t_name | set(tgt.desc_tokens)
    foreign = extra - explained
    # ...and the extra tokens are a foreign qualifier (not explained by the
    # target's own vocabulary).
    return bool(foreign)


# --------------------------------------------------------------------------
# Semantic (embedding) tier helpers. OFFLINE-SAFE + DETERMINISTIC-GIVEN-CACHE:
# embeddings are computed lazily through the shared provider and memoized per
# evidence string, so a given run is reproducible and repeated identical strings
# are embedded once. On the hashing fallback (no semantic meaning) or on ANY
# embedding error the tier is a NO-OP, so the matcher stays fully deterministic
# and functional without a model.
# --------------------------------------------------------------------------

_embed_cache: dict[str, list[float]] = {}
_SEMANTIC_PROBED = False
_SEMANTIC_ENABLED = False


def _semantic_available() -> bool:
    """True only when a REAL embedding model is active. The hashing fallback is a
    bag-of-words with no meaning, so the measurement showed it cannot separate
    fields — we disable the semantic tier there rather than add noise. Probed
    once per process; never raises."""
    global _SEMANTIC_PROBED, _SEMANTIC_ENABLED
    if _SEMANTIC_PROBED:
        return _SEMANTIC_ENABLED
    _SEMANTIC_PROBED = True
    try:
        from ..embeddings import embedder
        _SEMANTIC_ENABLED = "minilm" in str(embedder.model).lower()
    except Exception:
        _SEMANTIC_ENABLED = False
    return _SEMANTIC_ENABLED


def _embed(text: str) -> list[float] | None:
    """Memoized, offline-safe embed. Returns None on any failure (tier no-ops)."""
    if not text:
        return None
    hit = _embed_cache.get(text)
    if hit is not None:
        return hit
    try:
        from ..embeddings import embedder
        vec = embedder.embed(text)
    except Exception:
        return None
    _embed_cache[text] = vec
    return vec


def _source_evidence(sf: FieldStat) -> str:
    name = " ".join(sf.tokens) or sf.path
    desc = " ".join(sf.desc_tokens)
    return evidence_string(name, desc, list(sf.examples))


def _target_evidence(tgt: TargetField) -> str:
    return evidence_string(tgt.name, tgt.description, [])


def _cosine_to_target(sf: FieldStat, tgt: TargetField) -> float:
    """Raw evidence-string cosine source->target, or 0.0 when no real model /
    error. Used by the gated booster to RE-RANK existing candidates (never to
    create one)."""
    if not _semantic_available():
        return 0.0
    sv = _embed(_source_evidence(sf))
    tv = _embed(_target_evidence(tgt))
    if sv is None or tv is None:
        return 0.0
    return sum(x * y for x, y in zip(sv, tv))


def _score_candidate(sf: FieldStat, tgt: TargetField) -> tuple[float, str]:
    """Return (score, method) for one source field against one target field.

    Tiers, strongest first: exact name, normalized token-set equality, declared
    alias, then token overlap. The overlap tier compares BOTH sides' name+
    description tokens, so a source metadata description (when available) is an
    honest extra signal — never fabrication."""
    s_name = set(sf.tokens)
    s_desc = set(sf.desc_tokens)
    t_name = set(tgt.tokens)
    t_desc = set(tgt.desc_tokens)
    leaf_norm = " ".join(sf.tokens)
    tgt_norm = " ".join(tgt.tokens)

    # 1. exact normalized name
    if leaf_norm and leaf_norm == tgt_norm:
        return _EXACT, "exact"
    # 2. normalized token-set equality (names only)
    if s_name and s_name == t_name:
        return _NORMALIZED, "normalized"
    # 3. alias: source name tokens match a declared alias of the target
    for alias in tgt.aliases:
        if name_tokens(str(alias)) and set(name_tokens(str(alias))) == s_name:
            return _ALIAS, "alias"
    # VALUE-EVIDENCE GATE: tiers below here are PROPOSALS (name/overlap based),
    # not name-identity. Before any of them may fire, veto the candidate if the
    # source field's actual VALUES positively contradict the target's declared
    # shape. This is what stops a golden-ADJACENT junk field (name overlaps,
    # values unrelated) from grounding, without touching exact/normalized/alias.
    if _value_contradicts(sf, tgt):
        return 0.0, "abstain"

    # Positive value-corroboration: a small confidence boost for a
    # sub-authoritative match whose VALUES fit the target shape, capped per-tier
    # so it only reorders within the sub-authoritative band (never promotes past
    # the next tier). Computed once; applied to synonym/fuzzy/description below.
    boost = _VALUE_BOOST if _value_corroborates(sf, tgt) else 0.0

    # 3b. curated field-name synonym (offline dictionary tier). Exact token-set
    #     equality against a closed synonym vocabulary; type-compatible only. A
    #     single synonym token (e.g. 'studio' for developer) would dilute below
    #     the description floor, so this tier is what recovers pure synonyms.
    if _synonym_match(s_name, tgt) and _type_compatible(sf.primary_type, tgt):
        # cap below alias (0.9) so a boosted synonym never outranks a real alias.
        return min(_SYNONYM + boost, _ALIAS - 0.01), "synonym"
    # 3c. fuzzy near-miss (typo tolerance). Bounded whole-name edit similarity
    #     with a length guard; type-compatible only. Recovers misspelled field
    #     names without opening the door to unrelated short words.
    fscore = _fuzzy_score(s_name, tgt)
    if fscore > 0.0 and _type_compatible(sf.primary_type, tgt):
        # cap below the synonym floor so a boosted fuzzy stays under synonyms.
        return min(fscore + boost, _SYNONYM - 0.01), "fuzzy"
    # 4. token overlap. Compare the richest available bag of tokens on each side
    #    (name + description), so metadata descriptions help when names are opaque.
    s_bag = s_name | s_desc
    t_bag = t_name | t_desc
    overlap = _jaccard(s_bag, t_bag)
    # Also consider name-vs-(name+desc) in case only one side has a description.
    overlap = max(overlap, _jaccard(s_name, t_bag), _jaccard(s_bag, t_name))
    if overlap >= _DESC_FLOOR and _type_compatible(sf.primary_type, tgt):
        # Name-structure veto: a description-tier match on a value-unconstrained
        # free-text target that rests only on a shared GENERIC token plus an
        # unexplained qualifier (station_name->name, publisher_region->publisher)
        # is too weak to confidently ground -> abstain (routes to needs_review).
        if _weak_generic_overlap(sf, tgt):
            return 0.0, "abstain"
        # cap below alias so a boosted description match never outranks alias.
        score = min(0.85 + boost, _ALIAS - 0.01, 0.5 + overlap / 2 + boost)
        return score, "description"
    # NOTE: the embedding (semantic) signal is intentionally NOT a standalone
    # grounding tier. Measurement showed that on thin data (short names, single
    # values, no descriptions) cosine cannot separate real synonyms from
    # in-domain junk — the ranges overlap — so a cosine-grounding tier re-opens
    # the junk leak. The embedding signal (_cosine_to_target) is used only as a
    # GATED BOOSTER in infer_mapping (re-ranking the DISPLAYED candidate list
    # among candidates that already cleared a lexical tier), never to ground a
    # pair no lexical tier proposed and never to select the grounded path.
    return 0.0, "abstain"


def infer_mapping(source: SourceProfile, target: TargetSchema) -> Mapping:
    """Infer a deterministic source->target mapping, abstaining when unsure.

    One source field may legitimately be the best candidate for multiple targets
    (e.g. a single 'genre' column), so we do NOT enforce 1:1; each target is
    resolved independently from its ranked candidates."""
    src_fields = source.fields
    out: list[FieldMapping] = []

    sf_by_path = {sf.path: sf for sf in src_fields}

    for tgt in target.fields:
        scored: list[tuple[float, str, str]] = []  # (score, method, source_path)
        for sf in src_fields:
            score, method = _score_candidate(sf, tgt)
            if score > 0.0:
                scored.append((score, method, sf.path))

        # GROUNDING ORDER is PURELY LEXICAL + deterministic (score desc, then
        # path asc). This decides which candidate is `top` and whether a tie is a
        # conflict — the embedding signal must NEVER influence it (adversarial
        # review: two distinct paths can both score 'exact'/'normalized' for one
        # target, e.g. movie.title and alt.title both normalize to {title}; if
        # cosine chose among them it would be the embedding selecting the grounded
        # path — a precision decision the measured mandate forbids).
        scored.sort(key=lambda x: (-x[0], x[2]))

        if not scored:
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method="abstain",
                confidence=0.0, status=STATUS_NEEDS_REVIEW,
                candidates=(),
                note="no source field cleared the match floor",
            ))
            continue

        top_score, top_method, top_path = scored[0]

        # GATED SEMANTIC BOOSTER (Option A): embeddings re-rank ONLY the
        # DISPLAYED candidate list (what a human reviews / the UI surfaces),
        # never the grounding choice above. Among candidates sharing a lexical
        # score, cosine orders the more meaning-relevant first. Recall/ordering
        # signal only; precision stays with the lexical + value gates. Computed
        # once per candidate, cached, and only when there is more than one.
        cand_order = scored
        if len(scored) > 1:
            cos_by_path = {
                p: _cosine_to_target(sf_by_path[p], tgt)
                for _s, _m, p in scored if p in sf_by_path
            }
            cand_order = sorted(
                scored, key=lambda x: (-x[0], -cos_by_path.get(x[2], 0.0), x[2]))
        candidates = tuple((p, s) for s, _m, p in cand_order)
        # Conflict only when two DISTINCT source paths tie at the very top with a
        # non-authoritative method (exact/normalized are self-evidently unique).
        tied = [c for c in scored if c[0] == top_score and c[2] != top_path]
        if tied and top_method not in ("exact", "normalized"):
            # A genuine tie stays a CONFLICT (abstain-with-candidates). The
            # semantic booster deliberately does NOT resolve it into a confident
            # map: when the tied candidates are all junk (as in a decoy-only
            # record), picking one by cosine would ground a decoy — turning a
            # precision-safe abstention into a false match. Measured: forcing a
            # tie-break raised false_match on the decoy-only axis. So the booster
            # only RE-RANKS the candidate list (already applied above, improving
            # the order a human reviews), never changes mapped/conflict status.
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method=top_method,
                confidence=top_score, status=STATUS_CONFLICT,
                candidates=candidates,
                note=f"ambiguous: {1 + len(tied)} source fields tie at {top_score:.2f}",
            ))
            continue

        out.append(FieldMapping(
            target=tgt.name, source_path=top_path, method=top_method,
            confidence=top_score, status=STATUS_MAPPED,
            candidates=candidates,
        ))
    return Mapping(fields=out)


__all__ = [
    "FieldMapping",
    "Mapping",
    "STATUS_CONFLICT",
    "STATUS_MAPPED",
    "STATUS_NEEDS_REVIEW",
    "infer_mapping",
]
