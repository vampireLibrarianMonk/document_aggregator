# JSON → golden-JSON batch — walkthrough

A complete, step-by-step walkthrough of the **mass JSON→golden** capability:
turning many messy source JSON files from different teams into one canonical
"golden" shape, through the app. You will set a golden schema, flip on batch
mode, upload files, process the queue, approve a learned shape, and watch files
of that shape convert automatically on the next run.

This is the aggregation analogue of the correction walkthroughs. Where the
TGX-9 guide uses the **template** and **first-draft** document pathways, this
guide uses their JSON equivalents:

- the **golden target schema** is the *template* — the required output shape;
- the **source JSON files** you upload are the *first drafts* — the real,
  inconsistent inputs to be conformed to it.

The reference material you can compare your results against lives in the repo at
`backend/tests/fixtures/json_alignment/` (`target_schema.json` and the golden
records under `variations/`). Everything here runs fully offline; the optional
model tier is off unless an administrator enables Bedrock.

Each step says exactly what to enter/upload and what you should see.

---

## Before you start

The golden schema used throughout is the committed **game** schema
(`backend/tests/fixtures/json_alignment/target_schema.json`). Its two
**required** fields are `name` and `releaseYear`; the rest (`developer`,
`publisher`, `platform`, `genres`, `criticScore`, `ESRB`) are optional. This
matters because a file's **relevance** is judged on how many *required* golden
fields it can supply.

---

## Step 1 — Create the project

Open the app (it starts empty on the **New Project** tab) and create the
project.

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

**What you should see:** the project is created and selected, and the app moves
you to the **Ingestion** tab. The Batch conversion section lives here.

---

## Step 2 — Turn on Batch mode and set the golden schema

On the **Ingestion** tab, find the **Batch conversion** panel at the top and
tick **Batch mode**. The batch controls appear.

In **Golden target schema**, paste the game schema and click **Save golden
schema**. (Copy it from
`backend/tests/fixtures/json_alignment/target_schema.json`, or paste the minimal
version below.)

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
required)"**. The golden schema is now configured for this project — batches
cannot be submitted until this is set.

---

## Step 3 — Prepare three source files

Make three small `.json` files on your machine. They stand in for three
different teams' exports plus one unrelated file.

**`team_clean.json`** — a team whose keys already match the golden names:

```json
[
  { "name": "Celeste", "releaseYear": "2018", "developer": "Maddy Makes Games",
    "publisher": "Maddy Makes Games", "platform": "Windows PC",
    "genres": ["Platformer"], "criticScore": 92, "ESRB": "E10+" }
]
```

**`team_renamed.json`** — a team that renamed the keys (no field descriptions):

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

> You can make several copies of `team_clean.json` (e.g. `team_clean_1.json`,
> `team_clean_2.json`) to see that files of the **same shape** are grouped and
> share one decision — that is the whole point at scale.

---

## Step 4 — Set the relevance cutoff

In the **Relevance cutoff** control, leave the slider at the default **50%**.

This is the dial that decides when a file is *unrelated*: a file is quarantined
when it can map fewer than this share of the **required** golden fields. The
helper text reminds you that a file where **zero** required fields map is always
rejected, whatever the slider says.

**What you should see:** the percentage label updates as you drag; the value is
saved to the project (it persists for future batches).

> Reasoning you can predict before running:
> - `team_clean.json` maps both required fields (`name`, `releaseYear`) →
>   **100%** required coverage → convertible.
> - `team_renamed.json` maps only `releaseYear` (`year`), not `name` (`title`)
>   without a model → **50%** → lands in the review band.
> - `weather.json` maps **0** required fields → always rejected.

---

## Step 5 — Process the batch (learning new shapes)

Tick **Learn new shapes**, then click **Upload JSON files** and select all three
(plus any copies). The files are read, grouped by shape, queued, and processed.

**What you should see** in the **Batch result** panel once the job completes
(counts assume one copy of each file):

| Pathway | Documents |
|---|---|
| New shape — needs approval (`novel_research`) | 2 |
| Needs human review (`review`) | 1 |

and a summary noting records conformed to the golden schema. Specifically:

- **`team_clean.json`** → **New shape — needs approval**. It converted cleanly
  (all 8 golden fields mapped, including the array `genres`), and a *provisional*
  profile was learned for its shape. It is waiting for your approval.
