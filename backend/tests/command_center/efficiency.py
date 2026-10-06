"""Task 1 — model-efficiency precursor study.

Goal: find the cheapest/fastest reliable way to use an approved Bedrock model
for the command center's hardest model sub-task — turning raw reviewer emails
into grounded, structured correction operations. We compare prompting SHAPES on
the same inputs (sample project 1, whose correct corrections are known) and
measure calls / input+output tokens / latency / accuracy.

Shapes compared:
  per_email   one Converse call per email               (N calls)
  batched     one call with ALL emails at once          (1 call)
  whole_doc   one call with corpus + draft + all emails  (1 call, biggest prompt)

Accuracy = how many of the known target corrections the shape recovers
(firmware->4.2.1, duration->four hour, severity High, severity Medium, figure
relabel). All calls use temperature 0 and are cached by input hash.

Run from backend/:  python -m tests.command_center.efficiency
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.bakeoff.harness import load_raw_inputs
from tests.command_center.modelcall import CallStats, ModelClient, default_model, extract_json

# The known correction signal for project 1 (what a correct parse should find).
EXPECTED = {
    "firmware": "4.2.1",
    "duration": "four",            # 'four hour'
    "severity_high": "high",
    "severity_med": "medium",
    "figure": "packet_loss_vs_temp.png",
}

_SYSTEM = (
    "You convert reviewer feedback about an incident report into structured "
    "correction operations. Return ONLY compact JSON: an array of objects, each "
    '{"target": "<section.field>", "operation": "replace|relabel_graphic", '
    '"new_value": "<value stated in the feedback>", "reason": "<short>"}. '
    "Use ONLY values explicitly stated in the feedback. Never invent a value. "
    "Valid targets include contributing_factors.firmware, description.duration, "
    "identifiers.severity, timeline.graphic."
)


def _email_body(raw: str) -> str:
    return re.sub(r"^(From|Subject):.*$", "", raw, flags=re.MULTILINE).strip()


def _score_ops(ops: list) -> dict:
    """How many expected signals the ops recovered + whether anything ungrounded."""
    blob = json.dumps(ops).lower()
    found = {
        "firmware": "4.2.1" in blob,
        "duration": "four" in blob and "hour" in blob,
        "severity_high": "high" in blob,
        "severity_med": "medium" in blob,
        "figure": "packet_loss_vs_temp" in blob,
    }
    return {"recovered": sum(found.values()), "of": len(found), "detail": found}


def _run_per_email(mc: ModelClient, emails: list[str]) -> list:
    ops: list = []
    for raw in emails:
        text = mc.complete(_SYSTEM, _email_body(raw), max_tokens=512)
        if text is None:
            return []
        got = extract_json(text)
        if isinstance(got, list):
            ops.extend(got)
        elif isinstance(got, dict):
            ops.append(got)
    return ops


def _run_batched(mc: ModelClient, emails: list[str]) -> list:
    joined = "\n\n---\n\n".join(f"EMAIL {i+1}:\n{_email_body(e)}"
                                for i, e in enumerate(emails))
    user = ("Parse ALL of the following reviewer emails into one combined JSON "
            "array of correction operations (one or more per email):\n\n" + joined)
    text = mc.complete(_SYSTEM, user, max_tokens=1024)
    got = extract_json(text or "")
    return got if isinstance(got, list) else ([got] if isinstance(got, dict) else [])


def _run_whole_doc(mc: ModelClient, raw) -> list:
    emails = "\n\n---\n\n".join(_email_body(e) for e in raw.corrections_emails)
    corpus = raw.corpus_blob[:4000]
    user = (f"SOURCE CORPUS (ground truth):\n{corpus}\n\n"
            f"REVIEWER EMAILS:\n{emails}\n\n"
            "Return one JSON array of correction operations grounded in the "
            "emails (values must be stated in the emails).")
    text = mc.complete(_SYSTEM, user, max_tokens=1024)
    got = extract_json(text or "")
    return got if isinstance(got, list) else ([got] if isinstance(got, dict) else [])


def main() -> int:
    model = default_model()
    raw = load_raw_inputs("1")
    results = []
    for shape, fn in (("per_email", lambda mc: _run_per_email(mc, raw.corrections_emails)),
                      ("batched", lambda mc: _run_batched(mc, raw.corrections_emails)),
                      ("whole_doc", lambda mc: _run_whole_doc(mc, raw))):
        mc = ModelClient(model_id=model, stats=CallStats())
        ops = fn(mc)
        score = _score_ops(ops)
        results.append({
            "shape": shape,
            "model": model,
            "available": mc.available(),
            "ops_count": len(ops),
            "accuracy": score,
            "stats": mc.stats.as_dict(),
        })

    out = {"model": model, "results": results}
    (Path(__file__).parent / "efficiency_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    # Also print a compact table (captured to a log by the caller).
    print(f"model={model}")
    for r in results:
        s, a, st = r["shape"], r["accuracy"], r["stats"]
        print(f"  {s:<10} ops={r['ops_count']:<3} recovered={a['recovered']}/{a['of']} "
              f"calls={st['calls']} in={st['input_tokens']} out={st['output_tokens']} "
              f"lat={st['latency_ms']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
