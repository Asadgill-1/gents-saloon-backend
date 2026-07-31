do $$ begin
  create type public.e_invoice_document_type as enum (
    'invoice',
    'credit_note'
  );
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.e_invoice_transaction_scope as enum ('b2b', 'b2g');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.e_invoice_document_status as enum ('prepared');
exception when duplicate_object then null;
end $$;

create index bookings_shop_created_report_idx
  on public.bookings (shop_id, created_at desc, id);
create index bookings_shop_completed_report_idx
  on public.bookings (shop_id, completed_at desc, id)
  where completed_at is not null;
create index cash_movements_shop_created_report_idx
  on public.cash_shift_movements (shop_id, created_at desc, id);
create index cash_shifts_shop_closed_report_idx
  on public.cash_shifts (shop_id, closed_at desc, id)
  where status = 'closed';
create index advances_shop_given_report_idx
  on public.advances (shop_id, given_at desc, id);
create index payout_runs_shop_paid_report_idx
  on public.payout_runs (shop_id, paid_at desc, id)
  where status = 'paid';

alter table public.subscription_cash_receipts
  add constraint subscription_cash_receipts_id_business_unique
  unique (id, business_id);

create table public.e_invoice_documents (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  subscription_cash_receipt_id uuid not null,
  reversal_of_document_id uuid,
  document_type public.e_invoice_document_type not null,
  transaction_scope public.e_invoice_transaction_scope not null,
  status public.e_invoice_document_status not null default 'prepared',
  source_schema_version text not null
    check (source_schema_version = 'platform_billing_source_v1'),
  currency text not null check (currency = 'AED'),
  amount numeric(14,2) not null check (amount > 0),
  source_snapshot jsonb not null
    check (jsonb_typeof(source_snapshot) = 'object'),
  prepared_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  prepared_at timestamptz not null default now(),
  foreign key (business_id)
    references public.businesses(id) on delete restrict,
  foreign key (subscription_cash_receipt_id, business_id)
    references public.subscription_cash_receipts(id, business_id)
    on delete restrict,
  foreign key (reversal_of_document_id)
    references public.e_invoice_documents(id) on delete restrict,
  unique (id, business_id),
  unique (subscription_cash_receipt_id),
  check (
    (document_type = 'invoice' and reversal_of_document_id is null)
    or
    (document_type = 'credit_note' and reversal_of_document_id is not null)
  )
);
create index e_invoice_documents_business_prepared_idx
  on public.e_invoice_documents (business_id, prepared_at desc, id);
create index e_invoice_documents_receipt_business_fk_idx
  on public.e_invoice_documents (subscription_cash_receipt_id, business_id);
create index e_invoice_documents_reversal_fk_idx
  on public.e_invoice_documents (reversal_of_document_id)
  where reversal_of_document_id is not null;
create index e_invoice_documents_prepared_by_fk_idx
  on public.e_invoice_documents (prepared_by_auth_user_id);

create function private.validate_e_invoice_document()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  receipt_row record;
  expected_document_type public.e_invoice_document_type;
  expected_reversal_document_id uuid;
  expected_snapshot jsonb;
begin
  select
    r.id,
    r.subscription_id,
    r.business_id,
    r.shop_id,
    r.amount,
    r.currency,
    r.receipt_reference,
    r.receipt_sequence,
    r.collected_at,
    r.coverage_from,
    r.coverage_until,
    r.collected_by,
    r.reversal_of_id,
    b.legal_name,
    b.display_name,
    b.trade_license_number,
    b.vat_registered,
    b.trn,
    b.invoice_address
  into receipt_row
  from public.subscription_cash_receipts r
  join public.businesses b on b.id = r.business_id
  where r.id = new.subscription_cash_receipt_id;

  if not found then
    raise exception 'e-invoice source receipt does not exist';
  end if;

  if receipt_row.reversal_of_id is null then
    expected_document_type := 'invoice';
    expected_reversal_document_id := null;
  else
    expected_document_type := 'credit_note';
    select d.id
    into expected_reversal_document_id
    from public.e_invoice_documents d
    where d.subscription_cash_receipt_id = receipt_row.reversal_of_id;
    if expected_reversal_document_id is null then
      raise exception 'e-invoice credit note requires its original document';
    end if;
  end if;

  expected_snapshot := jsonb_build_object(
    'receipt_id', receipt_row.id,
    'receipt_sequence', receipt_row.receipt_sequence,
    'subscription_id', receipt_row.subscription_id,
    'shop_id', receipt_row.shop_id,
    'receipt_reference', receipt_row.receipt_reference,
    'collected_at', receipt_row.collected_at,
    'coverage_from', receipt_row.coverage_from,
    'coverage_until', receipt_row.coverage_until,
    'reversal_of_receipt_id', receipt_row.reversal_of_id,
    'buyer', jsonb_build_object(
      'business_id', receipt_row.business_id,
      'legal_name', receipt_row.legal_name,
      'display_name', receipt_row.display_name,
      'trade_license_number', receipt_row.trade_license_number,
      'vat_registered', receipt_row.vat_registered,
      'trn', receipt_row.trn,
      'invoice_address', receipt_row.invoice_address
    )
  );

  if new.business_id <> receipt_row.business_id
    or new.document_type <> expected_document_type
    or new.transaction_scope <> 'b2b'
    or new.status <> 'prepared'
    or new.source_schema_version <> 'platform_billing_source_v1'
    or new.currency <> receipt_row.currency
    or new.amount <> receipt_row.amount
    or new.prepared_by_auth_user_id <> receipt_row.collected_by
    or new.reversal_of_document_id is distinct from expected_reversal_document_id
    or new.source_snapshot is distinct from expected_snapshot
  then
    raise exception 'e-invoice source envelope does not reconcile';
  end if;
  return new;
