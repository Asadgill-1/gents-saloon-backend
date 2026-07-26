from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request

from app.api.context import context_response, get_context, verified_identity
from app.core.auth import AuthenticationError, VerifiedIdentity
from app.core.authorization import InactiveIdentityError, resolve_actor_context
from app.core.config import Settings
from app.main import create_app


class _AsyncContext:
    def __init__(self, value: object | None = None) -> None:
        self.value = value

    async def __aenter__(self) -> object | None:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(
        self,
        identity_rows: list[tuple[object, ...]],
        access_rows: list[tuple[object, ...]],
    ) -> None:
        self.cursors = [_Cursor(identity_rows), _Cursor(access_rows)]

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def execute(self, _query: str, _params: tuple[UUID, ...]) -> _Cursor:
        return self.cursors.pop(0)


class _Pool:
    def __init__(
        self,
        identity_rows: list[tuple[object, ...]],
        access_rows: list[tuple[object, ...]],
    ) -> None:
        self.connection_value = _Connection(identity_rows, access_rows)

    def connection(self, timeout: int) -> _AsyncContext:
        assert timeout == 5
        return _AsyncContext(self.connection_value)


class _Verifier:
    def __init__(self, identity: VerifiedIdentity | None = None) -> None:
        self.identity = identity

    async def verify(self, _token: str) -> VerifiedIdentity:
        if self.identity is None:
            raise AuthenticationError
        return self.identity


class _FailingPool:
    def connection(self, timeout: int) -> _AsyncContext:
        raise ConnectionError


class _AllowRedis:
    async def eval(self, *_args: object) -> int:
        return 1


def _request(verifier: _Verifier) -> Request:
    app = create_app(Settings(_env_file=None))
    app.state.jwt_verifier = verifier
    return Request({"type": "http", "app": app})


async def test_bearer_header_is_required_and_fails_closed() -> None:
    request = _request(_Verifier())

    with pytest.raises(HTTPException) as missing:
        await verified_identity(request, None)
    with pytest.raises(HTTPException) as malformed:
        await verified_identity(request, "Basic credentials")
    with pytest.raises(HTTPException) as invalid:
        await verified_identity(request, "Bearer bad-token")

    assert missing.value.status_code == 401
    assert malformed.value.status_code == 401
    assert invalid.value.status_code == 401
    assert missing.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_active_owner_sees_every_shop_owned_by_the_business() -> None:
    auth_user_id = uuid4()
    business_id = uuid4()
    shop_one = uuid4()
    shop_two = uuid4()
    pool = _Pool(
        [("Owner", False)],
        [
            (business_id, "Business", shop_one, "Marina", "MAR", True, "owner"),
            (business_id, "Business", shop_two, "JLT", "JLT", True, "owner"),
        ],
    )

    context = await resolve_actor_context(pool, auth_user_id)
    response = context_response(context)

    assert response.auth_user_id == auth_user_id
    assert response.businesses[0].is_owner is True
    assert {shop.id for shop in response.businesses[0].shops} == {shop_one, shop_two}
    assert all(shop.roles == ["owner"] for shop in response.businesses[0].shops)


async def test_staff_sees_only_database_assigned_shop_and_role() -> None:
    auth_user_id = uuid4()
    business_id = uuid4()
    assigned_shop = uuid4()
    pool = _Pool(
        [("Receptionist", False)],
        [
            (
                business_id,
                "Business",
                assigned_shop,
                "Marina",
                "MAR",
                False,
                "receptionist",
            )
        ],
    )

    response = context_response(await resolve_actor_context(pool, auth_user_id))

    assert len(response.businesses) == 1
    assert response.businesses[0].is_owner is False
    assert [shop.id for shop in response.businesses[0].shops] == [assigned_shop]
    assert response.businesses[0].shops[0].roles == ["receptionist"]


async def test_owner_and_membership_roles_are_merged_from_database() -> None:
    auth_user_id = uuid4()
    business_id = uuid4()
    shop_id = uuid4()
    row = (business_id, "Business", shop_id, "Marina", "MAR")
    pool = _Pool(
        [("Owner", False)],
        [
            (*row, True, "owner"),
            (*row, False, "barber"),
        ],
    )

    response = context_response(await resolve_actor_context(pool, auth_user_id))

    assert response.businesses[0].shops[0].roles == ["barber", "owner"]


async def test_platform_admin_status_comes_from_database() -> None:
    auth_user_id = uuid4()
    pool = _Pool([("Admin", True)], [])

    response = context_response(await resolve_actor_context(pool, auth_user_id))

    assert response.is_platform_admin is True
    assert response.businesses == []


async def test_missing_or_inactive_profile_is_denied() -> None:
    with pytest.raises(InactiveIdentityError):
        await resolve_actor_context(_Pool([], []), uuid4())


async def test_endpoint_fails_closed_when_authorization_database_is_unavailable() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.database_pool = _FailingPool()
    app.state.redis = _AllowRedis()
    request = Request({"type": "http", "app": app})

    with pytest.raises(HTTPException) as unavailable:
        await get_context(request, VerifiedIdentity(uuid4()))

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "authorization_unavailable"


def test_context_route_is_registered() -> None:
    app = create_app(Settings(_env_file=None))

    assert "/api/v1/me/context" in app.openapi()["paths"]
