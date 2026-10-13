# JSON → golden-JSON batch — walkthrough

A complete, step-by-step walkthrough of the **mass JSON→golden** capability:
turning many messy source JSON files from different teams into one canonical
"golden" shape, through the app. You will create a **JSON-alignment project**,
define a golden schema, upload files, convert the batch, approve a learned shape,
watch files of that shape convert automatically on the next run, and download the
conformed output per file.

This is the aggregation counterpart to the correction walkthroughs, but it is a
**different kind of project** with its own focused tab flow — no corpus, figures,
or reconciliation. The vocabulary maps across like this:

- the **golden target schema** is the required output shape (the "template");
- the **source JSON files** you upload are the real, inconsistent inputs to be
  conformed to it (the "first drafts").

A ready-made sample set ships in the repo at `sample_docs/project/7/`
(`golden/target_schema.json` plus a difficulty ladder of source files under
`first_draft/`, and a `generate_storm.py` to scale it up). Everything here runs
fully offline; the optional model tier is off unless an administrator enables
Bedrock.

Each step says exactly what to enter/upload and what you should see.

---

## Before you start

The golden schema used throughout is the **game** schema
(`sample_docs/project/7/golden/target_schema.json`). Its two **required** fields
are `name` and `releaseYear`; the rest (`developer`, `publisher`, `platform`,
`genres`, `criticScore`, `ESRB`) are optional. This matters because a file's
**relevance** is judged on how many *required* golden fields it can supply.

---

## Step 1 — Create a JSON-alignment project

Open the app (it starts empty on the **New Project** tab). The New Project screen
first asks **what kind of project this is** — there is no default; you choose.

- Select **"Convert JSON to a golden schema."**
- **Project name:**

  ```
  Game Catalog — JSON to Golden
  ```

- **Description:**

  ```
  Mass JSON->golden conversion. Many teams export game records in different
  shapes; conform them all to one canonical game schema. Demonstrates the batch
  pipeline: relevance cutoff, shape clustering, per-shape approval, replay, and
  quarantine of unrelated files.
  ```

Click **Create project**.

**What you should see:** the project is created and selected, and you land on the
**Golden Schema** tab. A JSON-alignment project exposes a focused flow —
**New Project → Golden Schema → Batch Convert → Report & Export** — with none of
the correction-flow tabs (no Ingestion / Correction Pipeline / Supplementals /
Search).

---

## Step 2 — Define the golden schema (Golden Schema tab)

On the **Golden Schema** tab, paste the game schema into **Golden target schema**
and click **Save golden schema**. (Copy it from
`sample_docs/project/7/golden/target_schema.json`, or paste the minimal version
below.)

> The inline schema below is a simplified subset of the committed fixture. The
> fixture additionally carries `x-pydi-taxonomy` alias tables on `platform` and
> `ESRB` (e.g. `PC → Windows PC`, `Mature → M`) that normalize known values. The
> walkthrough works with either; use the fixture if you want the alias
> normalization.

```json
{
  "title": "game",
  "type": "object",
  "properties": {
    "name": { "type": "string", "description": "The title of the game" },
    "releaseYear": { "type": "string", "pattern": "^\\d{4}$", "description": "the year the game was published" },
    "developer": { "type": "string", "description": "the studio that developed or made the game" },
    "publisher": { "type": "string", "description": "the company that published or distributed the game" },
    "platform": { "type": "string", "description": "the console or hardware system" },
    "genres": { "type": "array", "items": { "type": "string" }, "description": "list of genres" },
    "criticScore": { "type": "integer", "minimum": 0, "maximum": 100, "description": "critic press rating out of 100" },
    "ESRB": { "type": "string", "description": "ESRB age rating" }
  },
  "required": ["name", "releaseYear"]
}
```

**What you should see:** a confirmation like **"Saved: game (8 fields, 2
required)"**. The golden schema is now configured — the **Batch Convert** tab
unlocks once it is saved (you cannot convert a batch before this).