end
$$;

revoke all on function private.validate_e_invoice_document()
  from public, anon, authenticated, service_role;

create trigger e_invoice_documents_validate
before insert on public.e_invoice_documents
for each row execute function private.validate_e_invoice_document();

create trigger e_invoice_documents_append_only
before update or delete on public.e_invoice_documents
for each row execute function private.reject_update_delete();

create function private.create_e_invoice_document(
  p_receipt_id uuid,
  p_request_id text
)
returns uuid
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  document_id uuid;
  document_business_id uuid;
  document_shop_id uuid;
  document_actor_id uuid;
begin
  insert into public.e_invoice_documents (
    business_id,
    subscription_cash_receipt_id,
    reversal_of_document_id,
    document_type,
    transaction_scope,
    status,
    source_schema_version,
    currency,
    amount,
    source_snapshot,
    prepared_by_auth_user_id,
    prepared_at
  )
  select
    r.business_id,
    r.id,
    original_document.id,
    case
      when r.reversal_of_id is null then 'invoice'
      else 'credit_note'
    end::public.e_invoice_document_type,
    'b2b',
    'prepared',
    'platform_billing_source_v1',
    r.currency,
    r.amount,
    jsonb_build_object(
      'receipt_id', r.id,
      'receipt_sequence', r.receipt_sequence,
      'subscription_id', r.subscription_id,
      'shop_id', r.shop_id,
      'receipt_reference', r.receipt_reference,
      'collected_at', r.collected_at,
      'coverage_from', r.coverage_from,
      'coverage_until', r.coverage_until,
      'reversal_of_receipt_id', r.reversal_of_id,
      'buyer', jsonb_build_object(
        'business_id', b.id,
        'legal_name', b.legal_name,
        'display_name', b.display_name,
        'trade_license_number', b.trade_license_number,
        'vat_registered', b.vat_registered,
        'trn', b.trn,
        'invoice_address', b.invoice_address
      )
    ),
    r.collected_by,
    r.created_at
  from public.subscription_cash_receipts r
  join public.businesses b on b.id = r.business_id
  left join public.e_invoice_documents original_document
    on original_document.subscription_cash_receipt_id = r.reversal_of_id
  where r.id = p_receipt_id
  on conflict (subscription_cash_receipt_id) do nothing
  returning id, business_id, prepared_by_auth_user_id
  into document_id, document_business_id, document_actor_id;

  if document_id is null then
    select d.id
    into document_id
    from public.e_invoice_documents d
    where d.subscription_cash_receipt_id = p_receipt_id;
    if document_id is null then
      raise exception 'e-invoice source receipt could not be prepared';
    end if;
    return document_id;
  end if;

  select r.shop_id
  into document_shop_id
  from public.subscription_cash_receipts r
  where r.id = p_receipt_id;

  insert into public.audit_log (
    business_id,
    shop_id,
    actor_type,
    actor_id,
    action,
    entity_type,
    entity_id,
    request_id,
    after
  )
  values (
    document_business_id,
    document_shop_id,
    'platform_admin',
    document_actor_id::text,
    'e_invoice.document_prepared',
    'e_invoice_document',
    document_id,
    p_request_id,
    jsonb_build_object(
      'document_id', document_id,
      'source_receipt_id', p_receipt_id,
      'transaction_scope', 'b2b',
      'status', 'prepared'
    )
  );

  insert into public.outbox_events (
    business_id,
    shop_id,
    topic,
    dedupe_key,
    payload
  )
  values (
    document_business_id,
    document_shop_id,
    'e_invoice.document_prepared',
    'e_invoice.document_prepared:' || document_id::text,
    jsonb_build_object(
      'document_id', document_id,
      'source_receipt_id', p_receipt_id,
      'transaction_scope', 'b2b',
      'status', 'prepared'
    )
  )
  on conflict (dedupe_key) do nothing;

  return document_id;
end
$$;

create function private.prepare_e_invoice_after_receipt()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  perform private.create_e_invoice_document(
    new.id,
    'subscription-receipt:' || new.id::text
  );
  return new;
end
$$;

revoke all on function private.create_e_invoice_document(uuid, text)
  from public, anon, authenticated, service_role;
revoke all on function private.prepare_e_invoice_after_receipt()
  from public, anon, authenticated, service_role;

create trigger subscription_cash_receipts_prepare_e_invoice
after insert on public.subscription_cash_receipts
for each row execute function private.prepare_e_invoice_after_receipt();

do $$
declare
  receipt record;
begin
  for receipt in
    select id
    from public.subscription_cash_receipts
    order by (reversal_of_id is not null), created_at, id
  loop
    perform private.create_e_invoice_document(
      receipt.id,
      'migration-e-invoice:' || receipt.id::text
    );
  end loop;
end
$$;

alter table public.e_invoice_documents enable row level security;
alter table public.e_invoice_documents force row level security;

create policy e_invoice_documents_read_owner_or_platform
on public.e_invoice_documents for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

revoke all on table public.e_invoice_documents
  from public, anon, authenticated;
grant select on table public.e_invoice_documents to authenticated;
grant select, insert, update, delete
  on table public.e_invoice_documents to service_role;
