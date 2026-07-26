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

select id as receptionist_a_id
from public.shop_memberships
where auth_user_id = '00000000-0000-0000-0000-000000000003'
\gset

select id as barber_a_id
from public.shop_memberships
where auth_user_id = '00000000-0000-0000-0000-000000000005'
\gset

insert into public.services (
  id, business_id, shop_id, name, price_gross, vat_rate, duration_minutes
) values
  (
    '50000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'Haircut',
    120,
    5,
    30
  ),
  (
    '50000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000002',
    'Beard Trim',
    60,
    5,
    20
  ),
  (
    '50000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000003',
    'Business B Haircut',
    100,
    0,
    30
  );

insert into public.customers (
  id, business_id, shop_id, telegram_user_id, display_name, phone_e164
) values
  (
    '51000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    7000000001,
    'Customer A1',
    '+971501234567'
  ),
  (
    '51000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000002',
    7000000001,
    'Customer A2',
    null
  ),
  (
    '51000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000003',
    7000000001,
    'Customer B1',
    null
  );

insert into public.shop_business_hours (
  id,
  business_id,
  shop_id,
  iso_weekday,
  open_time,
  close_time,
  effective_from
) values (
  '52000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  1,
  '09:00',
  '23:00',
  current_date
);

insert into public.shop_closures (
  id,
  business_id,
  shop_id,
  starts_at,
  ends_at,
  reason,
  created_by_auth_user_id
) values (
  '53000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  '2030-01-01 00:00:00+04',
  '2030-01-02 00:00:00+04',
  'Public holiday',
  '00000000-0000-0000-0000-000000000002'
);

insert into public.staff_schedules (
  id,
  business_id,
  shop_id,
  barber_membership_id,
  iso_weekday,
  start_time,
  end_time,
  effective_from
) values (
  '54000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  :'barber_a_id',
  1,
  '09:00',
  '18:00',
  current_date
);

insert into public.staff_schedule_breaks (
  business_id,
  shop_id,
  schedule_id,
  start_offset_minutes,
  duration_minutes
) values (
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  '54000000-0000-0000-0000-000000000001',
  240,
  30
);

insert into public.staff_leave (
  business_id,
  shop_id,
  barber_membership_id,
  starts_at,
  ends_at,
  reason,
  created_by_auth_user_id
) values (
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  :'barber_a_id',
  '2030-02-01 09:00:00+04',
  '2030-02-01 18:00:00+04',
  'Approved leave',
  '00000000-0000-0000-0000-000000000002'
);

insert into public.staff_unavailability (
  business_id,
  shop_id,
  barber_membership_id,
  starts_at,
  ends_at,
  reason,
  created_by_auth_user_id
) values (
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  :'barber_a_id',
  '2030-02-02 12:00:00+04',
  '2030-02-02 13:00:00+04',
  'Training',
  '00000000-0000-0000-0000-000000000002'
);

insert into public.shop_legal_profiles (
  id,
  business_id,
  shop_id,
  legal_name,
  address,
  vat_registered,
  trn,
  pricing_mode,
  invoice_document_type,
  effective_from,
  created_by_auth_user_id
) values
  (
    '55000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'Business A LLC - A One',
    'Dubai, UAE',
    true,
    '100000000000001',
    'vat_inclusive',
    'tax_invoice',
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000002'
  ),
  (
    '55000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000002',
    'Business A LLC - A Two',
    'Dubai, UAE',
    false,
    null,
    'vat_inclusive',
    'receipt',
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000002'
  ),
  (
    '55000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000002',
    '20000000-0000-0000-0000-000000000003',
    'Business B LLC - B One',
    'Dubai, UAE',
    false,
    null,
    'vat_exclusive',
    'receipt',
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000004'
  );

