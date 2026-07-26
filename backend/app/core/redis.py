from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
