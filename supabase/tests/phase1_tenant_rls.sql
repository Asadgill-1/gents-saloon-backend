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

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-000000000001', 'platform@example.test'),
  ('00000000-0000-0000-0000-000000000002', 'owner-a@example.test'),
  ('00000000-0000-0000-0000-000000000003', 'staff-a@example.test'),
  ('00000000-0000-0000-0000-000000000004', 'owner-b@example.test'),
  ('00000000-0000-0000-0000-000000000005', 'barber-a@example.test'),
  ('00000000-0000-0000-0000-000000000006', 'inactive-a@example.test'),
  ('00000000-0000-0000-0000-000000000007', 'new-owner-a@example.test'),
  ('00000000-0000-0000-0000-000000000008', 'new-owner-b@example.test'),
  ('00000000-0000-0000-0000-000000000009', 'rollback-owner@example.test');

insert into public.user_profiles (auth_user_id, display_name, active) values
  ('00000000-0000-0000-0000-000000000001', 'Platform Admin', true),
  ('00000000-0000-0000-0000-000000000002', 'Owner A', true),
  ('00000000-0000-0000-0000-000000000003', 'Staff A', true),
  ('00000000-0000-0000-0000-000000000004', 'Owner B', true),
  ('00000000-0000-0000-0000-000000000005', 'Barber A', true),
  ('00000000-0000-0000-0000-000000000006', 'Inactive A', false);

insert into public.platform_admins (auth_user_id, display_name)
values ('00000000-0000-0000-0000-000000000001', 'Platform Admin');

insert into public.businesses (
  id, legal_name, display_name, billing_mode
) values
  (
    '10000000-0000-0000-0000-000000000001',
    'Business A LLC',
    'Business A',
    'business'
  ),
  (
    '10000000-0000-0000-0000-000000000002',
    'Business B LLC',
    'Business B',
    'business'
  );

insert into public.business_owners (business_id, auth_user_id) values
  (
    '10000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000002'
  ),
  (
    '10000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000004'
  );

insert into public.shops (
  id,
  business_id,
  name,
  internal_code,
  public_queue_token_hash,
  open_time,
  close_time,
  default_service_minutes,
  eod_time
) values
  (
    '20000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'A One',
    'A1',
    'hash-a1',
    '09:00',
    '23:00',
    30,
    '23:30'
  ),
  (
    '20000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    'A Two',
    'A2',
    'hash-a2',
    '09:00',
    '23:00',
    30,
    '23:30'
  ),
  (
    '20000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000002',
    'B One',
    'B1',
    'hash-b1',
    '09:00',
    '23:00',
    30,
    '23:30'
  );

insert into public.shop_memberships (
  business_id, shop_id, auth_user_id, role, display_name
) values
  (
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000003',
    'receptionist',
    'Staff A'
  ),
  (
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000005',
    'barber',
    'Barber A'
  ),
  (
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000006',
    'manager',
    'Inactive A'
  );

insert into public.subscriptions (
  id, business_id, scope, status, paid_from, paid_until
) values
  (
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'business',
    'active',
    date '2026-07-01',
    greatest(current_date + 30, date '2026-12-31')
  ),
  (
    '30000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    'business',
    'active',
    date '2026-07-01',
    greatest(current_date + 30, date '2026-12-31')
  );

do $$
begin
  begin
    insert into public.business_owners (business_id, auth_user_id)
    values (
      '10000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000004'
    );
    raise exception 'second primary owner was accepted';
  exception when unique_violation then
    null;
  end;

  begin
    insert into public.shop_memberships (
      business_id, shop_id, auth_user_id, role, display_name
    ) values (
      '10000000-0000-0000-0000-000000000002',
      '20000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000004',
      'manager',
      'Cross Tenant'
    );
    raise exception 'cross-business membership was accepted';
  exception when foreign_key_violation then
    null;
  end;

  begin
    insert into public.subscriptions (
      business_id, shop_id, scope, status, paid_from, paid_until
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      'shop',
      'active',
      current_date,
      current_date + 30
    );
    raise exception 'incompatible subscription scope was accepted';
  exception when others then
    if sqlerrm not like '%business billing requires business subscription scope%' then
      raise;
    end if;
  end;

  begin
    update public.businesses
    set billing_mode = 'per_shop'
    where id = '10000000-0000-0000-0000-000000000001';
    raise exception 'incompatible billing mode transition was accepted';
  exception when others then
    if sqlerrm not like '%billing mode conflicts with current subscriptions%' then
      raise;
    end if;
  end;

  begin
    insert into public.subscription_cash_receipts (
      subscription_id,
      business_id,
      amount,
      receipt_reference,
      collected_at,
      coverage_from,
      coverage_until,
      collected_by
    ) values (
      '30000000-0000-0000-0000-000000000002',
      '10000000-0000-0000-0000-000000000001',
      100,
      'CROSS-TENANT',
      now(),
      current_date,
      current_date + 30,
      '00000000-0000-0000-0000-000000000001'
    );
    raise exception 'cross-tenant cash receipt was accepted';
  exception when others then
    if sqlerrm not like '%receipt subject does not match subscription%' then
      raise;
    end if;
  end;
