# User Guide

This guide is for people **using** the platform, not developers. It walks you
through every demo project, tells you where input and output documents live, and
describes exactly what you should see on each tab of the GUI.

It runs fully offline. Nothing leaves your machine or your enclave.

## Opening the app

Once the stack is running (see the README or your administrator), open:

```
http://localhost:8080
```

At the top you pick a **Project** from one dropdown. That one choice scopes all
five tabs. Below it are the five tabs:

1. **Correction Pipeline**
2. **Ingestion**
3. **Supplementals**
4. **Search**
5. **Report & Export**

---

## The one thing to understand first: two kinds of project

Everything in the app is a "project," but a project is populated for the tabs
that match the kind of work it holds. **No single demo project fills all five
tabs at once.** If a tab looks empty, you have almost certainly selected a
project of the other kind. This is expected, not a bug.

| Kind | What it holds | Which tabs show data | Which tabs look empty |
|------|---------------|----------------------|-----------------------|
| **Correction case** (demos `1`–`6`) | A corpus, a first-attempt report, and reviewer comments | **Correction Pipeline** | Ingestion, Supplementals, Search, Report & Export |
| **Aggregation project** (`proj_demo`) | Uploaded source documents, supplementals, a search index | **Ingestion, Supplementals, Search, Report & Export** | Correction Pipeline |

So:

- Pick a **correction case** (e.g. "TGX-9 Telemetry Gateway Incident") to see
  the **Correction Pipeline** work. Its Ingestion/Search/Report tabs will be
  empty, because a correction case does not ingest its own documents.
- Pick the **aggregation project** ("TGX-9 Incident Aggregation", id
  `proj_demo`) to see **Ingestion, Supplementals, Search, and Report & Export**
  populated. Its Correction Pipeline tab will say "no correction-pipeline data,"
  because an aggregation project has no first-attempt report to correct.

When you make your own project, you choose which kind you build:
**upload documents on the Ingestion tab** to grow an aggregation project, or use
**+ New project** on the Correction Pipeline tab to generate a correction case.

---

## Where documents live on disk

