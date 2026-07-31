# Production and Recovery Runbook

Last updated: **2026-07-31, Asia/Dubai**

This runbook is the executable Phase 6 baseline. It does not prove that a VPS, Supabase project, Vercel project, Grafana stack, backup bucket, drill, or cutover exists. Every evidence field remains open until an owner-authorized operator records the real identifier, timestamp, result, and approver outside Git.

## 1. Release invariants

- Staging and production use different Supabase projects, Auth users, private Storage buckets, Telegram bot registrations, Moonshot keys, Redis credentials, encryption/HMAC keys, Grafana credentials, S3 prefixes, Vercel projects, and DNS names.
- Vercel previews use staging only. Production credentials are unavailable to preview deployments.
- PostgreSQL remains managed by Supabase. The VPS runs Caddy, FastAPI, Celery worker, Beat, and private Redis only.
- Only TCP 80/443 are public. SSH is restricted at the cloud firewall and UFW to explicit administrator and deployment sources. Redis, API, worker, Beat, Docker, and telemetry ports are not published.
- A backend release is a GHCR `sha256` digest. Its manifest records the Git SHA, image digest, ordered migration checksums, and both Vercel deployment IDs.
- Application rollback selects a prior image/configuration release. Database migrations are forward-only; PITR is disaster recovery, not a normal rollback.
- No cutover occurs while Phases 0/1/3/4/5 have an open Critical/High finding or required owner gate.

## 2. Host baseline

Provision Ubuntu 24.04 LTS with at least 4 vCPU, 8 GB RAM, and a 100 GB encrypted SSD. Apply security updates, enable unattended security upgrades, use SSH key-only non-root administrators, disable root/password login, and install Docker Engine from Docker's official repository.

The cloud firewall and UFW must implement:

```text
inbound TCP 22: named administrator/deployment source IPs only
inbound TCP 80: any
inbound TCP 443: any
all other inbound: deny
outbound: allow only required DNS, NTP, updates, Supabase, Telegram, Moonshot, Grafana OTLP, GHCR, S3, and ACME
```

Create `/opt/gents-saloon/incoming`, `/opt/gents-saloon/releases`, and `/etc/gents-saloon` with deployment-user ownership and mode `0750`. Populate `compose.env` and `runtime.env` from `ops/env/` templates with mode `0600`. Disable agent/port forwarding and PTY allocation for the deployment SSH key; the account has no password and only deployment-directory/Docker access.

Record the VPS ID, disk-encryption evidence, public IP, firewall policy ID, allowed CIDRs, SSH host-key fingerprint, and patch date in the owner-controlled operations register.

## 3. Environment setup

Use `ops/env/compose.env.example` only as a field list. `REDIS_PASSWORD` must match the percent-encoded password in all three backend Redis URLs. `runtime.env` is backend-only and is never mounted into Caddy or Redis.

Use standard OTLP variables for Grafana Cloud. Compose sets independent API, worker, and Beat service names. Never put customer, chat, phone, receipt, authorization, database URL, Telegram URL, or token values in telemetry attributes.

Validate configuration inside the immutable image before deployment. The command must print only the fixed success string:

```bash
docker run --rm --env-file /etc/gents-saloon/runtime.env IMAGE_DIGEST \
  python -c 'from app.core.config import get_settings; get_settings(); print("configuration valid")'
```

## 4. Database release gate

1. Confirm a successful Supabase backup and PITR coverage before a production migration.
2. Compare every migration checksum with `supabase_migrations.schema_migrations`. Stop on a missing historical migration or checksum mismatch.
3. Reconstruct an empty database in CI from the complete forward chain.
4. Restore a current production-like snapshot to staging and rehearse the exact pending migration set.
5. Run RLS advisors, database integration/concurrency tests, and application smoke tests against staging.
6. Apply the same immutable set to production during the approved window. Never edit an applied migration or run a down migration during rollback.
7. Attach migration identifiers, checksums, duration, approver, and advisor results to the release record.

## 5. Build, deployment, and rollback

The manual GitHub release workflow is protected by separate `staging` and `production` environments. It validates topology, builds the non-root image, attaches SBOM and max-mode provenance, pushes to GHCR, creates the manifest, transfers only release files over verified-host-key SSH, and invokes `ops/deploy.sh`.

`ops/deploy.sh` accepts only a GHCR digest and full Git SHA, serializes deployments, starts the release with Compose health gates, checks public TLS readiness, and restores the previous application release if a gate fails. It never reverses database migrations. Before production, both Vercel deployment IDs must have passed their checks and point to production API/Supabase resources.

Rollback triggers include failed readiness, elevated 5xx/latency, broken authorization, outbox growth, duplicate Telegram handling, checkout divergence, or a security/privacy signal:

1. Freeze deployment and record the incident/request ID.
2. Suspend the affected scope first if financial or cross-tenant integrity may be affected.
3. Run `ops/rollback.sh FULL_PRIOR_GIT_SHA`; then select both prior approved Vercel deployments.
4. Re-run readiness, auth, tenant-isolation, booking/checkout replay, Telegram synthetic update, and outbox checks.
5. If the forward schema is incompatible, deploy a forward compatibility fix. Use PITR only for owner-declared disaster recovery with an accepted data-loss window.

