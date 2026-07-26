# Master Plan — Production-Ready Gents Saloon SaaS

> Canonical implementation contract. Read [SECURITY.md](SECURITY.md), [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md), this file, [REQUIREMENTS.md](REQUIREMENTS.md), and [DATA_MODEL.md](DATA_MODEL.md) before coding. The original owner brief remains in [../Prompt.md.txt](../Prompt.md.txt); decisions logged after it in PROJECT_CONTEXT take precedence.

> Implementation status is maintained in [../START_HERE.md](../START_HERE.md). This file defines the target system; a described feature is not necessarily built.

## 1. Product and tenancy

Gents Saloon is a UAE SaaS platform for barbershops and salons. It combines reception, queue and appointment management, POS, barber earnings, manual subscription collection, Telegram bots, and two web dashboards.

The hierarchy is:

```text
Platform
└── Business (one primary owner)
    ├── Shop A
    │   ├── staff, services, customers, bookings, POS, shifts
    │   └── four Telegram bots
    └── Shop B
        ├── separate staff, services, customers, bookings, POS, shifts
        └── four Telegram bots
```

Tenant boundary rules:

- A `business` is the commercial tenant. One primary business owner may own multiple shops.
- Operational data is always shop-scoped. Staff, customers, services, bookings, queue entries, transactions, cash shifts, advances, payouts, and bots never leak between shops.
- A business owner can see aggregate business data and drill into every shop belonging to that business.
- Receptionists and barbers access only the shops to which they have active membership.
- A platform administrator can operate across tenants, with every privileged action audited.
- First release supports one primary owner per business. Co-owners are intentionally deferred.

## 2. Locked technology decisions

| Area | Decision |
|---|---|
| Backend | Python 3.12+, FastAPI, async PostgreSQL transactions |
| Database/Auth/Realtime | Supabase PostgreSQL + Supabase Auth + RLS |
| Bots | aiogram 3; polling in development, webhooks in production |
| Background work | Celery 5 + Beat; Redis broker |
| Redis | Cache, rate limits, FSM, short coordination only; never money or booking truth |
| AI | Moonshot OpenAI-compatible API; intent/tool extraction only |
| Shop dashboard | Separate Next.js App Router app on Vercel |
| Platform dashboard | Separate Next.js App Router app on Vercel |
| Deployment | Backend services on one hardened VPS using Docker Compose; managed Supabase |
| Money/time | AED; `Decimal`/PostgreSQL `numeric`; timestamps stored UTC; business dates in `Asia/Dubai` |
| Payments | POS records cash, card slip, and split tender; no online gateway in first release |
| SaaS collection | Platform owner records cash receipts and an inclusive `paid_until` date manually |

The backend uses a direct PostgreSQL connection for atomic business mutations. Supabase remains responsible for Auth, RLS-protected reads, Realtime, and controlled administrative operations. The service-role key never ships to either frontend.

## 3. SaaS commercial lifecycle

Each business selects exactly one billing mode:

- `business`: one subscription controls every shop in the business.
- `per_shop`: each shop has its own subscription.

The modes cannot overlap. Switching mode is an audited platform-admin operation with a validation check that prevents active overlapping subscriptions.

Subscription states:

```text
active ── expiry/manual action ──> expired/suspended
  ▲                                  │
  └──── cash receipt + resume ───────┘
  │
  └── offboarding ──> archived
```

Rules:

- `paid_until` is inclusive in the business timezone. Access expires at 00:05 the following day.
- There is no grace period.
- A new cash receipt records amount, currency, receipt reference, collector, collection time, coverage dates, and evidence note; it never overwrites receipt history.
- Receipt correction creates a linked positive mirror reversal. It corrects immutable collection evidence but does not silently remove access; coverage removal is an explicit audited suspension/coverage action.
- A suspended tenant cannot use shop APIs, POS, staff dashboards, booking operations, or customer-bot business flows.
- During suspension, customer/staff bots return a generic unavailable message and public shop pages show a suspension screen. No tenant details or payment status are exposed publicly.
- Telegram webhooks still acknowledge valid updates so Telegram does not retry indefinitely, but they perform no business operation.
- Platform administrators keep access to collect payment, resume, export, and offboard.
- Resume is explicit and audited. A valid paid period is required for non-payment suspension.

Offboarding is export-first and non-destructive:

1. Put the billing scope into `offboarding`.
2. Revoke tenant sessions and block mutations.
3. Produce a versioned, checksum-protected export.
4. Let the platform owner confirm delivery.
5. Archive the business or shop and disable its bots/public page.

Tenant records are not hard-deleted. Privacy requests use targeted anonymization where legally permitted; financial and audit records retain their integrity.

## 4. Money contract

### 4.1 POS

