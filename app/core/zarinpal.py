from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ZarinPalError(Exception):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ZarinPalClient:
    """ZarinPal v4 client. Empty merchant id → local demo authorities."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return self.settings.zarinpal_live

    async def request_payment(
        self,
        *,
        amount_rial: int,
        description: str,
        callback_url: str | None = None,
        email: str | None = None,
        mobile: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if amount_rial < 1000:
            raise ZarinPalError("Amount too small for ZarinPal.", status_code=400)

        if not self.configured:
            authority = f"DEMO{secrets_token()}"
            logger.warning("ZARINPAL_MERCHANT_ID empty — demo authority %s", authority)
            return {
                "ok": True,
                "demo": True,
                "authority": authority,
                "payment_url": f"{self.settings.zarinpal_startpay_base}{authority}",
                "fee": 0,
            }

        payload: dict[str, Any] = {
            "merchant_id": self.settings.zarinpal_merchant_id.strip(),
            "amount": amount_rial,
            "callback_url": callback_url or self.settings.zarinpal_callback_url,
            "description": description[:500],
            "currency": self.settings.zarinpal_currency,
        }
        meta: dict[str, Any] = dict(metadata or {})
        if email:
            meta["email"] = email
        if mobile:
            meta["mobile"] = mobile
        if meta:
            payload["metadata"] = meta

        data = await self._post(self.settings.zarinpal_request_url, payload)
        code = (data.get("data") or {}).get("code")
        authority = (data.get("data") or {}).get("authority")
        if code != 100 or not authority:
            message = (data.get("errors") or {}).get("message") or "ZarinPal request failed."
            raise ZarinPalError(str(message))
        return {
            "ok": True,
            "demo": False,
            "authority": authority,
            "payment_url": f"{self.settings.zarinpal_startpay_base}{authority}",
            "fee": (data.get("data") or {}).get("fee"),
            "raw": data,
        }

    async def verify_payment(self, *, authority: str, amount_rial: int) -> dict[str, Any]:
        if authority.startswith("DEMO"):
            return {
                "ok": True,
                "demo": True,
                "code": 100,
                "ref_id": f"DEMO-REF-{authority[-8:]}",
                "card_pan": None,
            }

        if not self.configured:
            raise ZarinPalError("ZarinPal is not configured.", status_code=503)

        payload = {
            "merchant_id": self.settings.zarinpal_merchant_id.strip(),
            "amount": amount_rial,
            "authority": authority,
        }
        data = await self._post(self.settings.zarinpal_verify_url, payload)
        code = (data.get("data") or {}).get("code")
        # 100 = first verify success, 101 = already verified
        if code not in (100, 101):
            message = (data.get("errors") or {}).get("message") or "Payment verification failed."
            raise ZarinPalError(str(message), status_code=400)
        return {
            "ok": True,
            "demo": False,
            "code": code,
            "ref_id": str((data.get("data") or {}).get("ref_id") or ""),
            "card_pan": (data.get("data") or {}).get("card_pan"),
            "raw": data,
        }

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("ZarinPal HTTP error")
            raise ZarinPalError("Payment gateway unavailable.") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ZarinPalError("Invalid payment gateway response.") from exc


def secrets_token() -> str:
    import secrets

    return secrets.token_hex(16).upper()


zarinpal_client = ZarinPalClient()
