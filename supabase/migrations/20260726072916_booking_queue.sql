do $$ begin
  create type public.booking_type as enum ('queue', 'appointment', 'walk_in');
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.booking_status as enum (
    'held',
    'requested',
    'confirmed',
    'in_service',
    'completed',
    'no_show',
    'cancelled',
    'expired'
  );
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.booking_source as enum ('telegram', 'pos', 'dashboard');
exception when duplicate_object then null;
end $$;

create function private.can_read_booking(
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
      p_barber_membership_id is not null
      and exists (
        select 1
        from public.shop_memberships sm
        where sm.id = p_barber_membership_id
          and sm.business_id = p_business_id
          and sm.shop_id = p_shop_id
          and sm.auth_user_id = (select auth.uid())
          and sm.role = 'barber'
          and sm.active
      )
    )
$$;

create function private.guard_booking_update()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if (
    new.id,
    new.business_id,
    new.shop_id,
    new.customer_id,
    new.barber_membership_id,
    new.booking_type,
    new.source,
    new.scheduled_start,
    new.scheduled_end,
    new.rescheduled_from_booking_id,
    new.created_at
  ) is distinct from (
    old.id,
    old.business_id,
    old.shop_id,
    old.customer_id,
    old.barber_membership_id,
    old.booking_type,
    old.source,
    old.scheduled_start,
    old.scheduled_end,
    old.rescheduled_from_booking_id,
    old.created_at
  ) then
    raise exception 'booking identity and snapshots are immutable';
  end if;

  if new.status <> old.status and not (
    (old.status = 'held' and new.status in ('requested', 'confirmed', 'cancelled', 'expired'))
    or (old.status = 'requested' and new.status in ('confirmed', 'cancelled'))
    or (old.status = 'confirmed' and new.status in ('in_service', 'cancelled', 'no_show'))
    or (old.status = 'in_service' and new.status = 'completed')
  ) then
    raise exception 'invalid booking status transition: % to %', old.status, new.status;
  end if;
  return new;
end
$$;

revoke all on function private.can_read_booking(uuid, uuid, uuid)
  from public, anon;
grant execute on function private.can_read_booking(uuid, uuid, uuid)
  to authenticated, service_role;
revoke all on function private.guard_booking_update()
  from public, anon, authenticated, service_role;

