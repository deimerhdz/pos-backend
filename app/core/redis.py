from datetime import datetime, timezone

from redis.asyncio import Redis
from app.core.config import settings

# Fallback cuando no se conoce el `exp` del token revocado.
JTI_EXPIRY = 3600

token_blocklist = Redis.from_url(settings.REDIS_URL)

async def add_jti_to_blocklist(jti: str, expires_at: int | None = None) -> None:
    """Revoca un jti hasta que el token caduque por sí solo.

    El TTL sale del `exp` real del token: con un TTL fijo más corto que la vida
    del token, la clave expiraría en Redis y el token revocado volvería a valer.
    """
    ttl = JTI_EXPIRY
    if expires_at is not None:
        remaining = int(expires_at - datetime.now(timezone.utc).timestamp())
        ttl = max(remaining, 1)  # Redis rechaza ex <= 0
    await token_blocklist.set(name=jti, value="", ex=ttl)


async def token_in_blocklist(jti: str) -> bool:
    value  = await token_blocklist.get(jti)
    return value  is not None