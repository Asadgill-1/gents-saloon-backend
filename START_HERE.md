# Start Here — Current Project Handoff

Last updated: **2026-07-31, Asia/Dubai**

Checkpoint: **FULL-RECOVERY-BASELINE-2026-07-31**

Current phase: **Phase 3 — Telegram and AI recovery in progress; Phases 4–6 are incomplete**

This is the authoritative restart document for a new human, LLM, or AI coding agent. Read it before planning or changing code.

## 1. Read in this order

1. This file.
2. [CLAUDE.md](CLAUDE.md) for binding implementation and security workflow.
3. [Phase 3 execution plan](docs/phases/PHASE_3_TELEGRAM_AI.md).
4. [Phase 2 security audit](docs/security-audits/PHASE_2_2026-07-26.md) and [Phase 1 security audit](docs/security-audits/PHASE_1_2026-07-26.md).
5. [Security rules](docs/SECURITY.md) and [requirements ledger](docs/REQUIREMENTS.md).
6. [Master plan](docs/MASTER_PLAN.md), [data model](docs/DATA_MODEL.md), and [project decisions](docs/PROJECT_CONTEXT.md).

`Prompt.md.txt` and `Github repo information.txt` are historical owner inputs. They are not the current plan where they conflict with the documents above.

## 2. Repository layout

This folder contains **three independent Git repositories**. Run Git checks and commits separately in each one.

| Repository | Local path | Remote | Current responsibility |
|---|---|---|---|
| Backend/canonical docs | project root | `Asadgill-1/gents-saloon-backend` | FastAPI, PostgreSQL/Supabase, Redis, Celery, bots, canonical contracts |
| Shop dashboard | `saloon-shop-dashboard/` | `Asadgill-1/saloon-shop-dashboard` | Owner/shop/reception/POS frontend; Phase 4 product UI |
| Platform dashboard | `saloon-gents-system-owner-dashboard/` | `Asadgill-1/saloon-gents-system-owner-dashboard` | Platform-owner SaaS operations frontend; Phase 5 product UI |

All three repositories have recovery work on `codex/full-recovery`. The remote `main` checkpoints that existed before recovery were:

- backend/canonical docs: `e6f2329`;
- shop dashboard: `2ae5729`;
- platform dashboard: `7c08910`.

Phase 2 reports/e-invoice work is locally verified and committed on the recovery branch. Phase 3 now has a secure webhook/authorization/outbox/AI-containment foundation, durable multilingual customer booking callbacks, receptionist booking lifecycle actions, and transactionally reauthorized AI tools, but not the remaining staff/owner/master canonical flows or audit. Phase 4/5 now use real SSR authentication, backend reads/actions, pagination, and nonce CSP while retaining the visual styling; their remaining feature and E2E/audit gates are listed below.

The local Phase 1/2 migration chain reconstructs successfully through `supabase/migrations/20260726122713_reports_einvoice_boundary.sql`. Fresh remote checksum reconciliation is open because this checkout is not currently linked to a Supabase project.

## 3. Current delivery status

| Phase | Status | Meaning |
|---|---|---|
| Phase 0 — foundation | **Implementation and CI verified; owner gates open** | Credential rotation and GitHub repository-protection evidence remain |
| Phase 1 — tenant and SaaS platform | **Implementation complete; audit open** | T1.0–T1.6 locally verified; T1.7 cannot pass yet |
| Phase 2 — booking, POS, and money | **Implementation and security audit verified** | T2.0–T2.8 complete; dated Phase 2 security audit green |
| Phase 3 — Telegram and AI | **Customer and reception booking flows built; incomplete** | Complete reception operations plus barber/owner/master callbacks, multilingual snapshots, 201-bot/live staging proof, adversarial matrix, and dated audit remain |
| Phase 4 — shop dashboard | **Core operational slice built; incomplete** | Finish appointment/POS/receipt/owner/team/money flows, Playwright/axe/staging proof, and dated audit |
| Phase 5 — platform dashboard | **Core operational slice built; incomplete** | Finish export/download/detail/security/backup/escalation flows, Playwright/axe/staging proof, and dated audit |
| Phase 6 — production rollout | **Local baseline built; rollout not started** | Production image/Compose/release/rollback/telemetry/backup tooling and runbooks are implemented; VPS, isolated services, drills, audits, UAT, canaries, variance, cutover, and hypercare require owner-controlled execution |

