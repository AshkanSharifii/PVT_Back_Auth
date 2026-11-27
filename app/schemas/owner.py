from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.auth import CamelModel, SitePlan


class PlanDefinitionOut(CamelModel):
    id: SitePlan
    name: str
    price_monthly_usd: float
    price_monthly_rial: int
    user_limit: int
    department_limit: int
    template_limit: int
    storage_gb: int
    featured: bool = False


class SiteUsageOut(CamelModel):
    workplace_id: str
    plan: SitePlan
    users: dict[str, int]
    departments: dict[str, int]
    templates: dict[str, int]
    storage_gb: dict[str, int]


class SiteBillingOut(CamelModel):
    workplace_id: str
    plan: SitePlan
    status: Literal["trialing", "active", "past_due", "canceled"]
    amount_monthly_usd: float
    amount_monthly_rial: int
    next_invoice_at: datetime | None = None
    last_payment_at: datetime | None = None
    days_remaining: int = 0


class PlatformInvoiceOut(CamelModel):
    id: str
    workplace_id: str
    workplace_name: str
    amount_usd: float
    amount_rial: int
    status: Literal["paid", "open", "void"]
    period_label: str
    created_at: datetime
    ref_id: str | None = None


class PlatformHealthOut(CamelModel):
    api: Literal["ok", "degraded"] = "ok"
    mock_mode: bool
    active_sites: int
    mrr_usd: float
    open_incidents: int = 0
    zarinpal_configured: bool
    sms_configured: bool


class SiteInviteCreateRequest(CamelModel):
    workplace_id: str
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=8, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["admin"])
    delivery: Literal["password", "invite"] = "invite"


class SiteInviteOut(CamelModel):
    id: str
    workplace_id: str
    email: str
    display_name: str
    status: Literal["pending", "accepted", "revoked", "expired"]
    invite_url: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    credentials_sms_sent: bool | None = None


class OwnerSecuritySettingsOut(CamelModel):
    two_factor_enabled: bool
    sessions: list[dict] = Field(default_factory=list)


class SetTwoFactorRequest(CamelModel):
    enabled: bool


class ImpersonationStartRequest(CamelModel):
    user_id: str
    membership_id: str
    reason: str = Field(min_length=3, max_length=500)


class ImpersonationSessionOut(CamelModel):
    active: bool
    target_user_id: str | None = None
    target_email: str | None = None
    target_workplace_id: str | None = None
    target_workplace_name: str | None = None
    owner_user_id: str | None = None
    owner_membership_id: str | None = None
    started_at: datetime | None = None
    reason: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class AuditEventOut(CamelModel):
    id: str
    user_id: str | None = None
    event_type: str
    payload: dict = Field(default_factory=dict)
    ip_address: str | None = None
    created_at: datetime


class PaginatedAudit(CamelModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int


class SignupRequestOwnerOut(CamelModel):
    id: str
    full_name: str
    email: str
    phone: str
    organization: str
    org_type: str
    city: str | None = None
    country: str | None = None
    plan: SitePlan
    payment_status: str
    created_at: datetime
    workplace_id: str | None = None
