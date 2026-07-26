# Start Here — Current Project Handoff

Last updated: **2026-07-26, Asia/Dubai**

Checkpoint: **PHASE2-CHECKOUT-JOURNAL-2026-07-26**

Current phase: **Phase 2 — T2.0–T2.4 complete; T2.5 void/refund/credit-note reversal next**

This is the authoritative restart document for a new human, LLM, or AI coding agent. Read it before planning or changing code.

## 1. Read in this order

1. This file.
2. [CLAUDE.md](CLAUDE.md) for binding implementation and security workflow.
3. [Phase 2 execution plan](docs/phases/PHASE_2_OPERATIONS_MONEY.md), which is the active phase.
4. [Phase 1 security audit](docs/security-audits/PHASE_1_2026-07-26.md), which records the open inherited and operational gates.
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

The prior checkpoint in all three repositories was on `main`, clean, pushed, and synchronized with its remote:

- backend/canonical docs: `c511eca777a13713df5a2e05b1af458e6a1d77d0`;
- shop dashboard: `9b25b124ebcb0eae3763daa6185176900942bfe2`;
- platform dashboard: `b22ee5bc710397751b1db93e506b2b947dd89e58`.

The matching GitHub Actions runs passed in all three repositories. Preserve unrelated future owner changes and inspect each repository independently before staging.

The T2.4 checkout/journal implementation and these synchronized handoff updates are the current local change set after those hashes; they have not been committed or pushed at this checkpoint. This is new Phase 2 work, not the resolved pre-Phase 1 uncommitted-delivery risk. Both T2.4 Supabase migrations are already applied remotely and locally use the matching remote versions `20260726092654` and `20260726094355`.

## 3. Current delivery status

| Phase | Status | Meaning |
|---|---|---|
| Phase 0 — foundation | **Implementation and CI verified; owner gates open** | Credential rotation and GitHub repository-protection evidence remain |
| Phase 1 — tenant and SaaS platform | **Implementation complete; audit open** | T1.0–T1.6 locally verified; T1.7 cannot pass yet |
| Phase 2 — booking, POS, and money | **In progress by owner direction** | T2.0–T2.4 complete; void/refund/credit-note reversal next |
| Phase 3 — Telegram and AI | Not started | Depends on Phase 1–2 services/outbox |
| Phase 4 — shop dashboard | Not started | Only the technical Next.js foundation exists |
| Phase 5 — platform dashboard | Not started | Only the technical Next.js foundation exists |
| Phase 6 — production rollout | Not started | No production deployment exists |

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
- Export schema `2026-07-26.v1` creates a ZIP containing `manifest.json` plus JSON/CSV datasets from explicit Phase 1 column allowlists. It excludes bot/webhook/queue/storage credentials and internal delivery/idempotency rows and redacts credential-like audit payload fields.
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

### Both Next.js foundations plus T1.5 authorization shells

- Next.js 16, React 19, Tailwind 4, strict TypeScript, ESLint, lockfiles, and SHA-pinned CI.
- Supabase SSR browser/server/proxy clients.
- Proxy session refresh plus server-side claims verification for protected route groups.
- Production HTTPS environment validation.
- CSP, HSTS, referrer, frame, content-type, and permissions headers.
- Minimal `/login`, protected server authorization shells, generic unavailable states, and server-side sign-out actions.
- No product dashboard modules are implemented yet.

### Security work

- Current-tree and full-history credential-pattern scans found no tracked matches across the three repositories.
- Backend and both frontend full dependency audits are clean. The dashboards use Next's documented direct `@next/eslint-plugin-next` flat-config path, TypeScript ESLint, and React Hooks rules; this removed the vulnerable legacy lint dependency chain without weakening CI.
- Built frontend bundles contain no checked backend-secret names.
- Static checks found no unsafe serializer, dangerous Python execution, wildcard CORS, unverified JWT decode, browser token storage, or raw-HTML sink.
- Project-scoped Supabase OAuth MCP is authenticated; thirteen migrations are applied and all 37 public application tables are forced-RLS-enabled. The only remote rows are eight controlled `journal_accounts` references; tenant and transaction tables remain empty. Security Advisor has zero findings.
- A dated security audit is now mandatory after every phase.
- The Phase 1 audit found missing distributed throttling as High and fixed it. The audit remains not passed because inherited credential rotation, authenticated repository-protection evidence, and the live Storage round trip are open.

