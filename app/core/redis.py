from redis.asyncio import Redis
from app.core.config import settings

JTI_EXPIRY = 3600

token_blocklist = Redis.from_url(settings.REDIS_URL)

async def add_jti_to_blocklist(jti: str) -> None:
    await token_blocklist.set(name=jti, value="", ex=JTI_EXPIRY)


async def token_in_blocklist(jti: str) -> bool:
    value  = await token_blocklist.get(jti)
    return value  is not None