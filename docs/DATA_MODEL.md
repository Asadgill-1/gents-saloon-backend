# Data Model — Production Schema Contract

> Target schema contract. All five Phase 1 migrations and ten Phase 2 migrations through T2.5 are implemented and applied to the development Supabase project. Sections 5.2–5.4, 6, 7.1–7.7, 7.10, and 7.11 describe implemented schema; advance/payout sections remain target design. Read [../START_HERE.md](../START_HERE.md) for current status.

Source of truth for SQL migrations. PostgreSQL/Supabase is authoritative for tenancy, subscriptions, bookings, queue numbers, money, idempotency, audit, and outbox delivery.

## 1. Database conventions

- UUID primary keys use `gen_random_uuid()`.
- All timestamps are `timestamptz` in UTC. Business dates are derived using the shop timezone.
- Currency amounts are `numeric(14,2)` and non-negative unless a column is explicitly a signed adjustment.
- Currency is ISO 4217 text constrained to `AED` in first release.
- Every tenant operational table carries `business_id` and `shop_id`.
- Parent tables expose composite unique keys so child foreign keys include tenant scope:

```sql
UNIQUE (id, business_id);
UNIQUE (id, business_id, shop_id);
```

- Deleting financial, audit, subscription receipt, export, or journal history is forbidden.
- Enums are created in idempotent `DO` blocks. Migrations are forward-only; a migration already applied in production is never edited.
- RLS is enabled and forced where supported on every application table in the same migration that creates it.

## 2. Core enums

```text
billing_mode          business | per_shop
billing_scope         business | shop
subscription_status   active | expired | suspended | offboarding | archived
suspension_reason     non_payment | manual | security | offboarding

membership_role       manager | receptionist | barber
bot_role              master | owner | receptionist | barber_crew | customer
language_code         en | ar | hi | ur

booking_type          queue | appointment | walk_in
booking_status        held | requested | confirmed | in_service | completed
                      | no_show | cancelled | expired
booking_source        telegram | pos | dashboard

transaction_status    completed | partially_refunded | refunded | voided
payment_method        cash | card
document_type         receipt | tax_invoice | simplified_tax_invoice
                      | credit_note

commission_rule_type  fixed_pct | tiered
advance_status        open | settled | cancelled
payout_status         draft | approved | paid | cancelled
cash_shift_status     open | closed

actor_type            auth_user | telegram_user | platform_admin | system
outbox_status         pending | processing | delivered | failed
export_status         requested | processing | ready | delivered | failed
```

## 3. Identity and tenancy

### 3.1 `user_profiles`

One row per Supabase Auth user.

```text
auth_user_id uuid PK REFERENCES auth.users(id)
display_name text NOT NULL
phone text NULL
active boolean NOT NULL DEFAULT true
created_at, updated_at timestamptz
```

No role is stored in client-editable Auth metadata. Platform administration uses a separate server-managed table.

### 3.2 `platform_admins`

```text
auth_user_id uuid PK REFERENCES auth.users(id)
telegram_user_id bigint UNIQUE NULL
display_name text NOT NULL
active boolean NOT NULL DEFAULT true
created_at timestamptz
```

### 3.3 `businesses`

```text
id uuid PK
legal_name text NOT NULL
display_name text NOT NULL
trade_license_number text NULL
trade_license_expiry date NULL
vat_registered boolean NOT NULL DEFAULT false
trn text NULL
invoice_address text NULL
contact_name text NULL
contact_phone text NULL
contact_email text NULL
billing_mode billing_mode NOT NULL
timezone text NOT NULL DEFAULT 'Asia/Dubai'
currency text NOT NULL DEFAULT 'AED' CHECK (currency = 'AED')
status subscription_status NOT NULL DEFAULT 'active'
created_at, updated_at, archived_at timestamptz
UNIQUE (id, billing_mode)
CHECK (NOT vat_registered OR trn IS NOT NULL)
```

`businesses.status` is the lifecycle state, not the paid-through truth. Entitlement is resolved from status plus the applicable subscription.

### 3.4 `business_owners`

```text
business_id uuid NOT NULL REFERENCES businesses
auth_user_id uuid NOT NULL REFERENCES auth.users
telegram_user_id bigint NULL
is_primary boolean NOT NULL DEFAULT true
active boolean NOT NULL DEFAULT true
created_at timestamptz
PRIMARY KEY (business_id, auth_user_id)
```