## 5. Latest verified evidence

Backend:

```text
uv lock --check                     PASS
uv run ruff check .                 PASS
uv run ruff format --no-cache --check .  PASS
uv run mypy app workers             PASS
uv run pytest -q                    PASS — 55 passed, 11 DB-gated skips
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
Supabase application tables      PASS — 37 RLS-enabled tables; 8 controlled journal-account rows only
Supabase migrations              PASS — 5 Phase 1 + 8 Phase 2 forward migrations
Supabase Security Advisor        PASS — zero findings
Supabase Performance Advisor     PASS — INFO-only unused indexes on empty tables
```

Phase 1/2 database:

```text
scripts/test-database.ps1            PASS — reconstruction + RLS + 11 application database tests
Remote migrations                    PASS — tenant core, FK indexes, deny policies, entitlement and export/offboarding hardening
Remote Security Advisor              PASS — 0 findings
Remote Performance Advisor           PASS — INFO-only unused indexes on empty tables
Remote application rows              PASS — 0; no test/customer data inserted
Remote database timezone             PASS — UTC
Private tenant-exports bucket        PASS — private, ZIP-only, 100 MB, no browser policies
Live Storage object round trip       OPEN — backend service-role runtime secret not provisioned
Phase 2 operations SQL suite         PASS — catalog/customer/calendar/legal/commission constraints and RLS
Remote Phase 2 migrations            PASS — operations source schema + calendar time hardening
Remote public tables/RLS             PASS — 37/37; tenant/transaction rows 0
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
2. **Remote repository controls:** all three repositories are committed, pushed, and green in GitHub Actions. Branch protection, required-check enforcement, GitHub secret scanning, and push protection still require authenticated settings evidence.
3. **Medium security follow-up:** replace the current framework-compatible inline-script CSP with nonce/hash CSP during Phase 4/5, before production.
4. **Development MCP acceptance:** the owner explicitly requested full-write Supabase MCP access. It is project-scoped and the project is empty; switch to read-only mode or a development branch before production data exists.
5. **Next.js MCP:** `next-devtools-mcp@0.4.0` was evaluated and removed because it introduced unresolved High npm advisories. Do not install it until an audited fixed release exists. A custom product MCP is not needed unless an approved external AI client later requires scoped platform access.
6. **Phase 1 operational proof:** provision the Supabase service-role credential to the backend only through the approved secret channel, then prove private upload, signed download, expiry, and object deletion. OAuth MCP cannot execute Storage object operations.

## 7. Exact next starting point

Continue [Phase 2 T2.5](docs/phases/PHASE_2_OPERATIONS_MONEY.md): void, refund, credit note, and journal reversal.

1. Expand T2.5 into exact correction tables, state transitions, permissions, and same-shift void policy before coding.
2. Add forward-only schema for refund headers/items/payments and links to the original transaction, credit-note counter, and reversing journal entry.
3. Bound partial/full refunds by the original immutable item quantity/value minus prior corrections; serialize concurrent corrections.
4. Reverse revenue, VAT, barber payable/commission, tip payable, cash/card settlement, and journal postings without editing the original receipt.
5. Require idempotency, in-transaction authorization/entitlement, audit, outbox, and an open matching shift for any physical cash return.
6. Prove duplicate/parallel refunds cannot over-refund and original plus all corrections reconcile exactly.

Inherited gates remain: owner Telegram credential rotation; authenticated repository-protection evidence; live private-Storage round trip; CSP resolution/acceptance. Commits, pushes, and remote CI are complete and green. Do not mark Phase 1 passed until its audit evidence is complete.

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
