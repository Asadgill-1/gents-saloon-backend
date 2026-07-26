alter table public.shop_legal_profiles
  drop constraint shop_legal_profiles_check;
alter table public.shop_legal_profiles
  add constraint shop_legal_profiles_document_check
  check (
    (
      vat_registered
      and trn ~ '^[0-9]{15}$'
      and invoice_document_type in ('tax_invoice', 'simplified_tax_invoice')
    )
    or
    (
      not vat_registered
      and trn is null
      and invoice_document_type = 'receipt'
    )
  );

do $$
begin
  create type public.document_counter_kind as enum ('sale', 'credit_note');
exception
  when duplicate_object then null;
end
$$;

do $$
begin
  create type public.cash_shift_status as enum ('open', 'closed');
exception
  when duplicate_object then null;
end
$$;

do $$
begin
  create type public.cash_movement_type as enum (
    'cash_sale',
    'pay_in',
    'pay_out',
    'advance',
    'payout',
    'refund'
  );
exception
  when duplicate_object then null;
end
$$;

create table public.receipt_counters (
  business_id uuid not null,
  shop_id uuid not null,
  fiscal_year integer not null check (fiscal_year between 2000 and 9999),
  counter_kind public.document_counter_kind not null,
  last_number bigint not null check (last_number >= 0),
  updated_at timestamptz not null default now(),
  primary key (shop_id, fiscal_year, counter_kind),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict
);
create index receipt_counters_shop_business_fk_idx
  on public.receipt_counters (shop_id, business_id);

create table public.cash_shifts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  register_label text not null
    check (
      register_label = btrim(register_label)
      and char_length(register_label) between 1 and 64
    ),
  status public.cash_shift_status not null default 'open',
  opening_float numeric(14,2) not null check (opening_float >= 0),
  opened_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  opened_at timestamptz not null default now(),
  expected_cash numeric(14,2),
  counted_cash numeric(14,2),
  variance numeric(14,2),
  closed_by_auth_user_id uuid
    references public.user_profiles(auth_user_id) on delete restrict,
  closed_at timestamptz,
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (
      status = 'open'
      and expected_cash is null
      and counted_cash is null
      and variance is null
      and closed_by_auth_user_id is null
      and closed_at is null
    )
    or
    (
      status = 'closed'
      and expected_cash is not null
      and counted_cash >= 0
      and variance = counted_cash - expected_cash
      and closed_by_auth_user_id is not null
      and closed_at >= opened_at
    )
  )
);
create unique index cash_shifts_one_open_register_idx
  on public.cash_shifts (shop_id, lower(register_label))
  where status = 'open';
create index cash_shifts_shop_business_fk_idx
  on public.cash_shifts (shop_id, business_id);
create index cash_shifts_opened_by_fk_idx
  on public.cash_shifts (opened_by_auth_user_id);
create index cash_shifts_closed_by_fk_idx
  on public.cash_shifts (closed_by_auth_user_id)
  where closed_by_auth_user_id is not null;
create index cash_shifts_shop_opened_idx
  on public.cash_shifts (shop_id, opened_at desc, id);

create table public.cash_shift_movements (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  cash_shift_id uuid not null,
  movement_type public.cash_movement_type not null,
  amount numeric(14,2) not null check (amount > 0),
  reason text,
  source_entity_id uuid,
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (cash_shift_id, business_id, shop_id)
    references public.cash_shifts(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (
      movement_type in ('pay_in', 'pay_out')
      and reason is not null
      and btrim(reason) <> ''
      and source_entity_id is null
    )
    or
    (
      movement_type in ('cash_sale', 'advance', 'payout', 'refund')
      and source_entity_id is not null
      and (reason is null or btrim(reason) <> '')
    )
  )
);
create unique index cash_shift_movements_source_unique
  on public.cash_shift_movements (shop_id, movement_type, source_entity_id)
  where source_entity_id is not null;
create index cash_shift_movements_shop_business_fk_idx
  on public.cash_shift_movements (shop_id, business_id);
create index cash_shift_movements_shift_tenant_fk_idx
  on public.cash_shift_movements (cash_shift_id, business_id, shop_id);
create index cash_shift_movements_creator_fk_idx
  on public.cash_shift_movements (created_by_auth_user_id);
