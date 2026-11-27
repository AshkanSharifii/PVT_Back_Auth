from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.ids import new_id
from app.core.sms import SmsError, normalize_iran_phone, sms_client
from app.db.models import NotificationLog

logger = logging.getLogger(__name__)
settings = get_settings()


async def _log(
    db: AsyncSession,
    *,
    purpose: str,
    recipient: str,
    payload: dict[str, Any],
    sent: bool,
) -> NotificationLog:
    row = NotificationLog(
        id=new_id("notif"),
        channel="sms",
        purpose=purpose,
        recipient=normalize_iran_phone(recipient) or recipient,
        payload=payload,
        sent=sent,
    )
    db.add(row)
    return row


async def send_otp_sms(
    db: AsyncSession,
    *,
    phone: str,
    code: str,
) -> dict[str, Any]:
    """OTP channel — used by auth; also logged for audit."""
    try:
        result = await sms_client.send_otp(phone=phone, code=code)
        sent = bool(result.get("sent"))
    except SmsError as exc:
        await _log(
            db,
            purpose="otp",
            recipient=phone,
            payload={"error": exc.message},
            sent=False,
        )
        raise
    await _log(
        db,
        purpose="otp",
        recipient=phone,
        payload={"demo": bool(result.get("demo")), "sent": sent},
        sent=sent,
    )
    return result


async def send_user_credentials_sms(
    db: AsyncSession,
    *,
    phone: str,
    email: str,
    password: str,
) -> dict[str, Any]:
    """SMS new login email + password when an admin adds a user."""
    message = settings.kavenegar_credentials_message_template.format(
        email=email,
        password=password,
    )
    try:
        result = await sms_client.send_text(phone=phone, message=message)
        sent = bool(result.get("sent"))
    except SmsError as exc:
        logger.warning("Credentials SMS failed for %s: %s", phone, exc.message)
        await _log(
            db,
            purpose="user_credentials",
            recipient=phone,
            payload={"email": email, "error": exc.message},
            sent=False,
        )
        return {"ok": False, "sent": False, "error": exc.message}

    await _log(
        db,
        purpose="user_credentials",
        recipient=phone,
        payload={"email": email, "demo": bool(result.get("demo")), "sent": sent},
        sent=sent,
    )
    return {"ok": True, "sent": sent, "demo": bool(result.get("demo"))}


async def send_invite_sms(
    db: AsyncSession,
    *,
    phone: str,
    invite_url: str,
) -> dict[str, Any]:
    message = f"TebNote invite: {invite_url}"
    try:
        result = await sms_client.send_text(phone=phone, message=message)
        sent = bool(result.get("sent"))
    except SmsError as exc:
        await _log(
            db,
            purpose="invite",
            recipient=phone,
            payload={"error": exc.message},
            sent=False,
        )
        return {"ok": False, "sent": False, "error": exc.message}
    await _log(
        db,
        purpose="invite",
        recipient=phone,
        payload={"invite_url": invite_url, "demo": bool(result.get("demo"))},
        sent=sent,
    )
    return {"ok": True, "sent": sent, "demo": bool(result.get("demo"))}
