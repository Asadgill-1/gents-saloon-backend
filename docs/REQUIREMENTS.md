# Requirements — Production Feature Ledger

Statuses: `planned`, `built`, `verified`. Nothing becomes `verified` without the named automated or operational proof.

> Current phase, blockers, and exact next task: [../START_HERE.md](../START_HERE.md). Product requirements below remain planned unless explicitly marked otherwise.

## Engineering foundation

| ID | Requirement | Verification | Status |
|---|---|---|---|
| FND-01 | Locked FastAPI foundation with validated/redacted configuration, PostgreSQL/Redis clients, health endpoints, and request IDs | Ruff, mypy, pytest, audit, HTTP smoke | verified |
| FND-02 | Celery uses Redis, JSON serializers, bounded task execution, and a real health worker | Unit/config tests plus live broker/worker test | verified |
| FND-03 | Both Next.js foundations use strict checks, Supabase SSR, server-side protected layouts, safe environment validation, and security headers | Lint, type, tests, builds, runtime header/auth smoke | verified |
| FND-04 | CI definitions use locked installs, dependency/secret checks, read-only permissions, and SHA-pinned Actions | Local workflow inspection plus remote runs | built |
| FND-05 | Secret scratch files are ignored and current/history/bundle scans are clean | Ignore rules and scanner evidence | verified |
| FND-06 | Every credential reported in the local owner token scratch file is revoked and replaced | Owner rotation confirmation | planned |
| FND-07 | Every phase has a dated security audit with no unresolved Critical/High finding | Audit notes and phase gate | built |

`built` foundation rows still have missing operational proof. Phase 0 does not complete until FND-04, FND-06, and FND-07 receive their remaining evidence.

## Tenant and identity

| ID | Requirement | Verification | Status |
|---|---|---|---|
| TEN-01 | One business has one primary owner and one or more shops | Schema + owner onboarding E2E | verified |
| TEN-02 | Owner sees aggregate business data and every owned shop | API/RLS integration tests | verified |
| TEN-03 | Staff, customers, services, queue, bookings, POS, shifts, bots, advances, and payouts are shop-scoped | Phase 1/2 cross-shop tests pass through advances/payouts; bots remain Phase 3 | partial |
| TEN-04 | A staff user may have explicit memberships in one or more shops; no access is inferred from a request parameter | Auth-context tests | verified |
| TEN-05 | Platform admin access is global, server-authorized, and audited | Privilege/audit plus suspended-tenant surface tests | verified |
| TEN-06 | One customer visiting two shops has two isolated shop profiles | Composite tenant constraints + reconstructed RLS tests | verified |
| TEN-07 | Supabase access JWTs are verified by current JWKS, issuer, audience, expiry, and not-before; authorization uses only the UUID subject | Cryptographic JWT tests + malformed/expired matrix | verified |
| TEN-08 | Platform-admin onboarding atomically creates the application profile, business ownership, initial shop, subscription, initial cash receipt, audit, and outbox with idempotent replay | Concurrent + rollback database integration tests | verified |

## SaaS commercial operations

| ID | Requirement | Verification | Status |
|---|---|---|---|
| SUB-01 | Business selects exactly one billing mode: business-wide or per-shop | DB constraint + service tests | verified |
| SUB-02 | Cash receipt captures amount, collector, receipt reference, inclusive `paid_until`, evidence note, and audit entry | API/integration tests | verified |
| SUB-03 | Expiry occurs at 00:05 on the day after `paid_until` in Asia/Dubai; no grace period | Clock-boundary + worker concurrency tests | verified |
| SUB-04 | Expired/manual/security suspension blocks all tenant operations and returns HTTP 423 to authenticated APIs | Reconstructed DB API/bot/public surface matrix | verified |
| SUB-05 | Bots acknowledge valid webhooks but only show a generic unavailable reply during suspension | Middleware unit + reconstructed DB integration tests; transport verification remains Phase 3 | verified |
| SUB-06 | Public page shows a generic suspension screen without billing details | API/privacy contract and production build pass; Playwright visual matrix remains Phase 4 | built |
| SUB-07 | Platform admin can collect, suspend, resume, export, and offboard while tenant is blocked | Reconstructed PostgreSQL API/service lifecycle tests | verified |
| SUB-08 | Resume after non-payment requires valid paid coverage | Service/database integration tests | verified |
| SUB-09 | Offboarding is export-first, revokes sessions, disables tenant surfaces, and soft-archives data | Reconstructed lifecycle, entitlement, bot/public, and audit assertions | verified |
| SUB-10 | Export is versioned, checksummed, access-controlled, and includes documented table/field coverage | Archive restore/inspection, checksum, authorization, expiry, and allowlist tests | verified |

