do $$
begin
  create type public.transaction_correction_kind as enum ('void', 'refund');
exception
  when duplicate_object then null;
end
$$;

alter table public.transaction_payments
  drop constraint transaction_payments_check;
alter table public.transaction_payments
  add constraint transaction_payments_card_reference_check
  check (
    (
      method = 'cash'
      and card_slip_reference is null
    )
    or
    (
      method = 'card'
      and card_slip_reference = btrim(card_slip_reference)
      and char_length(card_slip_reference) between 1 and 64
      and card_slip_reference ~ '^[A-Za-z0-9._:/-]+$'
      and card_slip_reference !~ '([0-9][._:/-]?){12}[0-9]'
    )
  );

create table public.transaction_corrections (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  original_transaction_id uuid not null,
  barber_membership_id uuid not null,
  cash_shift_id uuid,
  kind public.transaction_correction_kind not null,
  credit_note_number text not null check (btrim(credit_note_number) <> ''),
  currency text not null default 'AED' check (currency = 'AED'),
  service_gross_refund numeric(14,2) not null
    check (service_gross_refund >= 0),
  net_refund numeric(14,2) not null check (net_refund >= 0),
  vat_refund numeric(14,2) not null check (vat_refund >= 0),
  tip_refund numeric(14,2) not null check (tip_refund >= 0),
  grand_total numeric(14,2) not null check (grand_total > 0),
  reason text not null
    check (
      reason = btrim(reason)
      and char_length(reason) between 3 and 500
    ),
  legal_snapshot jsonb not null
    check (jsonb_typeof(legal_snapshot) = 'object'),
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (original_transaction_id, business_id, shop_id)
    references public.transactions(id, business_id, shop_id)
    on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  foreign key (cash_shift_id, business_id, shop_id)
    references public.cash_shifts(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (id, original_transaction_id, business_id, shop_id),
  unique (shop_id, credit_note_number),
  check (net_refund + vat_refund = service_gross_refund),
  check (service_gross_refund + tip_refund = grand_total)
);
create index transaction_corrections_shop_business_fk_idx
  on public.transaction_corrections (shop_id, business_id);
create index transaction_corrections_original_tenant_fk_idx
  on public.transaction_corrections (
    original_transaction_id,
    business_id,
    shop_id
  );
create index transaction_corrections_barber_tenant_fk_idx
  on public.transaction_corrections (
    barber_membership_id,
    business_id,
    shop_id
  );
create index transaction_corrections_shift_tenant_fk_idx
  on public.transaction_corrections (
    cash_shift_id,
    business_id,
    shop_id
  ) where cash_shift_id is not null;
create index transaction_corrections_creator_fk_idx
  on public.transaction_corrections (created_by_auth_user_id);
create index transaction_corrections_shop_created_idx
  on public.transaction_corrections (shop_id, created_at desc, id);

create table public.transaction_correction_items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  correction_id uuid not null,
  original_transaction_id uuid not null,
  original_transaction_item_id uuid not null,
  barber_membership_id uuid not null,
  service_name text not null check (btrim(service_name) <> ''),
  refund_net numeric(14,2) not null check (refund_net >= 0),
  refund_vat numeric(14,2) not null check (refund_vat >= 0),
  refund_gross numeric(14,2) not null check (refund_gross > 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  ) references public.transaction_corrections(
    id,
    original_transaction_id,
    business_id,
    shop_id
  ) on delete restrict,
  foreign key (
    original_transaction_item_id,
    original_transaction_id,
    business_id,
    shop_id
  ) references public.transaction_items(
    id,
    transaction_id,
    business_id,
    shop_id
  ) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (id, correction_id, original_transaction_id, business_id, shop_id),
  unique (correction_id, original_transaction_item_id),
  check (refund_net + refund_vat = refund_gross)
);
create index correction_items_shop_business_fk_idx
  on public.transaction_correction_items (shop_id, business_id);
create index correction_items_correction_tenant_fk_idx
  on public.transaction_correction_items (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  );