A partial unique index permits one active primary owner per business:

```sql
CREATE UNIQUE INDEX one_primary_owner_per_business
ON business_owners (business_id)
WHERE active AND is_primary;
```

Application validation rejects additional owners in first release even though the relation can support a future controlled migration.

### 3.5 `shops`

```text
id uuid PK
business_id uuid NOT NULL REFERENCES businesses
name text NOT NULL
internal_code text NOT NULL
timezone text NOT NULL DEFAULT 'Asia/Dubai'
currency text NOT NULL DEFAULT 'AED' CHECK (currency = 'AED')
public_queue_token_hash text NOT NULL UNIQUE
status subscription_status NOT NULL DEFAULT 'active'
open_time, close_time time NOT NULL
default_service_minutes integer NOT NULL CHECK (> 0)
eod_time time NOT NULL
settings jsonb NOT NULL DEFAULT '{}'
created_at, updated_at, archived_at timestamptz
UNIQUE (id, business_id)
UNIQUE (business_id, internal_code)
```

The public URL contains a high-entropy opaque token. Only its hash is stored. Human-readable slugs are not authorization secrets.

### 3.6 `shop_memberships`

Operational staff assignment.

```text
id uuid PK
business_id uuid NOT NULL
shop_id uuid NOT NULL
auth_user_id uuid NULL REFERENCES auth.users
telegram_user_id bigint NULL
role membership_role NOT NULL
display_name text NOT NULL
phone text NULL
active boolean NOT NULL DEFAULT true
created_at, updated_at timestamptz
FOREIGN KEY (shop_id, business_id) REFERENCES shops (id, business_id)
UNIQUE (id, business_id, shop_id)
```

Checks and indexes:

- At least one of `auth_user_id` or `telegram_user_id` is present.
- Unique active `(shop_id, auth_user_id)` when Auth identity is present.
- Unique active `(shop_id, telegram_user_id)` when Telegram identity is present.
- A barber referenced by a booking/transaction must be an active `barber` membership for that same shop.

## 4. Subscription, cash collection, and offboarding

### 4.1 `subscriptions`

```text
id uuid PK
business_id uuid NOT NULL
shop_id uuid NULL
scope billing_scope NOT NULL
status subscription_status NOT NULL
paid_from date NOT NULL
paid_until date NOT NULL CHECK (paid_until >= paid_from)
manual_override_until timestamptz NULL
manual_override_reason text NULL
suspended_reason suspension_reason NULL
suspended_at, resumed_at timestamptz NULL
status_changed_by uuid NULL REFERENCES platform_admins(auth_user_id)
created_at, updated_at timestamptz
FOREIGN KEY (business_id) REFERENCES businesses
FOREIGN KEY (shop_id, business_id) REFERENCES shops (id, business_id)
CHECK (
  (scope = 'business' AND shop_id IS NULL) OR
  (scope = 'shop' AND shop_id IS NOT NULL)
)
```

Partial unique indexes permit only one non-archived subscription per billing subject. A constraint trigger verifies:

- business billing mode accepts only a business-scoped subscription;
- per-shop mode accepts only shop-scoped subscriptions;
- mode changes cannot leave incompatible active subscriptions.

The application service locks the business, shops in UUID order, and subscriptions in UUID order during a mode transition. Business-to-shop copies the current entitlement to every non-archived shop. Shop-to-business is rejected unless all non-archived shops have identical compatible status, coverage, and override state.

### 4.2 `subscription_cash_receipts`

Append-only evidence of cash collected by the platform owner.

```text
id uuid PK
subscription_id uuid NOT NULL REFERENCES subscriptions
business_id uuid NOT NULL
shop_id uuid NULL
amount numeric(14,2) NOT NULL CHECK (> 0)
currency text NOT NULL DEFAULT 'AED' CHECK (currency = 'AED')
receipt_reference text NOT NULL UNIQUE
receipt_sequence bigint NOT NULL UNIQUE
collected_at timestamptz NOT NULL
coverage_from, coverage_until date NOT NULL
collected_by uuid NOT NULL REFERENCES platform_admins(auth_user_id)
evidence_note text NULL
reversal_of_id uuid NULL UNIQUE REFERENCES subscription_cash_receipts(id)
created_at timestamptz NOT NULL
CHECK (coverage_until >= coverage_from)
```

