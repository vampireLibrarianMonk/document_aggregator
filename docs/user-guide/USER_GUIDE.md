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
7. **Samples** — only present when your administrator has enabled it (see
   "Running the sample cases" below)

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

## Running the sample cases

The repo ships six complete worked correction cases (telemetry incident,
security incident, lab safety event, manufacturing defect, aviation maintenance,
interface control document). They live under `sample_docs/project/` in the
repository. There are two ways to run them.

Each has a step-by-step walkthrough under `docs/user-guide/projects/` with the
exact values to expect on screen:

| # | Case | Walkthrough |
|---|------|-------------|
| 1 | TGX-9 Telemetry Gateway Incident | [tgx-9](projects/tgx-9/README.md) |
| 2 | Customer Portal Credential-Stuffing Incident | [credential-stuffing](projects/credential-stuffing/README.md) |
| 3 | Clinical Lab Reagent Spill Safety Event | [reagent-spill](projects/reagent-spill/README.md) |
| 4 | Injection Molding Line Defect Event | [injection-molding](projects/injection-molding/README.md) |
| 5 | Aircraft Hydraulic Decay Maintenance Event | [hydraulic-decay](projects/hydraulic-decay/README.md) |
| 6 | Nav Bus Interface Control Document | [nav-bus-icd](projects/nav-bus-icd/README.md) |

A seventh walkthrough covers the mass **JSON→golden** batch capability (which
uses committed fixtures rather than a `sample_docs` case):
[json-batch](projects/json-batch/README.md).

### In the app (optional, off by default)

Sample instantiation is an opt-in feature so the app does not carry demo content
by default. Your administrator enables it by setting `SAMPLES_ENABLED=true`
(environment variable / `.env`). When enabled, a **Samples** tab appears:

1. Open the **Samples** tab.
2. Click **Use this sample** on a case. The platform copies it into a new
   project of your own and selects it; you land on the Correction Pipeline tab
   with everything populated.

The sample fixtures in the repo are never changed, so you can instantiate the
same case as many times as you like. The Samples tab is a separate page — it is
never mixed into the New Project page.

### From the repository (always available)

Each case under `sample_docs/project/<id>/` contains its source corpus, a
first-attempt report, and reviewer comments. You can inspect those files
directly, or (for developers) load one with the documented repo command. This
is the default path and needs no in-app feature flag.

---

## A project is one of two kinds

This matters for what each tab shows. The kind is decided by how you created the
project.

- **A sample-based project** (Option A) is a **correction** project. It comes
  with source material, a first-attempt report, and reviewer comments, so the
  **Correction Pipeline** tab is full. It has no uploaded documents of its own,
  so Ingestion, Search, and Report & Export start empty for it.
- **An upload-based project** (Option B) is an **aggregation** project. Its
  **Ingestion, Supplementals, Search, and Report & Export** tabs fill as you add
  documents. It has no first-attempt report, so the Correction Pipeline tab will
  say it has no correction data.

If a tab looks empty, it is almost always because the selected project is the
other kind. This is expected, not a fault.

---

## The tabs, and what to do on each

### Correction Pipeline

Turns a flawed or blank first attempt into a corrected report, grounded entirely
in the source material.

What to do: select a sample-based project. You will see four stages left to
right — **Original corpus**, **First attempt**, **Comments / emails**, and
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
Generate a project there, or upload documents on the Ingestion tab. To run a
bundled sample case, see "Running the sample cases" above.

**I selected a project and a tab is empty.**
Each project is one of two kinds. A sample-based project fills the Correction
Pipeline tab; an upload-based project fills Ingestion, Supplementals, Search, and
Report & Export. See "A project is one of two kinds" above.

**Does using a sample change the original?**
No. Using a sample copies it into a new project of your own. The sample stays
untouched, and you can create as many copies as you like.

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