create index correction_items_original_item_tenant_fk_idx
  on public.transaction_correction_items (
    original_transaction_item_id,
    original_transaction_id,
    business_id,
    shop_id
  );
create index correction_items_barber_tenant_fk_idx
  on public.transaction_correction_items (
    barber_membership_id,
    business_id,
    shop_id
  );

create table public.transaction_correction_item_commissions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  correction_id uuid not null,
  original_transaction_id uuid not null,
  correction_item_id uuid not null,
  original_commission_id uuid not null,
  barber_membership_id uuid not null,
  commission_base_refund numeric(14,2) not null
    check (commission_base_refund >= 0),
  barber_commission_refund numeric(14,2) not null
    check (barber_commission_refund >= 0),
  shop_share_refund numeric(14,2) not null
    check (shop_share_refund >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  ) references public.transaction_corrections(
    id,
    original_transaction_id,
    business_id,
    shop_id
  ) on delete restrict,
  foreign key (
    correction_item_id,
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  ) references public.transaction_correction_items(
    id,
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  ) on delete restrict,
  foreign key (original_commission_id, business_id, shop_id)
    references public.transaction_item_commissions(id, business_id, shop_id)
    on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (correction_item_id),
  check (
    barber_commission_refund + shop_share_refund
    = commission_base_refund
  )
);
create index correction_commissions_shop_business_fk_idx
  on public.transaction_correction_item_commissions (shop_id, business_id);
create index correction_commissions_correction_tenant_fk_idx
  on public.transaction_correction_item_commissions (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  );
create index correction_commissions_item_tenant_fk_idx
  on public.transaction_correction_item_commissions (
    correction_item_id,
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  );
create index correction_commissions_original_tenant_fk_idx
  on public.transaction_correction_item_commissions (
    original_commission_id,
    business_id,
    shop_id
  );
create index correction_commissions_barber_tenant_fk_idx
  on public.transaction_correction_item_commissions (
    barber_membership_id,
    business_id,
    shop_id
  );

create table public.transaction_correction_payments (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  correction_id uuid not null,
  original_transaction_id uuid not null,
  method public.payment_method not null,
  amount numeric(14,2) not null check (amount > 0),
  card_slip_reference text,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  ) references public.transaction_corrections(
    id,
    original_transaction_id,
    business_id,
    shop_id
  ) on delete restrict,
  unique (id, business_id, shop_id),
  unique (correction_id, method),
  check (
    (
      method = 'cash'
      and card_slip_reference is null
    )
    or
    (
      method = 'card'
      and card_slip_reference = btrim(card_slip_reference)
      and char_length(card_slip_reference) between 1 and 64
      and card_slip_reference ~ '^[A-Za-z0-9._:/-]+$'
      and card_slip_reference !~ '([0-9][._:/-]?){12}[0-9]'
    )
  )
);
create index correction_payments_shop_business_fk_idx
  on public.transaction_correction_payments (shop_id, business_id);
create index correction_payments_correction_tenant_fk_idx
  on public.transaction_correction_payments (
    correction_id,
    original_transaction_id,
    business_id,
    shop_id
  );

create function private.validate_transaction_correction(p_correction_id uuid)
returns void
language plpgsql
set search_path = pg_catalog
as $$
declare
  correction public.transaction_corrections%rowtype;
  original public.transactions%rowtype;
  original_journal_id uuid;
  cash_refund numeric(14,2);
