import os
from uuid import UUID

import pytest

from app.core.authorization import InactiveIdentityError, resolve_actor_context
from app.core.config import Settings
from app.core.database import create_database_pool

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_INTEGRATION") != "1",
    reason="requires reconstructed Phase 1 PostgreSQL test database",
)


async def test_database_derived_actor_scope() -> None:
    pool = create_database_pool(Settings(_env_file=None))
    await pool.open()
    try:
        owner = await resolve_actor_context(
            pool,
            UUID("00000000-0000-0000-0000-000000000002"),
        )
        staff = await resolve_actor_context(
            pool,
            UUID("00000000-0000-0000-0000-000000000003"),
        )

        assert len(owner.shop_access) == 2
        assert {access.role for access in owner.shop_access} == {"owner"}
        assert len(staff.shop_access) == 1
        assert staff.shop_access[0].role == "receptionist"

        with pytest.raises(InactiveIdentityError):
            await resolve_actor_context(
                pool,
                UUID("00000000-0000-0000-0000-000000000006"),
            )
    finally:
        await pool.close()
