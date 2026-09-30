# TGX-9 Root Cause Analysis Notes

Date: 2026-03-15
Author: Reliability Engineering

## Scope

Follow-up investigation into the North Ridge packet loss event of 2026-03-02
and two similar events at other sites during February and March 2026.

## Findings

- Firmware 4.2.1 allocates a fixed inbound buffer that does not scale with
  packet rate spikes.
- Under thermal stress the buffer flush routine slows, compounding occupancy.
- Three sites running 4.2.1 experienced the same signature. Sites on 4.1.8
  did not.

## Contributing Factors

- Elevated cabinet temperature above 38 C.
- Sustained inbound packet rate above 12k packets per second.
- Absence of adaptive buffer sizing in firmware 4.2.1.

## Recommendation

Roll back to firmware 4.1.8 on all affected sites, or apply the 4.2.2 hotfix
once validated. Add cabinet thermal monitoring to the alerting baseline.
