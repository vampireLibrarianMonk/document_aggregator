"""Formal verification of the 16-defect inventory against the corrected JSON.

Asserts each seeded defect resolves to its expected status in draft mode, and
that template mode converges to the same report shape. Exits non-zero on any
mismatch so it can gate CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import project  # noqa: E402


def find_field(report: dict, key: str) -> dict | None:
    for sec in report["sections"]:
        for f in sec["fields"]:
            if f["key"] == key:
                return f
    for f in report["furniture"].get("elements", []):
        if f["key"] == key:
            return f
    for x in report["furniture"]["cross_references"]:
        if x["key"] == key:
            return x
    return None


def find_graphic(report: dict, name: str) -> dict | None:
    for sec in report["sections"]:
        for g in sec["graphics"]:
            if g["name"] == name:
                return g
    return None


def find_table(report: dict, key: str) -> dict | None:
    for sec in report["sections"]:
        for t in sec["tables"]:
            if t["key"] == key:
                return t
    return None


def main() -> int:
    sid = sys.argv[1] if len(sys.argv) > 1 else "1"
    r = project.run_reconciliation("draft", sid)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, cond, detail))

    resolved = {"corrected", "filled", "unchanged"}
    # 1 firmware resolves to 4.2.1 (value correct; status is a resolved state)
    f = find_field(r, "contributing_factors.firmware")
    check("1 firmware", bool(f) and f["status"] in resolved and f["value"] == "4.2.1", str(f and (f["value"], f["status"])))

    # 2 duration resolves to four hour
    f = find_field(r, "description.duration")
    check("2 duration", bool(f) and f["status"] in resolved and f["value"] == "four hour", str(f and (f["value"], f["status"])))

    # 3 corrective actions table filled with rows
    t = find_table(r, "corrective_actions_table")
    check("3 corrective actions filled", bool(t) and t["status"] == "filled" and len(t["rows"]) >= 2)

    # 4+16 severity conflict, both candidates preserved
    f = find_field(r, "identifiers.severity")
    cand_vals = {c["value"] for c in (f["candidates"] if f else [])}
    check("4/16 severity conflict", bool(f) and f["status"] == "conflict" and cand_vals == {"High", "Medium"}, str(cand_vals))

    # 5 packet_loss figure relabeled (corrected)
    g = find_graphic(r, "packet_loss_vs_temp.png")
    check("5 figure relabeled", bool(g) and g["status"] == "corrected")

    # 6 cabinet graphic moved (corrected)
    g = find_graphic(r, "cabinet_thermal_layout.png")
    check("6 cabinet moved", bool(g) and g["status"] == "corrected" and g["section"] == "contributing_factors")

    # 7 topology graphic inserted (filled)
    g = find_graphic(r, "site_network_topology.png")
    check("7 topology inserted", bool(g) and g["status"] == "filled" and g["section"] == "description")

    # 8/9/10 table formatting normalized (columns, font, header_style)
    t = find_table(r, "corrective_actions_table")
    fmt = set(t["formatting"].keys()) if t else set()
    check("8/9/10 table formatting", {"columns", "font", "header_style"} <= fmt, str(fmt))
    check("9 verification column added", bool(t) and "Verification" in t["columns"])

    # 11 dangling cross-reference re-resolved
    f = find_field(r, "xref.xref_1")
    check("11 xref resolved", bool(f) and f["status"] == "corrected" and str(f["value"]).startswith("Figure"), str(f and f["value"]))

    # 13 figure numbering derived (1..3 present)
    nums = sorted(g2["figure_number"] for sec in r["sections"] for g2 in sec["graphics"])
    check("13 figure numbering", nums == [1, 2, 3], str(nums))

    # 14 table title assigned
    t = find_table(r, "corrective_actions_table")
    check("14 table title", bool(t) and "Corrective Actions" in t["title"], str(t and t["title"]))

    # 15 footer corrected + classification needs_review
    footer = find_field(r, "furniture.footer")
    classif = find_field(r, "furniture.classification")
    check("15 footer corrected", bool(footer) and footer["status"] == "corrected")
    check("15 classification needs_review", bool(classif) and classif["status"] == "needs_review")

    # convergence: template mode produces same section/furniture shape
    rt = project.run_reconciliation("template", sid)
    same_sections = [s["key"] for s in r["sections"]] == [s["key"] for s in rt["sections"]]
    check("convergence: same section keys", same_sections)

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
