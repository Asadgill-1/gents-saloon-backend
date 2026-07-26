create schema if not exists extensions;
create extension if not exists btree_gist with schema extensions;

do $$ begin
  create type public.language_code as enum ('en', 'ar', 'hi', 'ur');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.pricing_mode as enum ('vat_inclusive', 'vat_exclusive');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.document_type as enum ('receipt', 'tax_invoice', 'credit_note');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.commission_rule_type as enum ('fixed_percentage', 'tier');
exception when duplicate_object then null;
end $$;

create function private.valid_commission_tiers(p_tiers jsonb)
returns boolean
language plpgsql
immutable
strict
set search_path = pg_catalog
as $$
declare
  tier jsonb;
  tier_min numeric;
  tier_max numeric;
  tier_pct numeric;
  tier_flat numeric;
  previous_max numeric := null;
  unlimited_seen boolean := false;
begin
  if jsonb_typeof(p_tiers) <> 'array'
    or jsonb_array_length(p_tiers) not between 1 and 20
  then
    return false;
  end if;

  for tier in select value from jsonb_array_elements(p_tiers)
  loop
    if jsonb_typeof(tier) <> 'object'
      or exists (
        select 1
        from jsonb_object_keys(tier) as keys(name)
        where name not in ('min_base', 'max_base', 'barber_pct', 'barber_flat')
      )
      or not tier ? 'min_base'
      or jsonb_typeof(tier -> 'min_base') <> 'number'
      or (
        tier ? 'max_base'
        and jsonb_typeof(tier -> 'max_base') <> 'number'
      )
      or (
        (tier ? 'barber_pct') = (tier ? 'barber_flat')
      )
      or (
        tier ? 'barber_pct'
        and jsonb_typeof(tier -> 'barber_pct') <> 'number'
      )
      or (
        tier ? 'barber_flat'
        and jsonb_typeof(tier -> 'barber_flat') <> 'number'
      )
    then
      return false;
    end if;

    tier_min := (tier ->> 'min_base')::numeric;
    tier_max := case when tier ? 'max_base' then (tier ->> 'max_base')::numeric end;
    tier_pct := case when tier ? 'barber_pct' then (tier ->> 'barber_pct')::numeric end;
    tier_flat := case when tier ? 'barber_flat' then (tier ->> 'barber_flat')::numeric end;

    if unlimited_seen
      or tier_min < 0
      or (previous_max is not null and tier_min < previous_max)
      or (tier_max is not null and tier_max <= tier_min)
      or (tier_pct is not null and (tier_pct < 0 or tier_pct > 100))
      or (tier_flat is not null and tier_flat < 0)
    then
      return false;
    end if;

    if tier_max is null then
      unlimited_seen := true;
    else
      previous_max := tier_max;
    end if;
  end loop;

  return true;
exception when invalid_text_representation or numeric_value_out_of_range then
  return false;
end
$$;

create function private.validate_barber_membership()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1
    from public.shop_memberships sm
    where sm.id = new.barber_membership_id
      and sm.business_id = new.business_id
      and sm.shop_id = new.shop_id
      and sm.role = 'barber'
  ) then
    raise exception 'barber membership is invalid for shop';
  end if;
  return new;
end
$$;

