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

update public.shop_legal_profiles
set effective_until = '2030-01-01 00:00:00+04'
where id = '55000000-0000-0000-0000-000000000001';

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
) values (
  '55000000-0000-0000-0000-000000000004',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  'Business A LLC - A One',
  'Dubai, UAE',
  true,
  '100000000000001',
  'vat_inclusive',
  'simplified_tax_invoice',
  '2030-01-01 00:00:00+04',
  '00000000-0000-0000-0000-000000000002'
);

select pg_temp.assert_true(
  (
    select vat_registered
      and trn = '100000000000001'
      and invoice_document_type = 'simplified_tax_invoice'
    from public.shop_legal_profiles
    where id = '55000000-0000-0000-0000-000000000004'
  ),
  'VAT legal profile must retain supplier/TRN/document fields'
);
select pg_temp.assert_true(
  (
    select not vat_registered
      and trn is null
      and invoice_document_type = 'receipt'
    from public.shop_legal_profiles
    where id = '55000000-0000-0000-0000-000000000002'
  ),
  'non-VAT legal profile must retain receipt fields without a TRN'
);

insert into public.receipt_counters (
  business_id, shop_id, fiscal_year, counter_kind, last_number
) values (
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  2035,
  'sale',
  1
);

insert into public.cash_shifts (
  id,
  business_id,
  shop_id,
  register_label,
  opening_float,
  opened_by_auth_user_id,
  opened_at
) values (
  '60000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001',
  'RLS Register',
  100.00,
  '00000000-0000-0000-0000-000000000002',
  '2035-01-01 08:00:00+04'
);

insert into public.cash_shift_movements (
  id,
  business_id,
  shop_id,
  cash_shift_id,
  movement_type,
  amount,
  reason,
  created_by_auth_user_id,
  created_at
) values
  (
    '61000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000001',
    'pay_in',
    20.00,
    'Additional float',
    '00000000-0000-0000-0000-000000000002',
    '2035-01-01 09:00:00+04'
  ),
  (
    '61000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000001',
    'pay_out',
    5.00,
    'Petty cash',
    '00000000-0000-0000-0000-000000000002',
    '2035-01-01 10:00:00+04'
  );

do $$
begin
  begin
    update public.cash_shifts
    set status = 'closed',
        expected_cash = 999,
        counted_cash = 999,
        variance = 0,
        closed_by_auth_user_id = '00000000-0000-0000-0000-000000000002',
        closed_at = '2035-01-01 18:00:00+04'
    where id = '60000000-0000-0000-0000-000000000001';
    raise exception 'expected invalid cash reconciliation to fail';
  exception
    when others then
      if sqlerrm not like '%cash shift expected amount does not reconcile%' then
        raise;
      end if;
  end;

  begin
    update public.cash_shift_movements
    set amount = 21
    where id = '61000000-0000-0000-0000-000000000001';
    raise exception 'expected movement update to fail';
  exception
    when others then
      if sqlerrm not like '%cash_shift_movements rows cannot be deleted%' then
        raise;
      end if;
  end;

  begin
    insert into public.cash_shift_movements (
      business_id,
      shop_id,
      cash_shift_id,
      movement_type,
      amount,
      reason,
      created_by_auth_user_id
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000002',
      '60000000-0000-0000-0000-000000000001',
      'pay_in',
      1,
      'Cross-shop attempt',
      '00000000-0000-0000-0000-000000000002'
    );
    raise exception 'expected cross-shop movement to fail';
  exception
    when others then
      if sqlerrm not like '%cash shift not found%' then
        raise;
      end if;
  end;
end
$$;

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.cash_shifts', 'select'),
  'anonymous role must not read cash shifts'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.cash_shifts', 'insert'),
  'authenticated browser role must not open cash shifts'
);
select pg_temp.assert_true(
  not has_table_privilege('authenticated', 'public.cash_shift_movements', 'update'),
  'authenticated browser role must not mutate cash movements'
);
select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename in (
        'receipt_counters',
        'cash_shifts',
        'cash_shift_movements'
      )
      and cmd <> 'SELECT'
  ),
  'legal/cash tables must not expose browser mutation policies'
);

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000003',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.receipt_counters),
  'shop receptionist must read assigned-shop receipt counters'
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.cash_shifts),
  'shop receptionist must read assigned-shop cash shifts'
);
select pg_temp.assert_true(
  (select count(*) = 2 from public.cash_shift_movements),
  'shop receptionist must read assigned-shop cash movements'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000005',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.cash_shifts),
  'barber must not read cash shifts'
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.cash_shift_movements),
  'barber must not read cash movements'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.cash_shifts),
  'business owner must read owned-shop cash shifts'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000004',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.cash_shifts),
  'other business owner must not read cash shifts'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000006',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.cash_shifts),
  'inactive user must fail closed on cash shifts'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.cash_shifts),
  'platform admin must read cash shifts'
);
reset role;

select 'phase2 legal/cash RLS tests passed' as result;
