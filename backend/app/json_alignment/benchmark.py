"""Benchmark harness for JSON schema-alignment.

Runs our deterministic alignment over a benchmark task and scores it with OUR
scorers (schema_match_score, normalization_score). Two sources of tasks:

  load_self_task(dir)   our committed, air-gap-clean fixture (the PRIMARY,
                        default benchmark)
  load_madi_task(path)  an OPTIONAL external MaDI-Bench task read from a locally
                        downloaded checkout (never committed). Enabled only when
                        the caller passes a path (typically from MADI_BENCH_PATH).
                        This reads MaDI's public artifact FILES with our own code
                        — no MaDI/PyDI software is imported or vendored.

A BenchmarkTask is benchmark-agnostic: records + target schema + gold
correspondences + normalization cases. `run_benchmark` executes alignment and
returns both scores.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pipeline import run_alignment
from .scoring import (
    Correspondence,
    NormalizationCase,
    NormalizationScore,
    SchemaMatchScore,
    mapping_to_correspondences,
    normalization_score,
    schema_match_score,
)
from .target_profile import extract_target
from .transforms import plan_and_apply


@dataclass
class BenchmarkTask:
    name: str
    records: list[dict[str, Any]]
    target_schema: dict[str, Any]
    gold_correspondences: set[Correspondence]
    normalization_cases: list[NormalizationCase] = field(default_factory=list)
    unmapped_source_fields: tuple[str, ...] = ()
    source_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    task: str
    schema_match: SchemaMatchScore
    normalization: NormalizationScore | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "schema_match": self.schema_match.to_dict(),
            "normalization": self.normalization.to_dict() if self.normalization else None,
        }


# --- self-generated committed task -----------------------------------------

def load_self_task(fixture_dir: str | Path) -> BenchmarkTask:
    """Load our committed fixture task from a directory of JSON files."""
    d = Path(fixture_dir)
    records = json.loads((d / "source_records.json").read_text(encoding="utf-8"))
    schema = json.loads((d / "target_schema.json").read_text(encoding="utf-8"))
    sm = json.loads((d / "schema_match_gold.json").read_text(encoding="utf-8"))
    gold = {
        (m["source_column"], m["target_column"])
        for m in sm.get("mappings", []) if m.get("label", True)
    }
    cases: list[NormalizationCase] = []
    nz_path = d / "normalization_gold.json"
    if nz_path.exists():
        nz = json.loads(nz_path.read_text(encoding="utf-8"))
        cases = [
            NormalizationCase(
                target_field=c["target_field"], raw_value=c["raw_value"],
                expected_value=c["expected_value"],
                category=c.get("category", "normalization"))
            for c in nz.get("cases", [])
        ]
    return BenchmarkTask(
        name=d.name, records=records, target_schema=schema,
        gold_correspondences=gold, normalization_cases=cases,
        unmapped_source_fields=tuple(sm.get("unmapped_source_fields", [])),
    )


# --- optional external MaDI task (never committed) --------------------------

def load_madi_task(task_input_dir: str | Path, *, dataset: str | None = None,
                   ) -> BenchmarkTask:
    """Read a locally downloaded MaDI-Bench task's public files into our shape.

    `task_input_dir` points at a MaDI '.../base/input' directory containing:
        data/<source>.csv              flat source tables
        schemamatching/target_schema.json
        schemamatching/sm_mapping_gold.json
        normalization/test.csv         (optional)

    We read these FILES with stdlib csv/json only. No MaDI/PyDI code is imported.
    If `dataset` is given, only that source table is loaded; otherwise all CSVs
    under data/ are loaded and their rows merged (fields are prefixed with the
    source name to disambiguate, matching MaDI's source_dataset.source_column)."""
    base = Path(task_input_dir)
    schema = json.loads(
        (base / "schemamatching" / "target_schema.json").read_text(encoding="utf-8"))
    smg = json.loads(
        (base / "schemamatching" / "sm_mapping_gold.json").read_text(encoding="utf-8"))

    data_dir = base / "data"
    csvs = sorted(p for p in data_dir.glob("*.csv"))
    if dataset:
        csvs = [p for p in csvs if p.stem == dataset]

    records: list[dict[str, Any]] = []
    descriptions: dict[str, str] = {}
    # Build one record per source row, keyed by "<source>.<column>" so paths are
    # unambiguous and align with MaDI's gold (source_dataset, source_column).
    for csv_path in csvs:
        src_name = csv_path.stem
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                records.append({f"{src_name}.{k}": v for k, v in row.items()})
        # MaDI ships a schema.org JSON-LD metadata file per source whose
        # variableMeasured[] gives a human description per column. That is a
        # legitimate semantic signal the benchmark itself provides.
        descriptions.update(_load_madi_descriptions(data_dir, src_name))

    gold: set[Correspondence] = set()
    for m in smg.get("mappings", []):
        if not m.get("label", True):
            continue
        sp = f"{m['source_dataset']}.{m['source_column']}"
        gold.add((sp, m["target_column"]))

    cases = _load_madi_normalization(base / "normalization" / "test.csv")
    return BenchmarkTask(
        name=base.parent.parent.name or "madi_task", records=records,
        target_schema=schema, gold_correspondences=gold,
        normalization_cases=cases, source_descriptions=descriptions,
    )


def _load_madi_descriptions(data_dir: Path, src_name: str) -> dict[str, str]:
    """Read '<src>_metadata.json' (schema.org JSON-LD) -> {<src>.<col>: desc}.

    variableMeasured[] entries are {name, description, unitText?}; we key each by
    the record path we built ('<src>.<name>')."""
    meta_path = data_dir / f"{src_name}_metadata.json"
    if not meta_path.exists():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    out: dict[str, str] = {}
    vm = meta.get("variableMeasured", [])
    if isinstance(vm, list):
        for var in vm:
            if isinstance(var, dict) and var.get("name"):
                out[f"{src_name}.{var['name']}"] = str(var.get("description", ""))
    return out


def _load_madi_normalization(test_csv: Path) -> list[NormalizationCase]:
    """Parse MaDI normalization/test.csv rows into NormalizationCase objects.

    Columns observed: entity_id, source, source_id, source_column, attribute,
    raw_value, expected_value, category, rule, context, n_instances. We use
    attribute as the target field, raw_value/expected_value as the case."""
    if not test_csv.exists():
        return []
    cases: list[NormalizationCase] = []
    with test_csv.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            cases.append(NormalizationCase(
                target_field=row.get("attribute", "") or row.get("source_column", ""),
                raw_value=row.get("raw_value"),
                expected_value=row.get("expected_value"),
                category=row.get("category", "normalization") or "normalization",
            ))
    return cases


# --- running + scoring ------------------------------------------------------

def run_benchmark(task: BenchmarkTask) -> BenchmarkResult:
    """Run alignment over a task and score schema matching + normalization."""
    res = run_alignment(task.records, task.target_schema,
                        source_descriptions=task.source_descriptions)
    predicted = mapping_to_correspondences(res.mapping)
    sm = schema_match_score(predicted, task.gold_correspondences)

    nz: NormalizationScore | None = None
    if task.normalization_cases:
        nz = _score_normalization(task.normalization_cases, task.target_schema)
    return BenchmarkResult(task=task.name, schema_match=sm, normalization=nz)


def _score_normalization(cases: list[NormalizationCase],
                         target_schema: dict[str, Any]) -> NormalizationScore:
    """Run each raw value through the transform for its target field, then score.

    Declining (fail-closed) yields None -> counted as a miss, never fabricated."""
    target = extract_target(target_schema)
    by_name = target.by_name()
    produced: list[Any] = []
    for case in cases:
        tf = by_name.get(case.target_field)
        if tf is None:
            produced.append(None)
            continue
        value, _op, ok = plan_and_apply(case.raw_value, tf)
        produced.append(value if ok else None)
    return normalization_score(cases, produced)


__all__ = [
    "BenchmarkResult",
    "BenchmarkTask",
    "load_madi_task",
    "load_self_task",
    "run_benchmark",
]
