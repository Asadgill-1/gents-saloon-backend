alter table public.shop_business_hours
  add constraint shop_business_hours_clock_check
  check (
    (closes_next_day and close_time < open_time)
    or (not closes_next_day and close_time > open_time)
  );

alter table public.staff_schedules
  add constraint staff_schedules_clock_check
  check (
    (ends_next_day and end_time < start_time)
    or (not ends_next_day and end_time > start_time)
  );

create function private.validate_schedule_break()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  selected_start time;
  selected_end time;
  selected_ends_next_day boolean;
  shift_minutes integer;
begin
  select s.start_time, s.end_time, s.ends_next_day
  into selected_start, selected_end, selected_ends_next_day
  from public.staff_schedules s
  where s.id = new.schedule_id
    and s.business_id = new.business_id
    and s.shop_id = new.shop_id;

  if not found then
    raise exception 'schedule is invalid for shop';
  end if;

  shift_minutes := (
    extract(epoch from (selected_end - selected_start)) / 60
  )::integer;
  if selected_ends_next_day then
    shift_minutes := shift_minutes + 1440;
  end if;

  if new.start_offset_minutes + new.duration_minutes > shift_minutes then
    raise exception 'schedule break exceeds shift';
  end if;
  return new;
end
$$;

revoke all on function private.validate_schedule_break()
  from public, anon, authenticated, service_role;

create trigger staff_schedule_breaks_validate_window
before insert or update of
  schedule_id,
  business_id,
  shop_id,
  start_offset_minutes,
  duration_minutes
on public.staff_schedule_breaks
for each row execute function private.validate_schedule_break();
