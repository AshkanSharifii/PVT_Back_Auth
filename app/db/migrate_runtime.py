from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Dev-friendly additive patches when Alembic isn't wired yet.
_STATEMENTS = [
    """
    ALTER TABLE workplaces
    ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(32) NOT NULL DEFAULT 'active'
    """,
    """
    ALTER TABLE workplaces
    ADD COLUMN IF NOT EXISTS subscription_period_ends_at TIMESTAMPTZ NULL
    """,
    """
    ALTER TABLE workplaces
    ADD COLUMN IF NOT EXISTS subscription_last_payment_at TIMESTAMPTZ NULL
    """,
]


async def apply_runtime_patches(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for statement in _STATEMENTS:
            try:
                await conn.execute(text(statement))
            except Exception:
                logger.exception("Runtime schema patch failed: %s", statement.strip()[:80])
