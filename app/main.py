from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import ORJSONResponse

from app.api.router import api_router
from app.config import get_settings
from app.core.kafka import start_kafka, stop_kafka
from app.core.redis import close_redis, get_redis
from app.core.security_headers import SecurityHeadersMiddleware
from app.db.base import Base
from app.db.migrate_runtime import apply_runtime_patches
from app.db.session import SessionLocal, engine
from app.seed import seed_demo_data

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO if settings.debug else logging.WARNING)

    if settings.is_production and settings.allow_otp_demo:
        raise RuntimeError("OTP demo mode must be disabled in production.")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await apply_runtime_patches(engine)

    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("Redis connected.")
    except Exception:
        logger.exception("Redis not available at startup.")

    await start_kafka()

    if settings.seed_on_startup:
        async with SessionLocal() as session:
            await seed_demo_data(session)
            logger.info("Demo auth data seeded (if empty).")

    yield

    await stop_kafka()
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Last added = outermost. TrustedHost should be outer.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Forwarded-For"],
)
if settings.trusted_host_list:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[*settings.trusted_host_list, "test", "testserver"],
    )

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
async def root() -> dict[str, str]:
    payload = {
        "service": settings.app_name,
        "health": f"{settings.api_prefix}/health",
    }
    if not settings.is_production:
        payload["docs"] = "/docs"
    return payload