Receipt rows are never updated/deleted. Corrections use one linked reversal per original; the reversal must mirror the original subscription, tenant subject, amount, currency, and coverage. A reversal corrects immutable collection evidence and does not silently retract entitlement. Suspension or coverage removal is a separate audited operation.

### 4.3 `tenant_exports`

```text
id uuid PK
business_id uuid NOT NULL
shop_id uuid NULL
scope billing_scope NOT NULL
status export_status NOT NULL
schema_version text NOT NULL
format text NOT NULL CHECK (format IN ('zip_json_csv'))
object_key text NULL
sha256 text NULL
size_bytes bigint NULL CHECK (> 0)
content_type text NULL CHECK ('application/zip')
attempt_count integer NOT NULL DEFAULT 0 CHECK (0..3)
processing_started_at timestamptz NULL
object_deleted_at timestamptz NULL
requested_by uuid NOT NULL REFERENCES platform_admins
requested_at, ready_at, delivered_at, expires_at timestamptz NULL
failure_reason text NULL
```

Database checks and transition triggers enforce `requested → processing → ready|failed` and `ready → delivered`; processing claims use partial indexes and `FOR UPDATE SKIP LOCKED`. Stored ZIP files use opaque keys in the private `tenant-exports` Supabase Storage bucket, are delivered with signed URLs capped at 15 minutes, and are deleted after expiry while checksum/metadata remain. Export contents and redaction rules are versioned in code.

### 4.4 `offboarding_cases`

```text
id uuid PK
business_id uuid NOT NULL
shop_id uuid NULL
scope billing_scope NOT NULL
reason text NOT NULL
export_id uuid NOT NULL REFERENCES tenant_exports
requested_by uuid NOT NULL REFERENCES platform_admins
state text NOT NULL CHECK (state IN
  ('requested','frozen','export_ready','delivered','archived','cancelled'))
requested_at, frozen_at, delivered_at, archived_at timestamptz NULL
```

Lifecycle checks and transition triggers enforce `requested → frozen → export_ready → delivered → archived`, with cancellation only before archival. Partial unique indexes allow only one open case for a business or shop, and each export can belong to only one case. T1.6 archival requires an export in `delivered` state; the previously proposed security-incident override is deliberately not implemented without a separate approved retention/legal contract.

## 5. Shop configuration and customer data

### 5.1 `bots`

```text
id uuid PK
business_id uuid NULL
shop_id uuid NULL
role bot_role NOT NULL
token_ciphertext text NOT NULL
bot_username text NOT NULL
webhook_secret_hash text NOT NULL
healthy boolean NOT NULL DEFAULT true
last_health_at timestamptz NULL
created_at timestamptz
```

The master bot has null tenant columns; shop bots require both. Unique `(shop_id, role)` plus one partial unique master index. Webhook URLs use opaque bot IDs; the secret exists only in Telegram’s secret-token header and is compared to the stored hash.

### 5.2 `services`

```text
id, business_id, shop_id uuid
name text NOT NULL
price_gross numeric(14,2) NOT NULL CHECK (>= 0)
vat_rate numeric(5,2) NOT NULL DEFAULT 0 CHECK (BETWEEN 0 AND 100)
duration_minutes integer NOT NULL CHECK (BETWEEN 1 AND 1440)
active boolean NOT NULL DEFAULT true
sort_order integer NOT NULL DEFAULT 0
created_at, updated_at timestamptz
UNIQUE (id, business_id, shop_id)
UNIQUE (shop_id, normalized name)
```

### 5.3 `customers`

```text
id, business_id, shop_id uuid
telegram_user_id bigint NULL
display_name text NULL
phone_e164 text NULL
language language_code NOT NULL DEFAULT 'en'
blocked_at timestamptz NULL
block_reason text NULL
last_visit_at timestamptz NULL
anonymized_at timestamptz NULL
created_at, updated_at timestamptz
UNIQUE (id, business_id, shop_id)
UNIQUE (shop_id, telegram_user_id)
```

Telegram identity is optional for walk-ins; when present it is unique only within one shop. E.164 shape, blocked/reason pairing, and anonymization redaction are database-checked. The same individual in two shops has two isolated records. Barbers have no direct customer-table read policy, and public projections never include customer names.

### 5.4 Calendars