## 4. What is implemented

### Canonical contracts

- Multi-tenant hierarchy is `Platform → Business → Shops`.
- One primary business owner may access all owned shops.
- Staff, customers, services, bookings, POS, cash, commissions, advances, payouts, bots, and reports are shop-scoped.
- Manual cash SaaS subscriptions support business-wide or per-shop billing.
- Non-payment suspension, resume, export-first offboarding, and soft archive behavior are specified.
- Fixed-percentage and tier/threshold commission models are specified. Tips remain 100% barber.
- PostgreSQL is authoritative for bookings, queue allocation, money, subscriptions, idempotency, audit, and outbox. Redis is disposable.

### Backend Phase 0

- Locked Python 3.12 environment in `backend/pyproject.toml` and `backend/uv.lock`.
- Validated/redacted configuration with production HTTPS, secret, CORS, and DEBUG-log rejection.
- Async PostgreSQL pool, Redis client, Supabase admin-client factory.
- FastAPI lifespan, exact-origin CORS, request IDs, production-disabled API docs.
- `/health/live` and fail-closed `/health/ready`.
- Celery JSON-only configuration with late acknowledgement, time limits, result expiry, and real `workers.health.ping`.
- Windows-compatible local Uvicorn selector-loop entry point.
- PostgreSQL/Redis development Compose and SHA-pinned backend CI.
- Native Windows dependency fallback with PostgreSQL 17.10 and a separate authenticated Memurai instance, plus verified start/stop scripts.

### Phase 1 tenant/RLS core

- Five forward Supabase migrations are applied locally and to the empty development project.
- Core identity, business, shop, membership, bot, subscription, receipt, export, offboarding, idempotency, audit, and outbox tables exist.
- Explicit Data API grants, forced RLS, private authorization helpers, backend-only deny policies, and composite tenant constraints are active.
- Local reconstruction tests cover owner, receptionist, barber, inactive user, other business, platform admin, anonymous/default-deny, forbidden browser writes, cross-tenant references, billing scope, append-only audit, helper execution, and FK indexes.
- Remote Supabase Security Advisor: zero findings.
- Remote Performance Advisor: INFO-only unused-index notices expected on empty tables; zero missing-FK-index findings.

### Phase 1 verified actor context

- Supabase access JWTs are verified against the current JWKS with an ES256/RS256 allowlist plus issuer, audience, expiry, issued-at, not-before, signature, and UUID-subject validation.
- JWT metadata, request business/shop IDs, and request roles are ignored for authorization.
- `GET /api/v1/me/context` resolves active profile, platform-admin status, owned businesses, all owned shops, and active staff memberships from PostgreSQL.
- Missing/malformed/expired tokens return generic 401; missing/inactive application profiles return generic 403; authorization-database failure returns generic 503.
- Unit/cryptographic tests and a real reconstructed-PostgreSQL integration gate prove owner-two-shop, staff-one-shop, and inactive-user behavior.

### Phase 1 atomic tenant onboarding

- `POST /api/v1/platform/tenants` requires a verified JWT, active platform-admin database record, and validated `Idempotency-Key`.
- The owner must already exist in Supabase Auth; the trusted-server Auth invitation is intentionally outside the PostgreSQL transaction.
- One short transaction creates/reuses the application owner profile, creates `businesses`, the primary `business_owners` relation, first shop, correctly scoped subscription, initial cash receipt, append-only audit, outbox, and completed idempotency response.
- Owners are not given fake manager/receptionist/barber memberships. Ownership itself grants every shop in the business.
- The public queue token is generated server-side; only its SHA-256 hash is stored in `shops`, and audit/outbox payloads omit the token.
- Concurrent same-key requests, changed-payload conflicts, non-admin/missing/inactive owner denials, both billing modes, and forced late-failure rollback pass against reconstructed PostgreSQL.

### Phase 1 subscription entitlement

