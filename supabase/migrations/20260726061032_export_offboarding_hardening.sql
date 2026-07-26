alter table public.tenant_exports
  add column size_bytes bigint,
  add column content_type text,
  add column attempt_count integer not null default 0,
  add column processing_started_at timestamptz,
  add column object_deleted_at timestamptz;

alter table public.tenant_exports
  add constraint tenant_exports_size_check
    check (size_bytes is null or size_bytes > 0),
  add constraint tenant_exports_content_type_check
    check (content_type is null or content_type = 'application/zip'),
  add constraint tenant_exports_attempt_count_check
    check (attempt_count between 0 and 3),
  add constraint tenant_exports_expiry_check
    check (expires_at is null or (ready_at is not null and expires_at > ready_at)),
  add constraint tenant_exports_deletion_check
    check (
      object_deleted_at is null
      or (expires_at is not null and object_deleted_at >= expires_at)
    ),
  add constraint tenant_exports_lifecycle_check
    check (
      (
        status = 'requested'
        and object_key is null
        and sha256 is null
        and size_bytes is null
        and content_type is null
        and processing_started_at is null
        and ready_at is null
        and delivered_at is null
        and expires_at is null
        and failure_reason is null
      )
      or (
        status = 'processing'
        and processing_started_at is not null
        and attempt_count > 0
        and ready_at is null
        and delivered_at is null
        and failure_reason is null
      )
      or (
        status = 'ready'
        and object_key is not null
        and sha256 is not null
        and size_bytes is not null
        and content_type = 'application/zip'
        and ready_at is not null
        and delivered_at is null
        and expires_at is not null
        and failure_reason is null
      )
      or (
        status = 'delivered'
        and object_key is not null
        and sha256 is not null
        and size_bytes is not null
        and content_type = 'application/zip'
        and ready_at is not null
        and delivered_at is not null
        and expires_at is not null
        and failure_reason is null
      )
      or (
        status = 'failed'
        and delivered_at is null
        and failure_reason is not null
      )
    );

alter table public.offboarding_cases
  add constraint offboarding_cases_lifecycle_check
    check (
      (
        state = 'requested'
        and frozen_at is null
        and delivered_at is null
        and archived_at is null
      )
      or (
        state in ('frozen', 'export_ready')
        and frozen_at is not null
        and delivered_at is null
        and archived_at is null
      )
      or (
        state = 'delivered'
        and frozen_at is not null
        and delivered_at is not null
        and archived_at is null
      )
      or (
        state = 'archived'
        and frozen_at is not null
        and delivered_at is not null
        and archived_at is not null
      )
      or state = 'cancelled'
    );

create unique index one_case_per_export
  on public.offboarding_cases (export_id);

create unique index one_open_business_offboarding
  on public.offboarding_cases (business_id)
  where scope = 'business' and state not in ('archived', 'cancelled');

create unique index one_open_shop_offboarding
  on public.offboarding_cases (shop_id)
  where scope = 'shop' and state not in ('archived', 'cancelled');

create index tenant_exports_claim_idx
  on public.tenant_exports (requested_at, id)
  where status = 'requested';

create index tenant_exports_stale_processing_idx
  on public.tenant_exports (processing_started_at, id)
  where status = 'processing';

create function private.validate_export_status_transition()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if old.status = new.status then
    return new;
  end if;
  if not (
    (old.status = 'requested' and new.status = 'processing')
    or (old.status = 'processing' and new.status in ('ready', 'failed'))
    or (old.status = 'ready' and new.status = 'delivered')
  ) then
    raise exception 'invalid tenant export status transition';
  end if;
  return new;
end
$$;

create function private.validate_offboarding_state_transition()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  export_state public.export_status;
begin
  if old.state = new.state then
    return new;
  end if;
  if not (
    (old.state = 'requested' and new.state in ('frozen', 'cancelled'))
    or (old.state = 'frozen' and new.state in ('export_ready', 'cancelled'))
    or (old.state = 'export_ready' and new.state in ('delivered', 'cancelled'))
    or (old.state = 'delivered' and new.state = 'archived')
  ) then
    raise exception 'invalid offboarding state transition';
  end if;

  select e.status
  into export_state
  from public.tenant_exports e
  where e.id = new.export_id;

  if new.state = 'export_ready' and export_state not in ('ready', 'delivered') then
    raise exception 'offboarding export is not ready';
  end if;
  if new.state in ('delivered', 'archived') and export_state <> 'delivered' then
    raise exception 'offboarding export is not delivered';
  end if;
  return new;
end
$$;

revoke all on function private.validate_export_status_transition()
  from public, anon, authenticated, service_role;
revoke all on function private.validate_offboarding_state_transition()
  from public, anon, authenticated, service_role;

create trigger tenant_exports_validate_status_transition
before update of status on public.tenant_exports
for each row execute function private.validate_export_status_transition();

create trigger offboarding_cases_validate_state_transition
before update of state on public.offboarding_cases
for each row execute function private.validate_offboarding_state_transition();
