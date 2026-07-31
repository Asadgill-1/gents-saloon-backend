create or replace function pg_temp.assert_true(condition boolean, message text)
returns void language plpgsql as $$
begin
  if not condition then raise exception 'assertion failed: %', message; end if;
end
$$;

do $$
declare
  customer_bot_id uuid := '60000000-0000-0000-0000-000000000001';
  master_bot_id uuid := '60000000-0000-0000-0000-000000000002';
  customer_id uuid := '61000000-0000-0000-0000-000000000001';
  business_id uuid := '10000000-0000-0000-0000-000000000001';
  shop_id uuid := '20000000-0000-0000-0000-000000000001';
begin
  insert into public.bots (
    id, business_id, shop_id, role, token_ciphertext,
    bot_username, webhook_secret_hash
  ) values (
    customer_bot_id, business_id, shop_id, 'customer',
    'v1.AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBBBB',
    'test_customer_bot', 'v1:' || repeat('a', 64)
  );
  insert into public.bots (
    id, business_id, shop_id, role, token_ciphertext,
    bot_username, webhook_secret_hash
  ) values (
    master_bot_id, null, null, 'master',
    'v1.AAAAAAAAAAAAAAAA.CCCCCCCCCCCCCCCCCCCCCC',
    'test_master_bot', 'v1:' || repeat('b', 64)
  );

  insert into public.customers (
    id, business_id, shop_id, telegram_user_id, display_name
  ) values (customer_id, business_id, shop_id, 999001, 'Telegram Test');

  insert into public.telegram_updates (bot_id, update_id, payload_ciphertext)
  values (
    customer_bot_id, 1001,
    'v1.AAAAAAAAAAAAAAAA.DDDDDDDDDDDDDDDDDDDDDD'
  );
  begin
    insert into public.telegram_updates (bot_id, update_id, payload_ciphertext)
    values (
      customer_bot_id, 1001,
      'v1.AAAAAAAAAAAAAAAA.EEEEEEEEEEEEEEEEEEEEEE'
    );
    raise exception 'duplicate Telegram update was accepted';
  exception when unique_violation then null;
  end;

  update public.telegram_updates
  set status = 'processing', attempt_count = 1,
      claimed_at = now(), claimed_by = 'test-worker'
  where bot_id = customer_bot_id and update_id = 1001;
  update public.telegram_updates
  set status = 'completed', completed_at = now(), claimed_by = null
  where bot_id = customer_bot_id and update_id = 1001;

  insert into public.telegram_sessions (
    bot_id, business_id, shop_id, telegram_user_id, bot_role, state
  ) values (
    customer_bot_id, business_id, shop_id, 999001, 'customer', 'main_menu'
  );
  insert into public.telegram_sessions (
    bot_id, business_id, shop_id, telegram_user_id, bot_role, state
  ) values (
    master_bot_id, null, null, 999002, 'master', 'main_menu'
  );

  insert into public.chat_messages (
    business_id, shop_id, customer_id, telegram_user_id,
    sender_role, content_redacted
  ) values (
    business_id, shop_id, customer_id, 999001, 'user', 'redacted hello'
  );

  perform pg_temp.assert_true(
    exists (
      select 1 from pg_constraint
      where conrelid = 'public.chat_messages'::regclass
        and contype = 'f'
        and pg_get_constraintdef(oid) like
          '%(customer_id, business_id, shop_id)%customers(id, business_id, shop_id)%'
    ),
    'chat customer foreign key is not tenant composite'
  );
  perform pg_temp.assert_true(
    (select count(*) from public.telegram_sessions) = 2,
    'master and shop-scoped sessions were not both accepted'
  );

  set local role authenticated;

  perform set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000003', true);
  perform pg_temp.assert_true(
    (select count(*) from public.telegram_sessions) = 0,
    'receptionist could read Telegram sessions'
  );

  perform set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000004', true);
  perform pg_temp.assert_true(
    (select count(*) from public.chat_messages) = 0,
    'unrelated owner could read chat messages'
  );

  perform set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000002', true);
  perform pg_temp.assert_true(
    (select count(*) from public.telegram_sessions) = 1,
    'authorized owner did not receive only the owned scoped session'
  );
  perform pg_temp.assert_true(
    (select count(*) from public.chat_messages) = 1,
    'authorized owner could not read redacted shop chat'
  );

  begin
    insert into public.telegram_updates (bot_id, update_id, payload_ciphertext)
    values (
      customer_bot_id, 1002,
      'v1.AAAAAAAAAAAAAAAA.FFFFFFFFFFFFFFFFFFFFFF'
    );
    raise exception 'authenticated browser inserted a Telegram update';
  exception when insufficient_privilege then null;
  end;

  reset role;
  perform set_config('request.jwt.claim.sub', '', true);
end
$$;

select 'phase3 telegram RLS tests passed' as result;
