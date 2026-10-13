# Project 7 — JSON → golden-JSON batch (sample inputs)

This is the **JSON-alignment** sample case. Unlike the six correction projects
(1–6), which conform real documents into one corrected report, this scenario
conforms **many messy source JSON files from different teams** into one canonical
**golden** shape — the "thousands of files across teams" workload, where cost
scales with the number of distinct *shapes*, not the number of files.

In the app this is a project of kind **"Convert JSON to a golden schema"**
(chosen on the New Project screen). That kind has its own focused tab flow —
**New Project → Golden Schema → Batch Convert → Report & Export** — with none of
the correction-flow tabs. The mapping onto the correction vocabulary:

| Correction case | This JSON case |
|---|---|
| the **template** (required output shape) | `golden/target_schema.json` (the golden JSON Schema) |
| the **first-draft** documents (real, flawed inputs) | `first_draft/*.json` (the source batch to conform) |
| the corrected report | the conformed golden records, downloaded per file |

Everything here is inert sample data: the app does **not** auto-load this folder
(it has no `project.json` manifest, so the correction loaders skip it). You
create a project in the app and **upload these files**, exactly as the
walkthrough below describes.

## Contents

```
7/
  README.md                       this file
  golden/
    target_schema.json            the golden target JSON Schema (the "template")
  first_draft/                    the batch of source JSONs (the inputs to conform)
    clean_catalog.json            keys already match the golden names (baseline)
    clean_catalog_drifted.json    clean shape, but criticScore stopped being a number
    weather_feed.json             unrelated data (no games)
    L1_renamed_keys.json          renamed keys that still share tokens with golden
    L2_renamed_typed.json         + datatype changes (year int, score string, genres delimited)
    L3_nested.json                fields buried under nested objects
    L4_arrays_flatten.json        genres delimited string; platform an array to flatten
    L5_split_merged.json          name split into title+subtitle; dev+pub merged
    L6_irrelevant_fields.json     clean keys buried among bookkeeping noise
    L7_missing_required.json      no name/title at all (a required field is absent)
    L8_conflicting_candidates.json  two fields both plausibly fill name / criticScore
    L9_opaque_names.json          opaque single-letter keys (f1..f8), no signal
    L10_combined.json             everything at once (nested + renamed + typed + aliased + noise)
  expected/
    golden_records.json           reference conformed output to compare against
  generate_storm.py               deterministic storm generator (see "Scaling", below)
```

## The golden schema

`golden/target_schema.json` is the `game` schema. Its two **required** fields are
`name` and `releaseYear`; the rest (`developer`, `publisher`, `platform`,
`genres`, `criticScore`, `ESRB`) are optional. A file's **relevance** is judged
on how many *required* golden fields it can supply. The schema also carries
`x-pydi-taxonomy` alias tables on `platform` and `ESRB` (e.g. `PC → Windows PC`,
`Mature → M`) that normalize known values.

## The batch — a difficulty ladder, one shape per file

The `first_draft/*.json` files are deliberately **different shapes** spanning the
research brief's §24 malformation ladder (L1 renamed → L10 everything-at-once), so
the engine clusters them separately and routes each to its own pathway. These are
the **real, measured** outcomes (deterministic, offline, no Bedrock), run with
**Learn new shapes ON** unless noted:

| File | Difficulty | Pathway (research ON) | What it demonstrates |
|---|---|---|---|
| `clean_catalog.json` | baseline | New shape — needs approval (`novel_research`) | fully populated golden records |
| `L1_renamed_keys.json` | L1 rename | New shape — needs approval | `game_name`→`name` etc. recovered by shared tokens |
| `L2_renamed_typed.json` | L2 types | New shape — needs approval | year int→string, delimited genres split |
| `L3_nested.json` | L3 nesting | New shape — needs approval | fields pulled out of nested objects |
| `L4_arrays_flatten.json` | L4 arrays | New shape — needs approval | genres split, platform flattened |
| `L6_irrelevant_fields.json` | L6 noise | New shape — needs approval | golden keys recovered, bookkeeping ignored |
| `L8_conflicting_candidates.json` | L8 conflict | New shape — needs approval | two candidates for one field |
| `L9_opaque_names.json` | L9 opaque | New shape — needs approval, **but maps 0 fields** | the honest abstain boundary (see caveat) |
| `L5_split_merged.json` | L5 split/merge | **Needs human review** (`review`) | split name / merged studio need a derive rule the matcher won't guess |
| `L7_missing_required.json` | L7 missing | **Needs human review** | a required field (`name`) is simply absent — not fabricated |
| `L10_combined.json` | L10 everything | **Needs human review** | the hardest shape exceeds confident deterministic recovery |
| `weather_feed.json` | unrelated | New shape (ON) / **Quarantined** (OFF) | nothing of the golden grounds |
| `clean_catalog_drifted.json` | drift | **Drift — needs re-approval** (`drift_repair`) *(after approving the clean shape)* | `criticScore` stopped being numeric |