### Set the relevance cutoff

Below the schema, the **Relevance cutoff** slider decides when a file is
*unrelated*: a file is quarantined when it can map fewer than this share of the
**required** golden fields. Leave it at the default **50%**. A file where
**zero** required fields map is always rejected, whatever the slider says. The
value is saved to the project and persists for future batches.

---

## Step 3 — Prepare source files

Use the ladder under `sample_docs/project/7/first_draft/`, or make a few small
`.json` files on your machine. For a first run, three files illustrate the three
headline outcomes:

**`team_clean.json`** — keys already match the golden names:

```json
[
  { "name": "Celeste", "releaseYear": "2018", "developer": "Maddy Makes Games",
    "publisher": "Maddy Makes Games", "platform": "Windows PC",
    "genres": ["Platformer"], "criticScore": 92, "ESRB": "E10+" }
]
```

**`team_renamed.json`** — renamed keys with no shared tokens:

```json
[
  { "title": "Hades", "year": "2020", "studio": "Supergiant Games",
    "console": "Windows PC", "metascore": 93 }
]
```

**`weather.json`** — an unrelated file that has nothing to do with games:

```json
[
  { "temperature_c": 21, "humidity": 55, "wind_kph": 12 }
]
```

> Files of the **same shape** are grouped and share one decision — that is the
> whole point at scale. To see the full difficulty ladder (renamed, nested,
> split/merged, missing-required, opaque, etc.) and to generate a storm of
> hundreds of files, use `sample_docs/project/7/` and its `generate_storm.py`.

---

## Step 4 — Convert the batch (Batch Convert tab)

Open the **Batch Convert** tab. Tick **Learn new shapes**, click **Upload JSON
files**, and select your files. They are read, grouped by shape, queued, and
converted.

**What you should see** in the **Batch result** panel once the job completes
(counts assume one copy of each file):

| Pathway | Documents |
|---|---|
| New shape — needs approval (`novel_research`) | 1 |
| Needs human review (`review`) | 1 |
| (unrelated, with Learn-new-shapes ON) sent to research | 1 |

Specifically:

- **`team_clean.json`** → **New shape — needs approval**. It converted cleanly
  and a *provisional* profile was learned for its shape, waiting for approval.
- **`team_renamed.json`** → **Needs human review**. Deterministic matching could
  not recover `title → name` with no shared tokens, so it is held for a human (or
  the optional model tier). Nothing was guessed.
- **`weather.json`** → because **Learn new shapes** is on, the unrelated file is
  *sent to research* rather than quarantined outright. You will reject it at the
  approval step. (To see it quarantined immediately instead, see Step 7.)

> **A note on the "conformed" count.** The summary counts records that validated
> against the golden schema — and an *empty* golden record validates clean. An
> unrelated or opaque file can therefore show a "conformed" count while actually
> mapping **zero fields** (nothing is fabricated; the record is simply empty).
> Treat the per-shape pathway and the downloaded output as the real signal, not
> the raw conformed count.

---

## Step 5 — Approve a learned shape

Scroll to **Shapes awaiting your approval**. Each provisional shape shows the
**golden field ← source path** rows the matcher actually grounded (plus any rows
it held back as *needs review*).

Find the card for the `team_clean` shape. Because its keys already match the
golden names, the card grounds the golden fields directly from the identically
named source paths. The exact rows depend on what the deterministic matcher
grounds offline, so don't be alarmed if a row shows as *needs review* rather than
mapped — that is the engine refusing to guess, not an error. Review the mapping,
then click **Approve**.

**What you should see:** the card's shape flips to approved. Do **not** approve
the `weather` shape — leave it (an unrelated shape should never be promoted).

This is the one-time human cost: you approve a shape **once**, not every file.

---

## Step 6 — Re-run and watch it replay

Upload the same `team_clean.json` (and any copies) again and process.

