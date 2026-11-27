from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.auth import CamelModel, SitePlan


class SubscriptionCheckoutRequest(CamelModel):
    plan: SitePlan | None = None  # None = renew current plan
    workplace_id: str | None = None  # owner may target any site; admin uses session site


class SubscriptionCheckoutOut(CamelModel):
    payment_id: str
    authority: str
    payment_url: str
    amount_rial: int
    plan: SitePlan
    demo: bool
    workplace_id: str


class SubscriptionVerifyRequest(CamelModel):
    authority: str
    status: Literal["OK", "NOK"] | None = "OK"


class SubscriptionVerifyOut(CamelModel):
    ok: bool
    payment_id: str
    ref_id: str | None = None
    plan: SitePlan
    workplace_id: str
    period_ends_at: datetime | None = None
    days_remaining: int
    demo: bool = False
