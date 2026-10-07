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

## Step 1 — Open the project

- **Samples tab (recommended, if enabled):** **Samples** → *Nav Bus Interface
  Control Document* → **Use this sample** → lands on the Correction Pipeline tab,
  populated.
- **From the repo:** create a project named `Nav Bus Interface Control Document`
  and inspect `sample_docs/project/6/` (corpus:
  `interface_spec_2026-04-10.txt`, `review_board_notes_2026-04-22.md`; flawed
  draft + blank template; reviewer `corrections/`).

**What you should see:** four stage boxes, each with a count.

---

## Step 2 — Choose the mode

Set **Draft**, **Single pass**, **Source fidelity: JSON**.

---

## Step 3 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 2 unchanged, 3 filled, 9 corrected, **2 needs
review**, 1 conflict. (Two needs-review items, not one — the ICD leaves more for
a human than the incident cases.)

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `scope.system` | **unchanged** (grey) | Navigation Data Bus (Nav Bus) | already correct |
| `scope.spec_date` | **corrected** (green) | 2026-04-22 | updated to the review-board date |
| `interface_overview.signaling_rate` | **corrected** (green) | The signaling rate is 100 kbps. | draft said 50 kbps |
| `interface_overview.bus_voltage` | **corrected** (green) | differential pair physical layer | rebuilt from the spec |
| `scope.approval_status` | **conflict** (red) | *blank* | "Draft" status challenged with no agreed value |
| section bodies (scope, interface_overview, data_formats) | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Confirm the guardrails: every corrected value has a **Source:** line;
`scope.approval_status` stays an unresolved **conflict**; the two **needs
review** rows are items the template requires but no source settles (a human
supplies them). The discipline findings list the table/furniture defects (empty
Signal Definitions table rows, missing table title, footer/page-number/marking
gaps).

---

## Step 4 — Compare Template mode

Switch to **Template**: **16 units — 11 filled, 2 corrected, 2 needs review, 1
conflict**. The resolved values appear as **filled** (populating a blank ICD)
rather than **corrected**. Both **needs review** items and the
`approval_status` **conflict** persist — a blank starting point doesn't make a
disagreement or a missing authority resolve itself.

---

## Step 5 — Export (optional)

**Report & Export** → export the corrected ICD.

---

## What this project proves

- The engine is **document-type-agnostic** — an ICD reconciles with the same
  rules as an incident report, no special-casing.
- Literal source-grounded fills, no fabrication, the challenged approval status
  preserved as a `conflict`, and **two** required-but-unsupported fields sent to
  review rather than guessed.