begin
  select * into correction
  from public.transaction_corrections
  where id = p_correction_id;
  if not found then
    return;
  end if;

  select * into original
  from public.transactions
  where id = correction.original_transaction_id;

  if not exists (
    select 1
    from public.transaction_correction_items ci
    where ci.correction_id = correction.id
  ) and correction.tip_refund = 0 then
    raise exception 'a correction must refund an item or tip';
  end if;

  if (
    select coalesce(sum(ci.refund_gross), 0)
    from public.transaction_correction_items ci
    where ci.correction_id = correction.id
  ) <> correction.service_gross_refund
  or (
    select coalesce(sum(ci.refund_net), 0)
    from public.transaction_correction_items ci
    where ci.correction_id = correction.id
  ) <> correction.net_refund
  or (
    select coalesce(sum(ci.refund_vat), 0)
    from public.transaction_correction_items ci
    where ci.correction_id = correction.id
  ) <> correction.vat_refund then
    raise exception 'correction item totals do not reconcile';
  end if;

  if (
    select coalesce(sum(cp.amount), 0)
    from public.transaction_correction_payments cp
    where cp.correction_id = correction.id
  ) <> correction.grand_total then
    raise exception 'correction payments do not reconcile';
  end if;

  if exists (
    select 1
    from public.transaction_correction_items ci
    left join public.transaction_correction_item_commissions cc
      on cc.correction_item_id = ci.id
    where ci.correction_id = correction.id
      and (
        cc.id is null
        or cc.commission_base_refund <> ci.refund_net
      )
  ) then
    raise exception 'correction commission snapshots do not reconcile';
  end if;

  if exists (
    select 1
    from public.transaction_items ti
    join public.transaction_item_commissions tic
      on tic.transaction_item_id = ti.id
    join (
      select
        ci.original_transaction_item_id as item_id,
        sum(ci.refund_gross) as gross_refunded,
        sum(ci.refund_net) as net_refunded,
        sum(cc.barber_commission_refund) as barber_refunded
      from public.transaction_correction_items ci
      join public.transaction_correction_item_commissions cc
        on cc.correction_item_id = ci.id
      where ci.original_transaction_id = correction.original_transaction_id
      group by ci.original_transaction_item_id
    ) totals on totals.item_id = ti.id
    where ti.transaction_id = correction.original_transaction_id
      and (
        totals.gross_refunded > ti.line_gross
        or totals.net_refunded <> case
          when totals.gross_refunded = ti.line_gross then ti.line_net
          else round(
            ti.line_net * totals.gross_refunded / nullif(ti.line_gross, 0),
            2
          )
        end
        or totals.barber_refunded <> case
          when totals.net_refunded = tic.commission_base
            then tic.barber_commission
          else round(
            tic.barber_commission
            * totals.net_refunded
            / nullif(tic.commission_base, 0),
            2
          )
        end
      )
  ) then
    raise exception 'cumulative item correction exceeds or misstates original';
  end if;

  if (
    select coalesce(sum(tc.tip_refund), 0)
    from public.transaction_corrections tc
    where tc.original_transaction_id = correction.original_transaction_id
  ) > original.tip_total then
    raise exception 'cumulative tip correction exceeds original';
  end if;

  if exists (
    select 1
    from (
      select cp.method, sum(cp.amount) as refunded
      from public.transaction_correction_payments cp
      where cp.original_transaction_id = correction.original_transaction_id
      group by cp.method
    ) totals
    left join public.transaction_payments tp
      on tp.transaction_id = correction.original_transaction_id
      and tp.method = totals.method
    where tp.id is null or totals.refunded > tp.amount
  ) then
    raise exception 'cumulative payment correction exceeds original tender';
  end if;

  if exists (
    select 1
    from public.transaction_corrections tc
    where tc.original_transaction_id = correction.original_transaction_id
      and tc.kind = 'void'
  ) and (
    select count(*)
    from public.transaction_corrections tc
    where tc.original_transaction_id = correction.original_transaction_id
  ) <> 1 then
    raise exception 'void must be the only correction';
  end if;

  if correction.kind = 'void' then
    if original.cash_shift_id is null
      or correction.cash_shift_id is distinct from original.cash_shift_id
      or correction.service_gross_refund <> original.service_gross_total
      or correction.tip_refund <> original.tip_total then
      raise exception 'void must fully reverse the original open-shift sale';
    end if;
  end if;

  select coalesce(sum(cp.amount), 0) into cash_refund
  from public.transaction_correction_payments cp
  where cp.correction_id = correction.id
    and cp.method = 'cash';

  if cash_refund > 0 then
    if correction.cash_shift_id is null or not exists (
      select 1
      from public.cash_shifts cs
      where cs.id = correction.cash_shift_id
        and cs.business_id = correction.business_id
        and cs.shop_id = correction.shop_id
        and cs.status = 'open'
    ) then
      raise exception 'cash correction requires an open cash shift';
    end if;
    if not exists (
      select 1
      from public.cash_shift_movements csm
      where csm.movement_type = 'refund'
        and csm.source_entity_id = correction.id
        and csm.cash_shift_id = correction.cash_shift_id
        and csm.amount = cash_refund
    ) then
      raise exception 'cash correction movement does not reconcile';
    end if;
  elsif correction.cash_shift_id is not null then
    raise exception 'card-only correction cannot reference a cash shift';
  end if;

  select je.id into original_journal_id
  from public.journal_entries je
  where je.source_type = 'checkout'
    and je.source_entity_id = correction.original_transaction_id;

  if not exists (
    select 1
    from public.journal_entries je
    where je.source_type = 'correction'
      and je.source_entity_id = correction.id
      and je.reversal_of_entry_id = original_journal_id
  ) then
    raise exception 'correction journal link is missing';
  end if;

  if exists (
    select expected.account_code, expected.barber_membership_id, expected.debit,
           expected.credit
    from (
      select
        case cp.method
          when 'cash' then 'cash'
          else 'card_clearing'
        end as account_code,
        null::uuid as barber_membership_id,
        0::numeric(14,2) as debit,
        sum(cp.amount)::numeric(14,2) as credit
      from public.transaction_correction_payments cp
      where cp.correction_id = correction.id
      group by cp.method
      union all
      select 'service_revenue', null::uuid,
             sum(cc.shop_share_refund)::numeric(14,2), 0::numeric(14,2)
      from public.transaction_correction_item_commissions cc
      where cc.correction_id = correction.id
      union all
      select 'barber_payable', correction.barber_membership_id,
             sum(cc.barber_commission_refund)::numeric(14,2), 0::numeric(14,2)
      from public.transaction_correction_item_commissions cc
      where cc.correction_id = correction.id
      union all
      select 'vat_payable', null::uuid,
             correction.vat_refund, 0::numeric(14,2)
      union all
      select 'tip_payable', correction.barber_membership_id,
             correction.tip_refund, 0::numeric(14,2)
    ) expected
    where (expected.debit > 0 or expected.credit > 0)
    except
    select jp.account_code, jp.barber_membership_id, jp.debit, jp.credit
    from public.journal_entries je
    join public.journal_postings jp on jp.journal_entry_id = je.id
    where je.source_type = 'correction'
      and je.source_entity_id = correction.id
  ) or exists (
    select jp.account_code, jp.barber_membership_id, jp.debit, jp.credit
    from public.journal_entries je
    join public.journal_postings jp on jp.journal_entry_id = je.id
    where je.source_type = 'correction'
      and je.source_entity_id = correction.id
    except
    select expected.account_code, expected.barber_membership_id, expected.debit,
           expected.credit
    from (
      select
        case cp.method
          when 'cash' then 'cash'
          else 'card_clearing'
        end as account_code,
        null::uuid as barber_membership_id,
        0::numeric(14,2) as debit,
        sum(cp.amount)::numeric(14,2) as credit
      from public.transaction_correction_payments cp
      where cp.correction_id = correction.id
      group by cp.method
      union all
      select 'service_revenue', null::uuid,
             sum(cc.shop_share_refund)::numeric(14,2), 0::numeric(14,2)
      from public.transaction_correction_item_commissions cc
      where cc.correction_id = correction.id
      union all
      select 'barber_payable', correction.barber_membership_id,
             sum(cc.barber_commission_refund)::numeric(14,2), 0::numeric(14,2)
      from public.transaction_correction_item_commissions cc
      where cc.correction_id = correction.id
      union all
      select 'vat_payable', null::uuid,
             correction.vat_refund, 0::numeric(14,2)
      union all
      select 'tip_payable', correction.barber_membership_id,
             correction.tip_refund, 0::numeric(14,2)
    ) expected
    where expected.debit > 0 or expected.credit > 0
  ) then
    raise exception 'correction journal postings do not reconcile';
  end if;