- Platform-admin APIs record immutable AED cash receipts and linked mirror reversals, suspend/resume subscriptions, and atomically transition business-wide/per-shop billing.
- Every mutation rechecks the active platform administrator inside its short transaction, requires an idempotency key, and writes audit plus outbox records.
- Non-payment resume requires current paid coverage. Other expired resumes require current coverage or a reasoned, expiring manual override.
- Entitlement resolution is database-derived for the business/shop scope. `paid_until` remains active through 00:04:59 Asia/Dubai on the next day and expires exactly at 00:05.
- Explicit suspension/offboarding/archive overrides paid coverage. Authenticated tenant APIs have a generic HTTP 423 `subscription_suspended` handler with no billing detail.
- Celery Beat runs the idempotent PostgreSQL expiry worker at 00:05 Asia/Dubai; critical operations can use the locking entitlement recheck to prevent time-of-check/time-of-use access.
- Concurrent receipt, billing-mode, and expiry execution; receipt append-only rules; reversal mirroring; rollback/state behavior; and both billing directions pass against reconstructed PostgreSQL.

### Phase 1 entitlement surfaces

- `GET /api/v1/businesses/{business_id}/shops/{shop_id}/session` uses one shared FastAPI dependency that derives the actor and allowed shop from PostgreSQL. Route IDs are locators only; request/JWT roles and tenant claims are not authority.
- Active owners reach every owned shop, active staff reach only their explicit shop memberships, cross-shop and cross-business access fail, and inactive entitlements produce the existing generic HTTP 423 response.
- Platform administrators retain global authorization and bypass only the tenant entitlement gate; they do not inherit tenant roles or data through request parameters.
- `GET /api/v1/public/shops/{opaque_token}/availability` hashes the opaque token and returns only `available` or `unavailable`. Invalid, suspended, archived, and backend-error states reveal no tenant or billing detail.
- Transport-independent Telegram subscription middleware acknowledges a trusted valid update during suspension, does not execute its tenant operation, and sends only a generic unavailable message. Bot/webhook identity verification remains Phase 3 transport work.
- The shop dashboard has a server-authorized active/suspended shell at `/businesses/[businessId]/shops/[shopId]` plus the privacy-safe public `/q/[token]` shell. The platform dashboard root now requires a database-derived platform-admin context and remains independent of tenant entitlement.
- Both dashboards verify Supabase claims before using the session solely to forward its raw access token to FastAPI. No product POS, queue, billing, export, or onboarding UI was started.
- Reconstructed PostgreSQL tests prove API, bot, public, owner/staff/admin, suspension, and cross-tenant agreement. Pure frontend contract tests prove neutral failure mapping and response-scope matching.

### Phase 1 export, offboarding, and request limits

- Platform-admin-only, idempotent APIs request tenant exports, issue reauthorized short-lived signed links, confirm delivery, freeze offboarding scope, and soft-archive only after delivery.
- Export schema `2026-07-26.v2` creates a ZIP containing `manifest.json` plus JSON/CSV datasets from explicit column allowlists. It retains the Phase 1 datasets and adds safe e-invoice source documents; it excludes bot/webhook/queue/storage/provider credentials and internal delivery/idempotency rows and redacts credential-like audit payload fields.
- PostgreSQL records schema version, object key, byte size, content type, SHA-256, attempts, lifecycle timestamps, and safe failure codes. Transition triggers and partial unique indexes prevent illegal lifecycle moves or duplicate open offboarding cases.
- The Celery export worker claims rows with `FOR UPDATE SKIP LOCKED`, commits before storage work, recovers stale claims, and deletes expired objects while retaining audit metadata.
- The Supabase development project has a private ZIP-only `tenant-exports` bucket with a 100 MB object limit and no browser object policies. A live object upload/signed-download/delete smoke remains open until an approved backend service-role secret is provisioned.
- Business offboarding disables the whole business; shop offboarding affects only that shop and its per-shop subscription. Ownership/membership revocation is subject-scoped so a user keeps legitimate access elsewhere.
- Redis-backed limits cover authenticated context/tenant routes, privileged platform mutations, download-link requests, and public availability. Keys hash actor/IP identifiers, counters are atomic, excess requests return `429`, and Redis failure returns `503`.

### Phase 2 operational and money sources

