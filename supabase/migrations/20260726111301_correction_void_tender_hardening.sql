create function private.validate_correction_void_tender(p_correction_id uuid)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  correction_kind public.transaction_correction_kind;
  original_transaction_id uuid;
begin
  select tc.kind, tc.original_transaction_id
  into correction_kind, original_transaction_id
  from public.transaction_corrections tc
  where tc.id = p_correction_id;

  if correction_kind = 'void' and exists (
    select 1
    from public.transaction_payments tp
    where tp.transaction_id = original_transaction_id
      and tp.method <> 'cash'
  ) then
    raise exception 'void requires cash-only original tender';
  end if;
end;
$$;

create function private.validate_correction_void_tender_trigger()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if tg_table_name = 'transaction_corrections' then
    perform private.validate_correction_void_tender(new.id);
  else
    perform private.validate_correction_void_tender(new.correction_id);
  end if;
  return null;
end;
$$;

revoke all on function private.validate_correction_void_tender(uuid)
from public, anon, authenticated, service_role;
revoke all on function private.validate_correction_void_tender_trigger()
from public, anon, authenticated, service_role;

create constraint trigger correction_void_tender_header_deferred
after insert on public.transaction_corrections
deferrable initially deferred
for each row execute function private.validate_correction_void_tender_trigger();

create constraint trigger correction_void_tender_payment_deferred
after insert on public.transaction_correction_payments
deferrable initially deferred
for each row execute function private.validate_correction_void_tender_trigger();
