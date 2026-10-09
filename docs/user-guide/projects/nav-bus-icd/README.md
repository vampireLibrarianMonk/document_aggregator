# Nav Bus Interface Control Document — walkthrough

A step-by-step walkthrough of sample project **#6**, a systems-engineering
interface control document (ICD). The source material lives in the repo under
`sample_docs/project/6/`; compare your results against it and the expected
values quoted here.

This is a **correction** project, but note it is **not an incident report** — it
is an ICD, with a different field schema (scope + interface overview rather than
identifiers + timeline). That makes it the best case for seeing that the engine
is **generic**: the same reconciliation handles a different document type with
no special-casing.

---

## The scenario

An ICD defines the electrical and data-format interface for a Navigation Data
Bus. The first-attempt document carries seeded value, table, and furniture
defects, a challenged **approval status**, and more unresolved items than the
incident cases. The pipeline fixes what the sources support, flags what they
don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Nav Bus Interface Control Document`. Click **Create project** — you are moved to
the **Ingestion** tab to add its files. The scenario files live in the repo under
`sample_docs/project/6/`.

---

## Step 2 — Ingestion: upload the project files

Open the **Ingestion** tab. Uploading the project's files is what populates the
Correction Pipeline. The readiness banner requires **the manifest + corpus +
corrections** and at least one of {**template**, **first draft**}. Drop the
manifest (`project.json`) and figure manifest (`corpus/graphics.json`) into
**Original corpus** alongside the source docs; the app routes each file by name.

Upload the files from `sample_docs/project/6/`:

| Area | Upload | Role |
|---|---|---|
| Original corpus | `project.json` | project manifest |
| Original corpus | `corpus/interface_spec_2026-04-10.txt`, `corpus/review_board_notes_2026-04-22.md` | source documents (required) |
| Original corpus | `corpus/graphics.json` | named-figure manifest |
| Template | `template/incident_report_template.json` | **template pathway** (blank ICD structure) |
| First draft | `first_attempt/incident_report_draft.json` | **first-draft pathway** (flawed ICD) |
| Corrections | `corrections/comments.json` | reviewer feedback (required) |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Inputs
incomplete"** to **"Inputs ready"** once the manifest + corpus + corrections +
one of template/first-draft are present. The Correction Pipeline and Report &
Export tabs then unlock. Here the **template** is a blank ICD structure and the
**first-draft** is the flawed ICD — both ship so you can compare Draft and
Template modes.

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab, then set **Draft**, **Single pass**,
**Source fidelity: JSON**.

---

## Step 4 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 2 unchanged, 3 filled, 9 corrected, **2 needs
review**, 1 conflict. (Two needs-review items, not one — the ICD leaves more for
a human than the incident cases.)

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `scope.system` | **unchanged** (grey) | Navigation Data Bus (Nav Bus) | already correct |
| `scope.spec_date` | **corrected** (green) | 2026-04-22 | updated to the review-board date |
| `interface_overview.signaling_rate` | **corrected** (green) | The signaling rate is 100 kbps. | draft said 50 kbps |
| `interface_overview.bus_voltage` | **corrected** (green) | differential pair physical layer | rebuilt from the spec |
| `scope.approval_status` | **conflict** (red) | *blank* | the draft's "Draft" status was challenged; the two candidates shown are **Approved** and **Rejected** |
| section bodies (scope, interface_overview, data_formats) | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Confirm the guardrails: every corrected value has a **Source:** line;
`scope.approval_status` stays an unresolved **conflict**; the two **needs
review** rows are items the template requires but no source settles (a human
supplies them). The discipline findings list the table/furniture defects (empty
Signal Definitions table rows, missing table title, footer/page-number/marking
gaps).

---

## Step 5 — Compare Template mode

Switch to **Template**: **16 units — 11 filled, 2 corrected, 2 needs review, 1
conflict**. The resolved values appear as **filled** (populating a blank ICD)
rather than **corrected**. Both **needs review** items and the
`approval_status` **conflict** persist — a blank starting point doesn't make a
disagreement or a missing authority resolve itself.

---

## Step 6 — Export (optional)

**Report & Export** → export the corrected ICD.

---

## What this project proves

- The engine is **document-type-agnostic** — an ICD reconciles with the same
  rules as an incident report, no special-casing.
- Literal source-grounded fills, no fabrication, the challenged approval status
  preserved as a `conflict`, and **two** required-but-unsupported fields sent to
  review rather than guessed.
