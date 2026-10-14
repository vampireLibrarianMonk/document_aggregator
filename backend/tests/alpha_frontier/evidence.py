"""Evidence-string embedding measurement + a rich-text fixture.

Task 7 of the semantic-matching pass. The names-only embedding measurement
(recorded in the session) showed MiniLM cosine does NOT separate real synonyms
from golden-adjacent junk when it only sees 2-word field NAMES: junk like
`esrb_descriptor` (0.615) and `platform_vendor` (0.601) out-scored every real
synonym. The user's real workload, though, is analytic reports where fields come
with VALUES and often sentences/paragraphs of description. This module measures
what embedding the richer EVIDENCE STRING (name + example values + any
description) buys us — the go/no-go gate for building the embedding tier (Task 8).

HONEST FINDING (in-domain hard-negative fixture; adversarial review forced the
junk to share the golden's VOCABULARY instead of being off-topic weather text):
  - RECALL: evidence strings rank EVERY real synonym's top golden CORRECTLY
    (all synonyms_top_correct == synonyms_total) where bare names fail — so
    embeddings ARE a good recall signal.
  - PRECISION: embeddings ALONE do NOT separate signal from in-domain junk (the
    weakest synonym does not out-score the strongest junk; e.g. a 'year_installed'
    column sits as close to releaseYear as a true synonym). So the embedding tier
    must be RECALL-ONLY and gated: a cosine proposal may ground ONLY if it also
    passes the deterministic value-evidence gate. Cosine is never authoritative.

Everything here is deterministic and offline (modulo the embedding model, whose
active backend is recorded). It is a research/measurement module + a reusable
fixture, not wired into the app.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The canonical evidence-string builder now lives in the app so the matcher tier
# and this measurement share ONE implementation (no drift). Re-exported here.
from app.json_alignment.evidence import clean_name as _clean_name
from app.json_alignment.evidence import evidence_string

# --------------------------------------------------------------------------
# Rich-text fixture: golden fields with full-sentence descriptions, and source
# fields that look like analytic-report columns — real synonyms (should match)
# and golden-adjacent junk (should NOT) — each with a sentence/paragraph
# description and representative values. This approximates the workload the user
# said is coming (reports conforming to a golden template), so the measurement
# isn't judged only on 2-word toy names.
# --------------------------------------------------------------------------

@dataclass
class RichField:
    name: str
    description: str
    examples: tuple[Any, ...] = ()
    intended_target: str | None = None   # None => junk (must not match)

    def evidence(self) -> str:
        return evidence_string(self.name, self.description, self.examples)


@dataclass
class RichFixture:
    """Golden target fields (name -> evidence) + labelled source fields."""
    golden: dict[str, str] = field(default_factory=dict)
    sources: list[RichField] = field(default_factory=list)


# Golden side: derived from the BROADENED research schema (rich_schema.json) so
# the fixture and the matcher share one template. The schema carries full-
# sentence descriptions and three extra analytic-report fields (summary,
# playerModes, ownerStudioCountry) that create new, genuinely hard confusables.
_RICH_SCHEMA_PATH = Path(__file__).with_name("rich_schema.json")


def rich_schema() -> dict[str, Any]:
    return json.loads(_RICH_SCHEMA_PATH.read_text(encoding="utf-8"))


def _golden_rich() -> dict[str, str]:
    props = rich_schema()["properties"]
    return {name: node.get("description", "") for name, node in props.items()}


# Source side: realistic report columns. Synonyms carry descriptions that paraphrase
# the golden meaning; junk carries descriptions about a DIFFERENT entity even though
# the field name shares a golden token. Junk is IN-DOMAIN (shares the golden's
# vocabulary) so the test is honest — an off-domain give-away would make it trivial.
_SOURCES_RICH: list[RichField] = [
    # --- real synonyms (should map) ---
    RichField("studio", "The development studio credited with creating this title.",
              ("Supergiant Games", "Valve"), "developer"),
    RichField("console", "The gaming hardware platform this release targets.",
              ("Windows PC", "Nintendo Switch"), "platform"),
    RichField("metascore",
              "Weighted average of professional critic reviews, scored out of 100.",
              (93, 90), "criticScore"),
    RichField("maturity_rating",
              "Age-suitability rating from the ratings board for this game.",
              ("T", "E10+"), "ESRB"),
    RichField("launch_year", "Calendar year the game first shipped to stores.",
              ("2020", "2007"), "releaseYear"),
    RichField("product_title", "The marketed title of the game product.",
              ("Hades", "Portal"), "name"),
    RichField("categories", "The gameplay categories this title falls under.",
              (["Roguelike", "Action"],), "genres"),
    RichField("synopsis",
              "A short prose synopsis of the game's premise, setting and story.",
              ("A rogue-like dungeon crawler set in the Greek underworld.",),
              "summary"),
    RichField("modes",
              "The supported play modes offered by this title.",
              (["Single-player", "Co-op"],), "playerModes"),
    RichField("studio_country",
              "The country where the development studio is headquartered.",
              ("United States", "Canada"), "ownerStudioCountry"),
    # --- golden-adjacent junk (must NOT map). HARD by design: real video-game-
    #     domain report columns that share a golden field's vocabulary but denote
    #     a DIFFERENT entity. ---
    RichField("developer_contact",
              "Primary business contact email for licensing with the game's "
              "developer studio.",
              ("licensing@supergiant.example",), None),
    RichField("platform_vendor",
              "The retail vendor or digital storefront that sells the game on "
              "each platform.",
              ("Steam", "Epic Games Store"), None),
    RichField("publisher_region",
              "The sales territory in which the game's publisher holds "
              "distribution rights.",
              ("North America", "EMEA"), None),
    RichField("esrb_descriptor",
              "The ESRB content descriptor phrases that explain the rating, such "
              "as violence or language.",
              ("Blood and Gore", "Strong Language"), None),
    RichField("year_installed",
              "The year this game was installed onto the player's console or "
              "library.",
              ("2024",), None),
    RichField("score_rating_stars",
              "The player community's average star rating of the game, from one "
              "to five stars.",
              ("4.5 stars",), None),
    RichField("publisher_name_contact",
              "Full name of the account manager at the game's publisher.",
              ("Jane Dreyfus",), None),
    RichField("summary_length_words",
              "The word count of the summary text for reporting layout.",
              (240, 180), None),
    RichField("owner_studio_revenue",
              "Annual revenue of the company that owns the developer studio.",
              ("$42M",), None),
]


def rich_fixture() -> RichFixture:
    golden = {k: evidence_string(k, v) for k, v in _golden_rich().items()}
    return RichFixture(golden=golden, sources=list(_SOURCES_RICH))


# --------------------------------------------------------------------------
# The measurement: for both the BARE-NAME representation and the EVIDENCE-STRING
# representation, compute each source's top golden cosine and whether it points
# at the intended target. Report the separation margin (min synonym top-cos vs
# max junk top-cos). A positive margin means a cosine floor exists that admits
# every synonym while rejecting every junk — i.e. embeddings separate.
# --------------------------------------------------------------------------

@dataclass
class SepResult:
    backend: str
    representation: str                 # "bare_name" | "evidence_string"
    rows: list[dict[str, Any]] = field(default_factory=list)
    min_synonym_top: float = 0.0        # lowest top-cosine among real synonyms
    max_junk_top: float = 0.0           # highest top-cosine among junk
    synonyms_top_correct: int = 0       # synonyms whose top golden is the intended one
    synonyms_total: int = 0

    @property
    def margin(self) -> float:
        """min(synonym top) - max(junk top). >0 => a separating floor exists."""
        return self.min_synonym_top - self.max_junk_top

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "representation": self.representation,
            "min_synonym_top": round(self.min_synonym_top, 4),
            "max_junk_top": round(self.max_junk_top, 4),
            "margin": round(self.margin, 4),
            "synonyms_top_correct": self.synonyms_top_correct,
            "synonyms_total": self.synonyms_total,
            "rows": self.rows,
        }


def _measure(representation: str, golden_text: dict[str, str],
             sources: list[RichField],
             src_text: dict[str, str]) -> SepResult:
    from app.embeddings import cosine, embedder

    gnames = list(golden_text)
    gvecs = {k: embedder.embed(golden_text[k]) for k in gnames}
    res = SepResult(backend=embedder.model, representation=representation)
    syn_tops: list[float] = []
    junk_tops: list[float] = []
    for sf in sources:
        sv = embedder.embed(src_text[sf.name])
        sims = sorted(((cosine(sv, gvecs[g]), g) for g in gnames), reverse=True)
        top_cos, top_g = sims[0]
        is_syn = sf.intended_target is not None
        row = {
            "source": sf.name,
            "kind": "synonym" if is_syn else "junk",
            "intended": sf.intended_target,
            "top_golden": top_g,
            "top_cos": round(top_cos, 4),
            "top_is_intended": (top_g == sf.intended_target) if is_syn else None,
        }
        res.rows.append(row)
        if is_syn:
            syn_tops.append(top_cos)
            res.synonyms_total += 1
            if top_g == sf.intended_target:
                res.synonyms_top_correct += 1
        else:
            junk_tops.append(top_cos)
    res.min_synonym_top = min(syn_tops) if syn_tops else 0.0
    res.max_junk_top = max(junk_tops) if junk_tops else 0.0
    return res


def measure_separation() -> dict[str, Any]:
    """Compare bare-name vs evidence-string separation on the rich fixture.

    Returns both SepResults + the verdict (does the evidence string open a
    positive separating margin where bare names did not?)."""
    fx = rich_fixture()
    # bare-name representation: just the cleaned field name on both sides.
    bare_golden = {k: _clean_name(k) for k in fx.golden}
    bare_src = {sf.name: _clean_name(sf.name) for sf in fx.sources}
    bare = _measure("bare_name", bare_golden, fx.sources, bare_src)

    # evidence-string representation: name + description + values on both sides.
    ev_src = {sf.name: sf.evidence() for sf in fx.sources}
    ev = _measure("evidence_string", fx.golden, fx.sources, ev_src)

    return {
        "backend": bare.backend,
        "bare_name": bare.to_dict(),
        "evidence_string": ev.to_dict(),
        "evidence_separates": ev.margin > 0.0,
        "bare_separates": bare.margin > 0.0,
    }


def main() -> None:
    out = measure_separation()
    print(f"backend: {out['backend']}")
    for rep in ("bare_name", "evidence_string"):
        r = out[rep]
        print(f"\n[{rep}] min_synonym_top={r['min_synonym_top']} "
              f"max_junk_top={r['max_junk_top']} MARGIN={r['margin']} "
              f"synonyms_top_correct={r['synonyms_top_correct']}/{r['synonyms_total']}")
        for row in sorted(r["rows"], key=lambda x: -x["top_cos"]):
            print(f"  {row['kind']:7} {row['source']:20} -> {row['top_golden']:12} "
                  f"{row['top_cos']:.3f} intended={row['intended']} "
                  f"hit={row['top_is_intended']}")
    print("\nbare_separates   :", out["bare_separates"])
    print("evidence_separates:", out["evidence_separates"])
    print(json.dumps({"margin_bare": out["bare_name"]["margin"],
                      "margin_evidence": out["evidence_string"]["margin"]}))


if __name__ == "__main__":
    main()
