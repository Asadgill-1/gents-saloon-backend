alter table public.bots
  add column webhook_secret_ciphertext text,
  add constraint bots_webhook_secret_envelope_check check (
    webhook_secret_ciphertext is null
    or webhook_secret_ciphertext ~ '^v[1-9][0-9]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'
  );

create table public.staff_invitations (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid not null,
  email text not null
    check (email = lower(btrim(email)) and char_length(email) between 3 and 320),
  role public.membership_role not null,
  status text not null default 'pending'
    check (status in ('pending', 'sent', 'accepted', 'expired', 'revoked')),
  invited_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  auth_user_id uuid references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  accepted_at timestamptz,
  expires_at timestamptz not null default (now() + interval '7 days'),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (expires_at > created_at),
  check ((status = 'accepted') = (accepted_at is not null and auth_user_id is not null))
);
create unique index staff_invitations_active_email_idx
  on public.staff_invitations (shop_id, email)
  where status in ('pending', 'sent');
create index staff_invitations_shop_business_fk_idx
  on public.staff_invitations (shop_id, business_id);
create index staff_invitations_business_fk_idx
  on public.staff_invitations (business_id);
create index staff_invitations_inviter_fk_idx
  on public.staff_invitations (invited_by_auth_user_id);
create index staff_invitations_auth_user_fk_idx
  on public.staff_invitations (auth_user_id)
  where auth_user_id is not null;
create index staff_invitations_status_expiry_idx
  on public.staff_invitations (status, expires_at, id)
  where status in ('pending', 'sent');

alter table public.staff_invitations enable row level security;
alter table public.staff_invitations force row level security;

create policy staff_invitations_read_owner_or_platform
on public.staff_invitations for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

revoke all on table public.staff_invitations from public, anon, authenticated;
grant select on table public.staff_invitations to authenticated;
grant select, insert, update, delete on table public.staff_invitations to service_role;
