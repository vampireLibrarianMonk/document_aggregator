"""Ad-hoc harness: run the generic reconciliation engine against a project
in both draft and template mode, and print a status summary per unit.

    python backend/try_reconcile.py [project_id]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import project as sc  # noqa: E402


def dump(report: dict, label: str) -> None:
    print(f"\n{'=' * 70}\n{label}  (mode={report['mode']})\n{'=' * 70}")
    print("summary:", json.dumps(report["summary"]))
    for sec in report["sections"]:
        print(f"\n[{sec['key']}] {sec['heading']}")
        for f in sec["fields"]:
            print(f"  field {f['key']:38s} {f['status']:12s} value={f['value']!r}")
            if f["candidates"]:
                print(f"        candidates: {f['candidates']}")
        for g in sec["graphics"]:
            print(f"  gfx   {g['name']:30s} fig#{g['figure_number']} {g['status']:12s} {g['note']}")
        for t in sec["tables"]:
            print(f"  table {t['title']!r} #{t['table_number']} {t['status']} cols={t['columns']} fmt={list(t['formatting'])}")
            for row in t["rows"]:
                print(f"        {row}")
    fu = report["furniture"]
    for f in fu.get("elements", []):
        print(f"  furn  {f['key']:30s} {f['status']:12s} value={f['value']!r}")
    for x in fu["cross_references"]:
        print(f"  xref  {x['key']:30s} {x['status']:12s} value={x['value']!r}")


def main() -> None:
    sid = sys.argv[1] if len(sys.argv) > 1 else sc.DEFAULT_PROJECT
    dump(sc.run_reconciliation("draft", sid), f"PROJECT {sid} — DRAFT MODE")
    dump(sc.run_reconciliation("template", sid), f"PROJECT {sid} — TEMPLATE MODE")


if __name__ == "__main__":
    main()
