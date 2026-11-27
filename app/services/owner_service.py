from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.plans import PUBLIC_PLANS, get_plan
from app.db.models import (
    Department,
    Membership,
    MembershipStatus,
    SignupRequest,
    SubscriptionPayment,
    SubscriptionPaymentStatus,
    User,
    Workplace,
    WorkplaceStatus,
)
from app.dependencies import AuthContext
from app.schemas.admin import CreateUserRequest
from app.schemas.owner import (
    PlanDefinitionOut,
    PlatformHealthOut,
    PlatformInvoiceOut,
    OwnerSecuritySettingsOut,
    SignupRequestOwnerOut,
    SiteBillingOut,
    SiteInviteOut,
    SiteUsageOut,
)
from app.services import admin_service
from app.services.subscription_service import to_subscription_out

settings = get_settings()


class OwnerError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def list_plan_definitions() -> list[PlanDefinitionOut]:
    return [
        PlanDefinitionOut(
            id=p["id"],  # type: ignore[arg-type]
            name=p["name"],
            price_monthly_usd=float(p["price_monthly_usd"]),
            price_monthly_rial=int(p["price_monthly_rial"]),
            user_limit=int(p["user_limit"]),
            department_limit=int(p["department_limit"]),
            template_limit=int(p["template_limit"]),
            storage_gb=int(p["storage_gb"]),
            featured=bool(p.get("featured")),
        )
        for p in PUBLIC_PLANS
    ]


async def get_site_usage(db: AsyncSession, workplace_id: str) -> SiteUsageOut:
    wp = await db.get(Workplace, workplace_id)
    if wp is None:
        raise OwnerError("Workplace not found.", status_code=404)
    plan = get_plan(wp.plan.value) or {}
    users_used = (
        await db.execute(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.workplace_id == workplace_id,
                Membership.status == MembershipStatus.active,
            )
        )
    ).scalar_one()
    depts_used = (
        await db.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.workplace_id == workplace_id)
        )
    ).scalar_one()
    return SiteUsageOut(
        workplace_id=workplace_id,
        plan=wp.plan.value,  # type: ignore[arg-type]
        users={"used": int(users_used or 0), "limit": int(plan.get("user_limit") or 0)},
        departments={
            "used": int(depts_used or 0),
            "limit": int(plan.get("department_limit") or 0),
        },
        templates={"used": 0, "limit": int(plan.get("template_limit") or 0)},
        storage_gb={"used": 0, "limit": int(plan.get("storage_gb") or 0)},
    )


async def get_site_billing(db: AsyncSession, workplace_id: str) -> SiteBillingOut:
    wp = await db.get(Workplace, workplace_id)
    if wp is None:
        raise OwnerError("Workplace not found.", status_code=404)
    sub = to_subscription_out(wp)
    await db.commit()
    return SiteBillingOut(
        workplace_id=workplace_id,
        plan=sub.plan,
        status=sub.status,
        amount_monthly_usd=sub.amount_monthly_usd,
        amount_monthly_rial=sub.amount_monthly_rial,
        next_invoice_at=sub.period_ends_at,
        last_payment_at=sub.last_payment_at,
        days_remaining=sub.days_remaining,
    )


async def list_invoices(db: AsyncSession) -> list[PlatformInvoiceOut]:
    result = await db.execute(
        select(SubscriptionPayment)
        .order_by(SubscriptionPayment.created_at.desc())
        .limit(100)
    )
    payments = list(result.scalars().all())
    outs: list[PlatformInvoiceOut] = []
    for pay in payments:
        wp = await db.get(Workplace, pay.workplace_id)
        plan = get_plan(pay.plan.value) or {}
        status = "paid" if pay.status == SubscriptionPaymentStatus.paid else "open"
        if pay.status in (SubscriptionPaymentStatus.canceled, SubscriptionPaymentStatus.failed):
            status = "void"
        outs.append(
            PlatformInvoiceOut(
                id=pay.id,
                workplace_id=pay.workplace_id,
                workplace_name=wp.display_name if wp else pay.workplace_id,
                amount_usd=float(plan.get("price_monthly_usd") or 0),
                amount_rial=pay.amount_rial,
                status=status,  # type: ignore[arg-type]
                period_label=pay.created_at.strftime("%Y-%m"),
                created_at=pay.created_at,
                ref_id=pay.ref_id,
            )
        )
    return outs


