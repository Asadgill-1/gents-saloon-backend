-- Phase 1 T1.4: tighten subscription state and immutable receipt reversals.

alter table public.subscriptions
  add constraint subscriptions_suspension_state
  check (
    (
      status = 'suspended'
      and suspended_reason is not null
      and suspended_at is not null
    )
    or
    (
      status <> 'suspended'
      and suspended_reason is null
      and suspended_at is null
    )
  ) not valid;

alter table public.subscriptions
  validate constraint subscriptions_suspension_state;

create or replace function private.validate_receipt_scope()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  original public.subscription_cash_receipts%rowtype;
begin
  if not exists (
    select 1
    from public.subscriptions s
    where s.id = new.subscription_id
      and s.business_id = new.business_id
      and s.shop_id is not distinct from new.shop_id
  ) then
    raise exception 'receipt subject does not match subscription';
  end if;

  if new.reversal_of_id is not null then
    select r.*
    into original
    from public.subscription_cash_receipts r
    where r.id = new.reversal_of_id
    for share;

    if original.id is null
       or original.reversal_of_id is not null
       or original.subscription_id <> new.subscription_id
       or original.business_id <> new.business_id
       or original.shop_id is distinct from new.shop_id then
      raise exception 'receipt reversal does not match an original receipt';
    end if;

    if new.amount <> original.amount
       or new.currency <> original.currency
       or new.coverage_from <> original.coverage_from
       or new.coverage_until <> original.coverage_until then
      raise exception 'receipt reversal must mirror the original receipt';
    end if;
  end if;
  return new;
end
$$;

revoke all on function private.validate_receipt_scope() from public, anon, authenticated;