- `shop_business_hours`: shop, ISO weekday, open/close, next-day flag, effective dates.
- `shop_closures`: shop, time range, reason.
- `staff_schedules`: barber membership, ISO weekday, work range/next-day flag, effective dates.
- `staff_schedule_breaks`: schedule, start offset, and duration; a trigger keeps each break inside its shift.
- `staff_leave`: barber membership, time range, reason.
- `staff_unavailability`: barber membership, temporary time range, reason and actor.

All carry composite tenant foreign keys. Exclusion constraints prevent overlapping active effective hours/schedules. Triggers require a real barber membership; clock constraints reject incorrectly flagged overnight ranges.

## 6. Booking and queue

### 6.1 `bookings`

```text
id, business_id, shop_id uuid
customer_id uuid NULL
barber_membership_id uuid NOT NULL
type booking_type NOT NULL
status booking_status NOT NULL
source booking_source NOT NULL
queue_business_date date NULL
queue_number integer NULL CHECK (> 0)
scheduled_start, scheduled_end timestamptz NULL
hold_expires_at timestamptz NULL
estimated_start_at timestamptz NULL
rescheduled_from_booking_id uuid NULL
cancellation_reason text NULL
no_show_reason text NULL
confirmed_at, started_at, completed_at timestamptz NULL
auto_confirmed boolean NOT NULL DEFAULT false
created_at, updated_at timestamptz
UNIQUE (id, business_id, shop_id)
```

Composite foreign keys tie customer and barber to the same shop. Unique partial `(shop_id, queue_business_date, queue_number)` applies when a queue number exists.

Active appointment rows expose:

```sql
tstzrange(scheduled_start, scheduled_end, '[)')
```

A GiST exclusion constraint prevents overlap for the same `(shop_id, barber_membership_id)` while status is `held`, `requested`, `confirmed`, or `in_service`. Stale holds are expired under the shop transaction lock before allocation and by the scheduled worker; availability ignores `hold_expires_at <= now()`.

Implemented transition matrix:

```text
held      → requested | confirmed | cancelled | expired
requested → confirmed | cancelled
confirmed → in_service | cancelled | no_show
in_service → completed
```

Terminal rows do not transition. Cancellation retains earlier lifecycle timestamps as historical facts. Identity, tenant, customer, barber, source, scheduled range, reschedule link, service snapshots, and any allocated queue number are immutable. Rescheduling atomically cancels the original and creates one linked five-minute hold.

### 6.2 `booking_services`

```text
id, business_id, shop_id uuid
booking_id uuid NOT NULL
service_id uuid NOT NULL
service_name text NOT NULL
price_gross numeric(14,2) NOT NULL
vat_rate numeric(5,2) NOT NULL
duration_minutes integer NOT NULL
sort_order integer NOT NULL
```

Service name, price, tax, and duration are immutable snapshots. Browser reads are RLS-filtered through the parent booking; browser writes are not granted.

### 6.3 `queue_counters`

```text
shop_id uuid
business_id uuid
business_date date
last_number integer NOT NULL CHECK (>= 0)
PRIMARY KEY (shop_id, business_date)
```

Allocation upserts this row inside the shop transaction lock. The business date is the shop-timezone local date. Redis does not allocate numbers.

## 7. POS and accounting

### 7.1 `shop_legal_profiles`

Effective-dated snapshot source:

```text
id, business_id, shop_id uuid
legal_name, address text NOT NULL
vat_registered boolean NOT NULL
trn text NULL
pricing_mode pricing_mode NOT NULL
invoice_document_type document_type NOT NULL
effective_from timestamptz NOT NULL
effective_until timestamptz NULL
created_by_auth_user_id uuid NOT NULL
```

Only one effective profile may cover a shop/time. VAT profiles require a 15-digit TRN and either full or simplified tax-invoice document type; non-VAT profiles require no TRN and use receipts. Values are immutable; a controlled update may only shorten `effective_until`, and deletion is rejected. T2.3 server selection returns the source profile/effective range plus immutable supplier, VAT, pricing, document, and AED currency fields for checkout snapshotting.

### 7.2 `receipt_counters`

```text
shop_id, business_id uuid
fiscal_year integer
counter_kind sale | credit_note
last_number bigint NOT NULL
PRIMARY KEY (shop_id, fiscal_year, counter_kind)
```

