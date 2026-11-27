from __future__ import annotations

import secrets
import uuid


def new_id(prefix: str | None = None) -> str:
    value = uuid.uuid4().hex
    return f"{prefix}_{value}" if prefix else value


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)
