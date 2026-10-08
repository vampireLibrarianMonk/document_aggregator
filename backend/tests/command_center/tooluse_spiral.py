r"""Adversarial spiral-out review of the PRODUCTION Bedrock tool-use path.

The model sweep (model_sweep.py) exercises the correction agent's TEXT path
(JSON-in-free-text). It does NOT touch the production `BEDROCK_ENABLED` tiers,
which use forced Converse tool-use (`BedrockInterpreter`, and the JSON-alignment
`BedrockSemanticProposer`). This harness is the tool-use counterpart, built as a
small -> big ladder so a per-model failure is isolated at the cheapest rung
before escalating:

    Rung 1  forced tool-use works?      one minimal converse with our EXACT
                                        toolConfig + forced toolChoice. Did a
                                        toolUse block come back? was the name
                                        decorated (harmony channel)? did the
                                        model ERROR on forced choice (some
                                        families only support auto)?
    Rung 2  tool input parses+validates one real feedback email -> the model's
                                        tool input is run through the SAME
                                        validate_op + target-resolvability the
                                        production path uses, plus a check for
                                        argument corruption (e.g. a leaked
                                        "<parameter=...>" framing inside a value).
    Rung 3  one email, one project      the real interpret_feedback() end to end
                                        on a single sample email -> accepted /
                                        rejected op counts (production yield).
    Rung 4  full project, scored        the full correction reconcile driven
                                        through the production tool-use
                                        interpreter, scored vs gold (value %,
                                        status %, fabrications, conflict kept).

GATING: a model that fails rung N is recorded and SKIPPED for N+1 (no wasted
tokens). Each per-model record carries ran / passed / failure_reason / fix_hint.

ON-DEMAND, NOT CI. Needs Bedrock reachable with credentials. Every rung is a
deliberate token spend; rungs 1-2 are tiny (1 call/model), 3-4 cost more.

Run (from backend/):
    $env:BEDROCK_ENABLED="true"
    $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
    $env:DATA_DIR="$env:TEMP\spiral"
    ..\.venv\Scripts\python.exe -m tests.command_center.tooluse_spiral --rung 2
    #   --rung N      run rungs 1..N (default 2, the cheap diagnostic pair)
    #   --models a,b  restrict to a subset (substring-matched to the catalog)

Writes tooluse_spiral_results.json next to this file.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings
from app.corrections.interpreter import _valid_targets, interpret_feedback
from app.corrections.refine import _interp_context
from app.corrections.schema import sanitize_tool_name, tool_spec, validate_op
from app.projectgen.bedrock_gen import list_approved_models

RESULTS_PATH = Path(__file__).with_name("tooluse_spiral_results.json")

# A tiny, deterministic feedback string for rungs 1-2 (we only need the model to
# emit SOME well-formed tool call against real targets; grounded content comes
# from the real sample emails at rung 3+).
_PROBE_FEEDBACK = (
    "The firmware version is wrong; it should be 4.2.1, not 4.2.0. "
    "The outage duration should be four hour. Severity needs a human to decide."
)
# Argument-corruption signature from the sibling project's Nemotron finding: a
# tool/parameter framing token leaking INSIDE an argument value (well-formed
# JSON, poisoned content). Detected on the model's tool input values.
_ARG_CORRUPTION = re.compile(r"<\s*/?\s*parameter\b|<\|", re.IGNORECASE)


@dataclass
class RungResult:
    rung: int
    ran: bool = False
    passed: bool = False
    failure_reason: str = ""
    fix_hint: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class ModelResult:
    model_id: str
    reachable: bool = False
    rungs: list[RungResult] = field(default_factory=list)


# --------------------------------------------------------------------------
# A thin tool-use probe client. ModelClient (modelcall.py) is TEXT-only, so the
# tool-use rungs need a direct Converse call with our real production toolConfig.
# Bounded timeout mirrors ModelClient so a non-tool-use model fails fast.
# --------------------------------------------------------------------------
class _ToolUseProbe:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._client = None

    def connect(self) -> bool:
        try:
            import boto3
            from botocore.config import Config

            cfg = Config(read_timeout=120, connect_timeout=10,
                         retries={"max_attempts": 2, "mode": "standard"})
            self._client = boto3.client("bedrock-runtime",
                                        region_name=settings.BEDROCK_REGION, config=cfg)
            return True
        except Exception:
            return False

    def call(self, feedback: str, targets: list[str]) -> dict:
        """Issue the EXACT production tool-use request. Returns the raw Converse
        response dict, or {'_error': ...} on failure (incl. forced-choice reject)."""
        system = (
            "You convert reviewer feedback about a report into structured "
            "correction operations. Only propose operations from the provided "
            "tool. Only use values explicitly stated in the feedback. If a value "
            "is not stated, use flag_needs_review. Never invent values. "
            "Valid targets: " + ", ".join(targets)
        )
        try:
            return self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": feedback}]}],
                toolConfig={
                    "tools": [tool_spec()],
                    "toolChoice": {"tool": {"name": "propose_corrections"}},
                },
                inferenceConfig={"maxTokens": 1024, "temperature": 0},
            )
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# --------------------------------------------------------------------------
# Rung logic. Each returns a RungResult. Pure given a response, so the parsing
# rungs are unit-testable with synthetic responses (no Bedrock).
# --------------------------------------------------------------------------
def rung1_from_response(resp: dict) -> RungResult:
    """Did forced tool-use produce a usable toolUse block?"""
    r = RungResult(rung=1, ran=True)
    if "_error" in resp:
        err = resp["_error"]
        r.failure_reason = err
        r.fix_hint = (
            "model likely rejects forced toolChoice over Converse; try "
            "toolChoice='auto' or treat as a text-path-only model"
            if "tool" in err.lower() or "choice" in err.lower()
            else "converse call failed (see failure_reason)"
        )
        return r
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    tool = next((b.get("toolUse") for b in blocks if b.get("toolUse")), None)
    if not tool:
        r.failure_reason = "no toolUse block in response (model answered in prose)"
        r.fix_hint = "model does not honor forced tool-use; use the text path for it"
        return r
    raw_name = tool.get("name")
    clean = sanitize_tool_name(raw_name)
    decorated = raw_name != clean
    r.detail = {"raw_tool_name": raw_name, "sanitized": clean, "decorated": decorated}
    if clean != "propose_corrections":
        r.failure_reason = f"tool name '{raw_name}' != expected after sanitize"
        r.fix_hint = "extend sanitize_tool_name() for this decoration pattern"
        return r
    r.passed = True
    if decorated:
        r.fix_hint = ("name was harmony-decorated but sanitize_tool_name handled "
                      "it — confirms the fix matters for this model")
    return r


def rung2_from_response(resp: dict) -> RungResult:
    """Does the model's tool INPUT parse, validate, and stay un-corrupted?"""
    r = RungResult(rung=2, ran=True)
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    tool = next((b.get("toolUse") for b in blocks if b.get("toolUse")), None)
    if not tool:
        r.failure_reason = "no toolUse block (rung 1 should have caught this)"
        return r
    payload = tool.get("input", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as exc:  # noqa: BLE001
            r.failure_reason = f"tool input is a string that is not JSON: {exc}"
            r.fix_hint = "model emitted non-JSON tool input; needs a repair/retry"
            return r
    ops = payload.get("operations", []) if isinstance(payload, dict) else []
    if not ops:
        r.failure_reason = "tool input carried zero operations"
        r.fix_hint = "model produced an empty op list; low yield, not a safety issue"
        return r
    corrupted = []
    valid = 0
    for i, op in enumerate(ops):
        # Argument-corruption check (sibling-project Nemotron finding): framing
        # tokens leaking into a value make well-formed JSON with poisoned content.
        for v in (op or {}).values():
            if isinstance(v, str) and _ARG_CORRUPTION.search(v):
                corrupted.append(v[:60])
        # Mirror production: interpret_feedback assigns an `id` before validating
        # (the tool schema never asks the model for one), so stamp a synthetic id
        # here or validate_op would reject EVERY raw model op as missing `id`.
        probe_op = {"id": op.get("id", f"spiral_{i}"), **op} if isinstance(op, dict) else op
        ok, _reason = validate_op(probe_op)
        if ok:
            valid += 1
    r.detail = {"ops": len(ops), "valid_ops": valid, "corrupted_values": corrupted}
    if corrupted:
        r.failure_reason = f"argument corruption in {len(corrupted)} value(s)"
        r.fix_hint = ("sanitize tool-input values (strip <parameter=...> / <|...| "
                      "framing) before validate_op, not just the envelope")
        return r
    if valid == 0:
        r.failure_reason = "no operation passed validate_op"
        r.fix_hint = "model proposed only malformed ops; schema-repair prompt or skip"
        return r
    r.passed = True
    return r


# --------------------------------------------------------------------------
# The per-model gated ladder (live). Rungs 3-4 reuse the production interpreter
# + the sweep's gold scorer directly.
# --------------------------------------------------------------------------
def _targets_for(project_id: str) -> tuple[list[str], dict]:
    from app import project as sc

    manifest = sc.load_manifest(project_id)
    template = sc.load_template(project_id)
    ctx = _interp_context(manifest, template)
    return _valid_targets(ctx), ctx


def run_model(model_id: str, max_rung: int) -> ModelResult:
    mr = ModelResult(model_id=model_id)
    probe = _ToolUseProbe(model_id)
    mr.reachable = probe.connect()
    if not mr.reachable:
        mr.rungs.append(RungResult(rung=1, failure_reason="bedrock-runtime client could not be built"))
        return mr

    targets, _ctx = _targets_for("1")

    # Rung 1
    resp = probe.call(_PROBE_FEEDBACK, targets)
    r1 = rung1_from_response(resp)
    mr.rungs.append(r1)
    if not r1.passed or max_rung < 2:
        return mr

    # Rung 2 (reuse the SAME response — no second call needed)
    r2 = rung2_from_response(resp)
    mr.rungs.append(r2)
    if not r2.passed or max_rung < 3:
        return mr

    # Rung 3: one real sample email through the production interpreter.
    from tests.bakeoff.harness import load_raw_inputs

    emails = load_raw_inputs("1").corrections_emails
    r3 = RungResult(rung=3, ran=True)
    try:
        settings.BEDROCK_MODEL = model_id  # the interpreter reads this
        ctx = _targets_for("1")[1]
        res = interpret_feedback(emails[0] if emails else _PROBE_FEEDBACK, ctx,
                                 round_index=0, author="spiral")
        r3.detail = {"interpreter": res.get("interpreter"),
                     "accepted": len(res.get("accepted", [])),
                     "rejected": len(res.get("rejected", []))}
        r3.passed = res.get("interpreter") == "bedrock"
        if not r3.passed:
            r3.failure_reason = "interpreter fell back off the bedrock path"
            r3.fix_hint = "model/credentials not driving the tool-use interpreter"
    except Exception as exc:  # noqa: BLE001
        r3.failure_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
    mr.rungs.append(r3)
    if not r3.passed or max_rung < 4:
        return mr

    # Rung 4: full project through the production tool-use path, scored vs gold.
    r4 = RungResult(rung=4, ran=True)
    try:
        from app import project as sc

        from tests.bakeoff.harness import load_raw_inputs as _lri
        from tests.bakeoff.harness import score_against_gold

        report = sc.run_reconciliation("draft", "1", "json", engine="orchestrator")
        sc_score = score_against_gold("1", f"tooluse/{model_id}", report, _lri("1"))
        r4.detail = {"value_pct": round(sc_score.value_pct, 1),
                     "status_pct": round(sc_score.status_pct, 1),
                     "fabrications": sc_score.fabrications,
                     "conflict_preserved": sc_score.conflict_preserved}
        r4.passed = sc_score.fabrications == 0
        if not r4.passed:
            r4.failure_reason = f"{sc_score.fabrications} fabrication(s)"
            r4.fix_hint = "SAFETY: a value escaped the grounding gate — investigate"
    except Exception as exc:  # noqa: BLE001
        r4.failure_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
    mr.rungs.append(r4)
    return mr


def _resolve_models(requested: list[str] | None) -> list[str]:
    info = list_approved_models()
    catalog = [m["id"] for m in info.get("models", [])]
    if not requested:
        return catalog
    out = []
    for want in requested:
        w = want.strip().lower()
        hit = next((c for c in catalog if w in c.lower()), None)
        out.append(hit or want.strip())
    return out


def spiral(model_ids: list[str] | None = None, max_rung: int = 2) -> dict:
    ids = _resolve_models(model_ids)
    results = [run_model(mid, max_rung) for mid in ids]
    return {
        "max_rung": max_rung,
        "ladder": {
            1: "forced tool-use returns a usable toolUse block",
            2: "tool input parses + validates + uncorrupted",
            3: "one real email through the production interpreter",
            4: "full project through tool-use path, scored vs gold",
        },
        "models": [asdict(r) for r in results],
    }


def _print(report: dict) -> None:
    print(f"=== Tool-use spiral (rungs 1..{report['max_rung']}) ===")
    for m in report["models"]:
        last = m["rungs"][-1] if m["rungs"] else None
        reached = last["rung"] if last else 0
        verdict = "pass" if (last and last["passed"]) else "STOP"
        print(f"{m['model_id']:<36} reached rung {reached} [{verdict}]"
              + (f"  {last['failure_reason']}" if last and last["failure_reason"] else ""))
        for rr in m["rungs"]:
            flag = "ok " if rr["passed"] else ("--" if not rr["ran"] else "XX")
            print(f"    rung {rr['rung']} [{flag}] {rr.get('failure_reason','') or rr.get('fix_hint','')}")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    max_rung = 2
    models: list[str] | None = None
    if "--rung" in argv:
        i = argv.index("--rung")
        if i + 1 < len(argv):
            max_rung = max(1, min(4, int(argv[i + 1])))
    if "--models" in argv:
        i = argv.index("--models")
        if i + 1 < len(argv):
            models = [s for s in argv[i + 1].split(",") if s.strip()]
    t0 = time.time()
    report = spiral(models, max_rung)
    report["elapsed_s"] = round(time.time() - t0, 1)
    _print(report)
    RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
