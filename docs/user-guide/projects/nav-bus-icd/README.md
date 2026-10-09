# Nav Bus Interface Control Document — walkthrough

A step-by-step walkthrough of sample project **#6**, a systems-engineering
interface control document (ICD). You upload the real source documents from
`sample_docs/project/6/`; the app builds the project and reconciles it. Compare
your results against the values quoted here — they are the real engine output.

This is a **correction** project, but note it is **not an incident report** — it
is an ICD, with a different structure (interface overview, signal definitions,
data formats). That makes it the best case for seeing that the app is
**generic**: it derives the structure from whatever documents you upload and the
same reconciliation handles a different document type with no special-casing.

---

## The scenario

An ICD defines the electrical and data-format interface for a Navigation Data
Bus. You upload the interface spec, the review-board notes, and three figures.
The pipeline fills what the sources support, flags what they don't, and invents
nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Nav Bus Interface Control Document`. Click **Create project** — you are moved to
the **Ingestion** tab to add its files.

---

## Step 2 — Ingestion: upload the real documents

Open the **Ingestion** tab and drop these files from `sample_docs/project/6/`
into the **Original corpus** area. Uploading them is what builds the project —
there is no JSON to assemble.

| Upload | Role |
|---|---|
| `corpus/interface_spec_2026-04-10.txt` | source document (interface spec) |
| `corpus/review_board_notes_2026-04-22.md` | source document (review-board notes) |
| `corpus/figures/nav_bus_topology.png` | figure |
| `corpus/figures/message_timing.png` | figure |
| `corpus/figures/signal_timing_detail.png` | figure |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Add your
documents"** to **"Project ready"**. The Correction Pipeline and Report & Export
tabs then unlock.

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab, then set **Draft**, **Single pass**,
**Source fidelity: JSON**.

---

## Step 4 — Read the corrected report (Draft mode)

The report is titled **"Nav Bus ICD Review Board Notes"**, with sections derived
from the documents: **Interface Overview**, **Signal Definitions**, **Data
Formats**, **Notes**. Draft mode produces **5 units: 3 corrected, 1 filled, 1
needs review.** (Fewer units than the incident cases — the ICD's later sections
are structural, with no labelled fields to correct.)

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `Date` | **corrected** (green) | 2026-04-10 | pulled from the spec |
| `System` | **corrected** (green) | Navigation Data Bus (Nav Bus) | pulled from the spec |
| `Author` | **corrected** (green) | Interface Working Group | pulled from the sources |
| `Reviewer Sign-off` | **needs review** (amber) | *blank* | required, no source settles it |
| Interface Overview body | **filled** (blue) | source-grounded prose | built from the corpus |

Confirm the guardrails: every corrected/filled value carries a **Source:** line;
`Reviewer Sign-off` is left **needs review**. Figures are placed next to the text
that references them — `nav_bus_topology.png` + `message_timing.png` together in
**Interface Overview**, and `signal_timing_detail.png` in **Signal Definitions**
— with the real images rendered inline. This ICD has no corrective-actions
table (the source has no such section), which is correct — the app derives a
table only when the documents contain one.

**Set and Undo:** **Set** a value for `Reviewer Sign-off`; **Undo** reverts it so
you can set it again. Resolving one unit never disturbs another.

---

## Step 5 — Compare Template mode

Switch to **Template**: **5 units — 4 filled, 1 needs review.** The resolved
values appear as **filled** (populating a blank ICD) rather than **corrected**;
`Reviewer Sign-off` stays **needs review**.

---

## Step 6 — Export

**Report & Export** shows the corrected ICD with a Draft/Template toggle and a
**Rebuild** button. Export as JSON / Markdown / PDF (always) or DOCX/PPTX
(offered based on what you uploaded). Real figures embedded; Markdown is a ZIP
with a `figures/` folder; filenames carry the project name + a UTC timestamp.

---

## What this project proves

- The app is **document-type-agnostic** — an ICD reconciles with the same rules
  as an incident report, no special-casing; the structure comes from your
  documents.
- Literal source-grounded fills with provenance, no fabrication, unsupported
  fields sent to **needs review** (Set/Undo), figures placed with the text that
  references them, and **no table invented** when the source has none.