create function private.can_read_private_staff_calendar(
  p_business_id uuid,
  p_shop_id uuid,
  p_barber_membership_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select private.is_platform_admin()
    or private.owns_business(p_business_id)
    or private.has_shop_membership(
      p_shop_id,
      array['manager', 'receptionist']::public.membership_role[]
    )
    or (
      private.is_active_auth_user()
      and exists (
        select 1
        from public.shop_memberships sm
        where sm.id = p_barber_membership_id
          and sm.business_id = p_business_id
          and sm.shop_id = p_shop_id
          and sm.auth_user_id = (select auth.uid())
          and sm.active
          and sm.role = 'barber'
      )
    )
$$;

create function private.is_own_barber_membership(
  p_business_id uuid,
  p_shop_id uuid,
  p_barber_membership_id uuid
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
      where sm.id = p_barber_membership_id
        and sm.business_id = p_business_id
        and sm.shop_id = p_shop_id
        and sm.auth_user_id = (select auth.uid())
        and sm.active
        and sm.role = 'barber'
    )
$$;

create function private.guard_effective_source_update()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if (to_jsonb(new) - 'effective_until') is distinct from
     (to_jsonb(old) - 'effective_until')
  then
    raise exception '% values are immutable', tg_table_name;
  end if;
  if old.effective_until is not null
    and (
      new.effective_until is null
      or new.effective_until > old.effective_until
    )
  then
    raise exception '% effective period cannot be extended', tg_table_name;
  end if;
  return new;
end
$$;

create function private.reject_delete()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception '% rows cannot be deleted', tg_table_name;
end
$$;

revoke all on function private.valid_commission_tiers(jsonb)
  from public, anon, authenticated;
grant execute on function private.valid_commission_tiers(jsonb) to service_role;
revoke all on function private.validate_barber_membership()
  from public, anon, authenticated, service_role;
revoke all on function private.guard_effective_source_update()
  from public, anon, authenticated, service_role;
revoke all on function private.reject_delete()
  from public, anon, authenticated, service_role;
revoke all on function private.can_read_private_staff_calendar(uuid, uuid, uuid)
  from public, anon;
grant execute on function private.can_read_private_staff_calendar(uuid, uuid, uuid)
  to authenticated, service_role;
revoke all on function private.is_own_barber_membership(uuid, uuid, uuid)
  from public, anon;
grant execute on function private.is_own_barber_membership(uuid, uuid, uuid)
  to authenticated, service_role;

create table public.services (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  name text not null check (btrim(name) <> ''),
  price_gross numeric(14,2) not null check (price_gross >= 0),
  vat_rate numeric(5,2) not null default 0 check (vat_rate between 0 and 100),
  duration_minutes integer not null check (duration_minutes between 1 and 1440),
  active boolean not null default true,
  sort_order integer not null default 0 check (sort_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id)
);
create unique index services_shop_name_unique
  on public.services (shop_id, lower(btrim(name)));
create index services_shop_business_fk_idx
  on public.services (shop_id, business_id);
create index services_active_list_idx
  on public.services (shop_id, sort_order, id)
  where active;

create table public.customers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  telegram_user_id bigint,
  display_name text,
  phone_e164 text,
  language public.language_code not null default 'en',
  blocked_at timestamptz,
  block_reason text,
  last_visit_at timestamptz,
  anonymized_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (display_name is null or btrim(display_name) <> ''),
  check (phone_e164 is null or phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  check ((blocked_at is null) = (block_reason is null)),
  check (block_reason is null or btrim(block_reason) <> ''),
  check (
    anonymized_at is null
    or (
      telegram_user_id is null
      and display_name is null
      and phone_e164 is null
    )
  )
);
create unique index customers_shop_telegram_unique
  on public.customers (shop_id, telegram_user_id)
  where telegram_user_id is not null and anonymized_at is null;
create index customers_shop_business_fk_idx
  on public.customers (shop_id, business_id);
create index customers_shop_recent_idx
  on public.customers (shop_id, last_visit_at desc, id)
  where anonymized_at is null;

create table public.shop_business_hours (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  iso_weekday smallint not null check (iso_weekday between 1 and 7),
  open_time time not null,
  close_time time not null,
  closes_next_day boolean not null default false,
  effective_from date not null,
  effective_until date,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (open_time <> close_time),
  check (effective_until is null or effective_until > effective_from),
  exclude using gist (
    shop_id with =,
    iso_weekday with =,
    daterange(effective_from, coalesce(effective_until, 'infinity'::date), '[)') with &&
  ) where (active)
);
create index shop_business_hours_shop_business_fk_idx
  on public.shop_business_hours (shop_id, business_id);

create table public.shop_closures (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  reason text not null check (btrim(reason) <> ''),
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (ends_at > starts_at)
);
create index shop_closures_shop_business_fk_idx
  on public.shop_closures (shop_id, business_id);
create index shop_closures_creator_fk_idx
  on public.shop_closures (created_by_auth_user_id);
create index shop_closures_window_idx
  on public.shop_closures (shop_id, starts_at, ends_at);

create table public.staff_schedules (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  barber_membership_id uuid not null,
  iso_weekday smallint not null check (iso_weekday between 1 and 7),
  start_time time not null,
  end_time time not null,
  ends_next_day boolean not null default false,
  effective_from date not null,
  effective_until date,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (start_time <> end_time),
  check (effective_until is null or effective_until > effective_from),
  exclude using gist (
    barber_membership_id with =,
    iso_weekday with =,
    daterange(effective_from, coalesce(effective_until, 'infinity'::date), '[)') with &&
  ) where (active)
);
create index staff_schedules_shop_business_fk_idx
  on public.staff_schedules (shop_id, business_id);
create index staff_schedules_barber_tenant_fk_idx
  on public.staff_schedules (barber_membership_id, business_id, shop_id);
create trigger staff_schedules_validate_barber
before insert or update of barber_membership_id, business_id, shop_id
on public.staff_schedules
for each row execute function private.validate_barber_membership();

create table public.staff_schedule_breaks (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  schedule_id uuid not null,
  start_offset_minutes integer not null check (start_offset_minutes >= 0),
  duration_minutes integer not null check (duration_minutes between 1 and 480),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (schedule_id, business_id, shop_id)
    references public.staff_schedules(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  unique (schedule_id, start_offset_minutes)
);
create index staff_schedule_breaks_shop_business_fk_idx
  on public.staff_schedule_breaks (shop_id, business_id);
create index staff_schedule_breaks_schedule_tenant_fk_idx
  on public.staff_schedule_breaks (schedule_id, business_id, shop_id);

create table public.staff_leave (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  barber_membership_id uuid not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  reason text,
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (ends_at > starts_at),
  check (reason is null or btrim(reason) <> '')
);
create index staff_leave_shop_business_fk_idx
  on public.staff_leave (shop_id, business_id);
create index staff_leave_barber_tenant_fk_idx
  on public.staff_leave (barber_membership_id, business_id, shop_id);
create index staff_leave_window_idx
  on public.staff_leave (barber_membership_id, starts_at, ends_at);
create index staff_leave_creator_fk_idx
  on public.staff_leave (created_by_auth_user_id);
create trigger staff_leave_validate_barber
before insert or update of barber_membership_id, business_id, shop_id
on public.staff_leave
for each row execute function private.validate_barber_membership();

create table public.staff_unavailability (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  barber_membership_id uuid not null,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  reason text not null check (btrim(reason) <> ''),
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (ends_at > starts_at)
);
create index staff_unavailability_shop_business_fk_idx
  on public.staff_unavailability (shop_id, business_id);
create index staff_unavailability_barber_tenant_fk_idx
  on public.staff_unavailability (barber_membership_id, business_id, shop_id);
create index staff_unavailability_window_idx
  on public.staff_unavailability (barber_membership_id, starts_at, ends_at);
create index staff_unavailability_creator_fk_idx
  on public.staff_unavailability (created_by_auth_user_id);
create trigger staff_unavailability_validate_barber
before insert or update of barber_membership_id, business_id, shop_id
on public.staff_unavailability
for each row execute function private.validate_barber_membership();

create table public.shop_legal_profiles (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  legal_name text not null check (btrim(legal_name) <> ''),
  address text not null check (btrim(address) <> ''),
  vat_registered boolean not null default false,
  trn text,
  pricing_mode public.pricing_mode not null,
  invoice_document_type public.document_type not null,
  effective_from timestamptz not null,
  effective_until timestamptz,
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (vat_registered and trn ~ '^[0-9]{15}$' and invoice_document_type = 'tax_invoice')
    or
    (not vat_registered and trn is null and invoice_document_type = 'receipt')
  ),
  check (effective_until is null or effective_until > effective_from),
  exclude using gist (
    shop_id with =,
    tstzrange(
      effective_from,
      coalesce(effective_until, 'infinity'::timestamptz),
      '[)'
    ) with &&
  )
);
create index shop_legal_profiles_shop_business_fk_idx
  on public.shop_legal_profiles (shop_id, business_id);
create index shop_legal_profiles_creator_fk_idx
  on public.shop_legal_profiles (created_by_auth_user_id);
create trigger shop_legal_profiles_guard_update
before update on public.shop_legal_profiles
for each row execute function private.guard_effective_source_update();
create trigger shop_legal_profiles_reject_delete
before delete on public.shop_legal_profiles
for each row execute function private.reject_delete();

create table public.commission_rules (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  barber_membership_id uuid,
  rule_type public.commission_rule_type not null,
  barber_pct numeric(5,2),
  tiers jsonb,
  effective_from timestamptz not null,
  effective_until timestamptz,
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (
      rule_type = 'fixed_percentage'
      and barber_pct between 0 and 100
      and tiers is null
    )
    or
    (
      rule_type = 'tier'
      and barber_pct is null
      and private.valid_commission_tiers(tiers)
    )
  ),
  check (effective_until is null or effective_until > effective_from)
);
alter table public.commission_rules
  add constraint commission_default_period_excl
  exclude using gist (
    shop_id with =,
    tstzrange(
      effective_from,
      coalesce(effective_until, 'infinity'::timestamptz),
      '[)'
    ) with &&
  ) where (barber_membership_id is null);