You rarely need this, but when you want to verify what the app read or wrote,
here are the on-disk locations. Paths are inside the running container (or under
the repo's `data/` and `sample_docs/` folders when run locally).

### Correction cases (demos 1–6) — read-only inputs

```
sample_docs/project/<id>/
  project.json                                   manifest (title, domain, fields, table spec)
  corpus/*.txt | *.md                            source documents (ground truth)
  corpus/graphics.json                           named figures/graphics
  first_attempt/incident_report_draft.json       the flawed draft (Draft mode input)
  first_attempt/incident_report_template.json    the blank rubric (Template mode input)
  first_attempt/generated/<mode>.<docx|pptx|pdf> pre-converted copies for the source-fidelity selector
  corrections/comments.json                      single-pass reviewer feedback
  corrections/rounds.json                        multi-round feedback (for the Rounds view)
```

These are inputs only. The **output** (the corrected report) is computed on the
fly and shown in the GUI; you save it from **Report & Export** on a project that
produces one.

### Aggregation projects (e.g. `proj_demo`) — your uploads and outputs

```
data/projects/<id>/
  project.json                                   project record (id, name)
  source/<doc_id>__<original_filename>           your uploaded file, byte-for-byte
  documents/<doc_id>.record.json                 pipeline status + stage history
  documents/<doc_id>.canonical.json              the normalized structure the tool extracted (output)
  supplementals/<supp_id>.json                   one file per comment/email you add
  index/manifest.json | vectors.json | chunks.json   the search index (output)
```

A project you **generate** with "+ New project" lands at
`data/projects/<id>/data/` and has the correction-case layout (corpus,
first_attempt, corrections) nested one level deeper.

---

## Guided tour of the demo projects

The stack ships with seven projects. Select each one from the top dropdown and
you should see the following.

### "TGX-9 Incident Aggregation" (`proj_demo`) — the aggregation demo

This is the project that exercises the aggregation half of the app. Select it,
then:

- **Ingestion** — a table of **7 documents** (a field report, executive summary,
  corrective action plan, root-cause notes, style guide, an incident-review
  slide deck, and a packet-loss figure). Each row shows its pipeline stages
  (ingest → parse → chunk → embed → index) completed. Click **Canonical** on a
  row to inspect the structure extracted from that file.
- **Supplementals** — **5 items** already attached (comments/emails), each with
  an author, subject, and a sentiment tag.
- **Search** — type a term that appears in the sources, e.g. `telemetry`,
  `packet loss`, or `firmware`, and press Enter. You should get several hits,
  each quoting the matched passage with its document and section, ordered by
  relevance.
- **Report & Export** — the assembled report with **7 sections**, one per
  completed document, ordered chronologically by effective date/time. Use the
  export buttons to download it as JSON, Markdown, DOCX, PPTX, or PDF.
- **Correction Pipeline** — shows "This project has no correction-pipeline
  data … aggregation-only project." That is correct for this project; its work
  lives on the four tabs above.

### Correction cases 1–6 — the correction-pipeline demos

| id | Title | Domain |
|----|-------|--------|
| 1 | TGX-9 Telemetry Gateway Incident | hardware reliability / incident response |
| 2 | Customer Portal Credential-Stuffing Incident | IT security / incident response |
| 3 | Clinical Lab Reagent Spill Safety Event | clinical laboratory / safety |
| 4 | Injection Molding Line Defect Event | manufacturing quality / defect analysis |
| 5 | Aircraft Hydraulic Decay Maintenance Event | aviation maintenance / reliability |
| 6 | Nav Bus Interface Control Document | systems engineering / interface control |

Select any of these, then open the **Correction Pipeline** tab. You should see:

- Four flow boxes left to right with counts: **Original corpus** (5 source
  documents), **First attempt** (2 items: the draft and the template),
  **Comments / emails** (the reviewer feedback), and **Corrected intermediate
  JSON**.
- The corrected report itself below, with a summary row counting units by status
  (for case 1, 17 units in total). Click a flow box to inspect that stage's raw
  contents.
- Controls:
  - **Draft mode vs Template mode** — Draft fixes the completed-but-flawed
    report; Template fills the blank template from the corpus.
  - **Single pass vs Rounds** — Single pass is one correction round; Rounds
    shows multiple feedback rounds converging.
  - **Source fidelity** (JSON / DOCX / PPTX / PDF) — which pre-converted copy of
    the demo first attempt to read. JSON is the clean baseline; DOCX preserves
    the most structure; PDF the least. This selects a demo copy; it does not
    convert a file you upload.

The Ingestion, Supplementals, Search, and Report & Export tabs will be empty for
a correction case. That is expected: a correction case has no uploaded documents
or search index of its own.

---

## Doing your own work

### Build an aggregation project (upload real documents)

1. Select an aggregation project from the top dropdown (the demo
   "TGX-9 Incident Aggregation" is one), then open the **Ingestion** tab and
   click **Upload documents**.
2. Pick one or more files (`.docx`, `.pptx`, `.pdf`, `.txt`, `.md`, `.png`,
   `.jpg`). Each uploaded file is stored byte-for-byte under `source/`, and the
   extracted structure is written to `documents/<id>.canonical.json`.
3. Watch the rows advance through ingest → parse → chunk → embed → index. The
   board refreshes itself while documents are processing.
4. Add human feedback on **Supplementals**, search the corpus on **Search**, and
   assemble/export the result on **Report & Export**.

### Generate a correction case

1. On the **Correction Pipeline** tab, click **+ New project**.
2. Provide a structured brief, a freeform description, or **upload a document**
   so its own text becomes the ground-truth corpus (the faithful, reproducible
   path — the model authors structure, never invents facts).
3. The new project appears in the top dropdown and the selector jumps to it. It
   now has a corpus, a first attempt, and corrections, so the Correction
   Pipeline tab is populated.

---

## Reading the corrected report

Each row is a single unit (a field, a figure, a table, or a page element). It
carries a **status** shown as a colored tag, plus its **source**.

### Status legend

| Status | Color | Meaning |
|--------|-------|---------|
| **unchanged** | grey | The draft already had it right; verified against a source. |
| **filled** | blue | It was blank; filled in from a source. |
| **corrected** | green | It was wrong; replaced with the correct value from a source. |
| **needs review** | amber | Required, but no trustworthy value exists. A human must supply it. |
| **conflict** | red | Two corrections disagree. The tool refuses to guess; you choose. |

Green means resolved/good, amber means attention needed, red means stop/decide,
blue means information added, grey means no change.

### The "Source:" line

Every row shows a short **Source:** line (e.g. "from field_report.txt" or "2
reviewer comments"). Hover it for the full audit detail. This is how you verify
a change is grounded in real material.

### Conflicts

When two reviewers disagree (one says a severity is "High," another "Medium"),
the row shows **conflict / unresolved — choose a candidate below**, lists both
candidates with who proposed each, and fills in nothing. The tool never
auto-picks a winner and never invents a value. You make the call, and your
choice is recorded as a new correction round.

### Formatting & placement checks

Below the content sections, this area lists where the document breaks the
template's layout and formatting rules: fonts, figure titles and captions, table
styles, and figure placement. These are learned from the template document
itself.

---

## Frequently asked

**I selected a project and a tab is empty. Is something broken?**
Almost certainly not. Each project is one of two kinds. Correction cases (1–6)
fill the Correction Pipeline tab; the aggregation project (`proj_demo`) fills
Ingestion, Supplementals, Search, and Report & Export. See the table at the top.

**Which project should I pick to see the Correction Pipeline work?**
Any of cases 1–6, for example "TGX-9 Telemetry Gateway Incident."

**Which project should I pick to see Ingestion, Search, and Report populated?**
"TGX-9 Incident Aggregation" (`proj_demo`).

**Why is a value blank with a red "conflict" tag?**
Reviewers disagreed and no source settles it. Pick one of the listed candidates.

**Why does a row say "needs review"?**
The template requires it, but no source provides a trustworthy value — for
example a classification marking or an approval signature.

**Is anything sent to the cloud?**
No. Everything runs locally/offline by default.
