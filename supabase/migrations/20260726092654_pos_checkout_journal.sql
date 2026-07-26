do $$ begin
  create type public.transaction_status as enum (
    'completed',
    'voided',
    'partially_refunded',
    'refunded'
  );
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.payment_method as enum ('cash', 'card');
exception when duplicate_object then null;
end $$;

create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  booking_id uuid not null,
  customer_id uuid,
  barber_membership_id uuid not null,
  cash_shift_id uuid,
  receipt_number text not null check (btrim(receipt_number) <> ''),
  document_type public.document_type not null
    check (
      document_type in (
        'receipt',
        'tax_invoice',
        'simplified_tax_invoice'
      )
    ),
  status public.transaction_status not null default 'completed'
    check (status = 'completed'),
  currency text not null default 'AED' check (currency = 'AED'),
  subtotal_gross numeric(14,2) not null check (subtotal_gross >= 0),
  discount_total numeric(14,2) not null check (discount_total >= 0),
  net_total numeric(14,2) not null check (net_total >= 0),
  vat_total numeric(14,2) not null check (vat_total >= 0),
  service_gross_total numeric(14,2) not null
    check (service_gross_total >= 0),
  tip_total numeric(14,2) not null check (tip_total >= 0),
  grand_total numeric(14,2) not null check (grand_total > 0),
  refunded_total numeric(14,2) not null default 0
    check (refunded_total = 0),
  legal_snapshot jsonb not null
    check (jsonb_typeof(legal_snapshot) = 'object'),
  created_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (booking_id, business_id, shop_id)
    references public.bookings(id, business_id, shop_id) on delete restrict,
  foreign key (customer_id, business_id, shop_id)
    references public.customers(id, business_id, shop_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  foreign key (cash_shift_id, business_id, shop_id)
    references public.cash_shifts(id, business_id, shop_id) on delete restrict,
  unique (id, business_id, shop_id),
  unique (booking_id),
  unique (shop_id, receipt_number),
  check (subtotal_gross - discount_total = service_gross_total),
  check (net_total + vat_total = service_gross_total),
  check (service_gross_total + tip_total = grand_total)
);
create index transactions_shop_business_fk_idx
  on public.transactions (shop_id, business_id);
create index transactions_booking_tenant_fk_idx
  on public.transactions (booking_id, business_id, shop_id);
create index transactions_customer_tenant_fk_idx
  on public.transactions (customer_id, business_id, shop_id)
  where customer_id is not null;
create index transactions_barber_tenant_fk_idx
  on public.transactions (barber_membership_id, business_id, shop_id);
create index transactions_cash_shift_tenant_fk_idx
  on public.transactions (cash_shift_id, business_id, shop_id)
  where cash_shift_id is not null;
create index transactions_creator_fk_idx
  on public.transactions (created_by_auth_user_id);
create index transactions_shop_created_idx
  on public.transactions (shop_id, created_at desc, id);

create table public.transaction_items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  transaction_id uuid not null,
  booking_service_id uuid not null,
  service_id uuid not null,
  barber_membership_id uuid not null,
  service_name text not null check (btrim(service_name) <> ''),
  quantity integer not null default 1 check (quantity = 1),
  unit_amount numeric(14,2) not null check (unit_amount >= 0),
  pricing_mode public.pricing_mode not null,
  vat_rate numeric(5,2) not null check (vat_rate between 0 and 100),
  pre_discount_gross numeric(14,2) not null
    check (pre_discount_gross >= 0),
  discount_input numeric(14,2) not null check (discount_input >= 0),
  discount_gross numeric(14,2) not null check (discount_gross >= 0),
  line_net numeric(14,2) not null check (line_net >= 0),
  line_vat numeric(14,2) not null check (line_vat >= 0),
  line_gross numeric(14,2) not null check (line_gross >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (transaction_id, business_id, shop_id)
    references public.transactions(id, business_id, shop_id)
    on delete restrict,
  foreign key (booking_service_id, business_id, shop_id)
    references public.booking_services(id, business_id, shop_id)
    on delete restrict,
  foreign key (service_id, business_id, shop_id)
    references public.services(id, business_id, shop_id) on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (id, transaction_id, business_id, shop_id),
  unique (transaction_id, booking_service_id),
  check (pre_discount_gross - discount_gross = line_gross),
  check (line_net + line_vat = line_gross)
);
create index transaction_items_shop_business_fk_idx
  on public.transaction_items (shop_id, business_id);
