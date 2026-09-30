"""Due-diligence harness: compare hand-authored field queries vs auto-derived
queries (from field label + section key), to see how much of the manifest's
`query` we can drop while keeping extraction accurate.

Auto-query = "<label words> <section words>"  (no hand tuning).
We keep the manifest `hint` as an override in both cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import scenario as sc  # noqa: E402
from app.reconcile.extract import RetrievalExtractor  # noqa: E402


def auto_query(field: dict) -> str:
    label = field.get("label", field["key"]).replace("_", " ")
    section = field["section"].replace("_", " ")
    return f"{label} {section}"


def main() -> None:
    for sid in ["1", "2", "3", "4", "5"]:
        scen = sc.load_manifest(sid)
        corpus = sc.load_corpus(sid)
        ex = RetrievalExtractor(corpus)
        print(f"\n=== scenario {sid} ===")
        for f in scen["fields"]:
            hand = ex.field(f["query"], f.get("extract", "line"), f.get("hint"))
            auto = ex.field(auto_query(f), f.get("extract", "line"), f.get("hint"))
            hv = hand.value if hand else None
            av = auto.value if auto else None
            match = "OK " if hv == av else "DIFF"
            print(f"  [{match}] {f['key']:18s} hand={hv!r:30s} auto={av!r}")


if __name__ == "__main__":
    main()
