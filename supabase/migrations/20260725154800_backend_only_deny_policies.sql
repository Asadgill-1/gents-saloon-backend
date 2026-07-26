create policy bots_deny_browser_read
on public.bots for select to anon, authenticated
using (false);

create policy idempotency_keys_deny_browser_read
on public.idempotency_keys for select to anon, authenticated
using (false);

create policy outbox_events_deny_browser_read
on public.outbox_events for select to anon, authenticated
using (false);