insert into public.commission_rules (
  id,
  business_id,
  shop_id,
  barber_membership_id,
  rule_type,
  barber_pct,
  tiers,
  effective_from,
  created_by_auth_user_id
) values
  (
    '56000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    null,
    'tier',
    null,
    '[
      {"min_base": 0, "max_base": 120, "barber_pct": 20},
      {"min_base": 120, "barber_flat": 25}
    ]'::jsonb,
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000002'
  ),
  (
    '56000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    :'barber_a_id',
    'fixed_percentage',
    30,
    null,
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000002'
  ),
  (
    '56000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000002',
    null,
    'fixed_percentage',
    25,
    null,
    '2026-01-01 00:00:00+04',
    '00000000-0000-0000-0000-000000000002'
  );

select pg_temp.assert_true(
  private.valid_commission_tiers(
    '[{"min_base": 120, "barber_flat": 25}]'::jsonb
  ),
  'AED 120 flat-tier shape must be accepted'
);

do $$
begin
  begin
    insert into public.services (
      business_id, shop_id, name, price_gross, duration_minutes
    ) values (
      '10000000-0000-0000-0000-000000000002',
      '20000000-0000-0000-0000-000000000001',
      'Cross Tenant',
      10,
      10
    );
    raise exception 'cross-tenant service was accepted';
  exception when foreign_key_violation then
    null;
  end;

  begin
    insert into public.customers (
      business_id, shop_id, display_name, phone_e164
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      'Bad Phone',
      '0501234567'
    );
    raise exception 'invalid E.164 customer phone was accepted';
  exception when check_violation then
    null;
  end;

  begin
    insert into public.staff_schedules (
      business_id,
      shop_id,
      barber_membership_id,
      iso_weekday,
      start_time,
      end_time,
      effective_from
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      (
        select id
        from public.shop_memberships
        where auth_user_id = '00000000-0000-0000-0000-000000000003'
      ),
      2,
      '09:00',
      '18:00',
      current_date
    );
    raise exception 'non-barber schedule was accepted';
  exception when others then
    if sqlerrm not like '%barber membership is invalid for shop%' then
      raise;
    end if;
  end;

  begin
    insert into public.staff_schedule_breaks (
      business_id,
      shop_id,
      schedule_id,
      start_offset_minutes,
      duration_minutes
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      '54000000-0000-0000-0000-000000000001',
      520,
      30
    );
    raise exception 'break outside barber shift was accepted';
  exception when others then
    if sqlerrm not like '%schedule break exceeds shift%' then
      raise;
    end if;
  end;

  begin
    insert into public.staff_schedules (
      business_id,
      shop_id,
      barber_membership_id,
      iso_weekday,
      start_time,
      end_time,
      ends_next_day,
      effective_from
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      (
        select id
        from public.shop_memberships
        where auth_user_id = '00000000-0000-0000-0000-000000000005'
      ),
      3,
      '22:00',
      '02:00',
      false,
      current_date
    );
    raise exception 'overnight shift without next-day flag was accepted';
  exception when check_violation then
    null;
  end;

  begin
    insert into public.shop_business_hours (
      business_id,
      shop_id,
      iso_weekday,
      open_time,
      close_time,
      effective_from
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      1,
      '10:00',
      '20:00',
      current_date + 1
    );
    raise exception 'overlapping business hours were accepted';
  exception when exclusion_violation then
    null;
  end;

  begin
    insert into public.shop_legal_profiles (
      business_id,
      shop_id,
      legal_name,
      address,
      vat_registered,
      trn,
      pricing_mode,
      invoice_document_type,
      effective_from,
      created_by_auth_user_id
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      'Overlapping Legal Profile',
      'Dubai, UAE',
      true,
      '100000000000002',
      'vat_inclusive',
      'tax_invoice',
      '2027-01-01 00:00:00+04',
      '00000000-0000-0000-0000-000000000002'
    );
    raise exception 'overlapping legal profile was accepted';
  exception when exclusion_violation then
    null;
  end;

  begin
    insert into public.shop_legal_profiles (
      business_id,
      shop_id,
      legal_name,
      address,
      vat_registered,
      trn,
      pricing_mode,
      invoice_document_type,
      effective_from,
      created_by_auth_user_id
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      'Bad Tax Profile',
      'Dubai, UAE',
      true,
      '123',
      'vat_inclusive',
      'tax_invoice',
      '2040-01-01 00:00:00+04',
      '00000000-0000-0000-0000-000000000002'
    );
    raise exception 'invalid TRN was accepted';
  exception when check_violation then
    null;
  end;

  begin
    insert into public.commission_rules (
      business_id,
      shop_id,
      rule_type,
      tiers,
      effective_from,
      created_by_auth_user_id
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000002',
      'tier',
      '[
        {"min_base": 0, "max_base": 100, "barber_pct": 20},
        {"min_base": 50, "barber_flat": 25}
      ]'::jsonb,
      '2040-01-01 00:00:00+04',
      '00000000-0000-0000-0000-000000000002'
    );
    raise exception 'overlapping commission tiers were accepted';
  exception when check_violation then
    null;
  end;

  begin
    update public.commission_rules
    set barber_pct = 40
    where id = '56000000-0000-0000-0000-000000000002';
    raise exception 'commission rule values were updated';
  exception when others then
    if sqlerrm not like '%commission_rules values are immutable%' then
      raise;
    end if;
  end;

  begin
    delete from public.shop_legal_profiles
    where id = '55000000-0000-0000-0000-000000000001';
    raise exception 'legal profile was deleted';
  exception when others then
    if sqlerrm not like '%shop_legal_profiles rows cannot be deleted%' then
      raise;
    end if;
  end;
