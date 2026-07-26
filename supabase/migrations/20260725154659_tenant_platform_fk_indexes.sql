create index shop_memberships_shop_business_fk_idx
  on public.shop_memberships (shop_id, business_id);
create index subscriptions_shop_business_fk_idx
  on public.subscriptions (shop_id, business_id)
  where shop_id is not null;
create index subscription_receipts_shop_business_fk_idx
  on public.subscription_cash_receipts (shop_id, business_id)
  where shop_id is not null;
create index tenant_exports_shop_business_fk_idx
  on public.tenant_exports (shop_id, business_id)
  where shop_id is not null;
create index offboarding_cases_shop_business_fk_idx
  on public.offboarding_cases (shop_id, business_id)
  where shop_id is not null;
create index audit_log_shop_business_fk_idx
  on public.audit_log (shop_id, business_id)
  where shop_id is not null;
create index outbox_events_shop_business_fk_idx
  on public.outbox_events (shop_id, business_id)
  where shop_id is not null;
