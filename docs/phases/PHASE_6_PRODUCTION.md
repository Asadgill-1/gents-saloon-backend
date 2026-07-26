# Phase 6 — Production Hardening and Rollout

## Status — 2026-07-25

**Not started.** There is no production deployment. Phase 0 development Compose is not a production topology. Begin only after Phases 1–5 pass their functional and security gates. See [../../START_HERE.md](../../START_HERE.md).

## Outcome

The system is operable, recoverable, observable, secure, and accepted for a controlled UAE launch.

## Work

1. Build hardened images/Compose for Caddy, API, worker, Beat, and private authenticated Redis; use non-root containers, health checks, resource limits, and read-only filesystems where possible.
2. Configure TLS, exact CORS, safe headers, body/time limits, firewall, SSH hardening, automated deploy/rollback, and immutable release identifiers.
3. Configure structured logs, metrics, traces, alert routing, bot/outbox/worker/database/backup dashboards, and redaction tests.
4. Enable encrypted backups/PITR and offsite export storage; document and execute restore.
5. Run migration rehearsal, load/soak, security/penetration, dependency, RLS, privacy, receipt/tax, and e-invoicing-readiness reviews.
6. Write runbooks for incident, key/token rotation, bot onboarding, suspension, cash receipt correction, export/offboarding, restore, rollback, and worker/outbox recovery.
7. Complete staging UAT, pilot shops, monitored rollout, and owner acceptance.

## Gates

- RPO ≤ 15 minutes and timed restore RTO ≤ 4 hours.
- At 50 active shops/201 bots: webhook acknowledgement <1 s, normal API p95 <500 ms, checkout p95 <2 s, queue propagation <3 s under the agreed profile.
- No unresolved Critical/High security findings; all accepted risks are written with expiry.
- UAE VAT/privacy/retention and platform B2B e-invoicing behavior are reviewed against current official sources.
- Backup, certificate, disk, API, worker, outbox, database, Redis, and bot alerts are proven by fault injection.
- Pilot acceptance and rollback drill complete.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Final `ponytail-debt` ledger has owners/releases for every production-relevant deferral.
