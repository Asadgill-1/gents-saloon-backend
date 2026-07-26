do $$ begin
  create type public.advance_status as enum ('open', 'settled');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.payout_status as enum (
    'draft',
    'approved',
    'paid',
    'cancelled'
  );
exception when duplicate_object then null;
end $$;

insert into public.journal_accounts (code, name, normal_side)
values ('payout_adjustments', 'Barber payout adjustments', 'debit');

create table public.advances (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  barber_membership_id uuid not null,
  cash_shift_id uuid not null,
  original_amount numeric(14,2) not null check (original_amount > 0),
  outstanding_amount numeric(14,2) not null
    check (outstanding_amount between 0 and original_amount),
  status public.advance_status not null default 'open',
  note text
    check (
      note is null
      or (
        note = btrim(note)
        and char_length(note) between 1 and 500
      )
    ),
  given_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  given_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  foreign key (cash_shift_id, business_id, shop_id)
    references public.cash_shifts(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (status = 'open' and outstanding_amount > 0)
    or (status = 'settled' and outstanding_amount = 0)
  )
);
create index advances_shop_business_fk_idx
  on public.advances (shop_id, business_id);
create index advances_barber_tenant_fk_idx
  on public.advances (barber_membership_id, business_id, shop_id);
create index advances_cash_shift_tenant_fk_idx
  on public.advances (cash_shift_id, business_id, shop_id);
create index advances_given_by_fk_idx
  on public.advances (given_by_auth_user_id);
create index advances_barber_open_idx
  on public.advances (shop_id, barber_membership_id, given_at, id)
  where status = 'open';

create table public.payout_runs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  period_start timestamptz not null,
  period_end timestamptz not null,
  status public.payout_status not null default 'draft',
  prepared_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  prepared_at timestamptz not null default now(),
  approved_by_auth_user_id uuid
    references public.user_profiles(auth_user_id) on delete restrict,
  approved_at timestamptz,
  paid_by_auth_user_id uuid
    references public.user_profiles(auth_user_id) on delete restrict,
  paid_at timestamptz,
  cancelled_by_auth_user_id uuid
    references public.user_profiles(auth_user_id) on delete restrict,
  cancelled_at timestamptz,
  cash_shift_id uuid,
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (cash_shift_id, business_id, shop_id)
    references public.cash_shifts(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  check (period_start < period_end and period_end <= prepared_at),
  check (
    (
      status = 'draft'
      and approved_by_auth_user_id is null
      and approved_at is null
      and paid_by_auth_user_id is null
      and paid_at is null
      and cancelled_by_auth_user_id is null
      and cancelled_at is null
      and cash_shift_id is null
    )
    or
    (
      status = 'approved'
      and approved_by_auth_user_id is not null
      and approved_at >= prepared_at
      and paid_by_auth_user_id is null
      and paid_at is null
      and cancelled_by_auth_user_id is null
      and cancelled_at is null
      and cash_shift_id is null
    )
    or
    (
      status = 'paid'
      and approved_by_auth_user_id is not null
      and approved_at >= prepared_at
      and paid_by_auth_user_id is not null
      and paid_at >= approved_at
      and cancelled_by_auth_user_id is null
      and cancelled_at is null
    )
    or
    (
      status = 'cancelled'
      and paid_by_auth_user_id is null
      and paid_at is null
      and cancelled_by_auth_user_id is not null
      and cancelled_at >= prepared_at
      and cash_shift_id is null
    )
  )
);
alter table public.payout_runs
  add constraint payout_runs_no_non_cancelled_overlap
  exclude using gist (
    shop_id with =,
    tstzrange(period_start, period_end, '[)') with &&
  ) where (status <> 'cancelled');
create unique index payout_runs_one_approved_per_shop_idx
  on public.payout_runs (shop_id)
  where status = 'approved';
create index payout_runs_shop_business_fk_idx
  on public.payout_runs (shop_id, business_id);
create index payout_runs_prepared_by_fk_idx
  on public.payout_runs (prepared_by_auth_user_id);
create index payout_runs_approved_by_fk_idx
  on public.payout_runs (approved_by_auth_user_id)
  where approved_by_auth_user_id is not null;