async def get_platform_health(db: AsyncSession) -> PlatformHealthOut:
    active_sites = (
        await db.execute(
            select(func.count())
            .select_from(Workplace)
            .where(Workplace.status == WorkplaceStatus.active)
        )
    ).scalar_one()
    result = await db.execute(
        select(Workplace).where(Workplace.status == WorkplaceStatus.active)
    )
    mrr = 0.0
    for wp in result.scalars().all():
        plan = get_plan(wp.plan.value)
        if plan:
            mrr += float(plan["price_monthly_usd"])
    return PlatformHealthOut(
        api="ok",
        mock_mode=not settings.sms_live or not settings.zarinpal_live,
        active_sites=int(active_sites or 0),
        mrr_usd=mrr,
        open_incidents=0,
        zarinpal_configured=settings.zarinpal_live,
        sms_configured=settings.sms_live,
    )


async def create_site_invite(
    db: AsyncSession,
    auth: AuthContext,
    *,
    workplace_id: str,
    email: str,
    display_name: str,
    phone: str | None,
    password: str | None,
    roles: list[str],
    delivery: str,
) -> SiteInviteOut:
    created = await admin_service.create_user(
        db,
        auth,
        CreateUserRequest(
            email=email,
            display_name=display_name,
            phone=phone,
            password=password,
            roles=roles,  # type: ignore[arg-type]
            workplace_id=workplace_id,
            delivery=delivery,  # type: ignore[arg-type]
        ),
    )
    mem = created.memberships[0] if created.memberships else None
    return SiteInviteOut(
        id=mem.id if mem else created.id,
        workplace_id=workplace_id,
        email=created.email,
        display_name=created.display_name,
        status="pending" if created.status == "invited" else "accepted",
        invite_url=created.invite_url,
        created_at=created.created_at,
        expires_at=None,
        credentials_sms_sent=created.credentials_sms_sent,
    )


async def list_site_invites(db: AsyncSession, workplace_id: str) -> list[SiteInviteOut]:
    result = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.workplace_id == workplace_id,
            Membership.status == MembershipStatus.invited,
        )
    )
    users = list(result.scalars().unique().all())
    outs: list[SiteInviteOut] = []
    for user in users:
        mem = next(
            (m for m in user.memberships if m.workplace_id == workplace_id),
            None,
        )
        outs.append(
            SiteInviteOut(
                id=mem.id if mem else user.id,
                workplace_id=workplace_id,
                email=user.email,
                display_name=user.display_name,
                status="pending",
                invite_url=None,
                created_at=user.created_at,
                expires_at=None,
            )
        )
    return outs


async def get_security_settings(db: AsyncSession, auth: AuthContext) -> OwnerSecuritySettingsOut:
    user = await db.get(User, auth.user_id)
    return OwnerSecuritySettingsOut(
        two_factor_enabled=bool(user and user.mfa_enabled),
        sessions=[],
    )


async def list_signup_requests(db: AsyncSession) -> list[SignupRequestOwnerOut]:
    result = await db.execute(select(SignupRequest).order_by(SignupRequest.created_at.desc()))
    return [
        SignupRequestOwnerOut(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            phone=row.phone,
            organization=row.organization,
            org_type=row.org_type,
            city=row.city,
            country=row.country,
            plan=row.plan.value,  # type: ignore[arg-type]
            payment_status=row.payment_status.value,
            created_at=row.created_at,
            workplace_id=row.workplace_id,
        )
        for row in result.scalars().all()
    ]


async def people_overview(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    active = (
        await db.execute(
            select(func.count()).select_from(User).where(User.status == MembershipStatus.active)
        )
    ).scalar_one()
    invited = (
        await db.execute(
            select(func.count()).select_from(User).where(User.status == MembershipStatus.invited)
        )
    ).scalar_one()
    sites = (await db.execute(select(func.count()).select_from(Workplace))).scalar_one()
    return {
        "totalUsers": int(total or 0),
        "activeUsers": int(active or 0),
        "invitedUsers": int(invited or 0),
        "sites": int(sites or 0),
        "generatedAt": datetime.now(UTC).isoformat(),
    }
