import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_database_pool
from app.services.legal_cash_service import CashShiftOpenRequest, open_cash_shift
from app.services.owner_bot_flow import (
    handle_owner_callback,
    handle_owner_input,
)
from app.services.payout_service import PayoutAdjustment, PayoutRunRequest, create_payout_run

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 3 PostgreSQL test database",
)

BOT_ID = UUID("60000000-0000-0000-0000-000000000004")
BUSINESS_ID = UUID("10000000-0000-0000-0000-000000000001")
SHOP_ID = UUID("20000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000002")
TELEGRAM_USER_ID = 999102


async def test_owner_reports_shop_context_and_advance_confirmation() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        async with pool.connection(timeout=5) as connection, connection.transaction():
            await connection.execute(
                """
                update public.business_owners
                set telegram_user_id = %s
                where business_id = %s and auth_user_id = %s and active
                """,
                (TELEGRAM_USER_ID, BUSINESS_ID, OWNER_ID),
            )
            await connection.execute(
                """
                insert into public.bots (
                  id, business_id, shop_id, role, token_ciphertext,
                  bot_username, webhook_secret_hash
                ) values (
                  %s, %s, %s, 'owner',
                  'v1.AAAAAAAAAAAAAAAA.RRRRRRRRRRRRRRRRRRRRRR',
                  'test_owner_bot', 'v1:' || repeat('d', 64)
                )
                on conflict (id) do nothing
                """,
                (BOT_ID, BUSINESS_ID, SHOP_ID),
            )

        business = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.o01",
            request_id="telegram:test:owner-business",
        )
        assert "Business today" in business.text

        shops = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.o02",
            request_id="telegram:test:owner-shops",
        )
        assert shops.keyboard is not None
        assert len(shops.keyboard.inline_keyboard) == 2
        shop_callback = next(
            button.callback_data
            for row in shops.keyboard.inline_keyboard
            for button in row
            if button.text == "A One"
        )
        assert shop_callback is not None
        selected = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=shop_callback,
            request_id="telegram:test:owner-shop-select",
        )
        assert selected.text == "Selected shop: A One."

        for callback, expected in (
            ("v1.o03", "Shop today"),
            ("v1.o04", "This month"),
            ("v1.o05", "Barbers:"),
            ("v1.o07", "recent audit"),
            ("v1.o08", "Subscription:"),
        ):
            response = await handle_owner_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                bot_shop_id=SHOP_ID,
                actor_id=OWNER_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback=callback,
                request_id=f"telegram:test:owner:{callback}",
            )
            assert expected in response.text

        shift = await open_cash_shift(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="telegram:test:owner-advance-shift",
            request_id="telegram:test:owner-advance-shift",
            payload=CashShiftOpenRequest(
                register_label="Owner Bot Desk", opening_float=Decimal("100.00")
            ),
        )
        finance = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.o06",
            request_id="telegram:test:owner-finance",
        )
        assert finance.keyboard is not None
        barbers = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.ownadv",
            request_id="telegram:test:owner-advance-start",
        )
        assert barbers.keyboard is not None
        barber_callback = barbers.keyboard.inline_keyboard[0][0].callback_data
        assert barber_callback is not None
        shifts = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=barber_callback,
            request_id="telegram:test:owner-advance-barber",
        )
        assert shifts.keyboard is not None
        cash_callback = next(
            button.callback_data
            for row in shifts.keyboard.inline_keyboard
            for button in row
            if button.text == shift.register_label
        )
        assert cash_callback is not None
        await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=cash_callback,
            request_id="telegram:test:owner-advance-cash",
        )
        confirmation = await handle_owner_input(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            text="10.00",
        )
        assert "Confirm advance AED 10.00" in confirmation.text
        for _attempt in range(2):
            granted = await handle_owner_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                bot_shop_id=SHOP_ID,
                actor_id=OWNER_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback="v1.ownconfirm",
                request_id="telegram:test:owner-advance-confirm",
            )
            assert "Advance granted: AED 10.00" in granted.text

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select id from public.shop_memberships
                where business_id = %s and shop_id = %s and role = 'barber' and active
                order by display_name, id limit 1
                """,
                (BUSINESS_ID, SHOP_ID),
            )
            barber_row = await cursor.fetchone()
            assert barber_row is not None
            barber_membership_id = UUID(str(barber_row[0]))

        now = datetime.now(UTC)
        payout = await create_payout_run(
            pool,
            actor_id=OWNER_ID,
            business_id=BUSINESS_ID,
            shop_id=SHOP_ID,
            idempotency_key="telegram:test:owner-payout-create",
            request_id="telegram:test:owner-payout-create",
            payload=PayoutRunRequest(
                period_start=now - timedelta(days=3),
                period_end=now - timedelta(days=2),
                adjustments=[
                    PayoutAdjustment(
                        barber_membership_id=barber_membership_id,
                        amount=Decimal("12.00"),
                        reason="Owner bot payout flow fixture",
                    )
                ],
            ),
            at=now,
        )
        assert payout.status == "draft"

        payout_menu = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.ownpayouts",
            request_id="telegram:test:owner-payout-menu",
        )
        assert payout_menu.keyboard is not None
        payout_callback = payout_menu.keyboard.inline_keyboard[0][0].callback_data
        assert payout_callback is not None
        approval = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=payout_callback,
            request_id="telegram:test:owner-payout-review",
        )
        assert "gross before advance deductions AED 12.00" in approval.text
        for _attempt in range(2):
            approved = await handle_owner_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                bot_shop_id=SHOP_ID,
                actor_id=OWNER_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback="v1.ownpapprove",
                request_id="telegram:test:owner-payout-approve",
            )
            assert "Net cash after advance deductions: AED 2.00" in approved.text

        payout_menu = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback="v1.ownpayouts",
            request_id="telegram:test:owner-payout-menu-approved",
        )
        assert payout_menu.keyboard is not None
        payout_callback = payout_menu.keyboard.inline_keyboard[0][0].callback_data
        assert payout_callback is not None
        cash_menu = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=payout_callback,
            request_id="telegram:test:owner-payout-pay-review",
        )
        assert cash_menu.keyboard is not None
        payout_cash_callback = next(
            button.callback_data
            for row in cash_menu.keyboard.inline_keyboard
            for button in row
            if button.text == shift.register_label
        )
        assert payout_cash_callback is not None
        payout_confirmation = await handle_owner_callback(
            pool,
            bot_id=BOT_ID,
            business_id=BUSINESS_ID,
            bot_shop_id=SHOP_ID,
            actor_id=OWNER_ID,
            telegram_user_id=TELEGRAM_USER_ID,
            callback=payout_cash_callback,
            request_id="telegram:test:owner-payout-cash",
        )
        assert "Confirm cash payout AED 2.00" in payout_confirmation.text
        for _attempt in range(2):
            paid = await handle_owner_callback(
                pool,
                bot_id=BOT_ID,
                business_id=BUSINESS_ID,
                bot_shop_id=SHOP_ID,
                actor_id=OWNER_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                callback="v1.ownppay",
                request_id="telegram:test:owner-payout-pay",
            )
            assert paid.text == "Payout paid once: AED 2.00."

        async with pool.connection(timeout=5) as connection:
            cursor = await connection.execute(
                """
                select
                  (select count(*) from public.advances
                   where business_id = %s and shop_id = %s and cash_shift_id = %s
                     and original_amount = 10.00
                     and note = 'Owner bot: deduct from next applicable payout'),
                  (select status::text from public.payout_runs where id = %s),
                  (select count(*) from public.cash_shift_movements
                   where movement_type = 'payout' and source_entity_id = %s)
                """,
                (
                    BUSINESS_ID,
                    SHOP_ID,
                    shift.cash_shift_id,
                    payout.payout_run_id,
                    payout.payout_run_id,
                ),
            )
            assert await cursor.fetchone() == (1, "paid", 1)
    finally:
        await pool.close()