An atomic `INSERT ... ON CONFLICT DO UPDATE` acquires the counter-row lock and advances exactly once. Sale and credit-note series are independent. Document numbers use the trusted shop internal code and Gregorian fiscal year; allocation remains an internal service used by checkout/correction transactions rather than a client endpoint.

### 7.3 `transactions`

```text
id, business_id, shop_id uuid
booking_id uuid NOT NULL
customer_id uuid NULL
barber_membership_id uuid NOT NULL
cash_shift_id uuid NULL
receipt_number text NOT NULL
document_type document_type NOT NULL
status transaction_status NOT NULL
currency text NOT NULL DEFAULT 'AED'
subtotal_gross numeric(14,2) NOT NULL
discount_total numeric(14,2) NOT NULL
net_total numeric(14,2) NOT NULL
vat_total numeric(14,2) NOT NULL
service_gross_total numeric(14,2) NOT NULL
tip_total numeric(14,2) NOT NULL
grand_total numeric(14,2) NOT NULL
refunded_total numeric(14,2) NOT NULL DEFAULT 0
legal_snapshot jsonb NOT NULL
created_by_auth_user_id uuid NOT NULL
created_at timestamptz NOT NULL
UNIQUE (id, business_id, shop_id)
UNIQUE (shop_id, receipt_number)
```

Checkout accepts only an existing completed booking; counter sales use a walk-in booking first. The server derives the customer, barber, legal profile, services, prices, VAT, and commission rules. Checks enforce all header arithmetic identities. Completed rows are immutable.

### 7.4 `transaction_items`

Immutable line snapshots:

```text
transaction_id, booking_service_id, service_id, barber_membership_id
service_name, quantity, unit_amount, pricing_mode, vat_rate
pre_discount_gross, discount_input, discount_gross
line_net, line_vat, line_gross
```

Composite foreign keys enforce same-shop ownership. Checks require the rounded line identities to reconcile.

`transaction_item_commissions` stores the restricted immutable financial split separately:

```text
transaction_item_id, commission_rule_id
rule_snapshot jsonb
commission_base, barber_commission, shop_share numeric(14,2)
```

The base is item net after discount, excluding VAT and tips. Receptionists cannot read commission rows. A barber can read only their own rows; managers, business owners, and platform administrators can read the approved scope. Deferred validation requires every item to have one commission snapshot and `barber_commission + shop_share = commission_base`.

### 7.5 `transaction_payments`

```text
transaction_id uuid
method payment_method
amount numeric(14,2) CHECK (> 0)
card_slip_reference text NULL
CHECK (method <> 'card' OR card_slip_reference IS NOT NULL)
```

One row per method is allowed. Card references use a strict safe-character format and reject PAN-like 13–19 digit sequences. Deferred validation requires payment sum = transaction grand total and an open matching cash shift whenever cash tender exists.

### 7.6 Refunds and credit notes

- `transaction_corrections`: immutable `void`/`refund` header linked to the original completed transaction, sequential credit-note identity, original cash shift where required, item/net/VAT/tip/grand totals, actor/reason/time.
- `transaction_correction_items`: original transaction-item reference plus server-derived gross, net, VAT, and shop-share reversal.
- `transaction_correction_item_commissions`: restricted per-item barber/commission reversal, separated so receptionists cannot browse pay data.
- `transaction_correction_payments`: returned cash/card tender snapshot and safe card slip reference; barbers cannot browse payment rows.
- Each correction allocates one shop/fiscal-year credit-note number and links one balanced reversing journal to the original checkout journal. Cash return creates one exact `refund` movement on an open shift.
- PostgreSQL deferred validation bounds cumulative gross per item, net/VAT, commission, tip, and each tender method against the immutable original. Partial slices round half-up; the final cumulative slice reconciles exactly to the stored original.
- A void must be the first and only correction, reverse the complete original item/tip/tender values, use cash-only original tender, and reuse the still-open original cash shift. Any sale containing card tender uses a refund/credit note because external terminal settlement is not tracked.
- Original transaction/item/payment/commission rows are never updated. The legacy `transactions.refunded_total` remains unchanged; correction tables are the append-only financial source.
- A provider-neutral `e_invoice_documents` adapter boundary is reserved for the platform's B2B SaaS invoices. No accredited provider is selected until the platform owner decides. Shop B2C service receipts remain on the normal POS document flow unless official scope changes.

