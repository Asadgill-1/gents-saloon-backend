create or replace function pg_temp.assert_true(condition boolean, message text)
returns void language plpgsql as $$
begin
  if not condition then raise exception 'assertion failed: %', message; end if;
end
$$;

do $$
begin
  insert into public.staff_invitations (
    id, business_id, shop_id, email, role, invited_by_auth_user_id
  ) values (
    '62000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '20000000-0000-0000-0000-000000000001',
    'invite@example.com',
    'receptionist',
    '00000000-0000-0000-0000-000000000001'
  );

  set local role authenticated;

  perform set_config(
    'request.jwt.claim.sub',
    '00000000-0000-0000-0000-000000000003',
    true
  );
  perform pg_temp.assert_true(
    (select count(*) from public.staff_invitations) = 0,
    'shop staff could read invitation records'
  );

  perform set_config(
    'request.jwt.claim.sub',
    '00000000-0000-0000-0000-000000000004',
    true
  );
  perform pg_temp.assert_true(
    (select count(*) from public.staff_invitations) = 0,
    'unrelated owner could read invitation records'
  );

  perform set_config(
    'request.jwt.claim.sub',
    '00000000-0000-0000-0000-000000000002',
    true
  );
  perform pg_temp.assert_true(
    (select count(*) from public.staff_invitations) = 1,
    'owning business owner could not read invitation status'
  );

  begin
    insert into public.staff_invitations (
      business_id, shop_id, email, role, invited_by_auth_user_id
    ) values (
      '10000000-0000-0000-0000-000000000001',
      '20000000-0000-0000-0000-000000000001',
      'forbidden@example.com',
      'barber',
      '00000000-0000-0000-0000-000000000001'
    );
    raise exception 'authenticated browser inserted an invitation';
  exception when insufficient_privilege then null;
  end;

  reset role;
  perform set_config('request.jwt.claim.sub', '', true);
end
$$;

select 'platform onboarding interface RLS tests passed' as result;