- The owner explicitly approved the full production money scope: VAT/legal profiles, cash shifts, credit notes/refunds, effective commission, tips, advances, payout runs, and append-only balanced double-entry journal.
- Two forward migrations add services, isolated shop customer profiles, effective business hours, closures, barber schedules/breaks/leave/unavailability, effective shop legal/VAT profiles, and default/barber commission rules.
- Composite tenant foreign keys, barber-role triggers, exclusion constraints, E.164/TRN checks, immutable configuration guards, tier-JSON validation, overnight flags, and break-within-shift validation fail closed in PostgreSQL.
- Authenticated browser access remains read-only. Barbers cannot browse customer/legal data, receptionists cannot read commission rules, and a barber sees only an explicit own commission override.
- The same Telegram identity may exist as separate customer profiles in different shops without joining the records.
- Current official UAE guidance was rechecked: tax-invoice lines round to the nearest fils; B2C POS documents remain separate from the changing provider-neutral B2B/B2G e-invoicing boundary.
- Reconstructed RLS/adversarial tests cover shop A/shop B isolation, owner multi-shop reads, receptionist/barber least privilege, inactive users, platform admin, invalid ranges/rules, and forbidden browser writes.

### Phase 2 booking, holds, availability, and queue

- Two forward migrations add `bookings`, immutable `booking_services`, and PostgreSQL `queue_counters`, plus historical-state hardening.
- The database validates the queue/appointment/walk-in state machine, same-shop customer/barber/service references, immutable identity/snapshots/allocated queue numbers, and one reschedule per original.
- A GiST exclusion constraint rejects overlapping active appointment ranges for one barber. Five-minute holds are expired under the same short shop transaction lock and by the minute worker.
- FastAPI create/confirm/reschedule/start/complete/cancel/no-show routes require a verified tenant context and `Idempotency-Key`; services repeat operator authorization and entitlement locking inside the transaction.
- Owner, manager, receptionist, and platform admin are booking operators. A barber can read only assigned bookings through RLS and cannot mutate bookings through this operations API.
- “Any Barber” filters shop hours, closures, schedule/break, leave, and unavailability, then sorts by active work count and stable barber UUID.
- Walk-in/queue numbers and T-30 appointment promotion use shop-local business dates and PostgreSQL counters. Celery Beat rescans durable state every minute; Redis is absent from booking/queue correctness.
- Every successful mutation, hold expiry, and promotion writes audit/outbox in the same transaction. Reschedule and worker retries are idempotent.
- T2.2 exposed and fixed the shared entitlement lock’s invalid `FOR SHARE` on a nullable outer-join subscription row by locking business/shop, then locking and rereading the applicable subscription.
- Reconstructed SQL/RLS and application concurrency tests prove one same-slot winner, unique parallel queue numbers, deterministic barber allocation, expired-hold replacement, same-key replay, transition rejection, reschedule history, and one-shot T-30 promotion.
- Deliberate debt: allocation uses one advisory lock per shop. This is correct and suitable for current scale; split into ordered barber/counter locks only if production contention metrics justify the extra deadlock complexity.

### Phase 2 legal documents, receipt counters, and cash shifts

- Effective legal-document selection snapshots the shop's supplier name/address, VAT state, TRN, pricing mode, document type, source profile, and effective range. VAT profiles support full or simplified tax invoices; non-VAT profiles require receipts without a TRN.
- PostgreSQL allocates separate fiscal-year sale and credit-note sequences per shop with atomic row updates. Generated numbers use the trusted shop internal code; no number-allocation endpoint exists for clients to burn numbers before checkout.
- Cash shifts enforce one open row per case-insensitive shop/register label. Opening, manual pay-in/pay-out, source-backed cash sale/advance/payout/refund effects, preview, and one-way close are implemented through authenticated FastAPI transactions.
- Expected cash is `opening + cash sales + pay-ins - pay-outs - advances - payouts - refunds`. Card never enters the physical-cash movement types. Closing stores expected, counted, and exact variance; database triggers independently reject mismatched close totals and post-close mutation.
- Owner, manager, receptionist, and platform admin may operate cash after an in-transaction entitlement recheck. Barbers cannot read cash/counters through RLS or operate the APIs. Browser writes are absent; every mutation is idempotent and writes audit/outbox in the same commit.
- Parallel receipt allocation produces unique sequences; parallel open attempts for one register produce one winner; same-key open/close replay returns one durable result. The clean reconstruction/RLS/concurrency suite passes.

### Phase 2 checkout, payments, commission snapshots, and journal

