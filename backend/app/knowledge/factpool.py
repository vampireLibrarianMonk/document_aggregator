"""Withdrawable fact pool.

A fact pool is a compact store of real, source-cited records (seeded once from
public-domain reports, committed as data, read offline). Each record funds one
generated page of genuinely DISTINCT ground truth. Withdrawal is destructive:
drawing a record removes it, so no two pages share facts, and the pool's
capacity caps how many pages can be generated (page limit = source capacity).
"""
from __future__ import annotations

import json
import random

from ..config import settings

SCEN = settings.SCENARIO_ROOT


class PoolExhaustedError(Exception):
    """Raised when more pages are requested than the pool can fund."""


class FactPool:
    def __init__(self, records: list[dict], sources: list[str], seed: int = 0) -> None:
        self._remaining = list(records)
        self.sources = sources
        self._rng = random.Random(seed)
        self._rng.shuffle(self._remaining)

    @classmethod
    def load(cls, scenario_id: str, seed: int = 0) -> FactPool:
        path = SCEN / scenario_id / "corpus" / "fact_pool.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["records"], data.get("sources", []), seed)

    def capacity(self) -> int:
        return len(self._remaining)

    def withdraw(self) -> dict:
        """Remove and return one record. Raises PoolExhaustedError when empty."""
        if not self._remaining:
            raise PoolExhaustedError("fact pool is empty; cannot fund another page")
        return self._remaining.pop()

    def withdraw_n(self, n: int) -> list[dict]:
        if n > self.capacity():
            raise PoolExhaustedError(
                f"requested {n} pages but pool funds only {self.capacity()}"
            )
        return [self.withdraw() for _ in range(n)]


def record_to_corpus_doc(record: dict, page: int) -> tuple[str, str]:
    """Render one withdrawn record into a per-page corpus document (filename,
    text) in the same shape the extractor understands (labelled summary lines +
    an assignments block), so each page's facts are independently retrievable."""
    fname = f"page_{page}_source.txt"
    text = f"""INCIDENT SOURCE RECORD (page {page})

Date: {record['incident_date']}
System: {record['site']}

Summary
{record['description_body']}

Observations
{record['timeline_body']}

Findings
Software {record['software_version']} was running on the affected unit.
Units upgraded to {record.get('fix_version', 'the fixed version')} were not affected.

Preliminary Assessment
{record['contributing_body']} The outage spanned a {record['duration']} window.

Corrective Action Assignments
- Action: {record['action']} Owner: {record['owner']}. Due: {record['due']}.
"""
    return fname, text
