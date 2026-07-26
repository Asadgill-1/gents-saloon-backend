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
  not exists (
    select 1
    from pg_tables
    where schemaname = 'public'
      and tablename in (
        'transaction_corrections',
        'transaction_correction_items',
        'transaction_correction_item_commissions',
        'transaction_correction_payments'
      )
      and not rowsecurity
  ),
  'all correction tables must enable RLS'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename like 'transaction_correction%'
      and cmd <> 'SELECT'
  ),
  'correction tables must expose no browser mutation policy'
);

select pg_temp.assert_true(
  not has_table_privilege(
    'anon',
    'public.transaction_corrections',
    'select'
  )
  and not has_table_privilege(
    'authenticated',
    'public.transaction_corrections',
    'insert'
  )
  and not has_table_privilege(
    'authenticated',
    'public.transaction_correction_payments',
    'delete'
  ),
  'anonymous reads and browser correction writes must be denied'
);

select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.validate_transaction_correction(uuid)',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.validate_correction_trigger()',
    'execute'
  ),
  'PUBLIC must not execute correction validators'
);

select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.validate_correction_void_tender(uuid)',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.validate_correction_void_tender_trigger()',
    'execute'
  ),
  'PUBLIC must not execute void-tender validators'
);

select pg_temp.assert_true(
  pg_get_constraintdef(
    (
      select oid
      from pg_constraint
      where conrelid = 'public.transaction_payments'::regclass
        and conname = 'transaction_payments_card_reference_check'
    )
  ) like '%([0-9][._:/-]?){12}[0-9]%',
  'database must reject separator-formatted PAN-like card references'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_constraint c
    join pg_class child on child.oid = c.conrelid
    where c.contype = 'f'
      and child.relnamespace = 'public'::regnamespace
      and child.relname like 'transaction_correction%'
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
  'correction foreign keys must have supporting indexes'
);

select 'phase2 correction RLS tests passed' as result;