create index payout_runs_paid_by_fk_idx
  on public.payout_runs (paid_by_auth_user_id)
  where paid_by_auth_user_id is not null;
create index payout_runs_cancelled_by_fk_idx
  on public.payout_runs (cancelled_by_auth_user_id)
  where cancelled_by_auth_user_id is not null;
create index payout_runs_cash_shift_tenant_fk_idx
  on public.payout_runs (cash_shift_id, business_id, shop_id)
  where cash_shift_id is not null;
create index payout_runs_shop_period_idx
  on public.payout_runs (shop_id, period_start, period_end, id);

create table public.payout_items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  payout_run_id uuid not null,
  barber_membership_id uuid not null,
  commission_earnings numeric(14,2) not null
    check (commission_earnings >= 0),
  tip_earnings numeric(14,2) not null check (tip_earnings >= 0),
  commission_reversals numeric(14,2) not null
    check (commission_reversals >= 0),
  tip_reversals numeric(14,2) not null check (tip_reversals >= 0),
  adjustments numeric(14,2) not null default 0,
  adjustment_reason text,
  gross_payable numeric(14,2) not null check (gross_payable >= 0),
  advance_deduction numeric(14,2) not null default 0
    check (advance_deduction >= 0),
  net_paid numeric(14,2) not null check (net_paid >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (payout_run_id, business_id, shop_id)
    references public.payout_runs(id, business_id, shop_id)
    on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (payout_run_id, barber_membership_id),
  check (
    (
      adjustments = 0
      and adjustment_reason is null
    )
    or
    (
      adjustments <> 0
      and adjustment_reason = btrim(adjustment_reason)
      and char_length(adjustment_reason) between 1 and 500
    )
  ),
  check (
    gross_payable =
      commission_earnings
      + tip_earnings
      - commission_reversals
      - tip_reversals
      + adjustments
  ),
  check (advance_deduction <= gross_payable),
  check (net_paid = gross_payable - advance_deduction)
);
create index payout_items_shop_business_fk_idx
  on public.payout_items (shop_id, business_id);
create index payout_items_run_tenant_fk_idx
  on public.payout_items (payout_run_id, business_id, shop_id);
create index payout_items_barber_tenant_fk_idx
  on public.payout_items (barber_membership_id, business_id, shop_id);

