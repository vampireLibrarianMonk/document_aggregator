# User Guide

This guide is for people **using** the platform. It walks you through what to do,
step by step, and what you should expect to see on screen. No prior setup
knowledge is assumed.

The platform runs fully offline. Nothing leaves your machine or your enclave.

## Opening the app

Once the stack is running (see the README or your administrator), open:

```
http://localhost:8080
```

**The app starts empty.** There are no projects until you create one. That is by
design: everything you see is something you made. The top of the screen has one
**Project** selector that scopes all tabs, and these tabs:

1. **New Project**
2. **Ingestion**
3. **Correction Pipeline**
4. **Supplementals**
5. **Search**
6. **Report & Export**

And, below the tabs, a footer link:

- **Diagnostics** — a separate status page (see "Diagnostics" below). It appears
  only when your administrator has enabled it (`DIAGNOSTICS_ENABLED=true`).

On a fresh app you land on the **New Project** tab automatically. You can return
to it anytime by clicking the **New Project** tab or the **+ New Project** button
next to the Project selector.

---

## Step 1: create a project (the New Project tab)

The **New Project** tab creates a new, empty project. There is nothing to
generate and no documents to pick yet — you just name it.

1. Enter a **Project name**.
2. Enter a **Description** (what the project is for).
3. Click **Create project**.

What to expect: the project is created and selected, and you are moved to the
**Ingestion** tab, which is where you add your documents (Step 2). The project
appears in the Project selector at the top of the screen.

### Then: add your documents (Ingestion)

Once the project exists, the **Ingestion** tab is where you upload your source
material. Ingestion is organized into four areas (original corpus, template,
corrections, first draft); see "Ingestion" below for the details and the rules
about which must be filled in.

> For a complete, worked example, see the project walkthroughs under
> `docs/user-guide/projects/` (e.g. the TGX-9 guide).

What to expect: each uploaded file appears as a row that advances to
**completed**. Scanned PDFs and images are run through OCR so their text becomes
searchable (see Diagnostics to confirm OCR is active).

---

## Running the worked example cases

The repo ships six complete worked correction cases (telemetry incident,
security incident, lab safety event, manufacturing defect, aviation maintenance,
interface control document). Each case's files live under
`sample_docs/project/<id>/` in the repository.

You run a case the same way you run any real project: **create a project, then
upload that case's files on the Ingestion tab.** Uploading the files is what
populates the Correction Pipeline — there is no separate "load sample" button.
Each case has a step-by-step walkthrough under `docs/user-guide/projects/` with
the exact files to upload and the exact values to expect on screen:

| # | Case | Files | Walkthrough |
|---|------|-------|-------------|
| 1 | TGX-9 Telemetry Gateway Incident | `sample_docs/project/1/` | [tgx-9](projects/tgx-9/README.md) |
| 2 | Customer Portal Credential-Stuffing Incident | `sample_docs/project/2/` | [credential-stuffing](projects/credential-stuffing/README.md) |
| 3 | Clinical Lab Reagent Spill Safety Event | `sample_docs/project/3/` | [reagent-spill](projects/reagent-spill/README.md) |
| 4 | Injection Molding Line Defect Event | `sample_docs/project/4/` | [injection-molding](projects/injection-molding/README.md) |
| 5 | Aircraft Hydraulic Decay Maintenance Event | `sample_docs/project/5/` | [hydraulic-decay](projects/hydraulic-decay/README.md) |
| 6 | Nav Bus Interface Control Document | `sample_docs/project/6/` | [nav-bus-icd](projects/nav-bus-icd/README.md) |

A seventh walkthrough covers the mass **JSON→golden** batch capability (which
uses committed fixtures rather than a `sample_docs` case):
[json-batch](projects/json-batch/README.md).

Each case folder contains the project manifest (`project.json`), the source
corpus, the blank template and the flawed first-draft, and the reviewer
comments. Upload them into the Ingestion areas as each walkthrough describes; the
app routes each file to its role by name. The repo fixtures are read-only — you
upload copies, so you can run a case as many times as you like.

---

## A project is one of two kinds

This matters for what each tab shows. The kind is decided by what you upload to the
project.

- **A correction project** is one where you uploaded the four correction
  components (a project manifest, source corpus, a template and/or a flawed
  first-draft, and reviewer corrections). The **Correction Pipeline** tab then
  reconciles them into a corrected report. The six worked cases are this kind.
- **An aggregation project** is one where you uploaded only source documents to
  ingest, search, and assemble. Its **Ingestion, Supplementals, Search, and
  Report & Export** tabs fill as you add documents; the Correction Pipeline tab
  will say it has no correction data because no first-draft/template was
  uploaded.

If a tab looks empty, it is almost always because the selected project does not
have the files that tab needs. This is expected, not a fault.

---

## The tabs, and what to do on each

### Correction Pipeline

Turns a flawed or blank first attempt into a corrected report, grounded entirely
in the source material.

What to do: select a correction project (one you built by uploading a manifest,
corpus, a template/first-draft, and corrections). You will see four stages left
to right — **Original corpus**, **First attempt**, **Comments / emails**, and
**Corrected intermediate JSON** — each with a count. Below them is the corrected
report.

Controls:
- **Draft vs Template** — Draft fixes a completed-but-flawed report; Template
  fills a blank report template from the source material.
