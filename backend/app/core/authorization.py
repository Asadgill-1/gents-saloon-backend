from dataclasses import dataclass
from typing import Any
from uuid import UUID


class InactiveIdentityError(Exception):
    """The authenticated subject is not an active application user."""


@dataclass(frozen=True)
class ShopAccess:
    business_id: UUID
    business_name: str
    shop_id: UUID
    shop_name: str
    internal_code: str
    is_owner: bool
    role: str


@dataclass(frozen=True)
class ActorContext:
    auth_user_id: UUID
    display_name: str
    is_platform_admin: bool
    shop_access: tuple[ShopAccess, ...]


IDENTITY_SQL = """
select
  up.display_name,
  exists (
    select 1
    from public.platform_admins pa
    where pa.auth_user_id = up.auth_user_id
      and pa.active
  ) as is_platform_admin
from public.user_profiles up
where up.auth_user_id = %s
  and up.active
"""

ACCESS_SQL = """
select
  b.id,
  b.display_name,
  s.id,
  s.name,
  s.internal_code,
  true,
  'owner'
from public.business_owners bo
join public.businesses b on b.id = bo.business_id
join public.shops s on s.business_id = b.id
where bo.auth_user_id = %s
  and bo.active
  and b.status <> 'archived'
  and s.status <> 'archived'
union all
select
  b.id,
  b.display_name,
  s.id,
  s.name,
  s.internal_code,
  false,
  sm.role::text
from public.shop_memberships sm
join public.businesses b on b.id = sm.business_id
join public.shops s
  on s.id = sm.shop_id
 and s.business_id = sm.business_id
where sm.auth_user_id = %s
  and sm.active
  and b.status <> 'archived'
  and s.status <> 'archived'
order by 2, 4, 7
"""


async def resolve_actor_context(pool: Any, auth_user_id: UUID) -> ActorContext:
    async with pool.connection(timeout=5) as connection, connection.transaction():
        identity_cursor = await connection.execute(IDENTITY_SQL, (auth_user_id,))
        identity = await identity_cursor.fetchone()
        if identity is None:
            raise InactiveIdentityError

        access_cursor = await connection.execute(ACCESS_SQL, (auth_user_id, auth_user_id))
        rows = await access_cursor.fetchall()

    return ActorContext(
        auth_user_id=auth_user_id,
        display_name=str(identity[0]),
        is_platform_admin=bool(identity[1]),
        shop_access=tuple(
            ShopAccess(
                business_id=UUID(str(row[0])),
                business_name=str(row[1]),
                shop_id=UUID(str(row[2])),
                shop_name=str(row[3]),
                internal_code=str(row[4]),
                is_owner=bool(row[5]),
                role=str(row[6]),
            )
            for row in rows
        ),
    )