- `POST /api/v1/businesses/{business_id}/shops/{shop_id}/pos/checkout` completes an existing `completed` booking. Counter sales use the same path through a walk-in booking, avoiding a second financial mutation flow.
- The server derives customer, barber, service price, legal/VAT profile, and effective commission rule from PostgreSQL. The request may provide only booking-service discounts, cash/card tender, a separate tip, and the open cash shift required for cash.
- Decimal-only, half-up calculations support VAT-inclusive and VAT-exclusive pricing, line discounts, split tender, and exact header reconciliation. Tips are separate and 100% barber. The AED 120 tier fixture produces barber AED 25 and shop AED 95.
- One transaction allocates the receipt, writes immutable transaction/item/payment/commission snapshots, records only the cash tender in the cash shift, creates a balanced double-entry journal, and completes idempotency, audit, and outbox.
- Commission snapshots live in a separate restricted table: receptionists can read operational receipt/payment data but not commission; a barber can read only their own commission; managers, owners, and platform administrators retain the approved views.
- Deferred PostgreSQL checks independently require item/header/payment/commission reconciliation, an open shift for cash, at least two journal postings, and equal positive debits and credits. Completed financial rows reject update/delete.
- Clean reconstruction, RLS/IDOR, exact-calculation, parallel same-key checkout, replay, golden journal, cash-linkage, forbidden authority/PAN input, and append-only tests pass.

### Phase 2 voids, refunds, credit notes, and reversals

- `POST /api/v1/businesses/{business_id}/shops/{shop_id}/pos/transactions/{transaction_id}/corrections` creates either a bounded refund or a conservative same-shift void; the route requires `Idempotency-Key`.
- The server locks and reads the immutable original receipt, derives VAT, commission, tips, and tender from stored snapshots, and rejects client financial authority beyond requested item/tip/payment return amounts.
- Partial refunds are cumulatively bounded per item, tip, and tender method. Proportional net/VAT and barber commission use Decimal half-up calculations, with an exact final slice that reconciles to the original snapshot.
- A void is the only correction for the receipt, reverses the complete original sale, and is permitted only for cash-only tender while the original cash shift remains open. Any sale containing card tender uses a refund/credit note because terminal settlement state is not tracked.
- Each correction atomically allocates a credit-note number and writes append-only correction header/items/payments/restricted commission reversals, cash refund movement where applicable, a journal linked to the original checkout journal, audit, outbox, and idempotency response.
- PostgreSQL deferred validation independently enforces original-plus-corrections reconciliation, tender bounds, same-shift voids, cash linkage, and exact reversing postings. Receptionists cannot read correction commission rows, barbers cannot read return payments, and tenant/cross-shop boundaries remain forced by RLS and composite foreign keys.
- Clean reconstruction, same-key replay, competing-refund race, exact half refund, full cash void, card-tender void denial at service/database layers, original immutability, journal/cash reconciliation, RLS/IDOR, and append-only tests pass.

### Phase 2 advances, payout runs, and settlement

- `POST .../advances` and the payout create/approve/pay/cancel routes are implemented. Only an active business owner or platform administrator may mutate; every route requires an `Idempotency-Key` and repeats tenant, role, and entitlement checks inside its transaction.
- An advance disburses cash once from an open shift, debits `advance_receivable`, credits `cash`, and remains `open` until paid-run applications reduce outstanding to zero. There is no advance cancellation transition.
- Payout periods are closed half-open UTC ranges `[start,end)`. Non-cancelled periods for one shop cannot overlap, and only one approved run can reserve a shop's outstanding advances at a time.
- Draft items aggregate immutable commission/tip earning and correction snapshots from the period. Signed manual adjustments require a reason. Approval bounds deductions by both gross payable and outstanding advances; payment alone creates applications, cash settlement, and the exact journal.
- Concurrent tests prove same-key advance/run replay, one winner for competing payment calls, no double deduction/payment, exact source totals, and cancellation without balance effects. Forced RLS proves receptionist none, barber-own reads, owner/business reads, unrelated-owner isolation, and platform visibility.
- Native PostgreSQL reconstruction, SQL/RLS tests, 13 application database integration tests, Ruff, mypy, 61 backend tests, dependency audit, secret scans, and the T2.6 task security checkpoint pass.

