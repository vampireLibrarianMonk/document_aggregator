"""The defect catalog.

Each Defect describes a real-world document mistake, how to inject it into a
section of a first_attempt, and the status we expect the engine to produce for
the affected unit after reconciliation. Grounded in the adversarial review plus
report-QA guidance on common report errors (numeric/transcription, terminology
drift, figure/label errors, table errors, formatting inconsistency, broken
references, sign-off gaps). Content rephrased for licensing compliance.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Defect:
    id: str
    defect_class: str  # value | graphic | table | furniture | consistency
    summary: str
    inject: Callable[[dict], None]  # mutate a section dict in place
    # After reconcile, the affected unit's status should be in this set.
    expect_status: set[str]


# ---- injectors (operate on a single section dict) ------------------------

def _blank_body(sec: dict) -> None:
    sec["body"] = ""


def _corrupt_field(sec: dict, fkey: str, bad: str) -> None:
    sec.setdefault("fields", {})[fkey] = bad


def _mislabel_first_graphic(sec: dict) -> None:
    gfx = sec.setdefault("graphics", [])
    if gfx:
        gfx[0]["ref_name"] = "wrong_" + gfx[0]["ref_name"]
    else:
        gfx.append({"ref_name": "misc_figure.png", "caption": ""})


def _drop_graphics(sec: dict) -> None:
    sec["graphics"] = []


def _clear_caption(sec: dict) -> None:
    for g in sec.get("graphics", []):
        g["caption"] = ""


def _scramble_table_columns(sec: dict) -> None:
    tbl = sec.get("table")
    if tbl and tbl.get("columns"):
        tbl["columns"] = list(reversed(tbl["columns"]))


def _drop_table_column(sec: dict) -> None:
    tbl = sec.get("table")
    if tbl and len(tbl.get("columns", [])) > 1:
        tbl["columns"] = tbl["columns"][:-1]


def _bad_table_font(sec: dict) -> None:
    tbl = sec.get("table")
    if tbl:
        tbl["font"] = "Comic Sans MS"
        tbl["header_style"] = "body-text"


def _clear_table_title(sec: dict) -> None:
    tbl = sec.get("table")
    if tbl:
        tbl["title"] = ""


# ---- catalog -------------------------------------------------------------
# Injectors are applied to whichever section the alpha loop targets; the
# expected status is what the engine should report for the affected unit.

DEFECTS: list[Defect] = [
    Defect("blank_body", "value", "section body left empty",
           _blank_body, {"filled", "corrected", "needs_review"}),
    Defect("corrupt_field", "value", "a field holds a wrong value",
           lambda s: _corrupt_field(s, next(iter(s.get("fields") or {"x": ""}), "x"), "WRONG_VALUE"),
           {"corrected", "needs_review", "conflict", "filled", "unchanged"}),
    Defect("mislabel_graphic", "graphic", "figure referenced by the wrong name",
           _mislabel_first_graphic, {"corrected", "filled"}),
    Defect("drop_graphic", "graphic", "required figure missing",
           _drop_graphics, {"filled", "corrected"}),
    Defect("clear_caption", "furniture", "figure caption removed",
           _clear_caption, {"corrected", "filled", "unchanged"}),
    Defect("scramble_columns", "table", "table columns in the wrong order",
           _scramble_table_columns, {"filled", "corrected"}),
    Defect("drop_column", "table", "a required table column missing",
           _drop_table_column, {"filled", "corrected"}),
    Defect("bad_table_font", "table", "table uses a non-conforming font/style",
           _bad_table_font, {"filled", "corrected"}),
    Defect("clear_table_title", "furniture", "table title removed",
           _clear_table_title, {"filled", "corrected"}),

    # --- expanded from report-QA research ---
    Defect("terminology_drift", "consistency",
           "same fact written differently across sections (drift)",
           lambda s: _corrupt_field(s, next(iter(s.get("fields") or {"x": ""}), "x"), "DRIFTED_TERM"),
           {"corrected", "needs_review", "filled", "unchanged", "conflict"}),
    Defect("wrong_caption", "furniture", "figure caption text is wrong",
           lambda s: [g.__setitem__("caption", "Wrong caption text") for g in s.get("graphics", [])] and None,
           {"corrected", "filled", "unchanged"}),
    Defect("wrong_table_cell", "table", "a single table cell holds a wrong value",
           lambda s: _corrupt_first_cell(s),
           {"filled", "corrected"}),
    Defect("swapped_number", "value", "a numeric field has a transposed/wrong digit",
           lambda s: _corrupt_field(s, next(iter(s.get("fields") or {"x": ""}), "x"), "9.9.9"),
           {"corrected", "needs_review", "filled", "unchanged", "conflict"}),
]


def _corrupt_first_cell(sec: dict) -> None:
    tbl = sec.get("table")
    if tbl and tbl.get("rows"):
        if tbl["rows"][0]:
            tbl["rows"][0][0] = "WRONG_CELL"
    elif tbl is not None:
        tbl.setdefault("rows", []).append(["WRONG_CELL"])


def defects_by_class() -> dict[str, list[Defect]]:
    out: dict[str, list[Defect]] = {}
    for d in DEFECTS:
        out.setdefault(d.defect_class, []).append(d)
    return out


def inject(defect: Defect, section: dict) -> None:
    """Apply a defect to a section dict in place."""
    defect.inject(section)