### 7.7 `commission_rules`

```text
id, business_id, shop_id uuid
barber_membership_id uuid NULL
rule_type commission_rule_type
barber_pct numeric(5,2) NULL
tiers jsonb NULL
effective_from timestamptz NOT NULL
effective_until timestamptz NULL
created_by_membership_id uuid NOT NULL
created_at timestamptz
```

Rules support a shop default or barber override. Values are immutable; only the end of an effective period may be shortened. Exclusion constraints prevent overlapping periods for the same shop/default or barber override. Tier JSON permits 1–20 ordered, non-overlapping `[min_base,max_base)` bands with exactly one percentage or flat result per band; SQL validation and Decimal calculation are implemented.

### 7.8 `advances` and `advance_applications`

`advances`:

```text
id, business_id, shop_id uuid
barber_membership_id uuid
original_amount, outstanding_amount numeric(14,2) CHECK (>= 0)
status advance_status
given_by_membership_id uuid
given_at timestamptz
note text NULL
```

`advance_applications`:

```text
id, business_id, shop_id uuid
advance_id, payout_item_id uuid
amount numeric(14,2) CHECK (> 0)
created_at timestamptz
UNIQUE (advance_id, payout_item_id)
```

Advance grant increases a receivable and records cash out. It does not reduce earned commission. Applying it in a payout reduces outstanding exactly once.

### 7.9 `payout_runs` and `payout_items`

Run header: shop, closed period, status, prepared/approved/paid actors and times. Unique non-cancelled `(shop_id, period_start, period_end)`.

Each item stores barber, commission earnings, tips, adjustments, advance deduction, gross payable, and net paid. Checks require:

```text
gross_payable = commission_earnings + tips + adjustments
net_paid = gross_payable - advance_deduction
```

### 7.10 Cash shifts

- `cash_shifts`: tenant/shop, case-insensitive register label, status, opening float, opened by/at, closing expected, counted, variance, closed by/at.
- `cash_shift_movements`: tenant/shop/shift, type (`cash_sale`, `pay_in`, `pay_out`, `advance`, `payout`, `refund`), positive amount, manual reason or unique source entity, actor/time.
- Only one open shift per shop/register. Shifts move once from open to closed; closed rows and all movements are immutable.
- Expected cash is `opening + cash_sale + pay_in - pay_out - advance - payout - refund`. A database trigger recomputes this aggregate before accepting close, and `variance = counted - expected`.
- Card is not a cash movement type and is therefore excluded from expected physical cash. T2.4 checkout creates one `cash_sale` movement for only the cash portion of tender.
- Authenticated browser access is read-only and RLS-filtered to platform admin, owner, manager, and receptionist. Barbers and anonymous users read none; all writes use trusted FastAPI transactions.

### 7.11 Double-entry journal

Financial truth is append-only:

- `journal_accounts`: controlled account codes such as `cash`, `card_clearing`, `service_revenue`, `vat_payable`, `barber_payable`, `tip_payable`, `advance_receivable`, `refunds`.
- `journal_entries`: event header, tenant, source type/id, idempotency key, actor, time.
- `journal_postings`: account, optional barber, debit, credit; exactly one side positive.

T2.4 seeds eight controlled accounts and posts checkout debits to `cash`/`card_clearing`, with credits to `service_revenue`, `barber_payable`, `vat_payable`, and `tip_payable`. T2.5 linked reversing entries debit the stored revenue/shop share, barber payable, VAT payable, and tip payable and credit the returned cash/card clearing amounts. Deferred constraint triggers require at least two postings, `SUM(debit) = SUM(credit) > 0`, and correction totals that exactly match the original snapshots and correction rows. Update/delete triggers reject changes.

## 8. Reliability and audit

### 8.1 `idempotency_keys`

```text
scope text
key text
actor_id text
request_hash text
response_status integer NULL
response_body jsonb NULL
locked_at, completed_at, expires_at timestamptz
PRIMARY KEY (scope, key)
```

Reusing a key with a different request hash is a conflict.

### 8.2 `audit_log`

Append-only:

```text
id uuid PK
business_id, shop_id uuid NULL
actor_type actor_type
actor_id text NULL
action text
entity_type text
entity_id uuid NULL
request_id text NULL
before jsonb NULL
after jsonb NULL
created_at timestamptz
```