### Phase 2 reports and provider-neutral e-invoice source

- `GET /api/v1/businesses/{business_id}/shops/{shop_id}/reports` is owner/assigned-manager/platform-only. It reads normalized booking, transaction, correction, tender, cash, advance, payout, and journal truth directly for a required half-open UTC period of at most 366 days.
- `GET /api/v1/businesses/{business_id}/overview` is owner/platform-only and aggregates only currently entitled active shops. Per-shop subscription pause therefore excludes that shop, and a manager cannot infer sibling-shop or business totals.
- Barber and shop result sets use stable UUID keyset pagination capped at 100. Exact integration fixtures prove one-page and multi-page equality plus sale/refund/cash/commission/payout/journal reconciliation.
- Every platform subscription cash receipt now creates one immutable B2B `prepared` source envelope; a receipt reversal creates one linked credit-note envelope. PostgreSQL derives the source/buyer snapshot and independently validates it before insert.
- `e_invoice_documents` is forced-RLS, owner/platform read-only in browser, and append-only. It has no POS transaction, booking, customer, provider-payload, callback, XML, or delivery field; ordinary B2C saloon receipts remain in the POS flow.
- Export schema `2026-07-26.v2` includes the safe e-invoice dataset. The boundary writes audit/outbox atomically, but does not claim accreditation or select an Accredited Service Provider.
- Local reconstruction and the T2.7 security checkpoint pass. Remote migration `20260726130318_reports_einvoice_boundary` is applied: 46/46 public tables force RLS, no tenant/financial rows exist, Security Advisor has zero findings, and missing e-invoice FK indexes/browser mutations/exposed private helpers are zero.

### Phase 4 shop dashboard visual prototype (not operational)

- Queue Board with walk-in/appointment management, barber allocation, and real-time status.
- POS Checkout Panel for completed bookings with cash/card tender, discounts, and tip handling.
- Cash Shift Manager with open/close lifecycle, pay-in/pay-out, and variance tracking.
- Shop Reports View with date-range revenue, barber performance, and service analytics.
- Dashboard Header with business/shop owner switching and sign-out.
- Components exist locally in `saloon-shop-dashboard/app/_components/`, but use hardcoded records and client-only state. They are design salvage, not Phase 4 completion evidence.

### Phase 5 platform dashboard visual prototype (not operational)

- Platform Header with session-aware admin display and sign-out.
- Tenant Fleet table with onboarding modal, subscription status, and search.
- Billing & Cash Receipts with receipt recording and reversal.
- Offboarding & Export workflow with guided confirmation.
- Bot Fleet Health monitor with per-tenant bot status.
- SaaS Executive Analytics with revenue, tenant, and growth KPIs.
- The existing components build locally, but their tenant, receipt, offboarding, bot-health, and analytics records are mock data and their mutations are not persisted.
- A prior Vercel deployment exists, but it is not a production-ready Phase 5 deployment and does not establish a working authenticated backend flow.

### Both Next.js foundations plus T1.5 authorization shells

- Next.js 16, React 19, Tailwind 4, strict TypeScript, ESLint, lockfiles, and SHA-pinned CI.
- Supabase SSR browser/server/proxy clients.
- Proxy session refresh plus server-side claims verification for protected route groups.
- Production HTTPS environment validation.
- CSP, HSTS, referrer, frame, content-type, and permissions headers.
- Minimal `/login`, protected server authorization shells, generic unavailable states, and server-side sign-out actions.

### Security work

- Current-tree and full-history credential-pattern scans found no tracked matches across the three repositories.
- Backend and both frontend full dependency audits are clean. The dashboards use Next's documented direct `@next/eslint-plugin-next` flat-config path, TypeScript ESLint, and React Hooks rules; this removed the vulnerable legacy lint dependency chain without weakening CI.
- Built frontend bundles contain no checked backend-secret names.
- Static checks found no unsafe serializer, dangerous Python execution, wildcard CORS, unverified JWT decode, browser token storage, or raw-HTML sink.
- Project-scoped Supabase OAuth MCP is authenticated; seventeen migrations are applied and all 46 public application tables are forced-RLS-enabled. The only remote rows are nine controlled `journal_accounts` references; tenant and financial tables remain empty. Security Advisor has zero findings.
- A dated security audit is now mandatory after every phase.
- The Phase 1 audit found missing distributed throttling as High and fixed it. The audit remains not passed because inherited credential rotation, authenticated repository-protection evidence, and the live Storage round trip are open.

