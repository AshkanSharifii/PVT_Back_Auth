from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.auth import CamelModel, Language, Role, SitePlan, WorkplaceType

MembershipStatusLit = Literal["invited", "active", "suspended", "expired", "revoked"]
SubscriptionStatusLit = Literal["trialing", "active", "past_due", "canceled"]


class MembershipOut(CamelModel):
    id: str
    user_id: str
    workplace_id: str
    department_id: str | None = None
    roles: list[Role]
    status: MembershipStatusLit
    start_date: date
    end_date: date | None = None


class UserAccountOut(CamelModel):
    id: str
    email: str
    phone: str | None = None
    display_name: str
    preferred_language: Language
    status: MembershipStatusLit
    created_at: datetime
    memberships: list[MembershipOut]
    invite_url: str | None = None
    credentials_sms_sent: bool | None = None


class CreateUserRequest(CamelModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=8, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    preferred_language: Language = "en"
    roles: list[Role] = Field(min_length=1)
    workplace_id: str
    department_id: str | None = None
    delivery: Literal["password", "invite"] = "password"


class SetUserStatusRequest(CamelModel):
    """active | invited | suspended | expired | revoked — also accepts ban→suspended, unban→active."""

    status: str = Field(min_length=1)


class BanUserRequest(CamelModel):
    reason: str | None = None


class UpdateRolesRequest(CamelModel):
    roles: list[Role] = Field(min_length=1)


class CreateDepartmentRequest(CamelModel):
    workplace_id: str
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=64)


class DepartmentAdminOut(CamelModel):
    id: str
    workplace_id: str
    name: str
    code: str
    status: Literal["active", "inactive"]


class CreateWorkplaceRequest(CamelModel):
    type: WorkplaceType
    display_name: str = Field(min_length=1, max_length=255)
    legal_name: str = Field(min_length=1, max_length=255)
    default_language: Language = "en"
    timezone: str = "Asia/Tehran"
    address: str | None = None
    contact_email: EmailStr | None = None
    plan: SitePlan = "starter"


class UpdateWorkplaceRequest(CamelModel):
    type: WorkplaceType | None = None
    display_name: str | None = None
    legal_name: str | None = None
    status: Literal["active", "inactive"] | None = None
    default_language: Language | None = None
    timezone: str | None = None
    address: str | None = None
    contact_email: EmailStr | None = None
    plan: SitePlan | None = None


class WorkplaceAdminOut(CamelModel):
    id: str
    tenant_id: str
    type: WorkplaceType
    legal_name: str
    display_name: str
    status: Literal["active", "inactive"]
    timezone: str
    default_language: Language
    address: str | None = None
    contact_email: str | None = None
    plan: SitePlan
    subscription_status: SubscriptionStatusLit
    subscription_period_ends_at: datetime | None = None
    onboarded_at: datetime


class AdminSubscriptionOut(CamelModel):
    workplace_id: str
    workplace_name: str
    plan: SitePlan
    status: SubscriptionStatusLit
    amount_monthly_usd: float
    amount_monthly_rial: int
    period_ends_at: datetime | None = None
    days_remaining: int
    last_payment_at: datetime | None = None
    # Present when renew/change-plan needs a live ZarinPal redirect.
    payment_url: str | None = None
    payment_authority: str | None = None
    payment_id: str | None = None
    payment_demo: bool | None = None


class ChangeSubscriptionPlanRequest(CamelModel):
    plan: SitePlan


class DashboardStatsOut(CamelModel):
    total_users: int
    active_users: int
    organizations: int
    departments: int
    documents_processed: int = 0
    templates_published: int = 0
    pending_admin_actions: int = 0


class PaginatedUsers(CamelModel):
    items: list[UserAccountOut]
    total: int
    page: int
    page_size: int


class RolesCatalogOut(CamelModel):
    roles: dict[str, list[str]]
    assignable_roles: list[str]