create index transaction_items_transaction_tenant_fk_idx
  on public.transaction_items (transaction_id, business_id, shop_id);
create index transaction_items_booking_service_tenant_fk_idx
  on public.transaction_items (booking_service_id, business_id, shop_id);
create index transaction_items_service_tenant_fk_idx
  on public.transaction_items (service_id, business_id, shop_id);
create index transaction_items_barber_tenant_fk_idx
  on public.transaction_items (barber_membership_id, business_id, shop_id);

create table public.transaction_payments (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  transaction_id uuid not null,
  method public.payment_method not null,
  amount numeric(14,2) not null check (amount > 0),
  card_slip_reference text,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (transaction_id, business_id, shop_id)
    references public.transactions(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (transaction_id, method),
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
      and card_slip_reference !~ '[0-9]{13,19}'
    )
  )
);
create index transaction_payments_shop_business_fk_idx
  on public.transaction_payments (shop_id, business_id);
create index transaction_payments_transaction_tenant_fk_idx
  on public.transaction_payments (transaction_id, business_id, shop_id);

create table public.transaction_item_commissions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  transaction_id uuid not null,
  transaction_item_id uuid not null,
  barber_membership_id uuid not null,
  commission_rule_id uuid not null,
  rule_snapshot jsonb not null
    check (jsonb_typeof(rule_snapshot) = 'object'),
  commission_base numeric(14,2) not null check (commission_base >= 0),
  barber_commission numeric(14,2) not null
    check (barber_commission >= 0),
  shop_share numeric(14,2) not null check (shop_share >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (transaction_id, business_id, shop_id)
    references public.transactions(id, business_id, shop_id)
    on delete restrict,
  foreign key (
    transaction_item_id,
    transaction_id,
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
  foreign key (commission_rule_id, business_id, shop_id)
    references public.commission_rules(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (transaction_item_id),
  check (barber_commission + shop_share = commission_base)
);
create index transaction_commissions_shop_business_fk_idx
  on public.transaction_item_commissions (shop_id, business_id);
create index transaction_commissions_transaction_tenant_fk_idx
  on public.transaction_item_commissions (
    transaction_id,
    business_id,
    shop_id
  );
create index transaction_commissions_item_tenant_fk_idx
  on public.transaction_item_commissions (
    transaction_item_id,
    business_id,
    shop_id
  );
create index transaction_commissions_barber_tenant_fk_idx
  on public.transaction_item_commissions (
    barber_membership_id,
    business_id,
    shop_id
  );
create index transaction_commissions_rule_tenant_fk_idx
  on public.transaction_item_commissions (
    commission_rule_id,
    business_id,
    shop_id
  );

create table public.journal_accounts (
  code text primary key
    check (code ~ '^[a-z][a-z0-9_]{1,63}$'),
  name text not null check (btrim(name) <> ''),
  normal_side text not null check (normal_side in ('debit', 'credit'))
);
insert into public.journal_accounts (code, name, normal_side) values
  ('cash', 'Cash', 'debit'),
  ('card_clearing', 'Card clearing', 'debit'),
  ('service_revenue', 'Service revenue', 'credit'),
  ('vat_payable', 'VAT payable', 'credit'),
  ('barber_payable', 'Barber commission payable', 'credit'),
  ('tip_payable', 'Barber tip payable', 'credit'),
  ('advance_receivable', 'Barber advance receivable', 'debit'),
  ('refunds', 'Refunds and allowances', 'debit');

create table public.journal_entries (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  source_type text not null
    check (source_type ~ '^[a-z][a-z0-9_.-]{1,63}$'),
  source_entity_id uuid not null,
  idempotency_key text not null
    check (
      char_length(idempotency_key) between 16 and 128
      and idempotency_key ~ '^[A-Za-z0-9._:-]+$'
    ),
  reversal_of_entry_id uuid,
  actor_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (reversal_of_entry_id, business_id, shop_id)
    references public.journal_entries(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  unique (shop_id, source_type, source_entity_id),
  unique (shop_id, source_type, idempotency_key),
  check (
    (source_type = 'checkout' and reversal_of_entry_id is null)
    or source_type <> 'checkout'
  )
);
create index journal_entries_shop_business_fk_idx
  on public.journal_entries (shop_id, business_id);
create index journal_entries_reversal_tenant_fk_idx
  on public.journal_entries (reversal_of_entry_id, business_id, shop_id)
  where reversal_of_entry_id is not null;
create index journal_entries_actor_fk_idx
  on public.journal_entries (actor_auth_user_id);
create index journal_entries_shop_created_idx
  on public.journal_entries (shop_id, created_at desc, id);

create table public.journal_postings (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  shop_id uuid not null,
  journal_entry_id uuid not null,
  account_code text not null
    references public.journal_accounts(code) on delete restrict,
  barber_membership_id uuid,
  debit numeric(14,2) not null default 0 check (debit >= 0),
  credit numeric(14,2) not null default 0 check (credit >= 0),
  created_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  foreign key (journal_entry_id, business_id, shop_id)
    references public.journal_entries(id, business_id, shop_id)
    on delete restrict,
  foreign key (barber_membership_id, business_id, shop_id)
    references public.shop_memberships(id, business_id, shop_id)
    on delete restrict,
  unique (id, business_id, shop_id),
  check (
    (debit > 0 and credit = 0)
    or (credit > 0 and debit = 0)
  )
);
create index journal_postings_shop_business_fk_idx
  on public.journal_postings (shop_id, business_id);
create index journal_postings_entry_tenant_fk_idx
  on public.journal_postings (journal_entry_id, business_id, shop_id);
create index journal_postings_account_fk_idx
  on public.journal_postings (account_code);
create index journal_postings_barber_tenant_fk_idx
  on public.journal_postings (
    barber_membership_id,
    business_id,
    shop_id
  ) where barber_membership_id is not null;

create function private.validate_checkout_transaction()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  target_transaction_id uuid;
  transaction_row public.transactions%rowtype;
  item_count bigint;
  item_subtotal numeric;
  item_discount numeric;
  item_net numeric;
  item_vat numeric;
  item_gross numeric;
  payment_count bigint;
  payment_total numeric;
  cash_total numeric;
  commission_count bigint;
  commission_base_total numeric;
begin
  if tg_table_name = 'transactions' then
    target_transaction_id := (to_jsonb(new) ->> 'id')::uuid;
  else
    target_transaction_id := (to_jsonb(new) ->> 'transaction_id')::uuid;
  end if;

  select *
  into transaction_row
  from public.transactions
  where id = target_transaction_id;

  if not found then
    raise exception 'checkout transaction not found';
  end if;

  select
    count(*),
    coalesce(sum(pre_discount_gross), 0),
    coalesce(sum(discount_gross), 0),
    coalesce(sum(line_net), 0),
    coalesce(sum(line_vat), 0),
    coalesce(sum(line_gross), 0)
  into
    item_count,
    item_subtotal,
    item_discount,
    item_net,
    item_vat,
    item_gross
  from public.transaction_items
  where transaction_id = target_transaction_id;

  select
    count(*),
    coalesce(sum(amount), 0),
    coalesce(sum(amount) filter (where method = 'cash'), 0)
  into payment_count, payment_total, cash_total
  from public.transaction_payments
  where transaction_id = target_transaction_id;

  select count(*), coalesce(sum(commission_base), 0)
  into commission_count, commission_base_total
  from public.transaction_item_commissions
  where transaction_id = target_transaction_id;

  if item_count < 1
    or payment_count < 1
    or commission_count <> item_count
    or item_subtotal <> transaction_row.subtotal_gross
    or item_discount <> transaction_row.discount_total
    or item_net <> transaction_row.net_total
    or item_vat <> transaction_row.vat_total
    or item_gross <> transaction_row.service_gross_total
    or commission_base_total <> transaction_row.net_total
    or payment_total <> transaction_row.grand_total
    or ((cash_total > 0) <> (transaction_row.cash_shift_id is not null))
  then
    raise exception 'checkout transaction does not reconcile';
  end if;

  return new;
end
$$;

create function private.validate_journal_balance()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  target_entry_id uuid;
  posting_count bigint;
  debit_total numeric;
  credit_total numeric;
begin
  if tg_table_name = 'journal_entries' then
    target_entry_id := (to_jsonb(new) ->> 'id')::uuid;
  else
    target_entry_id := (to_jsonb(new) ->> 'journal_entry_id')::uuid;
  end if;

  select count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0)
  into posting_count, debit_total, credit_total
  from public.journal_postings
  where journal_entry_id = target_entry_id;

  if posting_count < 2
    or debit_total <= 0
    or debit_total <> credit_total
  then
    raise exception 'journal entry does not balance';
  end if;

  return new;
end
$$;

revoke all on function private.validate_checkout_transaction()
  from public, anon, authenticated, service_role;
revoke all on function private.validate_journal_balance()
  from public, anon, authenticated, service_role;

create constraint trigger transactions_validate_deferred
after insert on public.transactions
deferrable initially deferred
for each row execute function private.validate_checkout_transaction();
create constraint trigger transaction_items_validate_deferred
after insert on public.transaction_items
deferrable initially deferred
for each row execute function private.validate_checkout_transaction();
create constraint trigger transaction_payments_validate_deferred
after insert on public.transaction_payments
deferrable initially deferred
for each row execute function private.validate_checkout_transaction();
create constraint trigger transaction_commissions_validate_deferred
after insert on public.transaction_item_commissions
deferrable initially deferred
for each row execute function private.validate_checkout_transaction();

create constraint trigger journal_entries_balance_deferred
after insert on public.journal_entries
deferrable initially deferred
for each row execute function private.validate_journal_balance();
create constraint trigger journal_postings_balance_deferred
after insert on public.journal_postings
deferrable initially deferred
for each row execute function private.validate_journal_balance();

create trigger transactions_reject_change
before update or delete on public.transactions
for each row execute function private.reject_update_delete();
create trigger transaction_items_reject_change
before update or delete on public.transaction_items
for each row execute function private.reject_update_delete();
create trigger transaction_payments_reject_change
before update or delete on public.transaction_payments
for each row execute function private.reject_update_delete();
create trigger transaction_commissions_reject_change
before update or delete on public.transaction_item_commissions
for each row execute function private.reject_update_delete();
create trigger journal_accounts_reject_change
before update or delete on public.journal_accounts
for each row execute function private.reject_update_delete();
create trigger journal_entries_reject_change
before update or delete on public.journal_entries
for each row execute function private.reject_update_delete();
create trigger journal_postings_reject_change
before update or delete on public.journal_postings
for each row execute function private.reject_update_delete();

alter table public.transactions enable row level security;
alter table public.transactions force row level security;
alter table public.transaction_items enable row level security;
alter table public.transaction_items force row level security;
alter table public.transaction_payments enable row level security;
alter table public.transaction_payments force row level security;
alter table public.transaction_item_commissions enable row level security;
alter table public.transaction_item_commissions force row level security;
alter table public.journal_accounts enable row level security;
alter table public.journal_accounts force row level security;
alter table public.journal_entries enable row level security;
alter table public.journal_entries force row level security;
alter table public.journal_postings enable row level security;
alter table public.journal_postings force row level security;

create policy transactions_read_authorized
on public.transactions for select to authenticated
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

create policy transaction_items_read_authorized
on public.transaction_items for select to authenticated
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

create policy transaction_payments_read_operations
on public.transaction_payments for select to authenticated
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

create policy transaction_commissions_read_authorized
on public.transaction_item_commissions for select to authenticated
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

create policy journal_accounts_read_authenticated
on public.journal_accounts for select to authenticated
using ((select private.is_active_auth_user()));

create policy journal_entries_read_finance
on public.journal_entries for select to authenticated
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

create policy journal_postings_read_finance
on public.journal_postings for select to authenticated
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

revoke all on table
  public.transactions,
  public.transaction_items,
  public.transaction_payments,
  public.transaction_item_commissions,
  public.journal_accounts,
  public.journal_entries,
  public.journal_postings
from anon, authenticated;

grant select on table
  public.transactions,
  public.transaction_items,
  public.transaction_payments,
  public.transaction_item_commissions,
  public.journal_accounts,
  public.journal_entries,
  public.journal_postings
to authenticated;

grant select, insert, update, delete on table
  public.transactions,
  public.transaction_items,
  public.transaction_payments,
  public.transaction_item_commissions,
  public.journal_accounts,
  public.journal_entries,
  public.journal_postings
to service_role;