## Booking, queue, and customer operations

| ID | Requirement | Verification | Status |
|---|---|---|---|
| BKG-01 | Queue, appointments, and walk-ins use a validated state machine | Pydantic, SQL transition, terminal-state, and API tests | verified |
| BKG-02 | Bookings support multiple services with price/duration snapshots | Immutable snapshot integration/RLS tests | verified |
| BKG-03 | Shop hours, closures, barber schedules, and leave determine appointment availability | Calendar plus deterministic availability fixtures | verified |
| BKG-04 | PostgreSQL prevents overlapping active appointments for the same barber | GiST constraint plus parallel transaction test | verified |
| BKG-05 | Five-minute slot holds prevent double booking and expire safely | Clock, worker, and replacement concurrency tests | verified |
| BKG-06 | “Any barber” assignment is deterministic and auditable | Active-work-count and stable UUID tie-break fixture | verified |
| BKG-07 | Appointments promote to live queue at T-30 minutes idempotently | Worker and outbox replay tests | verified |
| BKG-08 | Queue numbers are allocated in PostgreSQL, unique per shop/business date | Parallel allocation and Redis-independence test | verified |
| BKG-09 | Public queue exposes tokens/status/estimate only, no customer names or other PII | Contract/privacy tests | planned |
| BKG-10 | Optional Telegram contact-share captures phone on first booking; absence does not block booking | Bot flow tests | planned |

## POS, tax, cash, commission, and payroll

| ID | Requirement | Verification | Status |
|---|---|---|---|
| POS-01 | Multi-service checkout stores immutable item, pricing, discount, VAT, barber, and legal-detail snapshots | Golden receipt and database reconciliation tests pass | verified |
| POS-02 | Cash, card with slip reference, and split tender reconcile to transaction gross total | Cash/card split and payment-total tests pass; PAN-like references rejected | verified |
| POS-03 | Sequential receipt number is unique per shop and safe under concurrency | Parallel same-key checkout produces one receipt/transaction | verified |
| POS-04 | VAT/TRN configuration supports non-registered and registered shops and renders required invoice fields | Full/simplified tax-invoice and non-VAT profiles plus immutable checkout legal snapshot verified | verified |
| POS-05 | Completed sales are corrected by void/refund/credit note, never edited | Same-key/race, exact reconciliation, original immutability, RLS, cash, and journal tests pass | verified |
| POS-06 | Cash shifts reconcile opening float, cash sales/movements, expected, counted, and variance | Lifecycle/concurrency tests plus checkout cash-only movement linkage pass | verified |
| MON-01 | Fixed percentage commission works from net-after-discount, excluding VAT and tips | Golden and range-invariant Decimal calculation tests pass | verified |
| MON-02 | Tier/threshold commission supports flat amount, including AED 120 → barber 25/shop 95 | SQL validation and AED 120 → 25/95 checkout fixture pass | verified |
| MON-03 | Tips are 100% barber and recorded separately | Separate tip snapshot and `tip_payable` journal posting pass | verified |
| MON-04 | Effective-dated immutable commission rules and transaction snapshots preserve history | Effective rule selection and restricted immutable snapshot tests pass | verified |
| MON-05 | Rounding is half-up to fils; any remainder goes to shop; split always reconciles | Golden and range-invariant reconciliation tests pass | verified |
| MON-06 | Advance grant creates one disbursement and one outstanding balance, not a negative earning | Ledger tests | planned |
| MON-07 | Advance deduction occurs once at payout and cannot exceed allowed outstanding/payable balance | Concurrent pay permits one winner; deferred database reconciliation proves one bounded application | built |
| MON-08 | Payout run records gross earnings, deductions, adjustments, net, approval, and payment | Payout E2E | planned |
| MON-09 | Every money mutation is atomic, idempotent, role-gated, and audited | Checkout, correction, advance, and payout paths verified through T2.6 | built |

