"""Ad-hoc: run the page-growth loop and print per-page-count results."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import project as sc  # noqa: E402
from app.knowledge.pagegrow import build_multipage  # noqa: E402
from app.reconcile import reconcile  # noqa: E402


def main() -> None:
    base = {
        "project": sc.load_manifest("1"),
        "template": sc.load_template("1"),
        "draft": sc.load_first_attempt("1", "draft"),
        "corpus": sc.load_corpus("1"),
        "graphics": sc.load_graphics("1"),
        "corrections": sc.load_corrections("1"),
    }
    for pages in range(1, 6):
        b = build_multipage(base["project"], base["template"], base["draft"],
                            base["corpus"], base["graphics"], base["corrections"],
                            pages, seed=7)
        r = reconcile(b["draft"], b["corpus"], b["graphics"], b["corrections"],
                      b["template"], b["project"]).model_dump()
        s = r["summary"]
        gfx = sum(len(sec["graphics"]) for sec in r["sections"])
        # numbering contiguity check
        nums = [g["figure_number"] for sec in r["sections"] for g in sec["graphics"]]
        contig = nums == list(range(1, len(nums) + 1))
        summ = {k: v for k, v in s.items() if k != "total_units"}
        print(f"pages={pages} sections={len(r['sections'])} units={s['total_units']} "
              f"graphics={gfx} fig_contiguous={contig} injected={len(b['injected'])} {summ}")


if __name__ == "__main__":
    main()
