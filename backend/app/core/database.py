from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings


def create_database_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=1,
        max_size=10,
        open=False,
        timeout=5,
        kwargs={"autocommit": False},
    )
