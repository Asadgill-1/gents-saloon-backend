# Phase 1 — Tenant and SaaS Platform

## Status — 2026-07-26

**Implementation complete; security gate open.** T1.0–T1.6 are implemented and locally verified. T1.7 was executed, but the [Phase 1 security audit](../security-audits/PHASE_1_2026-07-26.md) is not passed because inherited credential rotation, remote GitHub/CI evidence, and a live private-Storage round trip remain open. The owner explicitly authorized Phase 2 to proceed while these gates remain visible; do not call Phase 1 passed without the missing evidence.

- Complete: T1.0 tooling/current-doc verification and live empty-project baseline.
- Complete: T1.1 core schema, local reconstruction, actor/RLS matrix, three forward remote migrations, and remote advisor remediation.
- Complete: T1.2 verified Supabase JWT and database-derived actor context endpoint.
- Complete: T1.3 atomic, idempotent, platform-admin tenant onboarding.
- Complete: T1.4 cash subscriptions, suspension/resume, billing-mode transition, entitlement clock, and expiry worker.
- Complete: T1.5 entitlement surface integration across API, bot gate, shop/public shells, and global platform shell.
- Complete: T1.6 versioned private export, delivery confirmation, export-first freeze, and soft archive.
- Executed but not passed: T1.7 security audit and handoff.

## Outcome

One business owner can operate multiple shops; staff remain explicitly shop-scoped. Manual subscriptions, suspension, export, and offboarding are enforced consistently.

## Locked boundaries

- Commercial tenant: `business`; operational tenant: `shop`.
- First release: exactly one active primary owner per business.
- Owner reads all shops in the owned business; staff read only active memberships.
- Browser/Supabase access is read-only. All mutations use FastAPI transactions.
- Anonymous roles receive no table grants.
- Public tables use explicit grants plus RLS. Privileged RLS helpers live in non-exposed `private`, use a fixed search path, and are not executable by `PUBLIC`.
- No remote DDL until the local migration and actor/table matrix pass.

## Executable tasks

### T1.0 — Tooling and contract lock

Files:

- `docs/phases/PHASE_1_TENANT_PLATFORM.md`
- `docs/DATA_MODEL.md`
- `START_HERE.md`

Work:

1. Verify current Supabase changelog, RLS, grants, and migration guidance.
2. Confirm live project has no application tables/migrations and zero advisor findings.
3. Record explicit Data API grants and private helper-function rules.

Verify:

```text
Supabase MCP list_tables(public) = []
Supabase MCP list_migrations = []
Supabase security/performance advisors = []
```

### T1.1 — Core tenant migration

Files:

- `supabase/migrations/*_tenant_platform_core.sql`
- `supabase/tests/bootstrap_local.sql`
- `supabase/tests/phase1_tenant_rls.sql`
- `scripts/test-database.ps1`

Create:

- enums required by Phase 1;
- `user_profiles`, `platform_admins`, `businesses`, `business_owners`, `shops`, `shop_memberships`, and `bots`;
- `subscriptions`, `subscription_cash_receipts`, `tenant_exports`, and `offboarding_cases`;
- `idempotency_keys`, append-only `audit_log`, and `outbox_events`;
- composite tenant foreign keys, partial uniqueness, lifecycle checks, and policy indexes;
- private authorization helpers, explicit grants, forced RLS, and read policies.

Acceptance:

- one active primary owner per business;
- memberships cannot reference a shop in another business;
- active Auth/Telegram membership is unique per shop;
- business and per-shop subscription scopes cannot overlap or contradict `billing_mode`;
- anonymous/table writes are denied;
- owner can read sibling shops; shop staff cannot;
- other-business and inactive actors are denied;
- token ciphertext, webhook secrets, idempotency, and outbox payloads have no browser grant.

