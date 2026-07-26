create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated, service_role;

alter default privileges for role postgres in schema public
  revoke select, insert, update, delete on tables from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
  revoke usage, select on sequences from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
  revoke execute on functions from public, anon, authenticated, service_role;

do $$ begin
  create type public.billing_mode as enum ('business', 'per_shop');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.billing_scope as enum ('business', 'shop');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.subscription_status as enum
    ('active', 'expired', 'suspended', 'offboarding', 'archived');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.suspension_reason as enum
    ('non_payment', 'manual', 'security', 'offboarding');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.membership_role as enum ('manager', 'receptionist', 'barber');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.bot_role as enum
    ('master', 'owner', 'receptionist', 'barber_crew', 'customer');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.actor_type as enum
    ('auth_user', 'telegram_user', 'platform_admin', 'system');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.outbox_status as enum
    ('pending', 'processing', 'delivered', 'failed');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.export_status as enum
    ('requested', 'processing', 'ready', 'delivered', 'failed');
exception when duplicate_object then null;
end $$;

create table public.user_profiles (
  auth_user_id uuid primary key references auth.users(id) on delete restrict,
  display_name text not null check (btrim(display_name) <> ''),
  phone text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.platform_admins (
  auth_user_id uuid primary key references auth.users(id) on delete restrict,
  telegram_user_id bigint unique,
  display_name text not null check (btrim(display_name) <> ''),
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.businesses (
  id uuid primary key default gen_random_uuid(),
  legal_name text not null check (btrim(legal_name) <> ''),
  display_name text not null check (btrim(display_name) <> ''),
  trade_license_number text,
  trade_license_expiry date,
  vat_registered boolean not null default false,
  trn text,
  invoice_address text,
  contact_name text,
  contact_phone text,
  contact_email text,
  billing_mode public.billing_mode not null,
  timezone text not null default 'Asia/Dubai',
  currency text not null default 'AED' check (currency = 'AED'),
  status public.subscription_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  unique (id, billing_mode),
  check (not vat_registered or nullif(btrim(trn), '') is not null),
  check ((status = 'archived') = (archived_at is not null))
);

create table public.business_owners (
  business_id uuid not null references public.businesses(id) on delete restrict,
  auth_user_id uuid not null references auth.users(id) on delete restrict,
  telegram_user_id bigint,
  is_primary boolean not null default true,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (business_id, auth_user_id)
);

create unique index one_primary_owner_per_business
  on public.business_owners (business_id)
  where active and is_primary;
create index business_owners_auth_user_idx
  on public.business_owners (auth_user_id, business_id)
  where active;

create table public.shops (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  name text not null check (btrim(name) <> ''),
  internal_code text not null check (btrim(internal_code) <> ''),
  timezone text not null default 'Asia/Dubai',
  currency text not null default 'AED' check (currency = 'AED'),
  public_queue_token_hash text not null unique,
  status public.subscription_status not null default 'active',
  open_time time not null,
  close_time time not null,
  default_service_minutes integer not null check (default_service_minutes > 0),
  eod_time time not null,
  settings jsonb not null default '{}'::jsonb check (jsonb_typeof(settings) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  unique (id, business_id),
  unique (business_id, internal_code),
  check ((status = 'archived') = (archived_at is not null))
);
create index shops_business_idx on public.shops (business_id, id);

create table public.shop_memberships (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  auth_user_id uuid references auth.users(id) on delete restrict,
  telegram_user_id bigint,
  role public.membership_role not null,
  display_name text not null check (btrim(display_name) <> ''),
  phone text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (auth_user_id is not null or telegram_user_id is not null)
);
create unique index active_shop_auth_membership
  on public.shop_memberships (shop_id, auth_user_id)
  where active and auth_user_id is not null;
create unique index active_shop_telegram_membership
  on public.shop_memberships (shop_id, telegram_user_id)
  where active and telegram_user_id is not null;
create index shop_memberships_auth_lookup
  on public.shop_memberships (auth_user_id, shop_id, role)
  where active and auth_user_id is not null;
create index shop_memberships_subject_idx
  on public.shop_memberships (business_id, shop_id);

create table public.bots (
  id uuid primary key default gen_random_uuid(),
  business_id uuid,
  shop_id uuid,
  role public.bot_role not null,
  token_ciphertext text not null check (btrim(token_ciphertext) <> ''),
  bot_username text not null check (btrim(bot_username) <> ''),
  webhook_secret_hash text not null check (btrim(webhook_secret_hash) <> ''),
  healthy boolean not null default true,
  last_health_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (
    (role = 'master' and business_id is null and shop_id is null)
    or
    (role <> 'master' and business_id is not null and shop_id is not null)
  )
);
create unique index one_master_bot
  on public.bots (role)
  where role = 'master';
create unique index one_shop_bot_per_role
  on public.bots (shop_id, role)
  where role <> 'master';
create index bots_shop_business_idx
  on public.bots (shop_id, business_id)
  where shop_id is not null;

create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid,
  scope public.billing_scope not null,
  status public.subscription_status not null default 'active',
  paid_from date not null,
  paid_until date not null check (paid_until >= paid_from),
  manual_override_until timestamptz,
  manual_override_reason text,
  suspended_reason public.suspension_reason,
  suspended_at timestamptz,
  resumed_at timestamptz,
  status_changed_by uuid references public.platform_admins(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (
    (scope = 'business' and shop_id is null)
    or
    (scope = 'shop' and shop_id is not null)
  ),
  check (
    (manual_override_until is null and manual_override_reason is null)
    or
    (manual_override_until is not null and nullif(btrim(manual_override_reason), '') is not null)
  ),
  check (
    (status = 'suspended' and suspended_reason is not null and suspended_at is not null)
    or
    status <> 'suspended'
  )
);
create unique index one_current_business_subscription
  on public.subscriptions (business_id)
  where scope = 'business' and status <> 'archived';
create unique index one_current_shop_subscription
  on public.subscriptions (shop_id)
  where scope = 'shop' and status <> 'archived';
create index subscriptions_business_shop_idx
  on public.subscriptions (business_id, shop_id, status);
create index subscriptions_status_changed_by_idx
  on public.subscriptions (status_changed_by)
  where status_changed_by is not null;

create table public.subscription_cash_receipts (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.subscriptions(id) on delete restrict,
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid,
  amount numeric(14,2) not null check (amount > 0),
  currency text not null default 'AED' check (currency = 'AED'),
  receipt_reference text not null unique check (btrim(receipt_reference) <> ''),
  receipt_sequence bigint generated always as identity unique,
  collected_at timestamptz not null,
  coverage_from date not null,
  coverage_until date not null check (coverage_until >= coverage_from),
  collected_by uuid not null references public.platform_admins(auth_user_id) on delete restrict,
  evidence_note text,
  reversal_of_id uuid unique references public.subscription_cash_receipts(id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (reversal_of_id is null or reversal_of_id <> id)
);
create index subscription_receipts_subject_idx
  on public.subscription_cash_receipts (business_id, shop_id, collected_at desc);
create index subscription_receipts_subscription_idx
  on public.subscription_cash_receipts (subscription_id, collected_at desc);
create index subscription_receipts_collector_idx
  on public.subscription_cash_receipts (collected_by, collected_at desc);

create table public.tenant_exports (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid,
  scope public.billing_scope not null,
  status public.export_status not null default 'requested',
  schema_version text not null check (btrim(schema_version) <> ''),
  format text not null check (format = 'zip_json_csv'),
  object_key text,
  sha256 text check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  requested_by uuid not null references public.platform_admins(auth_user_id) on delete restrict,
  requested_at timestamptz not null default now(),
  ready_at timestamptz,
  delivered_at timestamptz,
  expires_at timestamptz,
  failure_reason text,
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (
    (scope = 'business' and shop_id is null)
    or
    (scope = 'shop' and shop_id is not null)
  )
);
create index tenant_exports_subject_idx
  on public.tenant_exports (business_id, shop_id, requested_at desc);
create index tenant_exports_requester_idx
  on public.tenant_exports (requested_by, requested_at desc);

create table public.offboarding_cases (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid,
  scope public.billing_scope not null,
  reason text not null check (btrim(reason) <> ''),
  export_id uuid not null references public.tenant_exports(id) on delete restrict,
  requested_by uuid not null references public.platform_admins(auth_user_id) on delete restrict,
  state text not null check (
    state in ('requested', 'frozen', 'export_ready', 'delivered', 'archived', 'cancelled')
  ),
  requested_at timestamptz not null default now(),
  frozen_at timestamptz,
  delivered_at timestamptz,
  archived_at timestamptz,
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (
    (scope = 'business' and shop_id is null)
    or
    (scope = 'shop' and shop_id is not null)
  )
);
create index offboarding_cases_subject_idx
  on public.offboarding_cases (business_id, shop_id, requested_at desc);
create index offboarding_cases_export_idx
  on public.offboarding_cases (export_id);
create index offboarding_cases_requester_idx
  on public.offboarding_cases (requested_by, requested_at desc);

create table public.idempotency_keys (
  scope text not null,
  key text not null,
  actor_id text not null,
  request_hash text not null,
  response_status integer,
  response_body jsonb,
  locked_at timestamptz not null default now(),
  completed_at timestamptz,
  expires_at timestamptz not null,
  primary key (scope, key),
  check (expires_at > locked_at),
  check (
    (completed_at is null and response_status is null and response_body is null)
    or
    (completed_at is not null and response_status between 100 and 599)
  )
);
create index idempotency_expiry_idx on public.idempotency_keys (expires_at);

create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete restrict,
  shop_id uuid,
  actor_type public.actor_type not null,
  actor_id text,
  action text not null check (btrim(action) <> ''),
  entity_type text not null check (btrim(entity_type) <> ''),
  entity_id uuid,
  request_id text,
  before jsonb,
  after jsonb,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict
);
create index audit_log_subject_created_idx
  on public.audit_log (business_id, shop_id, created_at desc);
create index audit_log_entity_idx
  on public.audit_log (entity_type, entity_id, created_at desc);

create table public.outbox_events (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete restrict,
  shop_id uuid,
  topic text not null check (btrim(topic) <> ''),
  dedupe_key text not null unique,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  status public.outbox_status not null default 'pending',
  attempt_count integer not null default 0 check (attempt_count >= 0),
  available_at timestamptz not null default now(),
  locked_at timestamptz,
  delivered_at timestamptz,
  last_error_code text,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict
);
create index outbox_claim_idx
  on public.outbox_events (available_at, created_at)
  where status in ('pending', 'failed');
create index outbox_subject_idx
  on public.outbox_events (business_id, shop_id, created_at desc);

create function private.is_active_auth_user()
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select (select auth.uid()) is not null
    and exists (
      select 1
      from public.user_profiles up
      where up.auth_user_id = (select auth.uid())
        and up.active
    )
$$;

create function private.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_active_auth_user()
    and exists (
      select 1
      from public.platform_admins pa
      where pa.auth_user_id = (select auth.uid())
        and pa.active
    )
$$;

create function private.owns_business(p_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_active_auth_user()
    and exists (
      select 1
      from public.business_owners bo
      where bo.business_id = p_business_id
        and bo.auth_user_id = (select auth.uid())
        and bo.active
        and bo.is_primary
    )
$$;

create function private.has_shop_membership(
  p_shop_id uuid,
  p_allowed_roles public.membership_role[] default null
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_active_auth_user()
    and exists (
      select 1
      from public.shop_memberships sm
      where sm.shop_id = p_shop_id
        and sm.auth_user_id = (select auth.uid())
        and sm.active
        and (p_allowed_roles is null or sm.role = any(p_allowed_roles))
    )
$$;

create function private.can_read_shop(p_business_id uuid, p_shop_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_platform_admin()
    or private.owns_business(p_business_id)
    or (
      private.is_active_auth_user()
      and exists (
      select 1
      from public.shop_memberships sm
      where sm.business_id = p_business_id
        and sm.shop_id = p_shop_id
        and sm.auth_user_id = (select auth.uid())
        and sm.active
      )
    )
$$;

create function private.can_read_business(p_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_platform_admin()
    or private.owns_business(p_business_id)
$$;

create function private.validate_subscription_scope()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  selected_mode public.billing_mode;
begin
  select b.billing_mode
  into selected_mode
  from public.businesses b
  where b.id = new.business_id;

  if selected_mode is null then
    raise exception 'subscription business does not exist';
  end if;
  if selected_mode = 'business' and new.scope <> 'business' then
    raise exception 'business billing requires business subscription scope';
  end if;
  if selected_mode = 'per_shop' and new.scope <> 'shop' then
    raise exception 'per-shop billing requires shop subscription scope';
  end if;
  return new;
end
$$;

create function private.validate_business_billing_mode()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if new.billing_mode = old.billing_mode then
    return new;
  end if;
  if exists (
    select 1
    from public.subscriptions s
    where s.business_id = new.id
      and s.status <> 'archived'
      and (
        (new.billing_mode = 'business' and s.scope <> 'business')
        or
        (new.billing_mode = 'per_shop' and s.scope <> 'shop')
      )
  ) then
    raise exception 'billing mode conflicts with current subscriptions';
  end if;
  return new;
end
$$;

create function private.validate_receipt_scope()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  original_subscription_id uuid;
begin
  if not exists (
    select 1
    from public.subscriptions s
    where s.id = new.subscription_id
      and s.business_id = new.business_id
      and s.shop_id is not distinct from new.shop_id
  ) then
    raise exception 'receipt subject does not match subscription';
  end if;

  if new.reversal_of_id is not null then
    select r.subscription_id
    into original_subscription_id
    from public.subscription_cash_receipts r
    where r.id = new.reversal_of_id
      and r.reversal_of_id is null;

    if original_subscription_id is null
       or original_subscription_id <> new.subscription_id then
      raise exception 'receipt reversal does not match an original receipt';
    end if;
  end if;
  return new;
end
$$;

create function private.validate_offboarding_export()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1
    from public.tenant_exports e
    where e.id = new.export_id
      and e.business_id = new.business_id
      and e.shop_id is not distinct from new.shop_id
      and e.scope = new.scope
  ) then
    raise exception 'offboarding subject does not match export';
  end if;
  return new;
end
$$;

create function private.reject_update_delete()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception '% is append-only', tg_table_name;
end
$$;

revoke all on all functions in schema private from public, anon;
grant execute on function private.is_active_auth_user() to authenticated, service_role;
grant execute on function private.is_platform_admin() to authenticated, service_role;
grant execute on function private.owns_business(uuid) to authenticated, service_role;
grant execute on function private.has_shop_membership(uuid, public.membership_role[])
  to authenticated, service_role;
grant execute on function private.can_read_shop(uuid, uuid) to authenticated, service_role;
grant execute on function private.can_read_business(uuid) to authenticated, service_role;

create trigger subscriptions_validate_scope
before insert or update of business_id, shop_id, scope
on public.subscriptions
for each row execute function private.validate_subscription_scope();

create trigger businesses_validate_billing_mode
before update of billing_mode
on public.businesses
for each row execute function private.validate_business_billing_mode();

create trigger subscription_cash_receipts_validate_scope
before insert on public.subscription_cash_receipts
for each row execute function private.validate_receipt_scope();

create trigger subscription_cash_receipts_append_only
before update or delete on public.subscription_cash_receipts
for each row execute function private.reject_update_delete();

create trigger offboarding_cases_validate_export
before insert or update of business_id, shop_id, scope, export_id
on public.offboarding_cases
for each row execute function private.validate_offboarding_export();

create trigger audit_log_append_only
before update or delete on public.audit_log
for each row execute function private.reject_update_delete();

alter table public.user_profiles enable row level security;
alter table public.user_profiles force row level security;
alter table public.platform_admins enable row level security;
alter table public.platform_admins force row level security;
alter table public.businesses enable row level security;
alter table public.businesses force row level security;
alter table public.business_owners enable row level security;
alter table public.business_owners force row level security;
alter table public.shops enable row level security;
alter table public.shops force row level security;
alter table public.shop_memberships enable row level security;
alter table public.shop_memberships force row level security;
alter table public.bots enable row level security;
alter table public.bots force row level security;
alter table public.subscriptions enable row level security;
alter table public.subscriptions force row level security;
alter table public.subscription_cash_receipts enable row level security;
alter table public.subscription_cash_receipts force row level security;
alter table public.tenant_exports enable row level security;
alter table public.tenant_exports force row level security;
alter table public.offboarding_cases enable row level security;
alter table public.offboarding_cases force row level security;
alter table public.idempotency_keys enable row level security;
alter table public.idempotency_keys force row level security;
alter table public.audit_log enable row level security;
alter table public.audit_log force row level security;
alter table public.outbox_events enable row level security;
alter table public.outbox_events force row level security;

create policy user_profiles_read_self_or_platform
on public.user_profiles for select to authenticated
using (
  auth_user_id = (select auth.uid())
  or (select private.is_platform_admin())
);

create policy platform_admins_read_platform
on public.platform_admins for select to authenticated
using ((select private.is_platform_admin()));

create policy businesses_read_authorized
on public.businesses for select to authenticated
using ((select private.can_read_business(id)));

create policy business_owners_read_authorized
on public.business_owners for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

create policy shops_read_authorized
on public.shops for select to authenticated
using ((select private.can_read_shop(business_id, id)));

create policy shop_memberships_read_authorized
on public.shop_memberships for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    auth_user_id = (select auth.uid())
    and active
  )
  or (
    (select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
    ))
  )
);

create policy subscriptions_read_owner_or_platform
on public.subscriptions for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

create policy subscription_receipts_read_owner_or_platform
on public.subscription_cash_receipts for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

create policy tenant_exports_read_platform
on public.tenant_exports for select to authenticated
using ((select private.is_platform_admin()));

create policy offboarding_cases_read_platform
on public.offboarding_cases for select to authenticated
using ((select private.is_platform_admin()));

create policy audit_log_read_owner_or_platform
on public.audit_log for select to authenticated
using (
  (select private.is_platform_admin())
  or (
    business_id is not null
    and (select private.owns_business(business_id))
  )
);

revoke all on all tables in schema public from anon, authenticated;
grant select on table
  public.user_profiles,
  public.platform_admins,
  public.businesses,
  public.business_owners,
  public.shops,
  public.shop_memberships,
  public.subscriptions,
  public.subscription_cash_receipts,
  public.tenant_exports,
  public.offboarding_cases,
  public.audit_log
to authenticated;

grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
