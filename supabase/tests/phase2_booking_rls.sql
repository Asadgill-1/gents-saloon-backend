\set ON_ERROR_STOP on

create function pg_temp.assert_true(value boolean, message text)
returns void
language plpgsql
as $$
begin
  if value is distinct from true then
    raise exception 'assertion failed: %', message;
  end if;
end
$$;

select id as barber_a_id
from public.shop_memberships
where auth_user_id = '00000000-0000-0000-0000-000000000005'
\gset

insert into public.bookings (
  id,
  business_id,
  shop_id,
  customer_id,
  barber_membership_id,
  booking_type,
  status,
  source,
  scheduled_start,
  scheduled_end,
  confirmed_at,
  started_at,
  completed_at
) values (
  '57000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  '51000000-0000-0000-0000-000000000001',
  :'barber_a_id',
  'appointment',
  'completed',
  'dashboard',
  '2035-01-01 10:00:00+04',
  '2035-01-01 10:30:00+04',
  '2035-01-01 09:00:00+04',
  '2035-01-01 10:00:00+04',
  '2035-01-01 10:30:00+04'
);

insert into public.booking_services (
  id,
  business_id,
  shop_id,
  booking_id,
  service_id,
  service_name,
  price_gross,
  vat_rate,
  duration_minutes,
  sort_order
) values (
  '58000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  '57000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000001',
  'Haircut',
  120,
  5,
  30,
  0
);

insert into public.queue_counters (
  business_id, shop_id, business_date, last_number
) values (
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  '2035-01-01',
  7
);

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.bookings', 'select'),
  'anonymous role must not read bookings'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.bookings', 'insert'),
  'authenticated browser role must not mutate bookings'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.queue_counters', 'update'),
  'authenticated browser role must not allocate queue numbers'
);
select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.can_read_booking(uuid,uuid,uuid)',
    'execute'
  ),
  'PUBLIC must not execute booking authorization helper'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename in ('bookings', 'booking_services', 'queue_counters')
      and cmd <> 'SELECT'
  ),
  'booking tables must not expose browser mutation policies'
);

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000003',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.bookings),
  'shop receptionist must read assigned-shop bookings'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.booking_services),
  'shop receptionist must read assigned-shop booking snapshots'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.queue_counters),
  'shop receptionist must read assigned-shop queue counter'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000005',
  false
);
select pg_temp.assert_true(
  (
    select count(*) = 1
    from public.bookings
    where barber_membership_id = :'barber_a_id'
  ),
  'barber must read only own assigned booking'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.queue_counters),
  'barber must not read queue counters'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.bookings),
  'business owner must read bookings across owned shops'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000004',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.bookings),
  'other business owner must not read bookings'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000006',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.bookings),
  'inactive user must fail closed on bookings'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.bookings),
  'platform admin must read bookings'
);
reset role;

select 'phase2 booking RLS tests passed' as result;
