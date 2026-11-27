from __future__ import annotations

import logging
from typing import Any

import httpx
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from app.config import get_settings

logger = logging.getLogger(__name__)


class SmsError(Exception):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_iran_phone(phone: str) -> str:
    """Normalize to E.164 digits without '+' for Kavenegar (98XXXXXXXXXX)."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    try:
        parsed = phonenumbers.parse(raw, "IR")
        if not phonenumbers.is_possible_number(parsed):
            raise NumberParseException(NumberParseException.NOT_A_NUMBER, "invalid")
        e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        return e164.lstrip("+")
    except NumberParseException:
        # Fallback for partially typed demo values.
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits.startswith("0098"):
            digits = digits[2:]
        if digits.startswith("0") and len(digits) == 11:
            digits = "98" + digits[1:]
        if digits.startswith("9") and len(digits) == 10:
            digits = "98" + digits
        return digits


class KavenegarClient:
    """Kavenegar SMS client. Leave api_key empty until you fill it in .env."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.kavenegar_api_key.strip())

    @property
    def base_url(self) -> str:
        key = self.settings.kavenegar_api_key.strip()
        return f"https://api.kavenegar.com/v1/{key}"

    async def send_otp(self, *, phone: str, code: str) -> dict[str, Any]:
        receptor = normalize_iran_phone(phone)
        if not self.configured:
            logger.warning(
                "KAVENEGAR_API_KEY is empty — OTP for %s not sent via SMS (demo/local).",
                receptor,
            )
            return {"ok": True, "provider": "kavenegar", "sent": False, "demo": True}

        template = self.settings.kavenegar_otp_template.strip()
        if template:
            return await self._verify_lookup(receptor=receptor, token=code, template=template)
        message = self.settings.kavenegar_otp_message_template.format(code=code)
        return await self._sms_send(receptor=receptor, message=message)

    async def send_text(self, *, phone: str, message: str) -> dict[str, Any]:
        receptor = normalize_iran_phone(phone)
        if not self.configured:
            logger.warning(
                "KAVENEGAR_API_KEY is empty — SMS to %s skipped (demo/local).",
                receptor,
            )
            return {"ok": True, "provider": "kavenegar", "sent": False, "demo": True}
        return await self._sms_send(receptor=receptor, message=message)

    async def _verify_lookup(
        self, *, receptor: str, token: str, template: str
    ) -> dict[str, Any]:
        url = f"{self.base_url}/verify/lookup.json"
        params = {
            "receptor": receptor,
            "token": token,
            "template": template,
        }
        return await self._request(url, params)

    async def _sms_send(self, *, receptor: str, message: str) -> dict[str, Any]:
        url = f"{self.base_url}/sms/send.json"
        params: dict[str, str] = {
            "receptor": receptor,
            "message": message,
        }
        sender = self.settings.kavenegar_sender.strip()
        if sender:
            params["sender"] = sender
        return await self._request(url, params)

    async def _request(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        timeout = self.settings.kavenegar_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, data=params)
        except httpx.HTTPError as exc:
            logger.exception("Kavenegar request failed")
            raise SmsError("SMS provider unavailable.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SmsError("Invalid response from SMS provider.") from exc

        # Kavenegar: return.status == 200 means success
        status = None
        if isinstance(payload, dict):
            status = (payload.get("return") or {}).get("status")
        if response.status_code >= 400 or status not in (200, "200", None):
            message = "SMS send failed."
            if isinstance(payload, dict):
                message = (payload.get("return") or {}).get("message") or message
            logger.error("Kavenegar error: %s", payload)
            raise SmsError(str(message))

        return {
            "ok": True,
            "provider": "kavenegar",
            "sent": True,
            "demo": False,
            "raw": payload,
        }


sms_client = KavenegarClient()
