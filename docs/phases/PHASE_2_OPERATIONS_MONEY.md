# Phase 2 — Booking, POS, and Money

## Status — 2026-07-26

**Started by explicit owner approval.** The owner approved the full production financial scope on 2026-07-26: VAT/legal profiles, cash shifts, credit notes/refunds, effective-dated commission, tips, advances, payout runs, and an append-only balanced double-entry journal all belong in Phase 2.

Phase 1 implementation is locally verified, but its inherited credential/remote operational audit gates remain open in [../../START_HERE.md](../../START_HERE.md). Phase 2 work may continue by owner direction; those gates must not be hidden or treated as passed.

- Complete: T2.0 scope decision and executable contracts.
- Complete: T2.1 shop catalog, customers, calendars, legal profiles, and commission-rule source schema.
- Complete: T2.2 booking, holds, availability, and queue.
- Complete: T2.3 legal documents, receipt counters, and cash shifts.
- Next: T2.4 checkout, payments, and commission snapshots.

## Outcome

Shop operations and financial records are tenant-isolated, atomic, concurrency-safe, tax-ready, immutable after finalization, and exactly reconcilable to the fils.

## Locked boundaries

- PostgreSQL is authoritative for availability, holds, queue/receipt numbers, money, cash, commission, advances, payouts, journal, idempotency, audit, and outbox. Redis is disposable.
- Every operational row carries `business_id` and `shop_id`; composite foreign keys prevent cross-shop references.
- One owner can operate all owned shops. Active manager/receptionist permissions are shop-scoped. Barber access is limited to the work and earnings explicitly authorized for that barber.
- Browser/Supabase access is read-only and RLS-filtered. All mutations use authenticated FastAPI transactions.
- Currency is AED. Money uses `numeric(14,2)` in PostgreSQL and `Decimal` with round-half-up in Python. Floats are forbidden.
- Service, tax, legal, barber, commission, and price facts are snapshotted when a booking/transaction becomes authoritative. Historical documents never recompute from edited configuration.
- Completed transactions, credit notes, payout items, journal entries/postings, and audit rows are append-only. Corrections are linked reversals or new corrective documents.
- All financial mutations require an idempotency key, short consistently ordered row locks, entitlement recheck, audit, outbox, and failure-injection/concurrency proof.
- No card PAN, CVV, expiry, magnetic-stripe, or PIN is collected. A card slip/reference is merchant evidence only.
- The provider-neutral e-invoicing boundary stores documents/statuses without selecting or embedding one accredited provider. B2C POS receipts remain separate from the UAE B2B/B2G e-invoicing adapter unless official scope changes.

## Locked calculations

### VAT and receipt lines

- Each line stores quantity, gross/net input mode, unit amount, discount, VAT rate, rounded net, rounded VAT, and rounded gross.
- VAT-registered shops support inclusive or exclusive pricing. Non-registered shops use a zero VAT rate and no TRN.
- Rounding is per invoice line to the nearest fils. Header totals equal the exact sum of stored rounded lines.
- Discounts cannot make a line negative. Refund quantities and values cannot exceed the original line less prior refunds.
- Payments must equal `grand_total`; split tender is multiple payment rows in the same transaction.
- Tips are stored and journaled separately from service consideration and are credited 100% to the selected barber.

### Commission

- Commission base is service net after discount, excluding VAT and tips.
- Fixed percentage: barber amount is base × percentage, rounded half-up to fils.
- Tier/threshold: the effective tier can return a percentage or flat barber amount. Tiers are ordered, non-overlapping, immutable JSON validated in SQL and Python.
- Any rounding remainder belongs to the shop:

```text
barber_commission + shop_share = commission_base
barber_tip = full tip amount
```

- Locked regression: service commission base AED 120.00, applicable flat tier AED 25.00 → barber AED 25.00, shop AED 95.00; tip remains separate.

### Advances and payouts

- Advance grant: credit cash, debit advance receivable, and increase one outstanding balance. It is not a negative earning.
- Payout gross = commission earnings + tips + signed adjustments.
- Advance deduction is capped by outstanding advance and available payable unless an explicit separately audited settlement is later authorized.
- Net paid = gross payable − advance deduction. One `advance_application` can affect one payout item once.
- A retry returns the same payout result and never pays or deducts twice.

### Double-entry journal

- Every financial event creates one journal entry with at least two postings.
- Every posting has exactly one positive side: debit or credit.
- Deferred database validation requires total debit = total credit before commit.
- Source event/type and idempotency identity are unique per shop.
- Journal corrections are reversing entries linked to the original; updates/deletes are rejected.

