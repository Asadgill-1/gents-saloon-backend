alter table public.bookings
  drop constraint bookings_check4,
  drop constraint bookings_check5;

alter table public.bookings
  add constraint bookings_confirmed_history_check check (
    (
      status in ('confirmed', 'in_service', 'completed', 'no_show')
      and confirmed_at is not null
    )
    or (
      status in ('held', 'requested', 'expired')
      and confirmed_at is null
    )
    or status = 'cancelled'
  ),
  add constraint bookings_started_history_check check (
    (
      status in ('in_service', 'completed')
      and started_at is not null
    )
    or (
      status in ('held', 'requested', 'confirmed', 'no_show', 'expired')
      and started_at is null
    )
    or status = 'cancelled'
  );

create or replace function private.guard_booking_update()
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

  if old.queue_number is not null and (
    new.queue_business_date,
    new.queue_number
  ) is distinct from (
    old.queue_business_date,
    old.queue_number
  ) then
    raise exception 'allocated booking queue number is immutable';
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

revoke all on function private.guard_booking_update()
  from public, anon, authenticated, service_role;
