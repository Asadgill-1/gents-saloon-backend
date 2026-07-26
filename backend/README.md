# Backend Foundation

Current cross-repository handoff: [../START_HERE.md](../START_HERE.md).

Phase 0 provides FastAPI, validated configuration, async PostgreSQL pooling, Redis, Celery, one worker-health task, safe health endpoints, tests, and a locked Python environment. Phase 1 T1.2 adds current-JWKS Supabase JWT verification and `GET /api/v1/me/context`, whose owner/shop roles are derived only from PostgreSQL. Static, unit, real-database, and native PostgreSQL/Memurai/API/Celery paths are verified.

Phase 1 T1.3 adds `POST /api/v1/platform/tenants`. It requires a verified platform administrator, an `Idempotency-Key`, and an existing Supabase Auth UUID for the owner. It atomically creates the application profile, business ownership, first shop, subscription, initial cash receipt, audit, and outbox records. The Supabase Auth invitation remains a separate trusted-server prerequisite because an external Auth API call cannot participate in the PostgreSQL transaction.

Phase 1 T1.4 adds platform-admin cash receipt/reversal, subscription suspend/resume, and billing-mode transition APIs. Entitlement is resolved from the database at the business/shop scope with an exact 00:05 Asia/Dubai expiry boundary. Celery Beat runs the idempotent expiry worker at that boundary, and tenant suspension maps to a generic HTTP 423 response without billing details.

Phase 1 T1.5 adds a shared database-derived tenant request dependency, the first protected shop-session shell, a privacy-safe opaque-token public availability endpoint, and transport-independent Telegram subscription middleware. The middleware acknowledges suspended trusted updates but runs no tenant operation. Platform administrators retain global authorization and bypass only the tenant entitlement gate.

Phase 1 T1.6 adds private versioned tenant exports, reauthorized short-lived signed downloads, explicit delivery confirmation, export-first freeze, and soft archive. The export worker uses short PostgreSQL claims with `FOR UPDATE SKIP LOCKED`, stale-claim recovery, safe failure codes, and private Supabase Storage. Redis-backed limits cover authenticated, privileged platform, and public routes and fail closed when enforcement is unavailable.

Phase 2 T2.1 adds the database sources for services, shop-isolated customers, business hours/closures, barber schedules/breaks/leave/unavailability, effective legal/VAT profiles, and immutable effective commission rules. All ten tables use composite tenant constraints, forced RLS, explicit read-only browser grants, and least-privilege policies.

Phase 2 T2.2 adds PostgreSQL-backed appointment holds, booking/service snapshots, queue counters, deterministic Any Barber allocation, booking mutation APIs, and a minute Celery task for hold expiry plus T-30 promotion. One short shop-scoped transaction performs operator authorization, entitlement locking, stale-hold cleanup, allocation, state, idempotency, audit, and outbox. GiST/unique constraints are final backstops; Redis is not part of booking or queue correctness.

Phase 2 T2.3 adds effective VAT/non-VAT legal-document selection, atomic fiscal-year sale/credit-note counters, and cash-shift APIs for open, preview, manual pay-in/pay-out, and close. Internal source-backed movement types connect later checkout/refund/advance/payout modules without exposing those authorities to clients. PostgreSQL independently enforces one open shift per register, immutable movements, one close transition, and expected/count/variance reconciliation. T2.4 checkout, payments, VAT, and commission snapshots are next.

## Local setup

Docker option, from the repository root:

```powershell
Copy-Item .env.example .env
docker compose -f docker/compose.dev.yml up -d
Set-Location backend
uv sync --locked --dev
uv run python -m app.devserver
```

Native Windows fallback when Docker is unavailable:

1. Install PostgreSQL 17 and Memurai in their standard `C:\Program Files` locations.
2. Copy `.env.example` to `.env`, then change `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` from port `6379` to `127.0.0.1:6380`.
3. Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-native-deps.ps1
Set-Location backend
uv sync --locked --dev
uv run python -m app.devserver
```

The one-process execution-policy bypass avoids changing the workstation policy. The script starts localhost-only PostgreSQL and a separate password-protected Memurai instance using `docker/memurai.dev.conf`. Stop only the project Memurai instance with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-native-deps.ps1
```

In another terminal for either dependency option:

```powershell
Set-Location backend
uv run celery -A app.core.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

Celery’s `--pool=solo` is for local Windows development. Production uses the default prefork worker inside Linux containers.

Checks:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy app workers
uv run pytest
uv run pip-audit
```

Liveness: `GET http://localhost:8000/health/live`.
Readiness: `GET http://localhost:8000/health/ready`.
Actor context: `GET http://localhost:8000/api/v1/me/context` with `Authorization: Bearer <Supabase access token>`.
Tenant onboarding: `POST http://localhost:8000/api/v1/platform/tenants` with bearer authentication and `Idempotency-Key`.
Protected shop session: `GET http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/session` with bearer authentication.
Public shop availability: `GET http://localhost:8000/api/v1/public/shops/{opaque_token}/availability`.
Request export: `POST http://localhost:8000/api/v1/platform/exports`.
Issue export download: `GET http://localhost:8000/api/v1/platform/exports/{export_id}/download`.
Confirm export delivery: `POST http://localhost:8000/api/v1/platform/exports/{export_id}/confirm-delivery`.
Freeze offboarding: `POST http://localhost:8000/api/v1/platform/offboarding`.
Archive offboarding: `POST http://localhost:8000/api/v1/platform/offboarding/{case_id}/archive`.
Create booking: `POST http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/bookings`.
Current legal document profile: `GET http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/legal-document-profile`.
Open cash shift: `POST http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/open`.
Preview cash shift: `GET http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/{cash_shift_id}`.
Record manual cash movement: `POST http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/{cash_shift_id}/movements`.
Close cash shift: `POST http://localhost:8000/api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/{cash_shift_id}/close`.
Booking transitions: `POST .../bookings/{booking_id}/{confirm|reschedule|start|complete|cancel|no-show}`.

The normal test suite skips its isolated-database integration modules. The root reconstruction command applies every migration, executes the RLS matrix, then enables and runs actor-context, onboarding, subscription, entitlement-surface, export/offboarding, and booking concurrency integration tests against `gents_saloon_phase1_test`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-database.ps1
```

## Worker ownership by phase

- Phase 0: `workers/health.py` and Celery application/configuration.
- Phase 1: subscription expiry, tenant export, and offboarding jobs.
- Phase 2: `workers/bookings.py` hold expiry/T-30 promotion, plus later outbox dispatch and reconciliation/report preparation.
- Phase 3: Telegram delivery, reminders, escalations, and scheduled reports.

The official Supabase MCP is authenticated as project-scoped development tooling, and the official `supabase` plus `supabase-postgres-best-practices` skills are installed globally. MCP is not part of the deployed API/worker runtime. As of 2026-07-26, the development project has 30 RLS-enabled application tables, eleven migrations, no application rows, a private ZIP-only `tenant-exports` bucket, zero Security Advisor findings, zero browser mutation policies/privileges on booking/legal/cash tables, zero missing FK indexes, and INFO-only unused-index notices expected before production traffic. A live object upload/signed-download/delete smoke remains open until the backend receives its service-role credential through the approved secret channel.

Next.js DevTools MCP `0.4.0` was evaluated and removed because it introduced unresolved High dependency advisories. Do not install it until an audited fixed release is available. A custom product MCP remains deferred until an approved external AI client requires scoped product access.