create table public.advance_applications (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  advance_id uuid not null,
  payout_item_id uuid not null,
  amount numeric(14,2) not null check (amount > 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (advance_id, business_id, shop_id)
    references public.advances(id, business_id, shop_id)
    on delete restrict,
  foreign key (payout_item_id, business_id, shop_id)
    references public.payout_items(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (advance_id, payout_item_id)
);
create index advance_applications_shop_business_fk_idx
  on public.advance_applications (shop_id, business_id);
create index advance_applications_advance_tenant_fk_idx
  on public.advance_applications (advance_id, business_id, shop_id);
create index advance_applications_item_tenant_fk_idx
  on public.advance_applications (payout_item_id, business_id, shop_id);

create function private.guard_advance_update()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if (to_jsonb(new) - array['outstanding_amount', 'status'])
      is distinct from
      (to_jsonb(old) - array['outstanding_amount', 'status'])
    or new.outstanding_amount >= old.outstanding_amount
  then
    raise exception 'advance rows only allow application settlement';
  end if;
  return new;
end
$$;

create function private.guard_payout_run_update()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if old.status = 'draft'
    and new.status = 'approved'
    and (to_jsonb(new) - array[
      'status',
      'approved_by_auth_user_id',
      'approved_at'
    ]) is not distinct from
      (to_jsonb(old) - array[
        'status',
        'approved_by_auth_user_id',
        'approved_at'
      ])
  then
    return new;
  end if;
  if old.status in ('draft', 'approved')
    and new.status = 'cancelled'
    and (to_jsonb(new) - array[
      'status',
      'cancelled_by_auth_user_id',
      'cancelled_at'
    ]) is not distinct from
      (to_jsonb(old) - array[
        'status',
        'cancelled_by_auth_user_id',
        'cancelled_at'
      ])
  then
    return new;
  end if;
  if old.status = 'approved'
    and new.status = 'paid'
    and (to_jsonb(new) - array[
      'status',
      'paid_by_auth_user_id',
      'paid_at',
      'cash_shift_id'
    ]) is not distinct from
      (to_jsonb(old) - array[
        'status',
        'paid_by_auth_user_id',
        'paid_at',
        'cash_shift_id'
      ])
  then
    return new;
  end if;
  raise exception 'invalid payout run transition';
end
$$;

create function private.guard_payout_item_update()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  run_status public.payout_status;
begin
  select status into run_status
  from public.payout_runs
  where id = old.payout_run_id;

  if run_status = 'draft'
    and old.advance_deduction = 0
    and (to_jsonb(new) - array['advance_deduction', 'net_paid'])
      is not distinct from
      (to_jsonb(old) - array['advance_deduction', 'net_paid'])
  then
    return new;
  end if;
  raise exception 'payout items are immutable after preparation';
end
$$;

create function private.validate_advance_financials()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  target_advance_id uuid;
  advance_row public.advances%rowtype;
  application_total numeric;
  cash_count bigint;
  cash_total numeric;
  journal_count bigint;
  journal_id uuid;
  posting_mismatch boolean;
begin
  if tg_table_name = 'advances' then
    target_advance_id := (to_jsonb(new) ->> 'id')::uuid;
  else
    target_advance_id := (to_jsonb(new) ->> 'advance_id')::uuid;
  end if;

  select * into advance_row
  from public.advances
  where id = target_advance_id;
  if not found then
    raise exception 'advance not found';
  end if;

  select coalesce(sum(amount), 0)
  into application_total
  from public.advance_applications
  where advance_id = target_advance_id;

  if advance_row.original_amount - application_total
      <> advance_row.outstanding_amount
  then
    raise exception 'advance outstanding amount does not reconcile';
  end if;

  if exists (
    select 1
    from public.advance_applications aa
    join public.payout_items pi on pi.id = aa.payout_item_id
    join public.payout_runs pr on pr.id = pi.payout_run_id
    where aa.advance_id = target_advance_id
      and (
        pi.barber_membership_id <> advance_row.barber_membership_id
        or pr.status <> 'paid'
      )
  ) then
    raise exception 'advance application target does not reconcile';
  end if;

  select count(*), coalesce(sum(amount), 0)
  into cash_count, cash_total
  from public.cash_shift_movements
  where movement_type = 'advance'
    and source_entity_id = target_advance_id
    and cash_shift_id = advance_row.cash_shift_id;

  if cash_count <> 1 or cash_total <> advance_row.original_amount then
    raise exception 'advance cash movement does not reconcile';
  end if;

  select count(*), min(id::text)::uuid
  into journal_count, journal_id
  from public.journal_entries
  where source_type = 'advance'
    and source_entity_id = target_advance_id
    and business_id = advance_row.business_id
    and shop_id = advance_row.shop_id;

  if journal_count <> 1 then
    raise exception 'advance journal does not reconcile';
  end if;

  with expected(account_code, barber_membership_id, debit, credit) as (
    values
      (
        'advance_receivable'::text,
        advance_row.barber_membership_id,
        advance_row.original_amount,
        0::numeric
      ),
      (
        'cash'::text,
        null::uuid,
        0::numeric,
        advance_row.original_amount
      )
  ),
  actual as (
    select
      account_code,
      barber_membership_id,
      sum(debit) as debit,
      sum(credit) as credit
    from public.journal_postings
    where journal_entry_id = journal_id
    group by account_code, barber_membership_id
  )
  select exists (
    select 1
    from expected e
    full join actual a
      on a.account_code = e.account_code
      and a.barber_membership_id is not distinct from e.barber_membership_id
    where e.account_code is null
      or a.account_code is null
      or e.debit <> a.debit
      or e.credit <> a.credit
  )
  into posting_mismatch;

  if posting_mismatch then
    raise exception 'advance journal postings do not reconcile';
  end if;
  return new;
end
$$;

create function private.validate_payout_run()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  target_run_id uuid;
  run_row public.payout_runs%rowtype;
  item_count bigint;
  gross_total numeric;
  source_mismatch boolean;
  application_mismatch boolean;
  total_net numeric;
  cash_count bigint;
  cash_total numeric;
  journal_count bigint;
  journal_id uuid;
  posting_mismatch boolean;
begin
  if tg_table_name = 'payout_runs' then
    target_run_id := (to_jsonb(new) ->> 'id')::uuid;
  elsif tg_table_name = 'payout_items' then
    target_run_id := (to_jsonb(new) ->> 'payout_run_id')::uuid;
  elsif tg_table_name = 'advance_applications' then
    select payout_run_id into target_run_id
    from public.payout_items
    where id = (to_jsonb(new) ->> 'payout_item_id')::uuid;
  else
    target_run_id := (to_jsonb(new) ->> 'source_entity_id')::uuid;
  end if;

  select * into run_row
  from public.payout_runs
  where id = target_run_id;
  if not found then
    return new;
  end if;

  select
    count(*),
    coalesce(sum(gross_payable), 0),
    coalesce(sum(net_paid), 0)
  into item_count, gross_total, total_net
  from public.payout_items
  where payout_run_id = target_run_id;
  if item_count < 1 or gross_total <= 0 then
    raise exception 'payout run requires a positive item total';
  end if;

  with source_rows as (
    select
      tic.barber_membership_id,
      sum(tic.barber_commission) as commission_earnings,
      0::numeric as tip_earnings,
      0::numeric as commission_reversals,
      0::numeric as tip_reversals
    from public.transaction_item_commissions tic
    join public.transactions t on t.id = tic.transaction_id
    where t.business_id = run_row.business_id
      and t.shop_id = run_row.shop_id
      and t.created_at >= run_row.period_start
      and t.created_at < run_row.period_end
    group by tic.barber_membership_id
    union all
    select
      t.barber_membership_id,
      0::numeric,
      sum(t.tip_total),
      0::numeric,
      0::numeric
    from public.transactions t
    where t.business_id = run_row.business_id
      and t.shop_id = run_row.shop_id
      and t.created_at >= run_row.period_start
      and t.created_at < run_row.period_end
    group by t.barber_membership_id
    union all
    select
      tcic.barber_membership_id,
      0::numeric,
      0::numeric,
      sum(tcic.barber_commission_refund),
      0::numeric
    from public.transaction_correction_item_commissions tcic
    join public.transaction_corrections tc on tc.id = tcic.correction_id
    where tc.business_id = run_row.business_id
      and tc.shop_id = run_row.shop_id
      and tc.created_at >= run_row.period_start
      and tc.created_at < run_row.period_end
    group by tcic.barber_membership_id
    union all
    select
      tc.barber_membership_id,
      0::numeric,
      0::numeric,
      0::numeric,
      sum(tc.tip_refund)
    from public.transaction_corrections tc
    where tc.business_id = run_row.business_id
      and tc.shop_id = run_row.shop_id
      and tc.created_at >= run_row.period_start
      and tc.created_at < run_row.period_end
    group by tc.barber_membership_id
  ),
  sources as (
    select
      barber_membership_id,
      sum(commission_earnings) as commission_earnings,
      sum(tip_earnings) as tip_earnings,
      sum(commission_reversals) as commission_reversals,
      sum(tip_reversals) as tip_reversals
    from source_rows
    group by barber_membership_id
  ),
  items as (
    select *
    from public.payout_items
    where payout_run_id = target_run_id
  )
  select exists (
    select 1
    from sources s
    full join items pi
      on pi.barber_membership_id = s.barber_membership_id
    where pi.barber_membership_id is null
      or pi.commission_earnings <> coalesce(s.commission_earnings, 0)
      or pi.tip_earnings <> coalesce(s.tip_earnings, 0)
      or pi.commission_reversals <> coalesce(s.commission_reversals, 0)
      or pi.tip_reversals <> coalesce(s.tip_reversals, 0)
  )
  into source_mismatch;

  if source_mismatch then
    raise exception 'payout source snapshots do not reconcile';
  end if;

  if run_row.status = 'draft'
    and exists (
      select 1 from public.payout_items
      where payout_run_id = target_run_id
        and advance_deduction <> 0
    )
  then
    raise exception 'draft payout cannot deduct advances';
  end if;

  if run_row.status = 'approved'
    and exists (
      select 1
      from public.payout_items pi
      where pi.payout_run_id = target_run_id
        and pi.advance_deduction > (
          select coalesce(sum(a.outstanding_amount), 0)
          from public.advances a
          where a.business_id = pi.business_id
            and a.shop_id = pi.shop_id
            and a.barber_membership_id = pi.barber_membership_id
            and a.status = 'open'
        )
    )
  then
    raise exception 'approved advance deduction exceeds outstanding';
  end if;

  select exists (
    select 1
    from public.payout_items pi
    where pi.payout_run_id = target_run_id
      and (
        select coalesce(sum(aa.amount), 0)
        from public.advance_applications aa
        where aa.payout_item_id = pi.id
      ) <> case
        when run_row.status = 'paid' then pi.advance_deduction
        else 0
      end
  )
  into application_mismatch;
  if application_mismatch then
    raise exception 'payout advance applications do not reconcile';
  end if;

  select count(*), coalesce(sum(amount), 0)
  into cash_count, cash_total
  from public.cash_shift_movements
  where movement_type = 'payout'
    and source_entity_id = target_run_id;

  select count(*), min(id::text)::uuid
  into journal_count, journal_id
  from public.journal_entries
  where source_type = 'payout'
    and source_entity_id = target_run_id
    and business_id = run_row.business_id
    and shop_id = run_row.shop_id;

  if run_row.status <> 'paid' then
    if cash_count <> 0 or journal_count <> 0 then
      raise exception 'unpaid payout has settlement records';
    end if;
    return new;
  end if;

  if journal_count <> 1
    or (total_net > 0 and (cash_count <> 1 or cash_total <> total_net))
    or (total_net = 0 and cash_count <> 0)
  then
    raise exception 'payout settlement does not reconcile';
  end if;

  if total_net > 0 and not exists (
    select 1
    from public.cash_shift_movements
    where movement_type = 'payout'
      and source_entity_id = target_run_id
      and cash_shift_id = run_row.cash_shift_id
  ) then
    raise exception 'payout cash shift does not reconcile';
  end if;
  if total_net = 0 and run_row.cash_shift_id is not null then
    raise exception 'zero-cash payout cannot reference a cash shift';
  end if;

  with expected_rows as (
    select
      'barber_payable'::text as account_code,
      barber_membership_id,
      greatest(
        commission_earnings - commission_reversals,
        0
      ) as debit,
      greatest(
        commission_reversals - commission_earnings,
        0
      ) as credit
    from public.payout_items
    where payout_run_id = target_run_id
    union all
    select
      'tip_payable',
      barber_membership_id,
      greatest(tip_earnings - tip_reversals, 0),
      greatest(tip_reversals - tip_earnings, 0)
    from public.payout_items
    where payout_run_id = target_run_id
    union all
    select
      'payout_adjustments',
      barber_membership_id,
      greatest(adjustments, 0),
      greatest(-adjustments, 0)
    from public.payout_items
    where payout_run_id = target_run_id
    union all
    select
      'advance_receivable',
      barber_membership_id,
      0::numeric,
      advance_deduction
    from public.payout_items
    where payout_run_id = target_run_id
    union all
    select
      'cash',
      null::uuid,
      0::numeric,
      total_net
  ),
  expected as (
    select account_code, barber_membership_id, sum(debit) debit, sum(credit) credit
    from expected_rows
    where debit > 0 or credit > 0
    group by account_code, barber_membership_id
  ),
  actual as (
    select
      account_code,
      barber_membership_id,
      sum(debit) as debit,
      sum(credit) as credit
    from public.journal_postings
    where journal_entry_id = journal_id
    group by account_code, barber_membership_id
  )
  select exists (
    select 1
    from expected e
    full join actual a
      on a.account_code = e.account_code
      and a.barber_membership_id is not distinct from e.barber_membership_id
    where e.account_code is null
      or a.account_code is null
      or e.debit <> a.debit
      or e.credit <> a.credit
  )
  into posting_mismatch;

  if posting_mismatch then
    raise exception 'payout journal postings do not reconcile';
  end if;
  return new;
end
$$;

revoke all on function private.guard_advance_update()
  from public, anon, authenticated, service_role;
revoke all on function private.guard_payout_run_update()
  from public, anon, authenticated, service_role;
revoke all on function private.guard_payout_item_update()
  from public, anon, authenticated, service_role;
revoke all on function private.validate_advance_financials()
  from public, anon, authenticated, service_role;
revoke all on function private.validate_payout_run()
  from public, anon, authenticated, service_role;

create trigger advances_guard_update
before update on public.advances
for each row execute function private.guard_advance_update();
create trigger advances_reject_delete
before delete on public.advances
for each row execute function private.reject_update_delete();
create trigger payout_runs_guard_update
before update on public.payout_runs
for each row execute function private.guard_payout_run_update();
create trigger payout_runs_reject_delete
before delete on public.payout_runs
for each row execute function private.reject_update_delete();
create trigger payout_items_guard_update
before update on public.payout_items
for each row execute function private.guard_payout_item_update();
create trigger payout_items_reject_delete
before delete on public.payout_items
for each row execute function private.reject_update_delete();
create trigger advance_applications_reject_change
before update or delete on public.advance_applications
for each row execute function private.reject_update_delete();

create constraint trigger advances_financials_deferred
after insert or update on public.advances
deferrable initially deferred
for each row execute function private.validate_advance_financials();
create constraint trigger applications_advance_deferred
after insert on public.advance_applications
deferrable initially deferred
for each row execute function private.validate_advance_financials();
create constraint trigger payout_runs_validate_deferred
after insert or update on public.payout_runs
deferrable initially deferred
for each row execute function private.validate_payout_run();
create constraint trigger payout_items_validate_deferred
after insert or update on public.payout_items
deferrable initially deferred
for each row execute function private.validate_payout_run();
create constraint trigger applications_payout_deferred
after insert on public.advance_applications
deferrable initially deferred
for each row execute function private.validate_payout_run();
create constraint trigger payout_cash_validate_deferred
after insert on public.cash_shift_movements
deferrable initially deferred
for each row
when (new.movement_type = 'payout')
execute function private.validate_payout_run();

alter table public.advances enable row level security;
alter table public.advances force row level security;
alter table public.payout_runs enable row level security;
alter table public.payout_runs force row level security;
alter table public.payout_items enable row level security;
alter table public.payout_items force row level security;
alter table public.advance_applications enable row level security;
alter table public.advance_applications force row level security;

create policy advances_read_finance
on public.advances for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager']::public.membership_role[]
    )
  )
  or (
    select private.is_own_barber_membership(
      business_id,
      shop_id,
      barber_membership_id
    )
  )
);

create policy payout_runs_read_finance
on public.payout_runs for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager']::public.membership_role[]
    )
  )
);

create policy payout_items_read_finance
on public.payout_items for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager']::public.membership_role[]
    )
  )
  or (
    select private.is_own_barber_membership(
      business_id,
      shop_id,
      barber_membership_id
    )
  )
);

create policy advance_applications_read_finance
on public.advance_applications for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager']::public.membership_role[]
    )
  )
  or exists (
    select 1
    from public.payout_items pi
    where pi.id = payout_item_id
      and (
        select private.is_own_barber_membership(
          advance_applications.business_id,
          advance_applications.shop_id,
          pi.barber_membership_id
        )
      )
  )
);

revoke all on table
  public.advances,
  public.payout_runs,
  public.payout_items,
  public.advance_applications
from anon, authenticated;

grant select on table
  public.advances,
  public.payout_runs,
  public.payout_items,
  public.advance_applications
to authenticated;

grant select, insert, update, delete on table
  public.advances,
  public.payout_runs,
  public.payout_items,
  public.advance_applications
to service_role;