## Execution tasks

### T2.0 — Scope and contract lock

Files:

- this phase file;
- `docs/MASTER_PLAN.md`;
- `docs/DATA_MODEL.md`;
- `docs/REQUIREMENTS.md`;
- `docs/PROJECT_CONTEXT.md`;
- `START_HERE.md`;
- both dashboard `STATUS.md` files.

Acceptance:

- the full owner-approved money scope is recorded without ambiguity;
- tax/rounding/commission/advance/payout/journal equations are executable test contracts;
- e-invoicing remains provider-neutral and follows current official UAE scope/timeline at implementation time;
- T2.1–T2.8 have concrete files, gates, and order.

### T2.1 — Catalog, customer, calendar, legal, and commission sources

Files:

- `supabase/migrations/*_operations_catalog_calendar.sql`;
- `supabase/migrations/*_calendar_time_hardening.sql`;
- `supabase/tests/phase2_operations_schema.sql`;
- `scripts/test-database.ps1`;
- canonical data-model and requirements updates.

Implement:

- `services` and isolated shop customer profiles;
- effective-dated shop business hours, closures, barber schedules, leave, and temporary unavailability;
- effective-dated shop legal/tax profiles;
- immutable default/barber commission rules with SQL validation for percentage/tier payloads;
- composite tenant foreign keys, all FK/RLS indexes, explicit authenticated read policies, no browser writes, and service-role write grants.

Gates:

- owner/shop staff see only allowed shop rows; another business and anonymous roles see none;
- barber cannot read unrelated customer profiles or private leave/unavailability rows;
- cross-shop customer/barber/legal/commission references fail at the database;
- invalid VAT/TRN, time ranges, effective periods, percentage, and tier payloads fail;
- applied migration reconstructs cleanly and Supabase Security Advisor has zero findings.

Evidence:

```text
Local clean reconstruction/RLS/adversarial suite  PASS
Remote migrations                                 PASS — 20260726065519 + 20260726065753
Remote public tables                              PASS — 24/24 RLS-enabled, 0 rows
Remote browser mutation policies                  PASS — 0
Remote missing foreign-key indexes                PASS — 0
Remote Supabase Security Advisor                  PASS — 0 findings
Remote Performance Advisor                        INFO-only unused indexes on empty tables
```

### T2.2 — Booking, holds, availability, and queue

Files:

- forward booking/queue and historical-state hardening migrations;
- booking service, API routes, worker tasks, and database/unit tests.

Implement:

- bookings plus immutable multi-service snapshots;
- validated queue/appointment/walk-in state machine;
- five-minute database slot holds;
- GiST exclusion for active barber appointment overlap;
- deterministic Any Barber allocation using availability, active work count, stable barber ID tie-break;
- PostgreSQL queue counters per shop/business date;
- T-30 minute appointment promotion worker and outbox.

Gates:

- parallel hold/confirm/queue allocation permits one valid result;
- expired holds never block availability;
- Redis flush changes no durable booking/queue result;
- reschedule/cancel/no-show and worker retries are idempotent and audited.

Evidence:

```text
Local clean reconstruction/RLS/concurrency suite  PASS
Booking unit/API contracts                         PASS
Parallel same-slot holds                           PASS — one winner, one conflict
Parallel same-key confirmation                     PASS — one durable result
Parallel queue allocation                          PASS — unique sequential numbers
Expired hold replacement                           PASS
Deterministic Any Barber                           PASS — active count, UUID tie-break
Reschedule/cancel/invalid transition                PASS
T-30 worker replay                                  PASS — one promotion, then zero
Redis booking/queue dependency                      PASS — none
Remote migrations                                   PASS — 20260726072916 + 20260726073229
Remote public tables                                PASS — 27/27 RLS-enabled, 0 rows
Remote browser booking mutations                    PASS — 0 policies/privileges
Remote missing foreign-key indexes                  PASS — 0
Remote Supabase Security Advisor                    PASS — 0 findings
Remote Performance Advisor                          INFO-only unused indexes on empty tables
Ponytail debt                                       1 — shop-wide lock; split only after measured contention
```

### T2.3 — Legal documents, receipt counters, and cash shifts

Files:

- one forward legal/cash migration if not fully established by T2.1;
- legal-profile, receipt-counter, cash-shift services/APIs/tests.

Implement:

- immutable effective legal snapshot selection;
- fiscal-year shop receipt counters with row locks;
- one open shift per shop/register;
- pay-in/pay-out/advance/payout/refund movements;
- expected/count/variance reconciliation excluding card from physical cash.

