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

select pg_temp.assert_true(
  exists (
    select 1
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname = 'e_invoice_documents'
      and c.relrowsecurity
      and c.relforcerowsecurity
  ),
  'e-invoice documents must force RLS'
);

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.e_invoice_documents', 'select')
  and has_table_privilege(
    'authenticated',
    'public.e_invoice_documents',
    'select'
  )
  and not has_table_privilege(
    'authenticated',
    'public.e_invoice_documents',
    'insert'
  )
  and not has_table_privilege(
    'authenticated',
    'public.e_invoice_documents',
    'update'
  )
  and not has_table_privilege(
    'authenticated',
    'public.e_invoice_documents',
    'delete'
  ),
  'browser roles must receive read-only, RLS-filtered access'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'e_invoice_documents'
      and cmd <> 'SELECT'
  ),
  'e-invoice documents must expose no browser mutation policy'
);

select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.validate_e_invoice_document()',
    'execute'
  )
  and not has_function_privilege(
    'authenticated',
    'private.create_e_invoice_document(uuid,text)',
    'execute'
  )
  and not has_function_privilege(
    'service_role',
    'private.create_e_invoice_document(uuid,text)',
    'execute'
  ),
  'e-invoice preparation and validation functions must stay private'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'e_invoice_documents'
      and column_name in (
        'transaction_id',
        'booking_id',
        'customer_id',
        'provider_payload',
        'provider_response'
      )
  )
  and not exists (
    select 1
    from pg_constraint c
    where c.conrelid = 'public.e_invoice_documents'::regclass
      and c.contype = 'f'
      and c.confrelid in (
        'public.transactions'::regclass,
        'public.bookings'::regclass,
        'public.customers'::regclass
      )
  ),
  'B2C POS and customer records must not source the B2B envelope'
);

select pg_temp.assert_true(
  enum_range(null::public.e_invoice_transaction_scope)::text
    = '{b2b,b2g}'
  and enum_range(null::public.e_invoice_document_status)::text
    = '{prepared}',
  'scope and status must not imply B2C or provider delivery'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_constraint c
    where c.contype = 'f'
      and c.conrelid = 'public.e_invoice_documents'::regclass
      and not exists (
        select 1
        from pg_index i
        where i.indrelid = c.conrelid
          and i.indisvalid
          and i.indnkeyatts >= cardinality(c.conkey)
          and not exists (
            select 1
            from generate_subscripts(c.conkey, 1) position
            where (i.indkey::smallint[])[position - 1] <> c.conkey[position]
          )
      )
  ),
  'all e-invoice foreign keys must have supporting indexes'
);

select pg_temp.assert_true(
  (
    select count(*) = 1
    from public.e_invoice_documents
    where subscription_cash_receipt_id =
      '40000000-0000-0000-0000-000000000001'
      and document_type = 'invoice'
      and transaction_scope = 'b2b'
      and status = 'prepared'
      and source_schema_version = 'platform_billing_source_v1'
      and source_snapshot -> 'buyer' ->> 'business_id' =
        '10000000-0000-0000-0000-000000000001'
  ),
  'receipt trigger must prepare one reconciled B2B source envelope'
);

do $$
begin
  begin
    update public.e_invoice_documents
    set amount = amount + 1;
    raise exception 'append-only e-invoice document was updated';
  exception when others then
    if sqlerrm not like '%append-only%' then
      raise;
    end if;
  end;

  begin
    insert into public.e_invoice_documents (
      business_id,
      subscription_cash_receipt_id,
      document_type,
      transaction_scope,
      status,
      source_schema_version,
      currency,
      amount,
      source_snapshot,
      prepared_by_auth_user_id
    )
    values (
      '10000000-0000-0000-0000-000000000001',
      '40000000-0000-0000-0000-000000000001',
      'invoice',
      'b2b',
      'prepared',
      'platform_billing_source_v1',
      'AED',
      999,
      '{}'::jsonb,
      '00000000-0000-0000-0000-000000000001'
    );
    raise exception 'unreconciled e-invoice envelope was inserted';
  exception when others then
    if sqlerrm not like '%does not reconcile%' then
      raise;
    end if;
  end;
end
$$;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.e_invoice_documents),
  'business owner must see owned platform billing documents'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000004',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.e_invoice_documents),
  'other owner must not see another tenant billing document'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000003',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.e_invoice_documents),
  'receptionist must not see platform billing documents'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000005',
  false
);
select pg_temp.assert_true(
  (select count(*) = 0 from public.e_invoice_documents),
  'barber must not see platform billing documents'
);
reset role;

set role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  false
);
select pg_temp.assert_true(
  (select count(*) = 1 from public.e_invoice_documents),
  'platform admin must see all platform billing documents'
);
reset role;

select pg_temp.assert_true(
  (
    select count(*) = 7
    from pg_indexes
    where schemaname = 'public'
      and indexname in (
        'bookings_shop_created_report_idx',
        'bookings_shop_completed_report_idx',
        'cash_movements_shop_created_report_idx',
        'cash_shifts_shop_closed_report_idx',
        'advances_shop_given_report_idx',
        'payout_runs_shop_paid_report_idx',
        'e_invoice_documents_business_prepared_idx'
      )
  ),
  'all report access paths must have their planned indexes'
);

select 'phase2 reports/e-invoice RLS tests passed' as result;