**What you should see:** the `team_clean` shape now takes the **Converted (known
shape)** pathway (`replay_clean`) — it converts **with zero re-inference**,
because its mapping was approved. This is the steady state: once a team's shape is
approved, every future file of that shape conforms automatically.

| Pathway | Documents |
|---|---|
| Converted (known shape) (`replay_clean`) | all `team_clean` copies |

---

## Step 7 — See a file quarantined (unrelated input)

To watch the relevance cutoff reject an unrelated file outright, **uncheck
Learn new shapes** and process a batch containing `weather.json`.

**What you should see:** `weather.json` takes the **Quarantined (unrelated)**
pathway (`reject_irrelevant`) with the reason *"zero required golden fields could
be grounded."* No golden record is produced for it — the app never forces an
unrelated file into the golden shape.

> With **Learn new shapes** unchecked, note that `team_clean.json` (a shape you
> have **not** approved in this fresh state) lands in **Needs human review**
> rather than converting — a brand-new shape with no research and no approval
> cannot proceed on its own. Approve it (Step 5) and it converts.

---

## Step 8 — See drift pull an approved shape back (the last pathway)

So far you have exercised four of the five batch pathways: **novel_research**
(Step 4), **replay_clean** (Step 6), **review** (Step 7 note), and
**reject_irrelevant** (Step 7). The fifth is **drift_repair** — what happens when
an approved shape's *values* stop conforming even though its *keys* are unchanged.

With `team_clean` already approved (Step 5), make a file **of the same shape**
whose `criticScore` is no longer a number:

**`team_clean_drifted.json`**

```json
[
  { "name": "Hollow Knight", "releaseYear": "2017", "developer": "Team Cherry",
    "publisher": "Team Cherry", "platform": "Windows PC",
    "genres": ["Metroidvania"], "criticScore": "not available", "ESRB": "E10+" }
]
```

Tick **Learn new shapes** and process it.

**What you should see:** the file takes the **Drift — needs re-approval** pathway
(`drift_repair`), not `replay_clean`. The keys still match the approved shape, but
`criticScore` can no longer be grounded as an integer across the cluster (its fill
rate collapsed), so the engine pulls the shape back out of fast replay,
re-researches it, and surfaces it in **Shapes awaiting your approval** again — this
time as a new *version* of the profile. You re-approve the delta (Step 5), and the
shape settles back into deterministic replay.

This is the full lifecycle: *research → approve → replay → drift → re-emerge →
re-approve → replay.* The engine never silently mis-converts a drifted file; it
flags it and asks.

---

## Step 9 — Download the conformed output (Report & Export tab)

Open the **Report & Export** tab. The deliverable is **golden JSON only**, offered
as a **per-file download**: each source file that produced records is listed with
its record count and shape. Each download is named

```
<original-name><postfix>_<UTC-timestamp>.json
```

where `<postfix>` defaults to **`_conformed`** and is **editable** in the panel —
e.g. `team_clean_conformed_20261013T183534Z.json`. Files whose shape went to
review or quarantine produced no records and are **not** listed (nothing is forced
into the golden shape). Compare a download against the golden schema to confirm
the fields and formats line up.

---

## What this demonstrates

- **Golden schema + source files:** the golden schema is the target shape; your
  uploaded files are the inputs conformed to it.
- **Cost scales with shape variety, not file count:** you approve a *shape* once;
  thousands of files of that shape then replay for free.
- **Precision-first:** unrelated files are quarantined, uncertain shapes go to
  review, and nothing is ever fabricated into the golden output.
- **The relevance dial is yours:** raise it to be stricter about what counts as
  "one of ours," lower it to let more through to review.
- **All five batch pathways, exercised:** `novel_research` (Step 4),
  `replay_clean` (Step 6), `review` (Step 7), `reject_irrelevant` (Step 7), and
  `drift_repair` (Step 8) — the complete decision space the engine routes a
  cluster through.
- **Per-file conformed download** (Step 9) with an editable `_conformed` postfix
  and a UTC timestamp.
