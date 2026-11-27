import pytest

from app.core.redis import delete_prefix, get_redis
from app.db.base import Base
from app.db.migrate_runtime import apply_runtime_patches
from app.db.session import SessionLocal, engine
from app.seed import seed_demo_data


@pytest.fixture(autouse=True)
async def _reset_between_tests():
    """Reset rate limits + demo users; avoid asyncpg loop bleed across tests."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await apply_runtime_patches(engine)
    except Exception:
        pass
    try:
        await delete_prefix("rl:")
        client = await get_redis()
        async for key in client.scan_iter(match="rl:*"):
            await client.delete(key)
    except Exception:
        pass
    try:
        async with SessionLocal() as session:
            await seed_demo_data(session)
    except Exception:
        pass
    yield
    await engine.dispose()