Gates:

- parallel receipt and shift-open attempts produce one valid result;
- VAT/non-VAT legal fixtures and receipt fields pass;
- cash expected, counted, and variance equations reconcile.

Evidence:

```text
Local clean reconstruction/RLS/concurrency suite  PASS
VAT/non-VAT legal selection                       PASS
Simplified tax-invoice schema support              PASS
Parallel sale-counter allocation                   PASS — unique 1..4
Parallel same-register open                        PASS — one winner, one conflict
Same-key open/close replay                         PASS — one durable result
Cash equation/card exclusion                       PASS — exact Decimal reconciliation
Post-close shift/movement mutation                 PASS — rejected by service + database
Remote migrations                                  PASS — document type hardening + legal cash shifts
Remote public tables                               PASS — 30/30 RLS-enabled, 0 rows
Remote browser legal/cash mutations                PASS — 0 policies/privileges
Remote Supabase Security Advisor                    PASS — 0 findings
Remote Performance Advisor                         INFO-only unused indexes on empty tables
```

### T2.4 — Checkout, payments, and commission snapshots

Files:

- transaction/item/payment schema migration;
- calculation module, checkout service/API, and golden/concurrency tests.

Implement:

- server-selected services/legal/commission rules;
- multi-item price/discount/VAT/commission snapshots;
- cash/card/split tender validation;
- separate 100% barber tips;
- atomic receipt allocation, transaction, journal, audit, and outbox.

Gates:

- inclusive/exclusive VAT, discount, rounding, split tender, and tips reconcile;
- AED 120 → barber 25/shop 95 passes;
- parallel same-key checkout creates one receipt/transaction/journal only;
- client-supplied totals, price, VAT, barber, role, or shop authority are ignored/rejected.

### T2.5 — Void, refund, credit note, and journal reversal

Files:

- refund/credit-note schema;
- correction services/APIs and reconciliation tests.

Implement:

- same-shift unsettled void policy;
- partial/full refunds bounded by unrefunded quantity/value;
- sequential credit-note number;
- reversing payments, cash movement, commission/payable, VAT, revenue, and journal postings.

Gates:

- duplicate/parallel refunds cannot over-refund;
- original completed document remains unchanged;
- original plus corrections reconcile to current financial position.

### T2.6 — Advances, payout runs, and settlement

Files:

- advance/payout schema;
- advance/payout services/APIs and concurrency tests.

Implement:

- advance disbursement/receivable;
- closed-period immutable commission/tip aggregation;
- draft → approved → paid/cancelled payout lifecycle;
- bounded advance applications and journal/cash movement settlement.

Gates:

- advance disburses once and deducts once;
- one non-cancelled payout run per shop/period;
- payout retry cannot double-pay;
- every item and journal entry balances exactly.

### T2.7 — Reports and provider-neutral e-invoicing boundary

Implement:

- cursor-paginated shop/owner operational and financial reports from stored snapshots;
- owner cross-shop aggregate with strict business authorization;
- e-invoice document/outbox status boundary for in-scope platform B2B/B2G documents;
- no provider SDK until the owner selects an accredited service provider.

Gates:

- reports reconcile to transactions, corrections, shifts, payouts, and journal;
- another business/shop cannot infer totals or identifiers;
- B2C service receipts are not incorrectly sent to the B2B/B2G adapter.

### T2.8 — Phase security audit and handoff

Verify:

- clean reconstruction and forward migration;
- full RLS/IDOR matrix and missing-FK-index query;
- booking/queue/receipt/checkout/refund/advance/payout concurrency;
- property/golden reconciliation and locked AED 120 fixture;
- backend lint/type/tests/dependency audit;
- Supabase security/performance advisors;
- current/history secret and dangerous-SQL/static guards;
- `ponytail-audit` and `ponytail-debt`;
- dated `docs/security-audits/PHASE_2_<date>.md` plus synchronized handoff docs.

## Phase gates

- Appointment, queue, receipt, checkout, advance, payout, and refund concurrency tests permit exactly one valid mutation.
- AED 120 tier fixture produces barber 25/shop 95; tips remain separate and 100% barber.
- Advance disburses once and deducts once; payout retry is idempotent.
- VAT inclusive/exclusive, discount, split tender, credit note, journal, and cash-shift equations reconcile exactly.
- Redis flush loses no durable state.
- Every mutation has audit and outbox rows in the same commit.
- Full actor/RLS matrix and Supabase advisors are green.
- A dated Phase 2 security audit has no unresolved Critical/High finding.
- No source-code `ponytail:` debt is unrecorded.