With **Learn new shapes OFF**, almost every brand-new shape lands in **Needs
human review** (a novel shape can't self-approve), and only the truly unrelated
ones (`L9_opaque`, `weather_feed`) are **Quarantined (unrelated)**
(`reject_irrelevant`).

> **The honest boundaries this ladder is built to show:**
> 1. **L5 / L7 / L10 go to review, not conversion.** Split/merge needs a derive
>    rule, missing-required has nothing to ground, and L10 piles on enough that
>    confident deterministic recovery drops out. The engine abstains rather than
>    guess — that is the no-fabrication rule, not a bug. These are exactly the
>    cases the optional Bedrock tier (or a human mapping) exists for.
> 2. **"Learn new shapes" changes what happens to unrelated files.** ON lets the
>    research path try them; OFF quarantines them outright.
> 3. **`conformed` is a weak success signal.** `L9_opaque` and `weather_feed`
>    report records "conformed" with research ON, but those are **empty golden
>    records `{}`** — an empty object validates clean against the schema, so the
>    count rewards empty-but-valid output. Nothing is fabricated (they map **zero
>    fields**), but do not read "conformed" as "usefully filled." A field-level
>    success metric is a planned refinement.

## Walkthrough

1. **New Project →** choose **"Convert JSON to a golden schema"**, name it, and
   create. The app opens the **Golden Schema** tab.
2. **Golden Schema tab:** paste `golden/target_schema.json` into **Golden target
   schema** → **Save golden schema** (confirms *"Saved: game (8 fields, 2
   required)"*). Leave the **Relevance cutoff** at **50%**.
3. **Batch Convert tab:** tick **Learn new shapes**, click **Upload JSON files**,
   select the `first_draft/*.json` files, and process.
4. Read the **Batch result** table against the table above.
5. In **Shapes awaiting your approval**, **Approve** the `clean_catalog` shape
   (and any others you want to replay). Leave `weather_feed` unapproved.
6. Re-upload `clean_catalog.json` → it now takes **Converted (known shape)**
   (`replay_clean`), zero re-inference.
7. Upload `clean_catalog_drifted.json` → **Drift — needs re-approval**
   (`drift_repair`).
8. To see a quarantine, **uncheck Learn new shapes** and process
   `weather_feed.json` → **Quarantined (unrelated)**.

The full step-by-step is in
[`docs/user-guide/projects/json-batch/README.md`](../../../docs/user-guide/projects/json-batch/README.md).

## Report & Export — per-file conformed download

Open the **Report & Export** tab. The deliverable is **golden JSON only**, offered
as a **per-file download**: each source file that produced records is listed with
its record count and shape/pathway. Each download is named

```
<original-stem><postfix>_<UTC-timestamp>.json
```

where `<postfix>` defaults to **`_conformed`** and is **editable** in the panel —
e.g. `clean_catalog_conformed_20261013T183534Z.json`. Files whose **shape** went
to review or quarantine are **not** listed as downloadable batch output (nothing
is forced into the golden shape at the cluster level).

`expected/golden_records.json` is the per-file **field-level** reference: for each
source file it shows the conformed records the deterministic engine produces.
It's instructive to read it alongside the ladder — note that even the harder
files (L5 split/merge, L7 missing-required, L10) still fill the fields they *can*
ground while **omitting** the ones that need a derive rule or have no source
(e.g. L7 fills everything but `name`; L5 omits `name` and the merged
developer/publisher). That field-level view — recovered vs omitted per golden
field — is the honest measure of conformance, finer-grained than the per-shape
pathway.

## Scaling to a storm (thousands of files)

The committed `first_draft/` set is a **representative** sample — one file per
difficulty level. To demonstrate the thousands-scale behavior (few shapes, many
files, approve-once-then-replay), use the generator:

```bash
# from this directory
python generate_storm.py --copies 50          # 50 files per level -> ./storm/
python generate_storm.py --levels L1 L4 L10    # only certain levels
python generate_storm.py --copies 200 --records 10 --seed 7
```

`generate_storm.py` is deterministic (seeded) and offline. Each difficulty level
is a **named mutation function** that transforms a clean golden record into a
malformed one, so the ground-truth outcome is mechanical (derived from the
mutation applied, never judged). This is the seam where a learned generator (a
micromodel) could later propose or replace mutations without changing the
harness. Upload a generated `storm/` on the Batch Convert tab to watch the same
few shapes cluster and replay at volume.
