# Line 3 Housing Crack Defect — Root Cause Review

Date: 2026-08-19
Author: Manufacturing Engineering

## Scope

Review of the 2026-08-09 hairline crack defect on Injection Molding Line 3 and
two similar defect clusters on Line 3 earlier in August 2026.

## Findings

- Mold-controller firmware 2.0.5 applies pressure compensation too slowly under
  melt-temperature drift.
- The cavity pressure sensor drifts out of calibration faster than the current
  interval assumes.
- Lines upgraded to mold-controller firmware 2.1.1 did not reproduce the defect.

## Contributing Factors

- Melt temperature drifting above the specified band.
- Cavity pressure sensor past its effective calibration interval.
- Firmware that compensates too slowly for pressure deviation.

## Recommendation

Upgrade to mold-controller firmware 2.1.1 on all molding lines, or shorten the
pressure-sensor calibration interval on 2.0.5 as an interim measure. Add
melt-temperature drift alerting to the line monitoring baseline.

## Corrective Action Assignments

- Action: Upgrade to mold-controller firmware 2.1.1 on all molding lines, or
  shorten the sensor calibration interval on 2.0.5. Owner: Manufacturing
  Engineering. Due: 2026-08-26.
- Action: Add melt-temperature drift alerting to the line monitoring baseline.
  Owner: Process Controls. Due: 2026-08-30.

## Figures

- pressure_compensation_curve.png: Firmware pressure-compensation response curve.
