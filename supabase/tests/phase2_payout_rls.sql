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
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname in (
        'advances',
        'payout_runs',
        'payout_items',
        'advance_applications'
      )
      and (not c.relrowsecurity or not c.relforcerowsecurity)
  ),
  'all advance and payout tables must force RLS'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename in (
        'advances',
        'payout_runs',
        'payout_items',
        'advance_applications'
      )
      and cmd <> 'SELECT'
  ),
  'advance and payout tables must expose no browser mutation policy'
);

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.advances', 'select')
  and not has_table_privilege('authenticated', 'public.advances', 'insert')
  and not has_table_privilege('authenticated', 'public.payout_runs', 'update')
  and not has_table_privilege(
    'authenticated',
    'public.advance_applications',
    'delete'
  ),
  'anonymous reads and browser finance writes must be denied'
);

select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.guard_advance_update()',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.guard_payout_run_update()',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.guard_payout_item_update()',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.validate_advance_financials()',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.validate_payout_run()',
    'execute'
  ),
  'PUBLIC must not execute finance guard or validator functions'
);

select pg_temp.assert_true(
  exists (
    select 1
    from pg_constraint
    where conrelid = 'public.payout_runs'::regclass
      and conname = 'payout_runs_no_non_cancelled_overlap'
      and contype = 'x'
  )
  and exists (
    select 1
    from pg_indexes
    where schemaname = 'public'
      and tablename = 'payout_runs'
      and indexname = 'payout_runs_one_approved_per_shop_idx'
      and indexdef like '%WHERE (status = ''approved''%'
  ),
  'payout periods must not overlap and only one run may reserve advances'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_constraint c
    join pg_class child on child.oid = c.conrelid
    where c.contype = 'f'
      and child.relnamespace = 'public'::regnamespace
      and child.relname in (
        'advances',
        'payout_runs',
        'payout_items',
        'advance_applications'
      )
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
  'advance and payout foreign keys must have supporting indexes'
);

select 'phase2 payout RLS tests passed' as result;