- **Single pass vs Rounds** — Single pass is one correction round; Rounds shows
  several feedback rounds converging.
- **Source fidelity** (JSON / DOCX / PPTX / PDF) — which pre-converted copy of
  the first attempt to read. JSON is the clean baseline; DOCX preserves the most
  structure; PDF the least.
- **+ New project** — generate a brand-new correction project from a brief or
  from a document you upload.

What to expect: each report row shows a status tag and a short **Source:** line.
Click a stage box to inspect its raw contents. Where the tool cannot resolve a
value on its own, it flags it rather than guessing (see the status legend).

### Ingestion

Add real documents to an aggregation project and watch them being processed.

What to do: select an aggregation project, click **Upload documents**, pick your
files. Click **Canonical** on any row to inspect the normalized structure the
tool extracted.

What to expect: a table of documents, each advancing ingest to parse to chunk to
embed to index, with block/chunk/artifact counts and the resolved date/time.

### Supplementals

Attach human feedback: comments, emails, corrections, or interview notes.

What to do: pick a **kind**, fill in author / subject / body, optionally target a
specific document, and submit.

What to expect: each item appears as a card with its kind, an automatically
assigned sentiment tag, author, subject, and target.

### Search

Find material across a project's source documents by meaning or keyword.

What to do: select an aggregation project that has ingested documents, type a
query, press Enter.

What to expect: a list of hits, each quoting the matched passage with its
document and section, ranked by relevance. With real embeddings active (the
default) this is semantic search, not just keyword matching — confirm the
embedding model on the Diagnostics page (footer link).

### Report & Export

Assemble the aggregated report and export it.

What to do: select an aggregation project with completed documents. Use the
export buttons for JSON, Markdown, DOCX, PPTX, or PDF.

What to expect: one section per completed document, ordered by effective
date/time, with supplementals attached. Exports download as files.

### Diagnostics

Diagnostics is a **separate page**, not one of the tabs. Reach it from the
**Diagnostics** link in the footer at the bottom of the app (it appears only when
your administrator has enabled it with `DIAGNOSTICS_ENABLED=true`), or by going
to `/diagnostics` directly. A link on the page takes you back to the app.

It shows, in plain terms, what the platform can actually do right now. Use it
whenever something looks weaker than expected (for example, search feels shallow
or a scanned PDF produced no text).

What to expect, with everything working, an **overall: OK** and:
- **Embeddings — OK**: a real model (all-MiniLM-L6-v2, 384 dimensions). If it
  says the hashing fallback, semantic search is degraded.
- **OCR — OK**: tesseract present, so scanned PDFs and images yield text. If
  Degraded, OCR is off or no engine is installed and scans will not be read.
- **Layout geometry tier — OK**: LibreOffice present, so formatting/placement
  checks run fully. If Degraded, only structural checks run.
- **Bedrock**: OK when enabled with approved models; **Offline** otherwise,
  which is the normal air-gap posture (the offline generator is used instead and
  nothing is lost in correctness).

The footer shows the pipeline/schema versions, the offline guards, and the data
directory. Click **Refresh** to re-check at any time.

---

## Reading the corrected report

Each row is a single unit (a field, a figure, a table, or a page element) with a
**status** and a **source**.

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

Every row shows a short **Source:** line (for example "from field_report.txt" or
"2 reviewer comments"). Hover it for the full audit detail. This is how you
verify a change is grounded in real material.

### Conflicts

When two reviewers disagree, the row shows **conflict / unresolved — choose a
candidate below**, lists both candidates with who proposed each, and fills in
nothing. The tool never auto-picks a winner and never invents a value. You make
the call, and your choice is recorded as a new correction round.

### Formatting and placement checks

Below the content sections, this area lists where the document breaks the
template's layout and formatting rules (fonts, figure titles and captions, table
styles, figure placement), learned from the template document itself. These run
fully only when the geometry tier is active (see Diagnostics).

---

## Frequently asked

**I just opened the app and there are no projects. Is it broken?**
No. The app starts empty on purpose and lands you on the **New Project** tab.
Create a project there, then upload its documents on the Ingestion tab. To run a
worked example case, see "Running the worked example cases" above.

**I selected a project and a tab is empty.**
Each project is one of two kinds. A correction project (manifest + corpus +
template/first-draft + corrections uploaded) fills the Correction Pipeline tab;
an aggregation project (documents uploaded) fills Ingestion, Supplementals,
Search, and Report & Export. See "A project is one of two kinds" above.

**Do the worked example cases change the repo files?**
No. You upload copies of the files from `sample_docs/project/<id>/`; the repo
fixtures stay untouched, and you can run a case as many times as you like.

**Search results feel shallow / my scanned PDF produced no text.**
Open the **Diagnostics** page (the link in the footer). If Embeddings shows the
hashing fallback, semantic search is degraded; if OCR shows Degraded, scanned
documents are not being read. That page tells you exactly which capability is
reduced.

**Why is a value blank with a red "conflict" tag?**
Reviewers disagreed and no source settles it. Pick one of the listed candidates.

**Why does a row say "needs review"?**
The template requires it, but no source provides a trustworthy value — for
example a classification marking or an approval signature.

**Is anything sent to the cloud?**
No. Everything runs locally/offline by default.