Verify:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-database.ps1
```

### T1.2 — Verified actor context

Files:

- `backend/app/core/auth.py`
- `backend/app/core/authorization.py`
- `backend/app/api/context.py`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_context.py`
- `backend/tests/test_context_database.py`
- `backend/pyproject.toml`
- `.github/workflows/backend-ci.yml`
- `scripts/test-database.ps1`

Work:

1. Verify Supabase JWT via current JWKS, issuer, audience, expiry, and not-before.
2. Resolve platform-admin, owner, and active shop memberships from PostgreSQL.
3. Add `GET /api/v1/me/context`.
4. Return business/shop IDs and roles only from database resolution.

Acceptance:

- request IDs/roles never create authorization;
- inactive or missing identity fails closed;
- owner with two shops receives both;
- staff receives only assigned shops.

Verified:

```text
ES256 signature, issuer, audience, expiry, not-before, malformed token PASS
Untrusted JWT/request role and shop scope ignored                         PASS
Owner two shops / staff one shop / inactive profile                      PASS
Authorization database unavailable -> generic HTTP 503                   PASS
Real PostgreSQL resolver against reconstructed migrations                PASS
PyJWT 2.13.0 pinned from verified PyPI release; dependency audit         PASS
```

### T1.3 — Atomic onboarding

Files:

- `backend/app/services/tenant_service.py`
- `backend/app/api/platform/tenants.py`
- `backend/tests/test_tenant_onboarding.py`
- `backend/tests/test_tenant_onboarding_database.py`
- `.github/workflows/backend-ci.yml`
- `scripts/test-database.ps1`

Work:

1. Accept an existing Supabase Auth UUID created by the separate trusted-server invite flow.
2. Platform-admin-only create the application profile, business, `business_owners` primary-owner relation, initial shop, correctly scoped subscription, initial cash receipt, audit, and outbox in one transaction.
3. Do not create a fake owner `shop_memberships` row; that table is only for manager/receptionist/barber assignments.
4. Generate the public queue token server-side, persist only its SHA-256 hash in `shops`, and return the token only in the protected onboarding response/idempotent replay.
5. Require `Idempotency-Key`.
6. Roll back every row on any validation or write failure.

Acceptance:

- retry returns the recorded result;
- changed payload with the same key returns conflict;
- no partial tenant exists after injected failure.

Verified:

```text
Two concurrent same-key requests create exactly one tenant and same response PASS
Same key with changed validated payload returns conflict                  PASS
Non-platform-admin, missing owner, and inactive owner fail closed         PASS
Business-wide and per-shop subscription scope derived correctly          PASS
Forced late shop-token collision rolls back profile/business/owner/key    PASS
Owner authorization uses business_owners; no fake shop_memberships row    PASS
Audit/outbox omit the returned public queue token                         PASS
```

### T1.4 — Manual subscriptions and entitlement

Files:

- a new forward migration created by `supabase migration new`;
- `backend/app/services/subscription_service.py`;
- `backend/app/core/entitlements.py`;
- worker task for expiry;
- clock, state, concurrency, and receipt tests.

Work:

1. Append cash receipt/reversal history.
2. Resolve business/per-shop entitlement at 00:05 after inclusive `paid_until` in `Asia/Dubai`.
3. Implement audited suspend/resume and billing-mode transition.
4. Recheck entitlement inside critical transactions.

Acceptance:

- no overlapping billing modes/scopes;
- non-payment resume requires current paid coverage;
- suspension returns HTTP 423 without exposing billing details;
- expiry and transition jobs are idempotent.

Verified:

```text
Forward migration created through Supabase CLI and reconstructed locally       PASS
Remote migration 20260725165706 applied through project-scoped Supabase MCP    PASS
Receipt/reversal append-only and mirror constraints                            PASS
Concurrent same-key cash receipt creates one immutable receipt                  PASS
Non-payment resume without current paid coverage fails closed                   PASS
Cash renewal while suspended followed by explicit resume                        PASS
Business -> per-shop -> business transition, exact scope coverage                PASS
Concurrent transition replay and changed-payload conflict                        PASS
00:04:59 active / 00:05 expired Asia/Dubai clock boundary                       PASS
Concurrent expiry workers produce one state transition and one outbox event      PASS
Generic authenticated HTTP 423 response exposes no billing detail                PASS
Remote Security Advisor zero findings; remote application rows remain zero       PASS
```