end
$$;

create function private.validate_correction_trigger()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  correction_id uuid;
begin
  if tg_table_name = 'transaction_corrections' then
    correction_id := new.id;
  elsif tg_table_name in (
    'transaction_correction_items',
    'transaction_correction_item_commissions',
    'transaction_correction_payments'
  ) then
    correction_id := new.correction_id;
  elsif tg_table_name = 'journal_entries' then
    if new.source_type = 'correction' then
      correction_id := new.source_entity_id;
    end if;
  elsif tg_table_name = 'journal_postings' then
    select je.source_entity_id into correction_id
    from public.journal_entries je
    where je.id = new.journal_entry_id and je.source_type = 'correction';
  elsif tg_table_name = 'cash_shift_movements' then
    if new.movement_type = 'refund' then
      correction_id := new.source_entity_id;
    end if;
  end if;
  if correction_id is not null then
    perform private.validate_transaction_correction(correction_id);
  end if;
  return null;
end
$$;

revoke all on function private.validate_transaction_correction(uuid)
from public, anon, authenticated, service_role;
revoke all on function private.validate_correction_trigger()
from public, anon, authenticated, service_role;

create constraint trigger transaction_corrections_validate_deferred
after insert on public.transaction_corrections
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_items_validate_deferred
after insert on public.transaction_correction_items
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_commissions_validate_deferred
after insert on public.transaction_correction_item_commissions
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_payments_validate_deferred
after insert on public.transaction_correction_payments
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_journal_entries_validate_deferred
after insert on public.journal_entries
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_journal_postings_validate_deferred
after insert on public.journal_postings
deferrable initially deferred
for each row execute function private.validate_correction_trigger();
create constraint trigger correction_cash_movements_validate_deferred
after insert on public.cash_shift_movements
deferrable initially deferred
for each row execute function private.validate_correction_trigger();