- A transaction snapshots shop legal details, receipt number, service description, quantity, unit price, discount, VAT rate, net, VAT, gross, and credited barber.
- Payments are separate rows so cash/card split tender is possible. Card payments require a slip/reference number; card PAN, CVV, or expiry are never collected.
- Tips are separate from service consideration and go 100% to the credited barber.
- Completed transactions are immutable. Corrections use voids, refunds, or credit notes with reversing ledger rows.
- Receipt numbers are sequential per shop and allocated inside the checkout transaction.
- Cash shifts record opening float, cash movements, expected close, counted close, variance, and who opened/closed them.
- VAT registration/TRN is configurable per shop. The receipt renderer must support UAE tax-invoice fields without assuming every shop is VAT registered.
- Store both calculation inputs and final values. Never recompute old receipts from edited services, tax settings, or commission rules.
- Round each stored invoice line to the nearest fils, then sum stored rounded lines into document totals. This follows the current [FTA tax-invoice guidance](https://tax.gov.ae/DataFolder/Files/Pdf/06-Tax-Invoices.pdf); legal/tax behavior is rechecked before production rather than frozen from memory.

### 4.2 Commission

Supported commission models are preserved:

1. Fixed percentage of the commission base.
2. Tier/threshold rule, including a flat barber amount above a threshold.

Example: if a service bill is AED 120 and the selected tier grants the barber AED 25, barber commission is AED 25 and shop share is AED 95 before tips.

Commission rules are immutable and effective-dated. Checkout stores the rule snapshot used. The commission base is service net after discount and excludes VAT and tips. Calculations use `Decimal`, round half up to fils, and assign any rounding remainder to the shop so:

```text
barber commission + shop share = commission base
barber tip = full tip amount
```

### 4.3 Advances and payouts

An advance is a cash disbursement and an outstanding receivable. It is not also an immediate negative earning.

- Giving AED 200 creates one cash-disbursement ledger entry and increases advance outstanding by AED 200.
- A payout run calculates earned commission + tips for a closed period.
- The approved advance deduction is applied once during payout, capped by both outstanding advance and payable earnings unless the owner explicitly records an audited settlement.
- The payout row records gross earnings, advance deduction, adjustments, and net paid.
- Settling or partially settling an advance reduces outstanding once. No monthly job creates a second deduction for the same amount.

Every checkout, void, refund, advance, deduction, and payout runs in one database transaction with row locks, constraints, idempotency keys, and audit/outbox writes.

### 4.4 UAE e-invoicing boundary

The current [Ministry of Finance e-invoicing portal](https://mof.gov.ae/en/about-us/initiatives/einvoicing/) is authoritative and changing. Current rules cover B2B/B2G transactions while B2C remains excluded until a later ministerial decision. As rechecked on 2026-07-26, businesses at or above AED 50 million appoint an Accredited Service Provider by 30 October 2026 and implement by 1 January 2027; smaller businesses appoint by 31 March 2027 and implement by 1 July 2027. The product therefore:

- keeps normal consumer saloon receipts/credit notes in the B2C POS document flow;
- stores a provider-neutral B2B/B2G e-invoice document/status/outbox boundary;
- does not select, emulate, or hard-code one Accredited Service Provider;
- revalidates mandatory fields, scope, timelines, and provider choice immediately before production onboarding.

## 5. Booking and queue contract

- Queue, appointment, and walk-in bookings share one state machine.
- Multi-service bookings use `booking_services`; duration and price are snapshots.
- Shop schedules, closures, barber schedules, and leave determine valid appointment windows.
- Appointment overlap is prevented by a PostgreSQL exclusion constraint over barber and time range, not an application-only check.
- A five-minute database-backed slot hold prevents two users completing the same booking slot.
- “Any barber” selection is deterministic and auditable.
- Future appointments are promoted into the live queue at T-30 minutes.
- Queue numbers are allocated atomically in PostgreSQL per shop/business date. Redis may cache a projection but is never the allocator or final referee.
- An outbox publishes Telegram notifications and Realtime updates after commit. Retries are idempotent.

The public queue exposes token, coarse status, and estimate only. It never exposes customer names, phones, money, staff private data, or subscription details.

## 6. API contract

All authenticated frontend mutations use FastAPI under `/api/v1`. The Supabase JWT is verified on every request; the database resolves memberships. A client-supplied `business_id`, `shop_id`, role, price, status, or money result is never trusted.

Core resources:

```text
GET  /api/v1/me/context
GET  /api/v1/businesses/{business_id}/overview

GET  /api/v1/businesses/{business_id}/shops/{shop_id}/queue
GET  /api/v1/businesses/{business_id}/shops/{shop_id}/reports
GET  /api/v1/businesses/{business_id}/shops/{shop_id}/staff
GET  /api/v1/businesses/{business_id}/shops/{shop_id}/services
GET  /api/v1/businesses/{business_id}/shops/{shop_id}/advances
GET  /api/v1/businesses/{business_id}/shops/{shop_id}/shifts

POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/confirm
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/reschedule
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/start
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/complete
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/no-show
POST /api/v1/businesses/{business_id}/shops/{shop_id}/bookings/{booking_id}/cancel

POST /api/v1/businesses/{business_id}/shops/{shop_id}/pos/checkout
POST /api/v1/transactions/{transaction_id}/void
POST /api/v1/transactions/{transaction_id}/refunds
POST /api/v1/businesses/{business_id}/shops/{shop_id}/cash-shifts/open
POST /api/v1/cash-shifts/{shift_id}/close

POST /api/v1/businesses/{business_id}/shops/{shop_id}/advances
POST /api/v1/businesses/{business_id}/shops/{shop_id}/payout-runs

POST /api/v1/platform/tenants
POST /api/v1/platform/subscriptions/cash-receipts
POST /api/v1/platform/subscriptions/cash-receipts/{receipt_id}/reversal
POST /api/v1/platform/subscriptions/{subscription_id}/suspend
POST /api/v1/platform/subscriptions/{subscription_id}/resume
POST /api/v1/platform/businesses/{business_id}/billing-mode
POST /api/v1/platform/exports
POST /api/v1/platform/offboarding

GET  /api/v1/public/queue/{public_token}
```

Mutation endpoints require an `Idempotency-Key`. Conflict, authorization, validation, and suspension responses have stable machine-readable error codes. Suspended tenant APIs return HTTP 423 with `subscription_suspended`.

## 7. Repository and delivery phases

Three Git repositories:

- `gents-saloon-backend` — this repository; backend, SQL migrations, canonical documentation.
- `saloon-shop-dashboard` — shop operations and business-owner dashboard.
- `saloon-gents-system-owner-dashboard` — platform administration.

Delivery order:

| Phase | Outcome |
|---|---|
| 0 | [Foundation](phases/PHASE_0_FOUNDATIONS.md): contracts, secrets, skeletons, CI |
| 1 | [Tenant platform](phases/PHASE_1_TENANT_PLATFORM.md): memberships, subscriptions, suspension, exports/offboarding |
| 2 | [Operations and money](phases/PHASE_2_OPERATIONS_MONEY.md): booking, queue, POS, commission, advances, payouts, cash |
| 3 | [Telegram and AI](phases/PHASE_3_TELEGRAM_AI.md): bot fleet, Moonshot containment, outbox, reports |
| 4 | [Shop dashboard](phases/PHASE_4_SHOP_DASHBOARD.md): owner aggregate/switcher, reception/POS, public queue |
| 5 | [Platform dashboard](phases/PHASE_5_PLATFORM_DASHBOARD.md): onboarding, cash billing, suspension, exports/offboarding |
| 6 | [Production](phases/PHASE_6_PRODUCTION.md): observability, backup/restore, load/security/accessibility, deploy |

## 8. Global implementation rules

1. Thin adapters, one service layer. Bots and API routes contain no business rules.
2. All tenant access starts from authenticated actor membership, not a single JWT `shop_id`.
3. Every relational reference across tenant data is protected by composite tenant-aware foreign keys or equivalent database constraints.
4. Every mutation is audited in the same transaction; notifications use a transactional outbox.
5. PostgreSQL is the source of truth for money, bookings, queue counters, subscriptions, and idempotency.
6. Frontends use Supabase for authentication and authorized reads/Realtime only. All mutations go through FastAPI.
7. No floats for money; no destructive edits to financial history.
8. No volatile package/model/legal claim is hard-coded without build-time verification.
9. A task is complete only after its listed automated verification runs successfully.
10. Security rules in [SECURITY.md](SECURITY.md) cannot be relaxed to unblock implementation.

## 9. Production acceptance gates

- A business owner with two shops sees both and an aggregate; each receptionist/barber sees only assigned shops.
- Automated RLS/API tests prove cross-business and cross-shop isolation for every tenant table and endpoint.
- Business and per-shop billing modes work without overlap.
- Expired/manual suspension blocks every tenant surface while platform administration remains available.
- Export/offboarding produces a complete, versioned, checksum-verified archive and never hard-deletes tenant records.
- Commission fixtures, including AED 120 → AED 25/AED 95, reconcile exactly.
- An advance is disbursed once, deducted once, and reconciles through payout.
- Two parallel checkout/slot/queue/payout requests result in exactly one valid mutation.
- Public pages contain no PII and correctly show active, suspended, and archived states.
- Receipt/VAT configuration, refunds/credit notes, split tender, and cash-shift reconciliation are verified.
- Load test target: 50 active shops, four shop bots each plus the master bot, without cross-tenant leakage or lost updates.
- Backup/restore drill proves RPO ≤ 15 minutes and RTO ≤ 4 hours.
- Security, accessibility, dependency, migration, and end-to-end gates pass in CI before production.

## 10. Explicitly deferred

Online payment gateways, inventory/product sales, loyalty and broadcasts, WhatsApp/SMS, customer web accounts, multiple primary/co-owners, multi-currency, and Arabic/RTL web UI. The database must not be distorted for these speculative features.
