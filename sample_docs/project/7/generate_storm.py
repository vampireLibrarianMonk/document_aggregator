#!/usr/bin/env python3
"""Malformed-JSON storm generator for the JSON->golden sample scenario.

Takes CLEAN golden records (records that already conform to golden/target_schema.json)
and emits a STORM of malformed source files, each file a distinct "shape" produced
by a named mutation at a difficulty level from the research brief's §24 ladder:

    L1  renamed keys
    L2  renamed keys + datatype changes
    L3  nesting changes
    L4  arrays / flattening
    L5  split / merged fields
    L6  irrelevant fields
    L7  missing required fields
    L8  conflicting candidate fields
    L9  opaque field names
    L10 combination of everything

This is DETERMINISTIC (seeded) and offline — no model, no network. It is the
seam where a micromodel could later MANUFACTURE malformed variants: each mutation
is a named function with the same contract (clean record -> malformed record), so
a learned generator can register new mutations or replace these without changing
the harness. For now the mutations are hand-written and auditable.

The whole point of the "storm" is scale with FEW shapes: pass --copies N to stamp
out N files per level (same shape, different records) so clustering / approve-once
/ replay can be exercised at volume without committing thousands of files.

Usage:
    python generate_storm.py                      # default: 1 file per level -> ./storm/
    python generate_storm.py --copies 50          # 50 files per level (a real storm)
    python generate_storm.py --levels L1 L4 L10   # only these levels
    python generate_storm.py --out /tmp/storm --seed 7

Clean source: by default the records in expected/golden_records.json (the ones
that already conform). Override with --source <file.json>.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE / "expected" / "golden_records.json"
DEFAULT_OUT = HERE / "storm"

Record = dict[str, Any]


# --------------------------------------------------------------------------
# Clean seed records
# --------------------------------------------------------------------------

# A small built-in pool of CLEAN golden records so the generator works even
# without an external source. These already conform to the golden schema.
BUILTIN_CLEAN: list[Record] = [
    {"name": "Celeste", "releaseYear": "2018", "developer": "Maddy Makes Games",
     "publisher": "Maddy Makes Games", "platform": "Windows PC",
     "genres": ["Platformer"], "criticScore": 92, "ESRB": "E10+"},
    {"name": "Hollow Knight", "releaseYear": "2017", "developer": "Team Cherry",
     "publisher": "Team Cherry", "platform": "Windows PC",
     "genres": ["Metroidvania"], "criticScore": 90, "ESRB": "E10+"},
    {"name": "Hades", "releaseYear": "2020", "developer": "Supergiant Games",
     "publisher": "Supergiant Games", "platform": "Windows PC",
     "genres": ["Roguelike", "Action"], "criticScore": 93, "ESRB": "T"},
    {"name": "The Witcher 3: Wild Hunt", "releaseYear": "2015",
     "developer": "CD Projekt Red", "publisher": "CD Projekt",
     "platform": "Windows PC", "genres": ["Action", "RPG"],
     "criticScore": 93, "ESRB": "M"},
    {"name": "Stardew Valley", "releaseYear": "2016", "developer": "ConcernedApe",
     "publisher": "ConcernedApe", "platform": "Windows PC",
     "genres": ["Simulation", "RPG"], "criticScore": 89, "ESRB": "E10+"},
]

# Value aliases a mutation can swap in (platform/ESRB have taxonomy tables in the
# golden schema; using the alias form is a legitimate malformation to normalize).
_PLATFORM_ALIAS = {"Windows PC": "PC", "Xbox": "XBOX", "PlayStation": "PS"}
_ESRB_ALIAS = {"M": "Mature", "T": "Teen", "E": "K-A"}


# --------------------------------------------------------------------------
# Mutations — one per difficulty level. Contract: clean record -> malformed.
# A micromodel could later replace/extend these; the signature is the seam.
# --------------------------------------------------------------------------

def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


def mut_L1_renamed(rec: Record, rng: random.Random) -> Record:
    """Rename keys to synonyms that still SHARE tokens with the golden names."""
    return {
        "game_name": rec["name"],
        "release_year": rec["releaseYear"],
        "developer_studio": rec["developer"],
        "publisher_name": rec["publisher"],
        "platform_system": rec["platform"],
        "genre_list": rec["genres"],
        "critic_score_value": rec["criticScore"],
        "esrb_rating": rec["ESRB"],
    }


def mut_L2_renamed_typed(rec: Record, rng: random.Random) -> Record:
    """Renamed keys + datatype changes: year as int, score as string, genres as
    a delimited string."""
    out = mut_L1_renamed(rec, rng)
    out["release_year"] = int(rec["releaseYear"])
    out["critic_score_value"] = str(rec["criticScore"])
    out["genre_list"] = ";".join(_as_list(rec["genres"]))
    return out


def mut_L3_nested(rec: Record, rng: random.Random) -> Record:
    """Bury fields under nested objects."""
    return {
        "gameData": {
            "name": rec["name"],
            "releaseInfo": {"year": rec["releaseYear"]},
            "studio": {"developer": rec["developer"], "publisher": rec["publisher"]},
        },
        "classification": {"platform": rec["platform"], "ESRB": rec["ESRB"]},
        "ratings": {"critic": rec["criticScore"]},
        "genres": rec["genres"],
    }


def mut_L4_arrays_flatten(rec: Record, rng: random.Random) -> Record:
    """Genres as a single delimited string (needs split); platform as an array
    (needs flatten to a scalar)."""
    out = dict(rec)
    out["genres"] = ", ".join(_as_list(rec["genres"]))
    out["platform"] = [rec["platform"], "PlayStation", "Xbox"]
    return out


def mut_L5_split_merged(rec: Record, rng: random.Random) -> Record:
    """Split name into title+subtitle; merge developer+publisher into one string."""
    name = str(rec["name"])
    head, _, tail = name.partition(":")
    return {
        "titlePart": head.strip(),
        "subtitlePart": tail.strip(),
        "releaseYear": rec["releaseYear"],
        "studioCredit": f"{rec['developer']} / {rec['publisher']}",
        "platform": rec["platform"],
        "genres": _as_list(rec["genres"]),
        "criticScore": rec["criticScore"],
        "ESRB": rec["ESRB"],
    }


def mut_L6_irrelevant(rec: Record, rng: random.Random) -> Record:
    """Clean golden keys buried among unrelated bookkeeping fields."""
    out = {
        "_row": rng.randint(1, 9999),
        "ingested_at": "2026-02-01T09:00:00Z",
        "source_system": "legacy-catalog-v2",
        "checksum": f"{rng.randrange(16**8):08x}",
    }
    out.update(rec)
    out["internal_notes"] = rng.choice(["do not edit", "verified", "stale"])
    out["legacy_price_cents"] = rng.choice([999, 1499, 1999, 5999])
    return out


def mut_L7_missing_required(rec: Record, rng: random.Random) -> Record:
    """Drop a required golden field (name) entirely -> needs_review, not invent."""
    out = dict(rec)
    out.pop("name", None)
    return out


def mut_L8_conflicting(rec: Record, rng: random.Random) -> Record:
    """Two fields that both plausibly fill the same golden field (name/title,
    criticScore/metascore) with DIFFERENT values -> conflict territory."""
    out = dict(rec)
    out["title"] = f"{rec['name']} (Deluxe Edition)"
    out["metascore"] = max(0, int(rec["criticScore"]) - rng.randint(2, 6))
    return out


def mut_L9_opaque(rec: Record, rng: random.Random) -> Record:
    """Opaque single-letter keys with NO shared tokens -> deterministic abstain
    (the honest no-fabrication boundary; Bedrock-tier territory)."""
    return {
        "f1": rec["name"], "f2": rec["releaseYear"], "f3": rec["developer"],
        "f4": rec["publisher"], "f5": rec["platform"], "f6": rec["genres"],
        "f7": rec["criticScore"], "f8": rec["ESRB"],
    }


def mut_L10_combined(rec: Record, rng: random.Random) -> Record:
    """Everything at once: nested + renamed + typed + delimited genres +
    irrelevant fields + aliased platform/ESRB values."""
    return {
        "_meta": {"row": rng.randint(1, 9999), "scraped": "2026-03-01"},
        "movieData": {
            "gameTitle": rec["name"],
            "releaseInfo": {"yr": int(rec["releaseYear"])},
        },
        "production": {"madeBy": rec["developer"], "distributedBy": rec["publisher"]},
        "hw": _PLATFORM_ALIAS.get(rec["platform"], rec["platform"]),
        "genreCsv": ";".join(_as_list(rec["genres"])),
        "ratings": {"critic": str(rec["criticScore"])},
        "ageRating": _ESRB_ALIAS.get(rec["ESRB"], rec["ESRB"]),
        "legacy_id": f"LEG-{rng.randrange(16**4):04x}",
    }


LEVELS: dict[str, Callable[[Record, random.Random], Record]] = {
    "L1": mut_L1_renamed,
    "L2": mut_L2_renamed_typed,
    "L3": mut_L3_nested,
    "L4": mut_L4_arrays_flatten,
    "L5": mut_L5_split_merged,
    "L6": mut_L6_irrelevant,
    "L7": mut_L7_missing_required,
    "L8": mut_L8_conflicting,
    "L9": mut_L9_opaque,
    "L10": mut_L10_combined,
}


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def load_clean(source: Path | None) -> list[Record]:
    if source and source.exists():
        data = json.loads(source.read_text(encoding="utf-8"))
        # expected/golden_records.json nests records under shape keys; flatten any
        # list values. A plain list is used as-is.
        if isinstance(data, list):
            return data
        out: list[Record] = []
        for v in data.values():
            if isinstance(v, list):
                out.extend(r for r in v if isinstance(r, dict))
        return out or BUILTIN_CLEAN
    return BUILTIN_CLEAN


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help="JSON file of CLEAN golden records to mutate")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="output directory for the storm")
    ap.add_argument("--levels", nargs="*", default=list(LEVELS),
                    help="which difficulty levels to emit (default: all)")
    ap.add_argument("--copies", type=int, default=1,
                    help="files per level (same shape, different records)")
    ap.add_argument("--records", type=int, default=3,
                    help="records per file")
    ap.add_argument("--seed", type=int, default=1337, help="deterministic seed")
    args = ap.parse_args()

    bad = [lv for lv in args.levels if lv not in LEVELS]
    if bad:
        ap.error(f"unknown levels: {bad}. valid: {list(LEVELS)}")

    clean = load_clean(args.source)
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    for level in args.levels:
        mutate = LEVELS[level]
        for copy_i in range(args.copies):
            records: list[Record] = []
            for _ in range(args.records):
                base = copy.deepcopy(rng.choice(clean))
                records.append(mutate(base, rng))
            suffix = "" if args.copies == 1 else f"_{copy_i + 1:03d}"
            path = args.out / f"{level}{suffix}.json"
            path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            total += 1

    print(f"wrote {total} file(s) across {len(args.levels)} level(s) "
          f"({args.copies} copy/copies each) to {args.out}")


if __name__ == "__main__":
    main()