## Telegram and AI

| ID | Requirement | Verification | Status |
|---|---|---|---|
| BOT-01 | Four bots per shop: customer, receptionist, barber crew, owner; one global master bot | Registry/onboarding tests | planned |
| BOT-02 | Customer bot supports EN/AR/HI/UR; staff/web surfaces are English in first release | Catalog coverage tests | planned |
| BOT-03 | All required flows have button paths; AI outage degrades to buttons | Bot E2E | planned |
| BOT-04 | Staff bot authorization resolves bot → shop → active membership on every update | Security tests | planned |
| BOT-05 | Outbox makes notifications retryable without duplicates | Worker/idempotency tests | planned |
| AI-01 | AI only extracts intent and invokes allowlisted customer/booking tools | Tool contract tests | planned |
| AI-02 | Tenant/customer context is injected server-side; model cannot choose IDs | Adversarial tests | planned |
| AI-03 | Guardrail runs before model; unsafe input produces canned response and escalation | Adversarial tests | planned |
| AI-04 | Price, wait, position, availability, and booking confirmations come only from tool results | Hallucination test set | planned |

## Frontends and platform operations

| ID | Requirement | Verification | Status |
|---|---|---|---|
| WEB-01 | Shop dashboard opens to authorized context, supports business aggregate for owner, and shop switcher | Playwright role matrix | planned |
| WEB-02 | Reception/POS controls are touch-first and only operate on selected authorized shop | Playwright + API auth tests | planned |
| WEB-03 | Frontend mutations use FastAPI; Supabase client access is read/Realtime only | Static check + network E2E | planned |
| WEB-04 | Suspension/archive states replace operational UI consistently | Server shell and neutral contract mapping built; Playwright product matrix remains Phase 4 | built |
| ADM-01 | Platform dashboard is business-first and manages shops beneath a business | Admin E2E | planned |
| ADM-02 | Platform dashboard records cash subscription receipts and shows due/expired status | Admin E2E | planned |
| ADM-03 | Platform dashboard performs audited suspend/resume/export/offboarding | Admin E2E | planned |
| ADM-04 | Health page covers API, worker, database, Redis, outbox, and all registered bots | Fault-injection test | planned |

## Production, privacy, and operations

| ID | Requirement | Verification | Status |
|---|---|---|---|
| OPS-01 | Exact-origin CORS, secure headers, verified JWTs, RLS on every table, no direct tenant mutation | Security suite | planned |
| OPS-02 | Logs/traces/metrics are structured, redact PII/secrets, and carry request/tenant correlation IDs | Log inspection | planned |
| OPS-03 | Database backups achieve RPO ≤ 15 minutes and restore drill achieves RTO ≤ 4 hours | Timed restore record | planned |
| OPS-04 | Load test supports 50 active shops, 200 shop bots, and one master bot at agreed traffic profile | Load report | planned |
| OPS-05 | Dependency, secret, migration, unit, integration, E2E, accessibility, and security checks run in CI | Protected-branch evidence | planned |
| OPS-06 | UAE VAT invoice/record-retention and e-invoicing readiness are documented and reviewed before launch | Compliance checklist | planned |
| OPS-07 | Privacy notice, lawful-purpose data map, export/anonymization workflow, and incident runbook exist | Owner sign-off | planned |
| OPS-08 | Authenticated, privileged platform, and public routes use distributed rate limits that fail closed when Redis is unavailable | Redis limiter unit tests plus production fault injection | built |

## Deferred

Online payment gateway, WhatsApp/SMS, customer web accounts, inventory/product sales, loyalty, marketing broadcasts, multiple primary/co-owners, multi-currency, and Arabic/RTL web UI.