create table public.bookings (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  customer_id uuid,
  barber_membership_id uuid not null,
  booking_type public.booking_type not null,
  status public.booking_status not null,
  source public.booking_source not null,
  queue_business_date date,
  queue_number integer check (queue_number > 0),
  scheduled_start timestamptz,
  scheduled_end timestamptz,
  hold_expires_at timestamptz,
  estimated_start_at timestamptz,
  rescheduled_from_booking_id uuid,
  cancellation_reason text,
  no_show_reason text,
  confirmed_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  auto_confirmed boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (customer_id, business_id, shop_id)
    references public.customers(id, business_id, shop_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id) on delete restrict,
  foreign key (rescheduled_from_booking_id, business_id, shop_id)
    references public.bookings(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check ((queue_business_date is null) = (queue_number is null)),
  check (
    booking_type = 'appointment'
    or (queue_business_date is not null and queue_number is not null)
  ),
  check (
    (booking_type = 'appointment'
      and scheduled_start is not null
      and scheduled_end is not null
      and scheduled_end > scheduled_start)
    or
    (booking_type <> 'appointment'
      and scheduled_start is null
      and scheduled_end is null)
  ),
  check (
    (status = 'held'
      and booking_type = 'appointment'
      and hold_expires_at is not null
      and hold_expires_at > created_at)
    or
    (status <> 'held' and hold_expires_at is null)
  ),
  check (
    (status in ('confirmed', 'in_service', 'completed', 'no_show')
      and confirmed_at is not null)
    or
    (status not in ('confirmed', 'in_service', 'completed', 'no_show')
      and confirmed_at is null)
  ),
  check (
    (status in ('in_service', 'completed') and started_at is not null)
    or
    (status not in ('in_service', 'completed') and started_at is null)
  ),
  check (
    (status = 'completed' and completed_at is not null)
    or
    (status <> 'completed' and completed_at is null)
  ),
  check (
    (status = 'cancelled' and nullif(btrim(cancellation_reason), '') is not null)
    or
    (status <> 'cancelled' and cancellation_reason is null)
  ),
  check (
    (status = 'no_show' and nullif(btrim(no_show_reason), '') is not null)
    or
    (status <> 'no_show' and no_show_reason is null)
  )
);

alter table public.bookings
  add constraint bookings_active_appointment_no_overlap
  exclude using gist (
    shop_id with =,
    barber_membership_id with =,
    tstzrange(scheduled_start, scheduled_end, '[)') with &&
  )
  where (
    booking_type = 'appointment'
    and status in ('held', 'requested', 'confirmed', 'in_service')
  );

create unique index bookings_queue_number_unique
  on public.bookings (shop_id, queue_business_date, queue_number)
  where queue_number is not null;
create unique index bookings_reschedule_once_unique
  on public.bookings (rescheduled_from_booking_id)
  where rescheduled_from_booking_id is not null;
create index bookings_shop_business_fk_idx
  on public.bookings (shop_id, business_id);
create index bookings_customer_tenant_fk_idx
  on public.bookings (customer_id, business_id, shop_id)
  where customer_id is not null;
create index bookings_barber_tenant_fk_idx
  on public.bookings (barber_membership_id, business_id, shop_id);
create index bookings_reschedule_tenant_fk_idx
  on public.bookings (rescheduled_from_booking_id, business_id, shop_id)
  where rescheduled_from_booking_id is not null;
create index bookings_live_queue_idx
  on public.bookings (shop_id, queue_business_date, queue_number, id)
  where queue_number is not null
    and status in ('confirmed', 'in_service');
create index bookings_barber_active_work_idx
  on public.bookings (shop_id, barber_membership_id, status, created_at, id)
  where status in ('confirmed', 'in_service');
create index bookings_appointment_promotion_idx
  on public.bookings (scheduled_start, id)
  where booking_type = 'appointment'
    and status = 'confirmed'
    and queue_number is null;
create index bookings_expired_hold_idx
  on public.bookings (hold_expires_at, id)
  where status = 'held';

create trigger bookings_validate_barber
before insert or update of barber_membership_id, business_id, shop_id
on public.bookings
for each row execute function private.validate_barber_membership();
create trigger bookings_guard_update
before update on public.bookings
for each row execute function private.guard_booking_update();
create trigger bookings_reject_delete
before delete on public.bookings
for each row execute function private.reject_delete();

create table public.booking_services (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  booking_id uuid not null,
  service_id uuid not null,
  service_name text not null check (btrim(service_name) <> ''),
  price_gross numeric(14,2) not null check (price_gross >= 0),
  vat_rate numeric(5,2) not null check (vat_rate between 0 and 100),
  duration_minutes integer not null check (duration_minutes between 1 and 1440),
  sort_order integer not null check (sort_order >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (booking_id, business_id, shop_id)
    references public.bookings(id, business_id, shop_id) on delete restrict,
  foreign key (service_id, business_id, shop_id)
    references public.services(id, business_id, shop_id) on delete restrict,
  unique (booking_id, sort_order),
  unique (id, business_id, shop_id)
);
create index booking_services_shop_business_fk_idx
  on public.booking_services (shop_id, business_id);
create index booking_services_booking_tenant_fk_idx
  on public.booking_services (booking_id, business_id, shop_id);
create index booking_services_service_tenant_fk_idx
  on public.booking_services (service_id, business_id, shop_id);
create trigger booking_services_reject_change
before update or delete on public.booking_services
for each row execute function private.reject_update_delete();

create table public.queue_counters (
  business_id uuid not null,
  shop_id uuid not null,
  business_date date not null,
  last_number integer not null default 0 check (last_number >= 0),
  updated_at timestamptz not null default now(),
  primary key (shop_id, business_date),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict
);
create index queue_counters_shop_business_fk_idx
  on public.queue_counters (shop_id, business_id);

alter table public.bookings enable row level security;
alter table public.bookings force row level security;
alter table public.booking_services enable row level security;
alter table public.booking_services force row level security;
alter table public.queue_counters enable row level security;
alter table public.queue_counters force row level security;

create policy bookings_read_authorized
on public.bookings for select to authenticated
using (
  (select private.can_read_booking(
    business_id,
    shop_id,
    barber_membership_id
  ))
);

create policy booking_services_read_authorized
on public.booking_services for select to authenticated
using (
  exists (
    select 1
    from public.bookings b
    where b.id = booking_services.booking_id
      and b.business_id = booking_services.business_id
      and b.shop_id = booking_services.shop_id
      and (select private.can_read_booking(
        b.business_id,
        b.shop_id,
        b.barber_membership_id
      ))
  )
);

create policy queue_counters_read_operations
on public.queue_counters for select to authenticated
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
  public.bookings,
  public.booking_services,
  public.queue_counters
from anon, authenticated;

grant select on table
  public.bookings,
  public.booking_services,
  public.queue_counters
to authenticated;

grant select, insert, update, delete on table
  public.bookings,
  public.booking_services,
  public.queue_counters
to service_role;
