# Synthetic growth dataset generator

**Added:** 2026-10-05
**Code:** `backend/tests/command_center/datagen/`
(`generator.py`, `__init__.py`)

## Why generated, not sourced

The precision-correction alpha loop needs documents at increasing sizes, each
with **known** localized edits and a **known** gold result, so precision, recall,
and the unintended-change count can be measured as size grows. We first looked for
a public dataset. The best-shaped candidate, **IteraTeR** (Grammarly, ACL 2022),
has exactly the right form — localized before/after edits with intent labels — and
has siblings (WikiAtomicEdits, NewsEdits, the GEC corpora). None of them fit our
constraints, for three concrete reasons:

1. **Air-gap.** They load over the network (the Hugging Face hub, Wikimedia
   dumps); our system is offline by design. Vendoring a snapshot would import
   external data provenance into the repo.
2. **License.** IteraTeR's dataset card carries no explicit license, and the
   underlying sources are mixed — arXiv's non-exclusive terms, Wikipedia's
   share-alike CC BY-SA, Wikinews — which is a compliance risk for a local-first
   product repository.
3. **Domain.** They are general academic/encyclopedic/news prose. Our system
   corrects structured incident reports with a manifest of discrete fields,
   section bodies, graphics, tables, and furniture, grounded against a corpus,
   under a no-fabrication floor. Public edit corpora have before/after prose but
   no corpus to ground against, no discrete fields, and no fabrication/conflict
   semantics — so they would test prose-rewrite drift but not our actual pipeline.

A self-generated, domain-matched corpus is air-gap clean (no external provenance),
deterministic (seeded), and exercises the exact pipeline we ship. IteraTeR's edit
taxonomy (clarity / fluency / meaning-changed) is still borrowed as a reference
for labelling the synthetic edit types.

## What it produces

`generate_project(GrowthSpec(project_id, size, seed))` returns a project whose
JSON shapes mirror the bundled samples exactly, so the real engine and the
bake-off harness consume it unchanged:

- `project.json` — the manifest (discrete fields, section bodies, a table).
- `corpus/field_report.txt` + `corpus/graphics.json` — the grounded truth values
  and figure records.
- `template/incident_report_template.json` — required sections, table spec,
  furniture.
- `first_attempt/incident_report_draft.json` — the draft, seeded with known
  defects.
- `corrections/comments.json` + `corrections/emails/email_N.txt` — the localized
  corrections, both as machine form and as raw reviewer emails.
- `edit_ledger.json` — the ground-truth ledger (per edit: kind, target, draft
  value, correct value, intent) that the alpha loop scores against. Not consumed
  by the engine.

## The scaling knob

`GrowthSpec.size` multiplies the document: body sections, discrete fields, figures,
table rows, and table columns all grow with it. Verified scaling (engine run via
`run_reconciliation`): size 1 → 4 sections / 7 fields / 1 figure / 12 units; size
3 → 6 sections / 11 fields / 2 figures / 17 units; size 6 → 9 sections / 17 fields
/ 4 figures / 25 units. The seeded conflict is preserved at every size.

## The seeded edits

Each project carries a fixed, known set of localized defects so the techniques
have something precise to be scored on:

- a **discrete-field value** defect (a wrong firmware version on the first body
  section);
- one **figure relabel** per figure (the draft uses a placeholder name; the
  reviewer email describes the figure in prose, forcing the grounding step to
  resolve the exact corpus filename);
- a **prose body** edit on a three-sentence paragraph where only the middle
  sentence should change and the surrounding two must survive verbatim — the case
  that separates a precision splice from a naive whole-unit replace;
- a genuine **severity conflict** (two reviewer emails disagree) whose correct
  behaviour is to stay unresolved;
- a **table fill** (empty draft table with scrambled columns and a wrong font;
  one column deliberately has no corpus backing so its cells land `needs_review`
  per cell — the no-fabrication floor exercised at table scale).

## Determinism and air-gap

The generator uses only seeded `random` over fixed vocabularies — no model, no
network. Verified: the same seed produces byte-identical output, and the
command-center coordinator is deterministic on generated projects. This keeps the
whole scaling study reproducible and air-gap safe.

## Future work

The seeded intents are unambiguous and grounded. To pressure-test the one place a
model might still add value over the deterministic editors, add an
**ambiguous-intent track** — reviewer notes that state a goal without the target
text — and re-run the
[precision-correction alpha loop](precision-correction-alpha-loop.md) to see
whether the model separates from the deterministic techniques there.
