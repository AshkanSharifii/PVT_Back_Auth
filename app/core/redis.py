from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def set_json(key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    try:
        client = await get_redis()
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        # Memory fallback for local runs without Redis.
        _memory_store[key] = (value, __import__("time").time() + ttl_seconds)


async def get_json(key: str) -> dict[str, Any] | None:
    try:
        client = await get_redis()
        raw = await client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        item = _memory_store.get(key)
        if not item:
            return None
        value, expires = item
        if __import__("time").time() > expires:
            _memory_store.pop(key, None)
            return None
        return value


async def delete_key(key: str) -> None:
    try:
        client = await get_redis()
        await client.delete(key)
    except Exception:
        _memory_store.pop(key, None)


async def delete_prefix(prefix: str) -> None:
    try:
        client = await get_redis()
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
    except Exception:
        for key in list(_memory_store):
            if key.startswith(prefix):
                _memory_store.pop(key, None)


_memory_store: dict[str, tuple[dict[str, Any], float]] = {}


async def rate_limit(key: str, *, limit: int, window_seconds: int = 60) -> bool:
    """Return True if allowed, False if rate-limited. Fails open if Redis is down."""
    try:
        client = await get_redis()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        return count <= limit
    except Exception:
        return True
