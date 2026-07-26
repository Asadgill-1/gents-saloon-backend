drop index public.transaction_commissions_item_tenant_fk_idx;

create index transaction_commissions_item_tenant_fk_idx
  on public.transaction_item_commissions (
    transaction_item_id,
    transaction_id,
    business_id,
    shop_id
  );
