alter table public.bots
  add column active boolean not null default true,
  add column token_key_version smallint not null default 1,
  add column registered_at timestamptz,
  add column disabled_at timestamptz,
  add constraint bots_token_key_version_check check (token_key_version > 0),
  add constraint bots_token_envelope_check
    check (token_ciphertext ~ '^v[1-9][0-9]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'),
  add constraint bots_webhook_digest_check
    check (webhook_secret_hash ~ '^v1:[0-9a-f]{64}$'),
  add constraint bots_active_disabled_check
    check (active = (disabled_at is null));

create index bots_business_shop_active_idx
  on public.bots (business_id, shop_id, role, id)
  where active;

create table public.telegram_updates (
  id uuid primary key default gen_random_uuid(),
  bot_id uuid not null references public.bots(id) on delete restrict,
  update_id bigint not null check (update_id >= 0),
  payload_ciphertext text not null
    check (payload_ciphertext ~ '^v[1-9][0-9]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'),
  status text not null default 'received'
    check (status in ('received', 'processing', 'completed', 'failed')),
  attempt_count smallint not null default 0 check (attempt_count between 0 and 5),
  available_at timestamptz not null default now(),
  claimed_at timestamptz,
  claimed_by text,
  completed_at timestamptz,
  last_failure_at timestamptz,
  exhausted_at timestamptz,
  last_error_code text check (last_error_code is null or btrim(last_error_code) <> ''),
  received_at timestamptz not null default now(),
  unique (bot_id, update_id),
  check (
    (status = 'received' and attempt_count = 0 and claimed_at is null and completed_at is null)
    or (status = 'processing' and attempt_count > 0 and claimed_at is not null and completed_at is null)
    or (status = 'completed' and attempt_count > 0 and completed_at is not null)
    or (status = 'failed' and attempt_count > 0 and last_failure_at is not null and completed_at is null)
  )
);
create index telegram_updates_claim_idx
  on public.telegram_updates (available_at, received_at, id)
  where status in ('received', 'failed') and exhausted_at is null;
create index telegram_updates_stale_idx
  on public.telegram_updates (claimed_at, id)
  where status = 'processing';
create index telegram_updates_retention_idx
  on public.telegram_updates (received_at, id)
  where status in ('completed', 'failed');

create table public.telegram_sessions (
  id uuid primary key default gen_random_uuid(),
  bot_id uuid not null references public.bots(id) on delete restrict,
  business_id uuid references public.businesses(id) on delete restrict,
  shop_id uuid,
  telegram_user_id bigint not null check (telegram_user_id > 0),
  bot_role public.bot_role not null,
  state text not null check (btrim(state) <> ''),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  unique (bot_id, telegram_user_id),
  check (
    (bot_role = 'master' and business_id is null and shop_id is null)
    or (bot_role <> 'master' and business_id is not null and shop_id is not null)
  )
);
create index telegram_sessions_business_fk_idx on public.telegram_sessions (business_id);
create index telegram_sessions_shop_business_fk_idx
  on public.telegram_sessions (shop_id, business_id);

create table public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete restrict,
  shop_id uuid not null,
  customer_id uuid not null,
  telegram_user_id bigint not null check (telegram_user_id > 0),
  sender_role text not null check (sender_role in ('user', 'assistant', 'system', 'tool')),
  content_redacted text not null check (char_length(content_redacted) between 1 and 4000),
  tool_names text[] not null default '{}',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '90 days'),
  foreign key (customer_id, business_id, shop_id)
    references public.customers(id, business_id, shop_id) on delete restrict,
  foreign key (shop_id, business_id)
    references public.shops(id, business_id) on delete restrict,
  check (expires_at > created_at)
);
create index chat_messages_business_fk_idx on public.chat_messages (business_id);
create index chat_messages_shop_business_fk_idx on public.chat_messages (shop_id, business_id);
create index chat_messages_customer_scope_fk_idx
  on public.chat_messages (customer_id, business_id, shop_id);
create index chat_messages_history_idx
  on public.chat_messages (shop_id, customer_id, created_at desc, id);
create index chat_messages_retention_idx on public.chat_messages (expires_at, id);

create table public.telegram_user_blocks (
  telegram_user_id bigint primary key check (telegram_user_id > 0),
  reason text not null check (char_length(btrim(reason)) between 3 and 500),
  blocked_by_auth_user_id uuid not null
    references public.user_profiles(auth_user_id) on delete restrict,
  blocked_at timestamptz not null default now(),
  expires_at timestamptz,
  check (expires_at is null or expires_at > blocked_at)
);
create index telegram_user_blocks_actor_fk_idx
  on public.telegram_user_blocks (blocked_by_auth_user_id);
alter table public.outbox_events
  add column dead_at timestamptz,
  add column telegram_message_id bigint,
  add constraint outbox_dead_state_check check (
    dead_at is null
    or (status = 'failed' and delivered_at is null)
  );
create index outbox_telegram_claim_idx
  on public.outbox_events (available_at, created_at, id)
  where status in ('pending', 'failed') and topic like 'telegram.%';

create trigger chat_messages_reject_update
  before update on public.chat_messages
  for each row execute function private.reject_update_delete();

alter table public.telegram_updates enable row level security;
alter table public.telegram_updates force row level security;
alter table public.telegram_sessions enable row level security;
alter table public.telegram_sessions force row level security;
alter table public.chat_messages enable row level security;
alter table public.chat_messages force row level security;
alter table public.telegram_user_blocks enable row level security;
alter table public.telegram_user_blocks force row level security;

create policy telegram_updates_read_platform
on public.telegram_updates for select to authenticated
using ((select private.is_platform_admin()));

create policy telegram_sessions_read_owner_or_platform
on public.telegram_sessions for select to authenticated
using (
  (select private.is_platform_admin())
  or (business_id is not null and (select private.owns_business(business_id)))
);

create policy chat_messages_read_owner_or_platform
on public.chat_messages for select to authenticated
using (
  (select private.is_platform_admin())
  or (select private.owns_business(business_id))
);

create policy telegram_user_blocks_read_platform
on public.telegram_user_blocks for select to authenticated
using ((select private.is_platform_admin()));

revoke all on table
  public.telegram_updates,
  public.telegram_sessions,
  public.chat_messages,
  public.telegram_user_blocks
from public, anon, authenticated;

grant select on table
  public.telegram_updates,
  public.telegram_sessions,
  public.chat_messages,
  public.telegram_user_blocks
to authenticated;

grant select, insert, update, delete on table
  public.telegram_updates,
  public.telegram_sessions,
  public.chat_messages,
  public.telegram_user_blocks
to service_role;
