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

from dataclasses import dataclass, field
from typing import Any

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
    # 3b. curated field-name synonym (offline dictionary tier). Exact token-set
    #     equality against a closed synonym vocabulary; type-compatible only. A
    #     single synonym token (e.g. 'studio' for developer) would dilute below
    #     the description floor, so this tier is what recovers pure synonyms.
    if _synonym_match(s_name, tgt) and _type_compatible(sf.primary_type, tgt):
        return _SYNONYM, "synonym"
    # 3c. fuzzy near-miss (typo tolerance). Bounded whole-name edit similarity
    #     with a length guard; type-compatible only. Recovers misspelled field
    #     names without opening the door to unrelated short words.
    fscore = _fuzzy_score(s_name, tgt)
    if fscore > 0.0 and _type_compatible(sf.primary_type, tgt):
        return fscore, "fuzzy"
    # 4. token overlap. Compare the richest available bag of tokens on each side
    #    (name + description), so metadata descriptions help when names are opaque.
    s_bag = s_name | s_desc
    t_bag = t_name | t_desc
    overlap = _jaccard(s_bag, t_bag)
    # Also consider name-vs-(name+desc) in case only one side has a description.
    overlap = max(overlap, _jaccard(s_name, t_bag), _jaccard(s_bag, t_name))
    if overlap >= _DESC_FLOOR and _type_compatible(sf.primary_type, tgt):
        score = min(0.85, 0.5 + overlap / 2)
        return score, "description"
    return 0.0, "abstain"


def infer_mapping(source: SourceProfile, target: TargetSchema) -> Mapping:
    """Infer a deterministic source->target mapping, abstaining when unsure.

    One source field may legitimately be the best candidate for multiple targets
    (e.g. a single 'genre' column), so we do NOT enforce 1:1; each target is
    resolved independently from its ranked candidates."""
    src_fields = source.fields
    out: list[FieldMapping] = []

    for tgt in target.fields:
        scored: list[tuple[float, str, str]] = []  # (score, method, source_path)
        for sf in src_fields:
            score, method = _score_candidate(sf, tgt)
            if score > 0.0:
                scored.append((score, method, sf.path))
        # Rank: score desc, then source path asc for a stable deterministic order.
        scored.sort(key=lambda x: (-x[0], x[2]))
        candidates = tuple((p, s) for s, _m, p in scored)

        if not scored:
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method="abstain",
                confidence=0.0, status=STATUS_NEEDS_REVIEW,
                candidates=candidates,
                note="no source field cleared the match floor",
            ))
            continue

        top_score, top_method, top_path = scored[0]
        # Conflict only when two DISTINCT source paths tie at the very top with a
        # non-authoritative method (exact/normalized are self-evidently unique).
        tied = [c for c in scored if c[0] == top_score and c[2] != top_path]
        if tied and top_method not in ("exact", "normalized"):
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