- **`team_renamed.json`** → **Needs human review**. Deterministic matching could
  not recover `title → name` from the name alone, so it is held for a human (or
  the optional model tier). Nothing was guessed.
- **`weather.json`** → because **Learn new shapes** is on, the unrelated file is
  *sent to research* rather than quarantined outright (the research path is
  allowed to try). You will reject it at the approval step. (To see it quarantined
  immediately instead, see Step 8.)

---

## Step 6 — Approve a learned shape

Scroll to **Shapes awaiting your approval**. Each provisional shape shows its
proposed **golden field ← source path** mapping.

Find the card for the `team_clean` shape. Its mapping should read:

```
name        ← name
releaseYear ← releaseYear
developer   ← developer
publisher   ← publisher
platform    ← platform
genres      ← genres[]
criticScore ← criticScore
ESRB        ← ESRB
```

Click **Approve**.

**What you should see:** the card's shape flips to approved. Do **not** approve
the `weather` shape — leave it (an unrelated shape should never be promoted).

This is the one-time human cost: you approve a shape **once**, not every file.

---

## Step 7 — Re-run and watch it replay

Upload the same `team_clean.json` (and its copies) again and process.

**What you should see:** the `team_clean` shape now takes the **Converted (known
shape)** pathway (`replay_clean`) — it converts **with zero re-inference**,
instantly, because its mapping was approved. This is the steady state: once a
team's shape is approved, every future file of that shape conforms automatically.

| Pathway | Documents |
|---|---|
| Converted (known shape) (`replay_clean`) | all `team_clean` copies |

---

## Step 8 — See a file quarantined (unrelated input)

To watch the relevance cutoff reject an unrelated file outright, **uncheck
Learn new shapes** and process a batch containing `weather.json`.

**What you should see:** `weather.json` takes the **Quarantined (unrelated)**
pathway (`reject_irrelevant`) with the reason *"zero required golden fields could
be grounded."* No golden record is produced for it — the app never forces an
unrelated file into the golden shape.

> With **Learn new shapes** unchecked, note that `team_clean.json` (a shape you
> have **not** approved in this fresh state) lands in **Needs human review**
> rather than converting — a brand-new shape with no research and no approval
> cannot proceed on its own. Approve it (Step 6) and it converts.

---

## Step 9 — See drift pull an approved shape back (the last pathway)

So far you have exercised four of the five batch pathways: **novel_research**
(Step 5), **replay_clean** (Step 7), **review** (Step 8 note), and
**reject_irrelevant** (Step 8). The fifth is **drift_repair** — what happens
when an approved shape's *values* stop conforming even though its *keys* are
unchanged.

With `team_clean` already approved (Step 6), make a file **of the same shape**
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

**What you should see:** the file takes the **Drift — needs re-approval**
pathway (`drift_repair`), not `replay_clean`. The keys still match the approved
shape, but `criticScore` can no longer be grounded as an integer across the
cluster (its fill rate collapsed), so the engine pulls the shape back out of
fast replay, re-researches it, and surfaces it in **Shapes awaiting your
approval** again — this time as a new *version* of the profile. You re-approve
the delta (Step 6), and the shape settles back into deterministic replay.

This is the full lifecycle: *research → approve → replay → drift → re-emerge →
re-approve → replay.* The engine never silently mis-converts a drifted file; it
flags it and asks.

---

## What this demonstrates

- **Template + first-draft, in JSON terms:** the golden schema is the template;
  your uploaded files are the first drafts conformed to it.
- **Cost scales with shape variety, not file count:** you approve a *shape*
  once; thousands of files of that shape then replay for free.
- **Precision-first:** unrelated files are quarantined, uncertain shapes go to
  review, and nothing is ever fabricated into the golden output.
- **The relevance dial is yours:** raise it to be stricter about what counts as
  "one of ours," lower it to let more through to review.
- **All five batch pathways, exercised:** `novel_research` (Step 5),
  `replay_clean` (Step 7), `review` (Step 8), `reject_irrelevant` (Step 8), and
  `drift_repair` (Step 9) — the complete decision space the engine routes a
  cluster through.

Compare any produced record against the golden schema and the reference records
in `backend/tests/fixtures/json_alignment/variations/golden_records.json` to
confirm the fields and formats line up.