end
$$;

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.services', 'select'),
  'anonymous role must not read Phase 2 tables'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.services', 'insert'),
  'authenticated browser role must not mutate services'
);
select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.valid_commission_tiers(jsonb)',
    'execute'
  ),
  'PUBLIC must not execute commission validation helpers'
);
select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.can_read_private_staff_calendar(uuid,uuid,uuid)',
    'execute'
  ),
  'PUBLIC must not execute private calendar authorization helpers'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename in (
        'services',
        'customers',
        'shop_business_hours',
        'shop_closures',
        'staff_schedules',
        'staff_schedule_breaks',
        'staff_leave',
        'staff_unavailability',
        'shop_legal_profiles',
        'commission_rules'
      )
      and cmd <> 'SELECT'
  ),
  'Phase 2 browser-facing tables must not expose mutation policies'
);

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000003',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.services),
  'shop A receptionist must see only shop A1 services'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.customers),
  'shop A receptionist must see only shop A1 customers'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.shop_legal_profiles),
  'shop A receptionist must see only shop A1 legal profile'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.commission_rules),
  'receptionist must not read commission rules'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.staff_leave),
  'receptionist may read assigned-shop leave for scheduling'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000005',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.services),
  'barber must see only assigned-shop services'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.customers),
  'barber must not browse customer profiles'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.shop_legal_profiles),
  'barber must not browse shop legal profiles'
);
select pg_temp.assert_true(
  (
    select count(*) = 1
    from public.commission_rules
    where barber_membership_id = :'barber_a_id'
  ),
  'barber must see only the explicit own commission rule'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.staff_leave),
  'barber must see own leave'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  false
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.services),
  'business owner A must see services in both owned shops'
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.customers),
  'business owner A must see customers in both owned shops'
);
select pg_temp.assert_true(
  (select count(*) = 3 from public.commission_rules),
  'business owner A must see all owned commission rules'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000004',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.services),
  'business owner B must see only business B services'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.customers),
  'business owner B must see only business B customers'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000006',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.services),
  'inactive user must fail closed on Phase 2 tables'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  false
);
select pg_temp.assert_true(
  (select count(*) = 3 from public.services),
  'platform admin must see all services'
);
select pg_temp.assert_true(
  (select count(*) = 3 from public.customers),
  'platform admin must see all customers'
);
select pg_temp.assert_true(
  (select count(*) = 3 from public.shop_legal_profiles),
  'platform admin must see all legal profiles'
);
reset role;

select 'phase2 operations schema tests passed' as result;
