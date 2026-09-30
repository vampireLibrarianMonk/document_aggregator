# Tail 512 Hydraulic Decay — Reliability Review

Date: 2026-09-16
Author: Fleet Reliability Engineering

## Scope

Review of the 2026-09-06 Hydraulic System B pressure decay on tail 512 and two
similar decay findings across the regional fleet in September 2026.

## Findings

- Actuator-controller software B2.3 does not annunciate slow pressure decay
  until it crosses a hard threshold.
- The actuator seal compound degrades faster in the current duty cycle.
- Aircraft on actuator-controller software B2.5 annunciated decay promptly.

## Contributing Factors

- Degraded actuator seal past its effective service life.
- Late leak annunciation from the actuator controller.
- Software that flags decay only at a hard threshold.

## Recommendation

Upgrade to actuator-controller software B2.5 across the fleet, or shorten the
seal inspection interval on B2.3 as an interim measure. Add slow-decay
annunciation to the maintenance monitoring baseline.

## Corrective Action Assignments

- Action: Upgrade to actuator-controller software B2.5 across the fleet, or
  shorten the seal inspection interval on B2.3. Owner: Fleet Engineering.
  Due: 2026-09-23.
- Action: Add slow-decay annunciation to the maintenance monitoring baseline.
  Owner: Avionics Team. Due: 2026-09-27.

## Figures

- seal_wear_comparison.png: Comparison of worn versus new actuator seal.
