# DATA MODEL — Supabase schema, RLS, Redis keys

Source of truth for Phase 0 migrations. Migrations live in `supabase/migrations/` as numbered SQL files (`0001_enums.sql`, `0002_tables.sql`, `0003_indexes.sql`, `0004_rls.sql`, `0005_functions.sql`, `0006_seed_static.sql`). All DDL idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE`). All timestamps `timestamptz` in UTC. All money `numeric(10,2)`. All PKs `uuid DEFAULT gen_random_uuid()` unless stated.

## 1. Enums (`0001_enums.sql`)

```sql
CREATE TYPE bot_role        AS ENUM ('master','owner','receptionist','barber_crew','customer');
CREATE TYPE staff_role      AS ENUM ('owner','receptionist','barber');
CREATE TYPE lang_code       AS ENUM ('en','ar','hi','ur');
CREATE TYPE booking_type    AS ENUM ('queue','appointment','walk_in');
CREATE TYPE booking_status  AS ENUM ('requested','confirmed','in_service','completed','no_show','cancelled');
CREATE TYPE booking_source  AS ENUM ('telegram','pos','dashboard');
CREATE TYPE payment_method  AS ENUM ('cash','card');
CREATE TYPE txn_status      AS ENUM ('completed','voided');
CREATE TYPE rule_type       AS ENUM ('fixed_pct','tiered');
CREATE TYPE ledger_type     AS ENUM ('commission','tip','advance','advance_deduction','adjustment');
CREATE TYPE deduction_mode  AS ENUM ('one_time','monthly');
CREATE TYPE advance_status  AS ENUM ('open','settled');
CREATE TYPE escalation_trigger AS ENUM ('guardrail','manual','ai_error');
CREATE TYPE escalation_status  AS ENUM ('open','monitoring','blocked','resolved');
CREATE TYPE report_period   AS ENUM ('daily','monthly');
CREATE TYPE actor_type      AS ENUM ('staff','platform_owner','system','customer');
CREATE TYPE chat_role       AS ENUM ('user','assistant','system');
```

Wrap each in `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object THEN NULL; END $$;` for idempotency.

## 2. Tables (`0002_tables.sql`)

### 2.1 `shops`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| name | text | NOT NULL |
| slug | text | NOT NULL, UNIQUE, `^[a-z0-9-]{3,40}$` CHECK — public queue URL key |
| timezone | text | NOT NULL DEFAULT 'Asia/Dubai' |
| currency | text | NOT NULL DEFAULT 'AED' |
| open_time | time | NOT NULL DEFAULT '10:00' |
| close_time | time | NOT NULL DEFAULT '23:00' |
| weekly_off | smallint[] | NOT NULL DEFAULT '{}' — ISO weekday numbers (1=Mon..7=Sun) |
| default_service_minutes | int | NOT NULL DEFAULT 30, CHECK (> 0) — used by 5-min auto-confirm |
| eod_time | time | NOT NULL DEFAULT '23:30' — local shop time the EOD report fires |
| active | boolean | NOT NULL DEFAULT true |
| settings | jsonb | NOT NULL DEFAULT '{}' |
| created_at | timestamptz | NOT NULL DEFAULT now() |

### 2.2 `bots`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NULL — NULL = the global Master bot |
| role | bot_role | NOT NULL |
| token_encrypted | text | NOT NULL — Fernet-encrypted; plaintext never stored |
| bot_username | text | NOT NULL — from getMe at onboarding |
| webhook_secret | text | NOT NULL — random 32 hex; path component + `X-Telegram-Bot-Api-Secret-Token` |
| healthy | boolean | NOT NULL DEFAULT true |
| last_health_at | timestamptz | NULL |
| created_at | timestamptz | NOT NULL DEFAULT now() |

UNIQUE `(shop_id, role)` (use a partial unique index `ON bots(role) WHERE shop_id IS NULL` to enforce a single master row).

### 2.3 `staff`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NOT NULL |
| role | staff_role | NOT NULL |
| name | text | NOT NULL |
| telegram_user_id | bigint | NOT NULL |
| phone | text | NULL |
| active | boolean | NOT NULL DEFAULT true |
| created_at | timestamptz | NOT NULL DEFAULT now() |

UNIQUE `(shop_id, telegram_user_id)`. Barbers are `role='barber'` rows; bookings/transactions FK to `staff.id` and services validate the row has role barber.

### 2.4 `services`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NOT NULL |
| name | text | NOT NULL |
| price | numeric(10,2) | NOT NULL CHECK (>= 0) |
| duration_min | int | NOT NULL CHECK (> 0) |
| active | boolean | NOT NULL DEFAULT true |
| sort_order | int | NOT NULL DEFAULT 0 |

UNIQUE `(shop_id, name)`.

### 2.5 `customers`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NOT NULL |
| telegram_user_id | bigint | NOT NULL |
| name | text | NULL — asked politely on first contact ("Sir, what is your good name?") |
| phone | text | NULL — from Telegram contact share (D9); E.164 |
| language | lang_code | NOT NULL DEFAULT 'en' |
| is_blocked | boolean | NOT NULL DEFAULT false — shop-level block |
| block_reason | text | NULL |
| last_visit_at | timestamptz | NULL — set on booking completed |
| created_at | timestamptz | NOT NULL DEFAULT now() |

UNIQUE `(shop_id, telegram_user_id)`. Same person in two shops = two rows (tenant isolation).

### 2.6 `bookings`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NOT NULL |
| customer_id | uuid | FK customers NULL — NULL only for anonymous walk-ins |
| staff_id | uuid | FK staff NOT NULL — the barber |
| service_id | uuid | FK services NOT NULL |
| type | booking_type | NOT NULL |
| status | booking_status | NOT NULL DEFAULT 'requested' |
| token_number | int | NULL — assigned at confirmation (queue/walk-in) or promotion (appointment) |
| queue_date | date | NULL — shop-local date the token belongs to |
| scheduled_at | timestamptz | NULL — appointments only; CHECK (`type <> 'appointment' OR scheduled_at IS NOT NULL`) |
| est_duration_min | int | NULL — set by receptionist at confirm, or shop default on auto-confirm |
| est_start_at | timestamptz | NULL — recomputed by queue engine |
| confirmed_at | timestamptz | NULL |
| auto_confirmed | boolean | NOT NULL DEFAULT false |
| started_at | timestamptz | NULL |
| completed_at | timestamptz | NULL |
| reminded_at | timestamptz | NULL — 5-min reminder pressed |
| source | booking_source | NOT NULL |
| created_at | timestamptz | NOT NULL DEFAULT now() |

UNIQUE `(shop_id, queue_date, token_number)` (partial: `WHERE token_number IS NOT NULL`).

**Status machine** (enforced in `booking_service`, single transition function):
```
requested → confirmed | cancelled            (receptionist Confirm/Reject, or auto-confirm task)
confirmed → in_service | no_show | cancelled (Start Service / No Show / customer cancel)
in_service → completed                        (Checkout)
```
No other transitions permitted; illegal transition = ValueError + audit row.

### 2.7 `transactions`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK shops NOT NULL |
| booking_id | uuid | FK bookings NULL — NULL for counter sales without queue entry |
| staff_id | uuid | FK staff NOT NULL — the barber credited |
| customer_id | uuid | FK customers NULL |
| subtotal | numeric(10,2) | NOT NULL CHECK (>= 0) — sum of items |
| tip_amount | numeric(10,2) | NOT NULL DEFAULT 0 CHECK (>= 0) |
| total | numeric(10,2) | NOT NULL CHECK (total = subtotal + tip_amount) |
| payment_method | payment_method | NOT NULL |
| card_slip_number | text | NULL, CHECK (`payment_method <> 'card' OR card_slip_number IS NOT NULL`) |
| status | txn_status | NOT NULL DEFAULT 'completed' |
| voided_by / voided_at / void_reason | uuid/timestamptz/text | NULL — voiding is owner-only, writes reversing ledger rows |
| created_by | uuid | FK staff NOT NULL |
| created_at | timestamptz | NOT NULL DEFAULT now() |

### 2.8 `transaction_items`

`id PK, transaction_id FK NOT NULL ON DELETE CASCADE, service_id FK NOT NULL, service_name text NOT NULL, price numeric(10,2) NOT NULL` — name/price are point-of-sale snapshots; later service edits never rewrite history.

### 2.9 `commission_rules`

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK NOT NULL |
| staff_id | uuid | FK staff NULL — NULL = shop default rule |
| rule_type | rule_type | NOT NULL |
| barber_pct | numeric(5,2) | NULL CHECK (0–100) — required when `fixed_pct` |
| tiers | jsonb | NULL — required when `tiered`; array sorted by `above` ascending, e.g. `[{"above": 0, "barber_pct": 50}, {"above": 100, "barber_flat": 25}]` |
| active | boolean | NOT NULL DEFAULT true |
| effective_from | date | NOT NULL DEFAULT current_date |
| created_at | timestamptz | NOT NULL DEFAULT now() |

**Resolution** (in `commission_service.resolve_rule`): active rule with `staff_id = barber` and latest `effective_from <= today`; else the shop-default (`staff_id IS NULL`) equivalent; no rule at all → hard error (onboarding guarantees a default).
**Tier evaluation**: pick the tier with the greatest `above` that is `< subtotal`; apply `barber_flat` if present else `barber_pct`. Tip is never part of the split — 100% barber, own ledger row.

### 2.10 `ledger_entries` — append-only money truth

| column | type | constraints |
|---|---|---|
| id | uuid | PK |
| shop_id | uuid | FK NOT NULL |
| staff_id | uuid | FK NOT NULL — the barber |
| entry_type | ledger_type | NOT NULL |
| amount | numeric(10,2) | NOT NULL — **signed**: commission +, tip +, advance −, advance_deduction −, adjustment ± |
| transaction_id | uuid | FK NULL |
| advance_id | uuid | FK NULL |
| note | text | NULL |
| created_by | uuid | NULL — staff id or NULL for system |
| created_at | timestamptz | NOT NULL DEFAULT now() |

No UPDATE/DELETE ever (enforced: RLS grants INSERT/SELECT only, plus a trigger raising exception on UPDATE/DELETE). Corrections are `adjustment` rows. Net payable for a period = `SUM(amount)` filtered by staff + date range. Shop profit side comes from `transactions.subtotal − commission entries` (report_service computes both and must reconcile).

### 2.11 `advances`

`id PK, shop_id FK NOT NULL, staff_id FK NOT NULL, amount numeric(10,2) NOT NULL CHECK (> 0), deduction_mode deduction_mode NOT NULL, outstanding numeric(10,2) NOT NULL, status advance_status NOT NULL DEFAULT 'open', given_by uuid FK staff NOT NULL, note text, created_at, settled_at`

Flow: giving an advance inserts the row AND a `ledger_entries(advance, −amount)` row atomically.
- `one_time`: the single ledger row already nets it against the next payout; `outstanding` tracks display until the next EOD marks `settled`.
- `monthly`: at monthly report time, `report_service` inserts `advance_deduction` rows (capped at that month's positive balance; remainder stays `outstanding`).

### 2.12 `chat_messages`

`id PK, shop_id FK NOT NULL, customer_id FK NOT NULL, role chat_role NOT NULL, content text NOT NULL, created_at DEFAULT now()`
Index `(shop_id, customer_id, created_at DESC)`. Powers the receptionist "[View Last 25 Messages]" button and AI context. Retention: Celery monthly purge > 90 days (Phase 1H).

### 2.13 `escalations`

`id PK, shop_id FK NOT NULL, customer_id FK NOT NULL, trigger escalation_trigger NOT NULL, message text NOT NULL, context jsonb NOT NULL DEFAULT '{}' (last 10 chat messages snapshot), status escalation_status NOT NULL DEFAULT 'open', created_at, resolved_at, resolved_by text`

### 2.14 `audit_log`

`id PK, shop_id FK NULL (NULL = platform-level), actor_type actor_type NOT NULL, actor_id text NULL, action text NOT NULL (verb.noun e.g. 'booking.confirm'), entity text NOT NULL, entity_id uuid NULL, payload jsonb NOT NULL DEFAULT '{}', created_at DEFAULT now()`
Append-only (same trigger protection as ledger).

### 2.15 `eod_reports`

`id PK, shop_id FK NOT NULL, report_date date NOT NULL, period report_period NOT NULL, payload jsonb NOT NULL, sent_at timestamptz`
UNIQUE `(shop_id, report_date, period)` — the idempotency latch for Celery.

### 2.16 `platform_admins`

`telegram_user_id bigint PK, name text, added_at timestamptz DEFAULT now()`
Seeded from `PLATFORM_ADMIN_TELEGRAM_IDS`. Master bot ignores everyone else silently.

### 2.17 `blocked_users`

`telegram_user_id bigint PK, reason text, blocked_by bigint NOT NULL, created_at DEFAULT now()`
Platform-wide block (Master bot [Block User]). Checked in customer-bot middleware before anything else; blocked users get no response at all.

## 3. Indexes (`0003_indexes.sql`)

```sql
CREATE INDEX ON bookings (shop_id, queue_date, status);
CREATE INDEX ON bookings (shop_id, staff_id, queue_date) WHERE status IN ('confirmed','in_service');
CREATE INDEX ON bookings (shop_id, scheduled_at) WHERE type = 'appointment' AND status = 'confirmed';
CREATE INDEX ON transactions (shop_id, created_at);
CREATE INDEX ON ledger_entries (shop_id, staff_id, created_at);
CREATE INDEX ON chat_messages (shop_id, customer_id, created_at DESC);
CREATE INDEX ON escalations (status) WHERE status = 'open';
CREATE INDEX ON audit_log (shop_id, created_at);
CREATE INDEX ON customers (shop_id, phone) WHERE phone IS NOT NULL;
```

## 4. RLS (`0004_rls.sql`)

`ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` on **every** table above. The backend (service-role key) bypasses RLS by design — code-level `shop_id` scoping is mandatory there (MASTER_PLAN convention 1). RLS exists for the web clients (anon + authenticated JWTs).

JWT shape (set via Supabase Auth admin API when creating web users, Phase 2/3):
```json
app_metadata: { "shop_id": "<uuid>", "app_role": "receptionist" | "shop_owner" | "platform_admin" }
```

Helper (SQL): `auth.jwt() -> 'app_metadata' ->> 'shop_id'` and `->> 'app_role'`.

Policy matrix (create as separate named policies; `platform_admin` gets unrestricted SELECT on all tables + UPDATE where noted):

| Table | anon | receptionist (own shop) | shop_owner (own shop) | platform_admin |
|---|---|---|---|---|
| shops | — | SELECT | SELECT | ALL |
| bots | — | — | — | ALL (token_encrypted still never decryptable client-side) |
| staff | — | SELECT | SELECT | ALL |
| services | — | SELECT | SELECT, UPDATE | ALL |
| customers | — | SELECT, UPDATE (name/phone only) | SELECT | ALL |
| bookings | — | SELECT, INSERT, UPDATE | SELECT | ALL |
| transactions + items | — | SELECT, INSERT | SELECT | SELECT |
| commission_rules | — | SELECT | SELECT | ALL |
| ledger_entries | — | INSERT, SELECT | SELECT | SELECT |
| advances | — | SELECT, INSERT, UPDATE | SELECT, INSERT, UPDATE | SELECT |
| chat_messages | — | SELECT | — | SELECT |
| escalations | — | — | SELECT | ALL |
| audit_log | — | — | SELECT | SELECT |
| eod_reports | — | — | SELECT | SELECT |
| platform_admins / blocked_users | — | — | — | ALL |

Every shop-scoped policy body: `shop_id::text = auth.jwt() -> 'app_metadata' ->> 'shop_id'` AND role check. Write policies additionally validate status-machine-safe columns where practical; complex transitions go through the backend anyway (Phase 2 UI calls backend API for mutations, direct Supabase writes limited to reads + Realtime — decided in PHASE_2 doc).

**Public queue access** — no table policy for anon. Instead one function:

```sql
CREATE OR REPLACE FUNCTION get_public_queue(p_slug text)
RETURNS TABLE (token_number int, display_name text, barber_name text,
               status booking_status, est_start_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT b.token_number,
         COALESCE(split_part(c.name,' ',1), 'Guest'),
         s.name, b.status, b.est_start_at
  FROM bookings b
  JOIN shops sh ON sh.id = b.shop_id AND sh.slug = p_slug AND sh.active
  JOIN staff s  ON s.id = b.staff_id
  LEFT JOIN customers c ON c.id = b.customer_id
  WHERE b.queue_date = (now() AT TIME ZONE sh.timezone)::date
    AND b.status IN ('confirmed','in_service')
  ORDER BY b.est_start_at NULLS LAST;
$$;
GRANT EXECUTE ON FUNCTION get_public_queue(text) TO anon;
```

First name only, no phones, no money, no chat. This function is the ONLY anon surface.

## 5. Realtime strategy

- Staff tablet (Phase 2): Supabase Realtime **Postgres Changes** on `bookings` + `transactions` filtered by `shop_id` (RLS-authorized via their JWT).
- Public TV / customer phone page: Supabase Realtime **Broadcast** channel `queue:{slug}` — backend publishes a sanitized queue snapshot (same shape as `get_public_queue`) after every queue mutation. No DB exposure to anon. Fallback: poll `get_public_queue` RPC every 15 s.

## 6. Redis key map (DB 0)

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `fsm:{bot_id}:{chat_id}:state` / `:data` | aiogram RedisStorage | none | button-flow state per user per bot |
| `qtoken:{shop_id}:{YYYY-MM-DD}` | INT (INCR) | 48h | daily token counter; DB unique constraint is the final guard |
| `lock:booking:{booking_id}` | SET NX EX 300 | 300s | confirm race guard (receptionist vs auto-confirm) |
| `lock:barber:{staff_id}` | SET NX EX 10 | 10s | serialize queue recompute per barber |
| `rl:ai:{shop_id}:{tg_user_id}` | INCR + EXPIRE 3600 | 1h | AI message budget (default 20/h → buttons-only notice) |
| `rl:msg:{bot_id}:{tg_user_id}` | INCR + EXPIRE 60 | 60s | flood guard (default 20 msg/min → ignore) |
| `aictx:{shop_id}:{customer_id}` | LIST (LPUSH/LTRIM 12) | 24h | rolling AI context window (DB `chat_messages` is the permanent record) |

Redis is cache/coordination only — **every fact Redis holds is reconstructible from Postgres**. Redis flush must never lose money or bookings.

## 7. Data lifecycle

- `chat_messages` purge > 90 days (monthly Celery task).
- `audit_log`, `ledger_entries`, `transactions`, `eod_reports`: never purged (financial record).
- Shop offboarding = `shops.active = false` (soft). Hard delete is a manual SQL runbook item only.