create index cash_shift_movements_shift_created_idx
  on public.cash_shift_movements (cash_shift_id, created_at, id);

create function private.guard_receipt_counter_update()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.business_id is distinct from old.business_id
    or new.shop_id is distinct from old.shop_id
    or new.fiscal_year is distinct from old.fiscal_year
    or new.counter_kind is distinct from old.counter_kind
    or new.last_number <> old.last_number + 1
  then
    raise exception 'receipt counters may only advance by one';
  end if;
  return new;
end
$$;

create function private.validate_cash_shift_movement()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  shift_status public.cash_shift_status;
begin
  select cs.status
  into shift_status
  from public.cash_shifts cs
  where cs.id = new.cash_shift_id
    and cs.business_id = new.business_id
    and cs.shop_id = new.shop_id
  for update;

  if shift_status is null then
    raise exception 'cash shift not found';
  end if;
  if shift_status <> 'open' then
    raise exception 'cash shift is closed';
  end if;
  return new;
end
$$;

create function private.guard_cash_shift_update()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  calculated_expected numeric(14,2);
begin
  if old.status <> 'open'
    or new.status <> 'closed'
    or (to_jsonb(new) - array[
      'status',
      'expected_cash',
      'counted_cash',
      'variance',
      'closed_by_auth_user_id',
      'closed_at'
    ]) is distinct from
      (to_jsonb(old) - array[
        'status',
        'expected_cash',
        'counted_cash',
        'variance',
        'closed_by_auth_user_id',
        'closed_at'
      ])
  then
    raise exception 'cash shift rows are immutable except for one close transition';
  end if;

  select old.opening_float + coalesce(
    sum(
      case
        when csm.movement_type in ('cash_sale', 'pay_in') then csm.amount
        else -csm.amount
      end
    ),
    0
  )
  into calculated_expected
  from public.cash_shift_movements csm
  where csm.cash_shift_id = old.id;

  if new.expected_cash is distinct from calculated_expected then
    raise exception 'cash shift expected amount does not reconcile';
  end if;
  if new.variance is distinct from new.counted_cash - calculated_expected then
    raise exception 'cash shift variance does not reconcile';
  end if;
  return new;
end
$$;

revoke all on function private.guard_receipt_counter_update()
  from public, anon, authenticated, service_role;
revoke all on function private.validate_cash_shift_movement()
  from public, anon, authenticated, service_role;
revoke all on function private.guard_cash_shift_update()
  from public, anon, authenticated, service_role;

create trigger receipt_counters_guard_update
before update on public.receipt_counters
for each row execute function private.guard_receipt_counter_update();
create trigger receipt_counters_reject_delete
before delete on public.receipt_counters
for each row execute function private.reject_delete();

create trigger cash_shifts_guard_update
before update on public.cash_shifts
for each row execute function private.guard_cash_shift_update();
create trigger cash_shifts_reject_delete
before delete on public.cash_shifts
for each row execute function private.reject_delete();

create trigger cash_shift_movements_validate_insert
before insert on public.cash_shift_movements
for each row execute function private.validate_cash_shift_movement();
create trigger cash_shift_movements_reject_update
before update on public.cash_shift_movements
for each row execute function private.reject_delete();
create trigger cash_shift_movements_reject_delete
before delete on public.cash_shift_movements
for each row execute function private.reject_delete();

alter table public.receipt_counters enable row level security;
alter table public.receipt_counters force row level security;
alter table public.cash_shifts enable row level security;
alter table public.cash_shifts force row level security;
alter table public.cash_shift_movements enable row level security;
alter table public.cash_shift_movements force row level security;

create policy receipt_counters_read_operations
on public.receipt_counters for select to authenticated
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

create policy cash_shifts_read_operations
on public.cash_shifts for select to authenticated
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

create policy cash_shift_movements_read_operations
on public.cash_shift_movements for select to authenticated
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

revoke all on table
  public.receipt_counters,
  public.cash_shifts,
  public.cash_shift_movements
from anon, authenticated;

grant select on table
  public.receipt_counters,
  public.cash_shifts,
  public.cash_shift_movements
to authenticated;

grant select, insert, update, delete on table
  public.receipt_counters,
  public.cash_shifts,
  public.cash_shift_movements
to service_role;
