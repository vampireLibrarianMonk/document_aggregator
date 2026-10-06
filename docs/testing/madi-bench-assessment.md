# MaDI-Bench — license & air-gap assessment

**Assessed:** 2026-10-06
**Repository:** https://github.com/wbsg-uni-mannheim/MaDI-Bench
**Paper:** https://arxiv.org/abs/2606.30371 (Steiner, Peeters, Bizer, 2026)

## Why we looked at it

MaDI-Bench (the Mannheim Data Integration Benchmark) is the closest public
benchmark to the proposed JSON→golden-JSON conversion capability: it provides
heterogeneous source tables + a target schema + gold mappings across the full
integration pipeline (schema matching → value normalization → blocking → entity
matching → data fusion), with ground truth for every step. The plan was to use
it as the external benchmark for the aggregator's transformation capability.

Before adopting it we held it to the same bar that led us to reject IteraTeR and
to generate our own growth dataset: **license, air-gap fit, and domain fit.**

## Finding: cannot be vendored into this repo

**License: none declared.** The GitHub API reports `"license": null` and
`"has_downloads": false`, and the repository has **no LICENSE file** under any
name (root contents verified via the GitHub contents API: `.github`,
`.gitignore`, `CITATION.cff`, `MANIFEST.in`, `README.md`, `baselines`,
`cluster`, `knobs`, `madi_bench`, `pipelines`, `pyproject.toml`, `reproduction`,
`requirements.txt`, `results`, `tests`, `use cases`, `usecases_synthetic`,
`website` — no license file). The README states artifacts are "available for
public download" but grants no license terms.

Under default copyright, **no license means all rights reserved**: the authors
retain copyright and grant no redistribution rights. We therefore **cannot
vendor the MaDI-Bench data (or code) into this repository.**

**Third-party data provenance.** Even setting the missing license aside, the
base tasks are built from real third-party web sources, each with its own terms:
Metacritic, DBpedia, Forbes, MusicBrainz, Discogs, Last.fm, DBLP, Crossref,
OpenAlex, and others (named explicitly in the benchmark's fusion anchor-prefix
rules). Redistributing these as part of our repo would import that mixed
provenance regardless of what MaDI-Bench itself licensed.

**Air-gap / operational fit.** The benchmark's own pipelines are heavy and
network-dependent, which is antithetical to this project's offline posture:
~180 Python packages including PyTorch; several steps require an
`OPENAI_API_KEY`; the reference pipelines and variant generation assume GPUs and
SLURM. (The pure scorers need only pandas, which is light — see below.)

**Domain fit.** MaDI-Bench is *relational table* integration (multi-source
entity resolution + fusion). Our capability is *arbitrary nested JSON → canonical
nested JSON* with a reusable conversion profile. There is strong conceptual
overlap (schema matching, value normalization), but it is not a drop-in source
corpus for JSON-to-JSON.

## Decision

- **Do NOT vendor MaDI-Bench into the repository** (no license to do so, mixed
  third-party provenance).
- **Primary benchmark = self-generated**, exactly as we did for the
  precision-correction alpha loop: a deterministic, air-gap-clean, domain-matched
  JSON→golden-JSON dataset we own and can commit. This keeps the capability
  provable and reproducible offline.
- **MaDI-Bench as an OPTIONAL external comparison only.** A developer may clone
  MaDI-Bench *separately* (never committed here), point our harness at it via an
  env var / path, and compare. We may reuse its *ideas* freely — the five-domain
  structure, the eight difficulty knobs, the step-wise metrics (schema-matching
  F1, normalization accuracy, fusion all-gold accuracy) — since ideas and metric
  definitions are not copyrightable; we just can't ship its data.
- The benchmark's conceptual framing informs our metric design: borrow its
  difficulty-knob taxonomy and its "ground truth per step" discipline when
  generating our own dataset.

## What is reusable without licensing concern

- The **evaluation methodology** (step-wise + end-to-end metrics, the
  difficulty-knob idea) — concepts, freely reusable.
- The **metric definitions** (F1 over correspondences, normalization accuracy,
  all-gold fusion accuracy) — reimplement independently.
- NOT the task data, NOT the gold files, NOT the code.

This mirrors the growth-dataset decision in
[`dataset-generator.md`](dataset-generator.md): when a public dataset fails the
license / air-gap / domain bar, generate a self-owned one and keep the public
set as an optional, separately-obtained external comparison.