Sensitive fields are redacted before insertion.

### 8.3 `outbox_events`

```text
id uuid PK
business_id, shop_id uuid NULL
topic text
dedupe_key text UNIQUE
payload jsonb
status outbox_status
attempt_count integer
available_at, locked_at, delivered_at timestamptz NULL
last_error_code text NULL
created_at timestamptz
```

Workers claim events with `FOR UPDATE SKIP LOCKED`. Payloads contain IDs and sanitized message data, never secrets.

### 8.4 Messaging/support tables

- `chat_messages`: shop/customer scoped; redacted plain text; 90-day default retention.
- `escalations`: shop/customer scope, trigger, sanitized context, status/resolution.
- `blocked_telegram_users`: platform-wide block with audit metadata.
- `report_runs`: idempotent generated report record and checksum.

## 9. RLS and authorization

All browser sessions are subject to RLS. Backend mutations additionally perform explicit authorization and use parameterized SQL.

Helper functions live in the non-exposed `private` schema, are `SECURITY DEFINER`, set `search_path = pg_catalog, public`, use qualified table names, and revoke execution from `PUBLIC`/`anon`. Only explicitly authorized roles may execute them:

```text
is_platform_admin()
is_active_auth_user()
owns_business(p_business_id)
has_shop_membership(p_shop_id, allowed_roles[])
can_read_shop(p_business_id, p_shop_id)
can_read_business(p_business_id)
```

Policy summary:

| Actor | Read | Direct write |
|---|---|---|
| anon | No tables; execute sanitized public-queue function only | none |
| barber | Own profile, assigned-shop queue needed for work, own earnings/payout summary | none |
| receptionist | Assigned-shop operational reads; no private owner/platform data | none |
| manager | Assigned-shop operations/reports/config reads | none |
| business owner | All shops and aggregate data in owned business | none |
| platform admin | Platform APIs; restricted direct diagnostic reads where explicitly granted | none from browser |

The browser has no direct mutation policy. FastAPI owns mutations, validates role and ownership, and writes audit records.

RLS tests seed two businesses, at least two shops in one business, and one shop in the other. Every table/function is tested against:

- same shop;
- sibling shop as receptionist/barber;
- sibling shop as business owner;
- other business;
- platform admin;
- anonymous user.

## 10. Subscription entitlement function

One database function resolves access using locked current rows:

```text
resolve_entitlement(business_id, shop_id, at_time)
→ active | expired | suspended | offboarding | archived
```

It applies business billing or per-shop billing, inclusive `paid_until`, and the 00:05 Asia/Dubai boundary. API middleware uses it for early rejection, and every critical mutation rechecks it inside its transaction to eliminate time-of-check/time-of-use gaps.

Platform-admin subscription/export/offboarding endpoints bypass only the tenant entitlement gate, never authentication/audit.

## 11. Public queue

Anonymous callers use:

```text
get_public_queue(public_token)
→ shop display state, token number, coarse queue status, approximate wait
```

The function:

- hashes the token and finds one active public page;
- returns a generic suspension/archive state when inactive;
- never returns customer name, phone, barber private information, money, IDs, or billing reason;
- uses a fixed return type and safe `search_path`;
- is rate-limited at the API/proxy.

## 12. Redis keys

```text
fsm:{bot_id}:{chat_id}:*                  bot conversation state
rl:telegram:{bot_id}:{telegram_user_id}   flood limit
rl:ai:{shop_id}:{telegram_user_id}        AI budget
dedupe:tg:{bot_id}:{update_id}            24-hour update replay guard
cache:entitlement:{business_id}:{shop_id} short cache; invalidated on billing change
```

No queue counter, slot ownership, subscription state, balance, receipt counter, or payout truth exists only in Redis.

## 13. Retention and archive

- Chat content: default 90 days, then purge or anonymize.
- Authentication/session artifacts: shortest operational period.
- Tenant exports: short delivery window, then encrypted object deletion while retaining export metadata/checksum.
- Transactions, invoices/credit notes, cash receipts, payouts, journal, and audit: default retention is at least seven years, or longer when applicable UAE legal/accounting requirements demand it; the production policy is reviewed before launch.
- Archived tenant records remain inaccessible to tenant/public surfaces.
- Approved privacy anonymization replaces direct identifiers while preserving financial referential integrity.
- Hard tenant deletion is not a supported product operation.