alter table public.commission_rules
  add constraint commission_barber_period_excl
  exclude using gist (
    barber_membership_id with =,
    tstzrange(
      effective_from,
      coalesce(effective_until, 'infinity'::timestamptz),
      '[)'
    ) with &&
  ) where (barber_membership_id is not null);
create index commission_rules_shop_business_fk_idx
  on public.commission_rules (shop_id, business_id);
create index commission_rules_barber_tenant_fk_idx
  on public.commission_rules (barber_membership_id, business_id, shop_id)
  where barber_membership_id is not null;
create index commission_rules_creator_fk_idx
  on public.commission_rules (created_by_auth_user_id);
create trigger commission_rules_validate_barber
before insert or update of barber_membership_id, business_id, shop_id
on public.commission_rules
for each row
when (new.barber_membership_id is not null)
execute function private.validate_barber_membership();
create trigger commission_rules_guard_update
before update on public.commission_rules
for each row execute function private.guard_effective_source_update();
create trigger commission_rules_reject_delete
before delete on public.commission_rules
for each row execute function private.reject_delete();

alter table public.services enable row level security;
alter table public.services force row level security;
alter table public.customers enable row level security;
alter table public.customers force row level security;
alter table public.shop_business_hours enable row level security;
alter table public.shop_business_hours force row level security;
alter table public.shop_closures enable row level security;
alter table public.shop_closures force row level security;
alter table public.staff_schedules enable row level security;
alter table public.staff_schedules force row level security;
alter table public.staff_schedule_breaks enable row level security;
alter table public.staff_schedule_breaks force row level security;
alter table public.staff_leave enable row level security;
alter table public.staff_leave force row level security;
alter table public.staff_unavailability enable row level security;
alter table public.staff_unavailability force row level security;
alter table public.shop_legal_profiles enable row level security;
alter table public.shop_legal_profiles force row level security;
alter table public.commission_rules enable row level security;
alter table public.commission_rules force row level security;