end
$$;

insert into public.subscription_cash_receipts (
  id,
  subscription_id,
  business_id,
  amount,
  receipt_reference,
  collected_at,
  coverage_from,
  coverage_until,
  collected_by
) values (
  '40000000-0000-0000-0000-000000000001',
  '30000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  100,
  'DB-RECEIPT-ORIGINAL',
  now(),
  current_date,
  current_date + 30,
  '00000000-0000-0000-0000-000000000001'
);

do $$
begin
  begin
    insert into public.subscription_cash_receipts (
      subscription_id,
      business_id,
      amount,
      receipt_reference,
      collected_at,
      coverage_from,
      coverage_until,
      collected_by,
      reversal_of_id
    ) values (
      '30000000-0000-0000-0000-000000000001',
      '10000000-0000-0000-0000-000000000001',
      99,
      'DB-RECEIPT-BAD-REVERSAL',
      now(),
      current_date,
      current_date + 30,
      '00000000-0000-0000-0000-000000000001',
      '40000000-0000-0000-0000-000000000001'
    );
    raise exception 'non-mirroring receipt reversal was accepted';
  exception when others then
    if sqlerrm not like '%receipt reversal must mirror the original receipt%' then
      raise;
    end if;
  end;

  begin
    update public.subscription_cash_receipts
    set evidence_note = 'mutated'
    where id = '40000000-0000-0000-0000-000000000001';
    raise exception 'append-only subscription receipt was updated';
  exception when others then
    if sqlerrm not like '%subscription_cash_receipts is append-only%' then
      raise;
    end if;
  end;
end
$$;

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.businesses', 'select'),
  'anonymous role must not read application tables'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.businesses', 'insert'),
  'authenticated browser role must not mutate application tables'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.bots', 'select'),
  'browser role must not read encrypted bot credentials'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.idempotency_keys', 'select'),
  'browser role must not read idempotency records'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.outbox_events', 'select'),
  'browser role must not read outbox payloads'
);
select pg_temp.assert_true(
  not has_function_privilege('public', 'private.is_platform_admin()', 'execute'),
  'PUBLIC must not execute privileged authorization helpers'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and cmd <> 'SELECT'
  ),
  'browser-facing RLS must not include direct mutation policies'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and (qual = 'true' or with_check = 'true')
  ),
  'permissive USING/WITH CHECK true policies are prohibited'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_tables
    where schemaname = 'public'
      and not rowsecurity
  ),
  'every public application table must enable RLS'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'
      and not c.relforcerowsecurity
  ),
  'every public application table must force RLS'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_constraint c
    where c.contype = 'f'
      and c.connamespace = 'public'::regnamespace
      and not exists (
        select 1
        from pg_index i
        where i.indrelid = c.conrelid
          and i.indisvalid
          and not exists (
            select 1
            from generate_subscripts(c.conkey, 1) position
            where (i.indkey::smallint[])[position - 1] <> c.conkey[position]
          )
      )
  ),
  'every foreign key must have a supporting index'
);

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000003',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.shops),
  'shop A staff must see only assigned shop A1'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.businesses),
  'shop staff must not read business legal/contact data'
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.shop_memberships),
  'receptionist must see only memberships in assigned shop'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000005',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.shops),
  'barber must see only assigned shop'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.shop_memberships),
  'barber must see only own membership'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000006',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.shops),
  'inactive application user must fail closed'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  false
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.shops),
  'business owner A must see both owned shops'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.businesses),
  'business owner A must not see business B'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.subscriptions),
  'business owner A must see only owned subscription'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000004',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.shops),
  'business owner B must see only business B shop'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  false
);
select pg_temp.assert_true(
  (select count(*) = 3 from public.shops),
  'platform admin must see all shops'
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.businesses),
  'platform admin must see all businesses'
);
reset role;

insert into public.audit_log (
  actor_type, actor_id, action, entity_type
) values (
  'system', 'phase1-test', 'test.created', 'test'
);

do $$
begin
  begin
    update public.audit_log set action = 'test.changed';
    raise exception 'append-only audit row was updated';
  exception when others then
    if sqlerrm not like '%audit_log is append-only%' then
      raise;
    end if;
  end;
end
$$;

select 'phase1 tenant RLS tests passed' as result;
