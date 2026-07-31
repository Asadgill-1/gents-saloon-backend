# Phase 6 — Production Hardening and Rollout

## Status — 2026-07-31

**Local production baseline built; rollout not started.** The recovery branch now contains a digest-pinned non-root backend image, hardened Caddy/API/worker/Beat/private-Redis Compose topology, OTLP instrumentation, immutable GHCR release workflow with SBOM/provenance and restricted SSH deployment, application rollback, encrypted recovery tooling, release manifests, and production/incident/application runbooks. Compose configuration validates locally; a live image/Caddy/container test cannot run on this workstation because its Docker Linux engine is unavailable.

This is implementation evidence only. There is no provisioned VPS or production deployment. External environment, PITR, backup, observability, load, penetration, UAT, canary, rollback, variance, cutover, and hypercare gates remain open. Phases 0/1/3/4/5 must pass before launch. See [../../START_HERE.md](../../START_HERE.md) and [../PRODUCTION_RUNBOOK.md](../PRODUCTION_RUNBOOK.md).

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

## Recovery implementation checkpoint — 2026-07-31

- `docker/Dockerfile.backend` builds one non-root Python 3.12 image from exact base-image digests and the locked production dependency set.
- `docker/compose.prod.yml` publishes only Caddy on TCP 80/443. API, worker, Beat, and authenticated Redis have no host ports. Read-only filesystems, `tmpfs`, health checks, restart policies, resource limits, bounded logs, and least Linux capabilities are defined.
- `docker/Caddyfile` enables automatic TLS, a 32 KB header limit, 1 MB Telegram/10 MB general body limits, bounded timeouts, sanitized proxy headers, HSTS, and JSON access logs.
- OpenTelemetry FastAPI/Celery/psycopg/Redis instrumentation and the OTLP exporter are exact dependencies. Grafana Cloud credentials remain outside Git.
- `.github/workflows/release.yml` builds/pushes by Git SHA, attaches SBOM/max provenance, resolves the digest, records migration checksums and both Vercel IDs, verifies SSH host keys, and deploys to a protected GitHub environment.
- `ops/deploy.sh` and `ops/rollback.sh` keep immutable releases and never reverse database migrations. Failed deployment gates restore the prior application release.
- Recovery bundles are locally age-encrypted before S3-compatible upload. Restore defaults to a checksum-verified dry run and requires `CONFIRM_RESTORE` to apply.
- Production, incident-response, and operations runbooks define the remaining owner-controlled gates.

## Gates

- RPO ≤ 15 minutes and timed restore RTO ≤ 4 hours.
- At 50 active shops/201 bots: webhook acknowledgement <1 s, normal API p95 <500 ms, checkout p95 <2 s, queue propagation <3 s under the agreed profile.
- No unresolved Critical/High security findings; all accepted risks are written with expiry.
- UAE VAT/privacy/retention and platform B2B e-invoicing behavior are reviewed against current official sources.
- Backup, certificate, disk, API, worker, outbox, database, Redis, and bot alerts are proven by fault injection.
- Pilot acceptance and rollback drill complete.
- Run the full phase security audit from [../security-audits/README.md](../security-audits/README.md), write the dated audit note, and leave zero unresolved Critical/High findings.
- Final `ponytail-debt` ledger has owners/releases for every production-relevant deferral.