Implementation notes:

- Onboarding now records the required initial cash receipt in the same tenant transaction.
- Receipt reversals are positive mirror rows linked to the original. They correct immutable collection evidence; they do not silently revoke already-granted access. Suspension or a new paid-coverage decision is an explicit audited operation.
- Billing-mode transition locks the business, shops, and subscriptions in a fixed order. Business-to-shop copies the current coverage to every non-archived shop. Shop-to-business is rejected unless every non-archived shop has the same compatible state and coverage.
- PostgreSQL remains UTC. Python and SQL calculate the entitlement deadline in `Asia/Dubai`.

### T1.5 — Entitlement surface integration

Files:

- `backend/app/api/tenant.py`
- `backend/app/api/public.py`
- `backend/telegram_bot/subscription_gate.py`
- `backend/tests/test_subscription_gate.py`
- `backend/tests/test_entitlement_surfaces_database.py`
- both dashboards' server backend helpers, protected/public shells, contract tests, and semantic status styling
- `.github/workflows/backend-ci.yml`
- `scripts/test-database.ps1`

Acceptance:

- **PASS:** tenant API, bot gate, dashboard contract, and public availability surfaces agree on active/suspended/archived state and fail closed on backend errors;
- **PASS:** valid trusted bot updates are acknowledged during suspension, perform no tenant operation, and receive only a generic unavailable reply;
- **PASS:** owner scope, explicit staff shop scope, cross-shop/cross-business denial, and platform-admin global bypass are database-derived and pass reconstructed-PostgreSQL tests;
- **PASS:** public invalid/suspended/archive states reveal no tenant ID, shop name, billing state, or payment detail;
- **PASS:** dashboard server components verify claims, forward only the raw access token to FastAPI, validate returned scope, and map all non-auth failures to one neutral shell;
- **PASS:** platform administration remains globally authorized and does not depend on tenant subscription status;
- **Scoped deferral:** actual Telegram webhook verification/transport is Phase 3; Playwright product-state coverage is Phase 4/5. T1.5 provides and tests the reusable gate and shells only.

Verification:

```text
uv run ruff check .                         PASS
uv run ruff format --check .                PASS
uv run mypy app workers                     PASS
uv run pytest -q                            PASS
scripts/test-database.ps1                   PASS — RLS + 6 application DB tests
shop npm run check                          PASS — 6 tests + production build
platform npm run check                      PASS — 5 tests + production build
pip-audit / both npm audits                 PASS — 0 known vulnerabilities
Supabase Security Advisor                   PASS — 0 findings
Supabase Performance Advisor                INFO-only unused indexes on empty tables
```

### T1.6 — Export and offboarding

Files:

- one forward lifecycle-hardening migration for export/offboarding constraints, transition guards, active-case uniqueness, and worker indexes;
- `backend/app/services/platform_operations.py` for shared platform-admin, idempotency, audit, and outbox primitives;
- `backend/app/services/export_service.py` for versioned archive generation, private object storage, download-link issuance, delivery confirmation, freeze, and archive transitions;
- `backend/app/api/platform/exports.py` for platform-admin-only request/download/delivery/archive endpoints;
- `backend/workers/tenant_exports.py` plus Celery Beat recovery sweep;
- archive unit tests, reconstructed-PostgreSQL state/concurrency/content tests, and remote advisor checks.

Locked contract:

