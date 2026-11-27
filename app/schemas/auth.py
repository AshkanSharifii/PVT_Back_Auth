from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


Role = Literal["doctor", "staff", "admin", "owner"]
Language = Literal["en", "fa"]
WorkplaceType = Literal["clinic", "hospital"]
SitePlan = Literal["starter", "professional", "enterprise"]


class LoginRequest(CamelModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    membership_id: str | None = None
    mfa_code: str | None = None


class SessionUser(CamelModel):
    id: str
    email: str
    display_name: str
    preferred_language: Language
    roles: list[Role]
    permissions: list[str]
    membership_id: str
    workplace_id: str
    workplace_name: str
    tenant_id: str
    department_id: str | None = None


class TokenPair(CamelModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: SessionUser


class RefreshRequest(CamelModel):
    refresh_token: str | None = None


class PasswordResetRequest(CamelModel):
    """Request password reset OTP via SMS (no email)."""

    phone: str = Field(min_length=8)


class PasswordResetConfirm(CamelModel):
    """Confirm reset with SMS OTP code."""

    phone: str = Field(min_length=8)
    code: str = Field(min_length=4, max_length=8)
    password: str = Field(min_length=8)


class SendInviteSmsRequest(CamelModel):
    phone: str = Field(min_length=8)
    invite_token: str = Field(min_length=8)
    invite_url: str | None = None


class AcceptInviteRequest(CamelModel):
    token: str
    password: str = Field(min_length=8)


class SwitchMembershipRequest(CamelModel):
    membership_id: str


class ChangeEmailRequest(CamelModel):
    current_password: str
    new_email: EmailStr


class ChangePasswordRequest(CamelModel):
    current_password: str
    new_password: str = Field(min_length=8)


class MfaSetupResponse(CamelModel):
    secret: str
    otpauth_url: str


class MfaCodeRequest(CamelModel):
    code: str = Field(min_length=6, max_length=8)


class MfaDisableRequest(CamelModel):
    current_password: str
    code: str = Field(min_length=6, max_length=8)


class LogoutAllResponse(CamelModel):
    ok: Literal[True] = True
    revoked: int


class OkResponse(CamelModel):
    ok: Literal[True] = True
    demo_token: str | None = None
    sms_sent: bool | None = None


class MembershipOption(CamelModel):
    membership_id: str
    workplace_id: str
    workplace_name: str
    workplace_type: WorkplaceType
    department_id: str | None = None
    department_name: str | None = None
    roles: list[Role]
    status: str


class WorkplaceOut(CamelModel):
    id: str
    tenant_id: str
    parent_id: str | None = None
    type: WorkplaceType
    legal_name: str
    display_name: str
    status: Literal["active", "inactive"]
    timezone: str
    default_language: Language
    address: str | None = None
    contact_email: str | None = None
    plan: SitePlan
    onboarded_at: datetime


class DepartmentOut(CamelModel):
    id: str
    workplace_id: str
    name: str
    code: str
    status: Literal["active", "inactive"]


class SendOtpRequest(CamelModel):
    phone: str = Field(min_length=8)


class VerifyOtpRequest(CamelModel):
    phone: str = Field(min_length=8)
    code: str = Field(min_length=4, max_length=8)


class SendOtpResponse(CamelModel):
    ok: Literal[True] = True
    expires_in_sec: int
    demo_code: str | None = None
    sms_sent: bool = False


OrgType = Literal["clinic", "hospital", "other"]
PaymentStatus = Literal["draft", "awaiting_payment", "paid", "failed"]


class PlanOut(CamelModel):
    id: SitePlan
    name: str
    price_monthly_usd: int
    user_limit: int
    department_limit: int
    template_limit: int
    storage_gb: int
    featured: bool = False


class SignupRegisterRequest(CamelModel):
    """Complete self-serve signup after SMS OTP verification."""

    full_name: str = Field(min_length=2)
    email: EmailStr
    phone: str = Field(min_length=8)
    password: str = Field(min_length=8)
    organization: str = Field(min_length=2)
    org_type: OrgType = "clinic"
    city: str | None = None
    country: str | None = None
    plan_id: SitePlan
    preferred_language: Language = "en"


class CheckoutSession(CamelModel):
    checkout_id: str
    status: Literal["requires_payment"] = "requires_payment"
    client_secret: str
    amount_usd: int
    plan_id: SitePlan


class SignupRegisterResponse(CamelModel):
    signup_id: str
    payment_status: PaymentStatus
    checkout: CheckoutSession
    user_id: str
    workplace_id: str


class SignupPaymentConfirmRequest(CamelModel):
    signup_id: str
    checkout_id: str


class SignupOut(CamelModel):
    id: str
    full_name: str
    email: str
    phone: str
    organization: str
    org_type: str
    city: str | None = None
    country: str | None = None
    plan: SitePlan
    payment_status: PaymentStatus
    checkout_id: str | None = None
    user_id: str | None = None
    workplace_id: str | None = None
    paid_at: datetime | None = None
    created_at: datetime