create policy services_read_authorized
on public.services for select to authenticated
using ((select private.can_read_shop(business_id, shop_id)));

create policy customers_read_operations
on public.customers for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
    )
  )
);

create policy shop_business_hours_read_authorized
on public.shop_business_hours for select to authenticated
using ((select private.can_read_shop(business_id, shop_id)));

create policy shop_closures_read_authorized
on public.shop_closures for select to authenticated
using ((select private.can_read_shop(business_id, shop_id)));

create policy staff_schedules_read_authorized
on public.staff_schedules for select to authenticated
using ((select private.can_read_shop(business_id, shop_id)));

create policy staff_schedule_breaks_read_authorized
on public.staff_schedule_breaks for select to authenticated
using ((select private.can_read_shop(business_id, shop_id)));

create policy staff_leave_read_authorized
on public.staff_leave for select to authenticated
using (
  (select private.can_read_private_staff_calendar(
    business_id,
    shop_id,
    barber_membership_id
  ))
);

create policy staff_unavailability_read_authorized
on public.staff_unavailability for select to authenticated
using (
  (select private.can_read_private_staff_calendar(
    business_id,
    shop_id,
    barber_membership_id
  ))
);

create policy shop_legal_profiles_read_operations
on public.shop_legal_profiles for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
    )
  )
);

create policy commission_rules_read_authorized
on public.commission_rules for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager']::public.membership_role[]
    )
  )
  or (
    barber_membership_id is not null
    and (select private.is_own_barber_membership(
      business_id,
      shop_id,
      barber_membership_id
    ))
  )
);

revoke all on table
  public.services,
  public.customers,
  public.shop_business_hours,
  public.shop_closures,
  public.staff_schedules,
  public.staff_schedule_breaks,
  public.staff_leave,
  public.staff_unavailability,
  public.shop_legal_profiles,
  public.commission_rules
from anon, authenticated;

grant select on table
  public.services,
  public.customers,
  public.shop_business_hours,
  public.shop_closures,
  public.staff_schedules,
  public.staff_schedule_breaks,
  public.staff_leave,
  public.staff_unavailability,
  public.shop_legal_profiles,
  public.commission_rules
to authenticated;

grant select, insert, update, delete on table
  public.services,
  public.customers,
  public.shop_business_hours,
  public.shop_closures,
  public.staff_schedules,
  public.staff_schedule_breaks,
  public.staff_leave,
  public.staff_unavailability,
  public.shop_legal_profiles,
  public.commission_rules
to service_role;