## 5. Latest verified evidence

Backend:

```text
uv lock --check                     PASS
uv run ruff check <Phase 2 files>   PASS
uv run ruff format --check <Phase 2 files> PASS
uv run mypy app workers             PASS
uv run pytest <Phase 2 files>       PASS — 2 passed, 5 DB-gated skips
uv run pip-audit                    PASS — no known vulnerabilities
```

Each dashboard:

```text
npm run check                       PASS — shop: 6 tests; platform: 5 tests; lint, types, production builds
npm audit --audit-level=high        PASS — 0 vulnerabilities
```

Runtime dashboard smoke:

```text
GET / without a session             307 → /login
GET /login                           200
Strict-Transport-Security            present on both dashboards
```

Compose:

```text
docker compose -f docker/compose.dev.yml config --quiet  PASS
```

Native runtime and Supabase:

```text
PostgreSQL 17.10                 PASS — localhost only, gents_saloon ready
Memurai 4.1.2 / Redis API 7.2.5 PASS — localhost only, password required
Redis CONFIG/FLUSHALL            PASS — disabled
GET /health/live                 PASS — 200
GET /health/ready                PASS — 200 with dependencies
Redis stopped → readiness        PASS — 503 while PostgreSQL stayed ready
workers.health.ping              PASS — Celery SUCCESS through real broker/worker
Supabase application tables      PASS — 46 forced-RLS tables; 9 controlled journal-account rows only
Supabase migrations              PASS — 5 Phase 1 + 12 Phase 2 forward migrations
Supabase Security Advisor        PASS — zero findings
Supabase Performance Advisor     PASS — INFO-only unused indexes on empty tables
```

Phase 1/2 database:

```text
scripts/test-database.ps1            PASS — reconstruction + RLS + 13 application database tests
Remote migrations                    PASS — tenant core, FK indexes, deny policies, entitlement and export/offboarding hardening
Remote Security Advisor              PASS — 0 findings
Remote Performance Advisor           PASS — INFO-only unused indexes on empty tables
Remote application rows              PASS — 0; no test/customer data inserted
Remote database timezone             PASS — UTC
Private tenant-exports bucket        PASS — private, ZIP-only, 100 MB, no browser policies
Live Storage object round trip       OPEN — backend service-role runtime secret not provisioned
Phase 2 operations SQL suite         PASS — catalog/customer/calendar/legal/commission constraints and RLS
Remote Phase 2 migrations            PASS — operations source schema + calendar time hardening
Remote public tables/RLS             PASS — 46/46 forced RLS; tenant/financial/e-invoice rows 0
Remote browser mutation policies     PASS — 0
Remote missing FK indexes            PASS — 0
Phase 2 booking RLS/concurrency       PASS — holds, queue, allocation, worker, reschedule
Remote booking migrations             PASS — booking queue + history hardening
Remote booking browser mutations      PASS — 0 policies/privileges
Phase 2 legal/cash RLS/concurrency    PASS — legal snapshots, counters, shift lifecycle/reconciliation
Remote legal/cash migrations          PASS — document type hardening + legal cash shifts
Remote legal/cash browser mutations   PASS — 0 policies/privileges
Phase 2 checkout RLS/concurrency      PASS — totals, split tender, commission, cash, journal, replay
Remote checkout migrations            PASS — 20260726092654 journal + 20260726094355 FK index hardening
Remote checkout browser mutations     PASS — 0 policies/privileges
Phase 2 correction RLS/concurrency    PASS — replay, race bounds, void/refund, journal/cash reconciliation
Remote correction migrations          PASS — 20260726105629 corrections + 20260726111301 void hardening
Remote correction browser mutations   PASS — 0 policies/privileges
Phase 2 payout RLS/concurrency         PASS — replay, period exclusion, one pay winner, exact advance/cash/journal
Remote payout migration                PASS — 20260726115339 advance/payout settlement
Remote payout browser mutations        PASS — 0 policies/privileges
Phase 2 report/e-invoice suite         PASS — reconciliation, pagination, role/IDOR, trigger, RLS, export v2
Remote report/e-invoice migration      PASS — 20260726130318 reports/e-invoice boundary
Remote report/e-invoice guards         PASS — 0 browser mutations, forbidden columns, missing FK indexes, exposed helpers
```

