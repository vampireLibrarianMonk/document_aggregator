"""Ad-hoc: pool-backed page growth with distinct facts per page."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import project as sc  # noqa: E402
from app.knowledge.factpool import FactPool, PoolExhaustedError  # noqa: E402
from app.knowledge.pagegrow import build_multipage_from_pool  # noqa: E402
from app.reconcile import reconcile  # noqa: E402


def main() -> None:
    for sid in ["1", "2", "3", "4", "5"]:
        cap = FactPool.load(sid).capacity()
        pool = FactPool.load(sid, seed=7)
        b = build_multipage_from_pool(sc.load_manifest(sid), sc.load_template(sid),
                                      sc.load_first_attempt(sid, "draft"),
                                      sc.load_graphics(sid), pool, cap, seed=7)
        r = reconcile(b["draft"], b["corpus"], b["graphics"], b["corrections"],
                      b["template"], b["project"]).model_dump()
        sites = [rec["site"] for rec in b["records"]]
        distinct_sites = len(set(sites)) == len(sites)
        durs = {f["value"] for s in r["sections"] for f in s["fields"]
                if f["key"].split(".")[-1] == "duration" and f["value"]}
        nums = [g["figure_number"] for s in r["sections"] for g in s["graphics"]]
        contig = nums == list(range(1, len(nums) + 1))
        print(f"scen {sid} capacity={cap} sections={len(r['sections'])} "
              f"units={r['summary']['total_units']} distinct_sites={distinct_sites} "
              f"distinct_durations={len(durs)} fig_contiguous={contig}")

        # Over-capacity must be refused.
        try:
            pool2 = FactPool.load(sid, seed=7)
            build_multipage_from_pool(sc.load_manifest(sid), sc.load_template(sid),
                                      sc.load_first_attempt(sid, "draft"),
                                      sc.load_graphics(sid), pool2, cap + 1, seed=7)
            print(f"  ERROR: scen {sid} over-capacity NOT refused")
        except PoolExhaustedError:
            print(f"  scen {sid} over-capacity ({cap + 1}) refused cleanly")


if __name__ == "__main__":
    main()