## 6. Backup and restore

Enable Supabase PITR at RPO 15 minutes or better and record the plan, recovery window, alert route, and restore-only credential owner.

`ops/backup-recovery-bundle.sh` packages only the two protected environment files and current release identifiers, age-encrypts locally, uploads the encrypted object and checksum to an S3-compatible path, and removes plaintext temporary files. The bucket enforces TLS, versioning, object retention, server-side encryption, least privilege, and separate restore-only access. The writer cannot delete or shorten retention.

Restore procedure:

1. Open an incident/change record and choose a recovery timestamp.
2. Fetch the object and checksum using restore-only access.
3. Set `AGE_IDENTITY_FILE` and `BUNDLE_SHA256_FILE`; run `ops/restore-recovery-bundle.sh BUNDLE` without confirmation and review the dry run.
4. Restore Supabase into an isolated project first. Verify migration state, RLS, row counts, journal balance, receipt continuity, Storage, Auth, and application smoke tests.
5. After owner approval, apply configuration with `CONFIRM_RESTORE` and deploy the recorded digest.
6. Rotate recovery credentials, reconnect Vercel/bots, and monitor a full business cycle.

Time the drill from declaration to verified service. It passes only with observed RPO ≤15 minutes and RTO ≤4 hours.

## 7. Grafana Cloud and alerts

Send OTLP logs, metrics, and traces over TLS using environment-scoped credentials and redacted low-cardinality labels. Create and fault-inject alerts for:

| Signal | Required alert |
|---|---|
| API | readiness down; p95 latency; rejection anomaly; 5xx rate |
| Database | connection exhaustion; query latency; failed readiness |
| Redis | unavailable; memory near limit; rejected writes; eviction (must remain zero) |
| Celery | worker/Beat missing; failure/retry surge; queue age |
| Telegram | webhook ack p95; secret-rejection spike; bot unhealthy; duplicate fixture failure |
| Outbox/export | oldest pending age; dead letters; exhausted delivery; export failure/expiry |
| Host/edge | disk/inode/memory/CPU; certificate expiry; Caddy 5xx; unexpected listening port |
| Recovery/release | backup/checksum/PITR/deployment/rollback/digest failure |

Route paging alerts to two owner-approved responders. Record alert IDs/screenshots without PII or secrets.

## 8. Performance, security, UAT, and cutover

Use synthetic identities in production-like staging. Test 50 shops and 201 bots with duplicate, retry, and worker-crash injection. Pass only when webhook acknowledgement is under 1 second, normal API p95 under 500 ms, checkout p95 under 2 seconds, and queue propagation under 3 seconds with no lost/duplicate update or financial divergence.

Run backend gates, both dashboard Playwright/axe suites, dependency/secret audits, clean migration reconstruction, Supabase advisors, penetration testing, privacy/90-day-chat-retention review, and a current official UAE VAT/e-invoicing review. A dated Phase 6 audit must have zero unresolved Critical/High findings. Staging UAT covers all roles, two owner shops, suspension, receipt/reversal, offboarding, every bot, AI outage, Realtime, mobile/touch, keyboard, accessibility, restore, and rollback.

The approved recovery plan replaces the canonical shop pilot with staging UAT, synthetic production canaries, and rollback validation followed by one cutover. Before launch, the owner signs a dated variance with rationale, risk, compensating controls, monitoring owner, rollback trigger, review date, and expiry. An unsigned variance is not approval.

Production canaries use synthetic tenant/customer identities for reads, booking create/cancel/replay, isolated non-fiscal checkout, Telegram delivery, and dashboard auth. They never mix with statutory receipt sequences.

At cutover: freeze changes, verify PITR/alerts, apply the approved migrations, deploy both Vercel IDs and backend digest, register production webhooks, run canaries, and obtain owner go/no-go. Maintain 24-hour hypercare with named responders, alert review, outbox/bot/checkout reconciliation, and written closeout.

## 9. Open evidence checklist

- [ ] Separate staging/production Supabase, Vercel, bot, Grafana, and S3 resources recorded.
- [ ] Hardened VPS, encrypted disk, network, and SSH evidence recorded.
- [ ] Production secrets validated without disclosure.
- [ ] GHCR/GitHub environment protection and restricted deployment key proven.
- [ ] Supabase PITR and encrypted retained S3 restore-only access proven.
- [ ] Grafana telemetry and every alert fault injection proven.
- [ ] Migration rehearsal, advisors, penetration, privacy, retention, and UAE legal reviews passed.
- [ ] 50-shop/201-bot load thresholds passed without loss/duplication.
- [ ] Both Playwright/accessibility suites and staging UAT passed.
- [ ] Restore RTO ≤4 hours and rollback drill passed.
- [ ] Owner signed the no-pilot variance and cutover approval.
- [ ] Canaries passed and 24-hour hypercare closed.
