"""CLI for the Adversarial Alpha Loop.

    # list the benign->hostile stage ladder
    python -m alpha_loop stages            (run with --app-dir backend, or from backend/)

    # step mode (a): fire ONE stage, print metrics + red-team verdicts, stop
    python -m alpha_loop step --stage 0 --n 3

    # run-to-break (b): escalate until a red-team FAIL
    python -m alpha_loop run --from 0 --n 3

Options:
    --base URL     stack base (default http://localhost:8001)
    --stage N      stage index (see `stages`)
    --from N       starting stage index for run-to-break
    --n N          records per fired shape (default 3)
    --conformity F approve bar as a fraction (default 0.90)
    --budget S     per-file wall-clock budget seconds (default 2.5)

Run from the `backend/` directory (so `alpha_loop` and the `app`/`tests`
packages are importable), with the API + a worker already running.
"""
from __future__ import annotations

import argparse
import sys

from alpha_loop.controller import Controller
from alpha_loop.generator import STAGES


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--base", default="http://localhost:8001")
    ap.add_argument("--n", type=int, default=3, help="records per fired shape")
    ap.add_argument("--conformity", type=float, default=0.90)
    ap.add_argument("--budget", type=float, default=2.5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alpha_loop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_stages = sub.add_parser("stages", help="list the benign->hostile ladder")

    p_step = sub.add_parser("step", help="fire ONE stage then stop (mode a)")
    _add_common(p_step)
    p_step.add_argument("--stage", type=int, required=True)

    p_run = sub.add_parser("run", help="escalate until a red-team FAIL (mode b)")
    _add_common(p_run)
    p_run.add_argument("--from", dest="start", type=int, default=0)

    p_wide = sub.add_parser("widesweep",
                            help="JSON SIZE stress: sweep field-width and nesting-depth, "
                                 "timing in-process infer_mapping (the hot stage)")
    p_wide.add_argument("--widths", default="8,25,50,100,200",
                        help="comma-separated golden field counts to sweep")
    p_wide.add_argument("--depths", default="0",
                        help="comma-separated source nesting depths to sweep")
    p_wide.add_argument("--records", type=int, default=3, help="records per doc")
    p_wide.add_argument("--repeats", type=int, default=5, help="timed repeats per point")

    p_scale = sub.add_parser("scale", help="length/number stress: fire N files × M records × K shapes")
    p_scale.add_argument("--base", default="http://localhost:8001")
    p_scale.add_argument("--shapes", default="2",
                         help="comma-separated stage indices to draw shapes from (default 2)")
    p_scale.add_argument("--files-per-shape", type=int, default=1)
    p_scale.add_argument("--records", type=int, default=3, help="records per file")
    p_scale.add_argument("--budget", type=float, default=2.5)
    p_scale.add_argument("--reuse", action="store_true",
                         help="reuse one project across repeated scale calls in a sweep")

    p_perf = sub.add_parser("perf", help="A/B two variants on one workload (correctness-gated)")
    p_perf.add_argument("--a", default="embed:hashing", help="variant A")
    p_perf.add_argument("--b", default="embed:sentence-transformers", help="variant B")
    p_perf.add_argument("--stage", type=int, default=2,
                        help="stage index to build the workload from (default 2: synonym)")
    p_perf.add_argument("--n", type=int, default=3, help="records in the workload")
    p_perf.add_argument("--repeats", type=int, default=5, help="timed repeats per variant")
    p_perf.add_argument("--semantic", action="store_true",
                        help="exercise the MiniLM semantic tier (needed to A/B embeddings)")
    p_perf.add_argument("--list-variants", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "perf":
        from alpha_loop.perf import VARIANTS, compare_variants
        if args.list_variants:
            print("Registered variants:")
            for name, v in sorted(VARIANTS.items()):
                print(f"  {name:32} {v.description}")
            return 0
        from alpha_loop.controller import _schema
        from alpha_loop.generator import Generator
        gen = Generator()
        doc = gen.emit_stage(args.stage, n_per_shape=args.n, round_id=0)[0]
        res = compare_variants(
            doc.records, _schema(),
            workload=f"stage{args.stage}:{doc.shape_label}",
            variant_a=args.a, variant_b=args.b, repeats=args.repeats,
            source_descriptions=doc.descriptions or None,
            use_semantic=args.semantic)
        print(res.render())
        return 0 if res.correct else 1

    if args.cmd == "widesweep":
        from alpha_loop.perf.probes import probe_workload
        from alpha_loop.wide import WideSpec, build_schema, make_wide_doc
        widths = [int(w) for w in args.widths.split(",") if w.strip()]
        depths = [int(d) for d in args.depths.split(",") if d.strip()]
        print("JSON SIZE sweep (in-process; isolates infer_mapping from job overhead)")
        print(f"{'width':>6} {'depth':>6} {'srcflds':>8} {'infer_ms':>10} "
              f"{'total_ms':>10} {'recovered':>10} {'false':>6}")
        for depth in depths:
            for width in widths:
                spec = WideSpec(width=width, depth=depth)
                schema = build_schema(spec)
                doc = make_wide_doc(spec, n_records=args.records)
                probe = probe_workload(doc.records, schema,
                                       label=spec.label, repeats=args.repeats,
                                       source_descriptions=doc.descriptions or None)
                infer_ms = probe.spans.get("infer_mapping", {}).get("total_s", 0.0) * 1000
                total_ms = probe.median_s * 1000
                # correctness: recovered = mapped to a true source path
                truth_inv = {}
                for k, g in doc.truth.items():
                    truth_inv.setdefault(g, set()).add(k.split(".")[-1].replace("[]", ""))
                recovered = sum(
                    1 for g, src in probe.mapped.items()
                    if src.split(".")[-1].replace("[]", "") in truth_inv.get(g, set()))
                false = sum(
                    1 for g, src in probe.mapped.items()
                    if src.split(".")[-1].replace("[]", "") not in truth_inv.get(g, set()))
                src_flds = width
                print(f"{width:>6} {depth:>6} {src_flds:>8} {infer_ms:>10.3f} "
                      f"{total_ms:>10.3f} {recovered:>7}/{width:<2} {false:>6}")
        return 0

    if args.cmd == "stages":
        print("Alpha-loop stage ladder (benign -> hostile):")
        for i, (name, specs) in enumerate(STAGES):
            shapes = ", ".join(s.label for s in specs)
            print(f"  [{i:2}] {name:22} {shapes}")
        return 0

    if args.cmd == "scale":
        ctl = Controller(base=args.base, per_file_budget_s=args.budget)
        try:
            ctl.ensure_project("alpha-loop scale (throwaway)")
        except Exception as e:  # noqa: BLE001
            print(f"setup failed: {e}", file=sys.stderr)
            return 2
        print(f"throwaway project: {ctl.project_id}  (base={args.base})")
        shapes = [int(s) for s in args.shapes.split(",") if s.strip() != ""]
        report = ctl.run_scale(shapes=shapes,
                               files_per_shape=args.files_per_shape,
                               records_per_file=args.records)
        print(report.render())
        return 0 if report.ok else 1

    ctl = Controller(base=args.base, approve_conformity=args.conformity,
                     per_file_budget_s=args.budget)
    try:
        ctl.ensure_project()
    except Exception as e:  # noqa: BLE001
        print(f"setup failed: {e}", file=sys.stderr)
        return 2
    print(f"throwaway project: {ctl.project_id}  (base={args.base})")

    if args.cmd == "step":
        report = ctl.run_stage(args.stage, n_per_shape=args.n)
        print(report.render())
        return 0 if report.all_pass else 1

    if args.cmd == "run":
        reports = ctl.run_to_break(start=args.start, n_per_shape=args.n,
                                   on_round=lambda r: print(r.render(), "\n"))
        broke = reports and reports[-1].broke
        print(f"\nran {len(reports)} round(s); "
              f"{'BROKE at stage ' + reports[-1].stage if broke else 'ladder exhausted, no break'}")
        return 1 if broke else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