create trigger transaction_corrections_reject_change
before update or delete on public.transaction_corrections
for each row execute function private.reject_update_delete();
create trigger correction_items_reject_change
before update or delete on public.transaction_correction_items
for each row execute function private.reject_update_delete();
create trigger correction_commissions_reject_change
before update or delete on public.transaction_correction_item_commissions
for each row execute function private.reject_update_delete();
create trigger correction_payments_reject_change
before update or delete on public.transaction_correction_payments
for each row execute function private.reject_update_delete();

alter table public.transaction_corrections enable row level security;
alter table public.transaction_corrections force row level security;
alter table public.transaction_correction_items enable row level security;
alter table public.transaction_correction_items force row level security;
alter table public.transaction_correction_item_commissions
  enable row level security;
alter table public.transaction_correction_item_commissions
  force row level security;
alter table public.transaction_correction_payments enable row level security;
alter table public.transaction_correction_payments force row level security;

create policy transaction_corrections_read_authorized
on public.transaction_corrections for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
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

create policy correction_items_read_authorized
on public.transaction_correction_items for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
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

create policy correction_commissions_read_authorized
on public.transaction_correction_item_commissions
for select to authenticated
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

create policy correction_payments_read_operations
on public.transaction_correction_payments for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
  or (
    select private.has_shop_membership(
      shop_id,
      array['manager', 'receptionist']::public.membership_role[]
    )
  )
);

revoke all on table
  public.transaction_corrections,
  public.transaction_correction_items,
  public.transaction_correction_item_commissions,
  public.transaction_correction_payments
from anon, authenticated;

grant select on table
  public.transaction_corrections,
  public.transaction_correction_items,
  public.transaction_correction_item_commissions,
  public.transaction_correction_payments
to authenticated;

grant select, insert, update, delete on table
  public.transaction_corrections,
  public.transaction_correction_items,
  public.transaction_correction_item_commissions,
  public.transaction_correction_payments
to service_role;
