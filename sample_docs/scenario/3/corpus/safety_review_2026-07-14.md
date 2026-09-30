# Bay 2 Reagent Spill — Safety Review

Date: 2026-07-14
Author: Laboratory Quality Assurance

## Scope

Review of the 2026-07-03 reagent spill on the Bay 2 chemistry analyzer and one
similar near-miss on Bay 4 in June 2026.

## Findings

- Control-software 7.1.2 does not halt aspiration when a reagent-level warning
  fires.
- The reagent seal design degrades faster under the current cleaning cycle.
- Analyzers upgraded to control-software 7.2.0 halt aspiration correctly.

## Contributing Factors

- Worn reagent seal past its service interval.
- Absence of a secondary catch tray under the reagent bay.
- Software that warns but does not stop aspiration.

## Recommendation

Upgrade to control-software 7.2.0 on all analyzers, or enable the aspiration
halt on 7.1.2 as an interim step. Add a secondary catch tray to each reagent bay.

## Corrective Action Assignments

- Action: Upgrade to control-software 7.2.0 on all analyzers, or enable the
  aspiration halt on 7.1.2. Owner: Biomedical Engineering. Due: 2026-07-21.
- Action: Install a secondary catch tray on each reagent bay.
  Owner: Laboratory Facilities. Due: 2026-07-28.

## Figures

- reagent_seal_cross_section.png: Cross-section of the worn reagent seal.
