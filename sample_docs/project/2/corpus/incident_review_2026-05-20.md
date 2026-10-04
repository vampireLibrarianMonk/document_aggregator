# Customer Portal Breach Attempt — Incident Review

Date: 2026-05-20
Author: Security Engineering

## Scope

Review of the 2026-05-11 credential-stuffing event against the customer portal
and two related probing events earlier in May 2026.

## Findings

- Auth-gateway 3.4.0 exposes a legacy login endpoint without rate limiting.
- The adaptive throttling module is disabled by default in 3.4.0.
- Portals upgraded to auth-gateway 3.5.2 were not targeted successfully.

## Contributing Factors

- Legacy login endpoint reachable from the public internet.
- Absence of rate limiting on the auth-gateway.
- Credential reuse across previously breached third-party sites.

## Recommendation

Upgrade to auth-gateway 3.5.2 on all public portals, or enable the adaptive
throttling module on 3.4.0 as an interim measure. Add anomalous-login alerting
to the monitoring baseline.

## Remediation Assignments

- Action: Upgrade to auth-gateway 3.5.2 on all public portals, or enable
  adaptive throttling on 3.4.0. Owner: Security Engineering. Due: 2026-05-27.
- Action: Add anomalous-login alerting to the monitoring baseline.
  Owner: SOC Team. Due: 2026-05-30.

## Figures

- auth_flow_diagram.png: Authentication flow showing the exposed legacy endpoint.