1. Export schema version `2026-07-26.v1` covers the Phase 1 tenant tables with explicit column allowlists. Bot token ciphertext, webhook hashes, queue-token hashes, internal idempotency/outbox rows, service credentials, and storage credentials are never exported.
2. The archive format is a ZIP containing `manifest.json` plus JSON and CSV for each dataset. The manifest records subject, generated time, table/column coverage, row counts, and redactions. PostgreSQL stores the archive byte size and SHA-256.
3. Objects use opaque export-ID keys in a private Supabase Storage bucket. Supabase provides TLS transport, encrypted disks at rest, and a signed download URL capped at 15 minutes. The bucket is never public.
4. Export processing claims rows with `FOR UPDATE SKIP LOCKED`, commits before archive/storage work, and finalizes in a new short transaction. A safe failure code is recorded; exception text and contents are never persisted or logged.
5. Every download-link request re-verifies the active platform administrator and export expiry before issuing a short-lived signed URL. Link issuance is audited. Delivery is a separate explicit idempotent confirmation.
6. Offboarding freezes first in one idempotent transaction. Business scope freezes all shops/subscriptions; shop scope freezes only that shop and its per-shop subscription. Bots become unhealthy and public/tenant operations fail closed immediately.
7. Archive is permitted only after export delivery confirmation. It soft-archives the subject, deactivates only subject-scoped ownership/memberships, keeps all history, and relies on database-derived authorization to revoke tenant access without harming a user’s access to other businesses/shops.
8. No hard deletion, no customer anonymization, and no security-incident two-person override are implemented in T1.6. Those require a separate approved retention/legal contract.

Acceptance:

- export is private, encrypted, versioned, checksummed, expiring, and reauthorized at download;
- offboarding freezes before export and archives only after recorded delivery;
- sessions, bots, and public pages are disabled;
- no hard tenant deletion.

Evidence:

```text
Local migration reconstruction                 PASS — all 5 Phase 1 migrations
RLS + database integration                     PASS — 8 tests
Backend unit suite                             PASS — 46 passed, 8 DB-gated skips
Backend lint/type/dependency audit              PASS
Remote migration 20260726061032                PASS
Remote tenant-exports bucket                   PASS — private, ZIP-only, 100 MB
Remote Supabase Security Advisor               PASS — 0 findings
Remote Performance Advisor                     INFO-only unused indexes on empty tables
Live Storage object round trip                 OPEN — service-role runtime secret not provisioned
```

### T1.7 — Phase security and handoff

Files:

- `docs/security-audits/PHASE_1_<date>.md`
- `docs/REQUIREMENTS.md`
- `docs/PROJECT_CONTEXT.md`
- `START_HERE.md`
- both dashboard `STATUS.md` files.

Verify:

- clean migration reconstruction;
- full actor/table/RPC/API isolation matrix;
- Supabase security and performance advisors;
- backend lint/type/tests/audit;
- frontend checks where touched;
- current/history/bundle secret scans;
- `ponytail-audit` and `ponytail-debt`.

Result:

- The dated audit is recorded at `docs/security-audits/PHASE_1_2026-07-26.md`.
- A missing distributed request limit was found as High and fixed with Redis-backed atomic limits plus allow/reject/fail-closed tests.
- No new unresolved Critical/High defect remains in the Phase 1 code.
- The phase gate remains open for inherited credential rotation, remote CI/repository controls, and the live private-Storage round trip.
- `ponytail-audit`: lean already; no deletion/simplification finding.
- `ponytail-debt`: no source-code markers.

## Phase gates

- Owner with two shops sees both and aggregate authorization context.
- Receptionist/barber in shop A cannot access shop B; owner can; other business cannot.
- Business and per-shop billing modes cannot overlap.
- Expiry boundary, manual suspension, resume rules, webhook acknowledgement, and HTTP 423 pass clock/state tests.
- Active/suspended/offboarded exports pass content, checksum, download-auth, and expiry tests.
- RLS advisor and full actor/table matrix are green.
- A dated Phase 1 security audit has zero unresolved Critical/High findings.
- No source-code `ponytail:` debt is unrecorded.
