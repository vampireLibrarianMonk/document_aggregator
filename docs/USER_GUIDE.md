# User Guide

This guide is for people **using** the platform, not developers. It explains
what the tool does, what to submit, where, and how to read what it shows you.

## What this tool does

You give it the raw source material and a first attempt at a report. It produces
a **corrected report** by fixing wrong values, filling gaps, and flagging
anything it cannot resolve or that breaks the formatting rules. Nothing is
invented: every change traces back to a source you provided.

It runs fully offline. Nothing leaves your machine or your enclave.

## Opening the app

Once the stack is running (see the README or your administrator), open:

```
http://localhost:8080
```

The app has five tabs across the top. You can use them in any order, but the
**Correction Pipeline** tab is the best place to start.

---

## The five tabs

### 1. Correction Pipeline (start here)

This is the heart of the tool. It shows a worked example (a "scenario") going
through four stages, left to right:

1. **Original corpus** - the raw source documents (the ground truth).
2. **First attempt** - the flawed draft (or a blank template) being corrected.
3. **Comments / emails** - human feedback saying what is wrong.
4. **Corrected intermediate JSON** - the cleaned-up result.

Controls:

- **Scenario** dropdown - pick which worked example to view.
- **Draft mode vs Template mode** - Draft mode fixes a completed-but-flawed
  report; Template mode fills a blank report template from the source material.
- **Single pass vs Rounds** - Single pass shows one round of corrections; Rounds
  shows several feedback rounds converging over time.
- **Source fidelity** - which pre-converted copy of the demo document to read
  (JSON is the clean baseline; DOCX is highest fidelity; PDF is lowest). This
  chooses a demo copy; it does not convert a file you upload.

Click any of the four flow boxes to inspect that stage's contents.

### 2. Ingestion (submit your own documents)

This is where you upload real files.

- Click **Upload documents** and pick one or more files
  (`.docx`, `.pptx`, `.pdf`, `.txt`, `.md`, `.png`, `.jpg`).
- Each document moves through stages: ingest -> parse -> chunk -> embed -> index.
- Click **Canonical** on any row to see the normalized structure the tool
  extracted from that document.

### 3. Supplementals (add human feedback)

Attach comments, emails, corrections, or interview notes to the project.

- Pick a **kind**, fill in author / subject / body, and optionally target one
  document.
- The tool auto-classifies the sentiment (neutral / positive / negative).

### 4. Search

Search the source documents by meaning or keyword.

- Type a query and press Enter.
- Each result shows the matched text, which document and section it came from,
  and a relevance score.

### 5. Report & Export

- See the assembled report.
- Export it to JSON, Markdown, DOCX, PPTX, or PDF.

---

## Reading the corrected report

Each row is a single unit (a field, a figure, a table, or a page element). It
carries a **status** shown as a colored tag, plus its **source**.

### Status legend (and what the colors mean)

| Status | Color | Meaning |
|--------|-------|---------|
| **unchanged** | grey | The draft already had it right; verified against a source. |
| **filled** | blue | It was blank; filled in from a source. |
| **corrected** | green | It was wrong; replaced with the correct value from a source. |
| **needs review** | amber | Required, but no trustworthy value exists. A human must supply it. |
| **conflict** | red | Two corrections disagree. The tool refuses to guess; you choose. |

The colors are consistent everywhere: green means resolved/good, amber means
attention needed, red means stop/decide, blue means information added, grey means
no change.

### The "Source:" line

Every row shows a short **Source:** line (e.g. "from field_report.txt" or "2
reviewer comments"). Hover it to see the full internal audit detail if you need
it. This is how you verify that a change is grounded in real material.

### Conflicts

When two people disagree (for example one says a severity is "High" and another
says "Medium"), the row shows **conflict / unresolved - choose a candidate
below**, lists both candidates with who proposed each, and does not fill in an
answer. This is intentional: the tool never auto-picks a winner and never
invents a value. You make the call.

### Formatting & placement checks

Below the content sections, this area lists where the document breaks the
template's layout and formatting rules: fonts, figure titles and captions, table
styles, and figure placement. These are learned from the template document
itself.

---

## Frequently asked

**Do I have to submit anything on the Correction Pipeline tab?**
No. It is a worked example. To process your own documents, use the Ingestion tab.

**Why is a value blank with a red "conflict" tag?**
Because reviewers disagreed and there is no source to settle it. Pick one of the
listed candidates.

**Why does a row say "needs review"?**
The template requires it, but no source provides a trustworthy value. It needs a
human, for example a classification marking or an approval signature.

**Is anything sent to the cloud?**
No. Everything runs locally/offline by default.