Workstation cleanup note:

```text
PostgreSQL 5432 / Memurai 6380       intentionally left running, localhost only
Celery worker process tree           stopped after verification
Extra FastAPI test process trees     stopped after verification
Port 8000                            an older tool-managed listener remains reachable under stale PID 11696
```

Windows no longer exposes PID 11696 through `Get-Process`, CIM, or `tasklist`, so it cannot be terminated by PID. If port 8000 is needed and remains occupied, reboot the workstation or temporarily select another development port after confirming the listener is still the Phase 0 liveness endpoint.

## 6. Open blockers — do not call Phase 0 complete

1. **Critical owner action:** revoke and replace the four Telegram bot credentials detected in local `tokkens.txt`. Values were not printed or committed. Follow [the rotation runbook](docs/SECRET_ROTATION_RUNBOOK.md).
2. **Remote repository controls:** the historical `main` checkpoints are synchronized and green, but recovery work is not merged to protected `main`. The recovery branches must pass remote CI/review, and branch protection, required checks, secret scanning, and push protection still require authenticated settings evidence.
3. **Development MCP acceptance:** the owner explicitly requested full-write Supabase MCP access. It is project-scoped and the project is empty; switch to read-only mode or a development branch before production data exists.
4. **Next.js MCP:** `next-devtools-mcp@0.4.0` was evaluated and removed because it introduced unresolved High npm advisories. Do not install it until an audited fixed release exists. A custom product MCP is not needed unless an approved external AI client later requires scoped platform access.
5. **Phase 1 operational proof:** provision the Supabase service-role credential to the backend only through the approved secret channel, then prove private upload, signed download, expiry, and object deletion. OAuth MCP cannot execute Storage object operations.

## 7. Exact next starting point

1. Complete receptionist walk-in/checkout/cash/advance/EOD callbacks, then replace the barber/owner/master placeholders; customer, receptionist booking lifecycle, and AI booking mutations already use idempotent domain services.
2. Complete multilingual menu/snapshot coverage, role/adversarial/retry tests, 201-bot capacity, Telegram/Moonshot staging proof, and the dated Phase 3 security audit.
3. Finish Phase 4/5 feature gaps and Playwright/axe matrices, then write their dated security audits.
4. Validate the Phase 6 image/Compose on a Linux Docker host, then execute the owner-controlled isolated-environment, PITR/S3/Grafana, security/load/UAT/canary/rollback/variance/cutover gates in the production runbook.

Inherited owner gates remain: rotate all four Telegram credentials; prove authenticated repository protection; perform the live private-Storage round trip; supply isolated staging/production projects, domains, VPS, and external service credentials through approved secret channels.

To restart the already-proven native dependencies:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-native-deps.ps1
```

## 8. Rules for every future handoff

- Update this file whenever current phase, completed work, blockers, commands, or verification results change.
- Update the active phase file and `docs/REQUIREMENTS.md` in the same change.
- Append durable decisions to `docs/PROJECT_CONTEXT.md`; do not rewrite decision history.
- Sync affected canonical docs into each dashboard repository.
- Run the mandatory dated security audit after every phase. Critical/High findings block completion.
- Run `ponytail-audit` and `ponytail-debt` at phase end.
- Never put secrets in chat, docs, Git, frontend variables, logs, URLs, screenshots, or test fixtures.
- Preserve unrelated owner files and dirty-worktree changes.

## 9. Files intentionally treated differently

- `tokkens.txt`: owner credential scratch file; ignored. At the owner's explicit MCP request it was inspected only to classify credential types; four Telegram tokens were detected, but values were never printed or committed. Rotate them.
- `security audit/`: owner-provided reusable baseline source; preserve it. Canonical project rules are in `docs/SECURITY.md`.
- `Skills intall.txt`: owner skill-installation source record; preserve it.
- `Prompt.md.txt`: historical original brief; canonical decisions now take precedence.
- `Github repo information.txt`: historical repository setup record; do not rerun its initialization commands on existing repositories.
