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
        'transactions',
        'transaction_items',
        'transaction_payments',
        'transaction_item_commissions',
        'journal_accounts',
        'journal_entries',
        'journal_postings'
      )
      and not rowsecurity
  ),
  'all checkout and journal tables must enable RLS'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename in (
        'transactions',
        'transaction_items',
        'transaction_payments',
        'transaction_item_commissions',
        'journal_accounts',
        'journal_entries',
        'journal_postings'
      )
      and cmd <> 'SELECT'
  ),
  'checkout and journal tables must expose no browser mutation policy'
);

select pg_temp.assert_true(
  not has_table_privilege('anon', 'public.transactions', 'select')
  and not has_table_privilege('authenticated', 'public.transactions', 'insert')
  and not has_table_privilege(
    'authenticated',
    'public.journal_postings',
    'update'
  ),
  'anonymous reads and browser mutations must be denied'
);

select pg_temp.assert_true(
  not has_function_privilege(
    'public',
    'private.validate_checkout_transaction()',
    'execute'
  )
  and not has_function_privilege(
    'public',
    'private.validate_journal_balance()',
    'execute'
  ),
  'PUBLIC must not execute financial validation helpers'
);

select pg_temp.assert_true(
  not exists (
    select 1
    from pg_constraint c
    join pg_class child on child.oid = c.conrelid
    where c.contype = 'f'
      and child.relnamespace = 'public'::regnamespace
      and child.relname in (
        'transactions',
        'transaction_items',
        'transaction_payments',
        'transaction_item_commissions',
        'journal_entries',
        'journal_postings'
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
  'financial foreign keys must have supporting indexes'
);

select 'phase2 checkout RLS tests passed' as result;
