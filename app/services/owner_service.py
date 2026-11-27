from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.ids import new_opaque_token
from app.core.mfa import generate_totp_secret
from app.core.plans import PUBLIC_PLANS, get_plan
from app.core.redis import delete_key, get_json, set_json
from app.core.security import hash_token
from app.db.models import (
    Department,
    Invite,
    Membership,
    MembershipStatus,
    RefreshToken,
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
    ImpersonationSessionOut,
    PlanDefinitionOut,
    PlatformHealthOut,
    PlatformInvoiceOut,
    OwnerSecuritySettingsOut,
    SignupRequestOwnerOut,
    SiteBillingOut,
    SiteInviteOut,
    SiteUsageOut,
)
from app.services import admin_service, notification_service
from app.services.subscription_service import to_subscription_out

settings = get_settings()
_IMPERSONATION_TTL = 60 * 60 * 4


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


def _invite_status(invite: Invite) -> str:
    now = datetime.now(UTC)
    if invite.revoked_at is not None:
        return "revoked"
    if invite.accepted_at is not None:
        return "accepted"
    expires = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=UTC)
    if expires < now:
        return "expired"
    return "pending"


async def list_site_invites(db: AsyncSession, workplace_id: str) -> list[SiteInviteOut]:
    result = await db.execute(
        select(Invite)
        .join(Membership, Membership.id == Invite.membership_id)
        .where(Membership.workplace_id == workplace_id)
        .options(selectinload(Invite.user))
        .order_by(Invite.created_at.desc())
    )
    outs: list[SiteInviteOut] = []
    for invite in result.scalars().unique().all():
        user = invite.user
        outs.append(
            SiteInviteOut(
                id=invite.id,
                workplace_id=workplace_id,
                email=user.email if user else "",
                display_name=user.display_name if user else "",
                status=_invite_status(invite),  # type: ignore[arg-type]
                invite_url=None,
                created_at=invite.created_at,
                expires_at=invite.expires_at,
            )
        )
    return outs


async def resend_site_invite(db: AsyncSession, invite_id: str) -> SiteInviteOut:
    result = await db.execute(
        select(Invite)
        .where(Invite.id == invite_id)
        .options(selectinload(Invite.user))
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise OwnerError("Invite not found.", status_code=404)
    if _invite_status(invite) != "pending":
        raise OwnerError("Only pending invites can be resent.")
    token = new_opaque_token()
    invite.token_hash = hash_token(token)
    invite.expires_at = datetime.now(UTC) + timedelta(seconds=settings.invite_token_ttl_seconds)
    invite_url = f"/invite?token={token}"
    user = invite.user
    mem = await db.get(Membership, invite.membership_id)
    workplace_id = mem.workplace_id if mem else ""
    if user and user.phone:
        await notification_service.send_invite_sms(db, phone=user.phone, invite_url=invite_url)
    await db.commit()
    return SiteInviteOut(
        id=invite.id,
        workplace_id=workplace_id,
        email=user.email if user else "",
        display_name=user.display_name if user else "",
        status="pending",
        invite_url=invite_url,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


async def revoke_site_invite(db: AsyncSession, invite_id: str) -> SiteInviteOut:
    result = await db.execute(
        select(Invite)
        .where(Invite.id == invite_id)
        .options(selectinload(Invite.user))
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise OwnerError("Invite not found.", status_code=404)
    invite.revoked_at = datetime.now(UTC)
    mem = await db.get(Membership, invite.membership_id)
    if mem and mem.status == MembershipStatus.invited:
        mem.status = MembershipStatus.revoked
    user = invite.user
    await db.commit()
    return SiteInviteOut(
        id=invite.id,
        workplace_id=mem.workplace_id if mem else "",
        email=user.email if user else "",
        display_name=user.display_name if user else "",
        status="revoked",
        invite_url=None,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


async def get_security_settings(db: AsyncSession, auth: AuthContext) -> OwnerSecuritySettingsOut:
    user = await db.get(User, auth.user_id)
    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == auth.user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .order_by(RefreshToken.created_at.desc())
        .limit(20)
    )
    sessions = []
    for row in result.scalars().all():
        sessions.append(
            {
                "id": row.id,
                "device": row.user_agent or "Unknown device",
                "location": row.ip_address or "—",
                "lastActiveAt": row.created_at.isoformat() if row.created_at else "",
                "current": row.membership_id == auth.membership_id,
            }
        )
    return OwnerSecuritySettingsOut(
        two_factor_enabled=bool(user and user.mfa_enabled),
        sessions=sessions,
    )


async def set_two_factor_enabled(
    db: AsyncSession,
    auth: AuthContext,
    enabled: bool,
) -> OwnerSecuritySettingsOut:
    user = await db.get(User, auth.user_id)
    if user is None:
        raise OwnerError("User not found.", status_code=404)
    if enabled:
        if not user.mfa_secret:
            user.mfa_secret = generate_totp_secret()
        # Confirm via /auth/mfa/confirm in production; demo enables immediately.
        if settings.allow_otp_demo:
            user.mfa_enabled = True
        else:
            raise OwnerError(
                "Complete MFA setup via POST /api/v1/auth/mfa/setup and /mfa/confirm.",
                status_code=400,
            )
    else:
        user.mfa_enabled = False
        user.mfa_secret = None
    await db.commit()
    return await get_security_settings(db, auth)


async def start_impersonation(
    db: AsyncSession,
    auth: AuthContext,
    *,
    user_id: str,
    membership_id: str,
    reason: str,
) -> ImpersonationSessionOut:
    from app.services import auth_service

    if not reason.strip():
        raise OwnerError("Support reason is required.")
    target = await db.get(User, user_id)
    if target is None:
        raise OwnerError("User not found.", status_code=404)
    result = await db.execute(
        select(Membership)
        .where(Membership.id == membership_id, Membership.user_id == user_id)
        .options(selectinload(Membership.workplace))
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise OwnerError("Membership not found.", status_code=404)
    if "owner" in (membership.roles or []):
        raise OwnerError("Cannot impersonate the platform owner.")
    if membership.status != MembershipStatus.active:
        raise OwnerError("Target membership is not active.")

    tokens = await auth_service._issue_tokens(  # noqa: SLF001 — shared token issuer
        db,
        user=target,
        membership=membership,
    )
    await db.commit()
    started = datetime.now(UTC).isoformat()
    payload = {
        "active": True,
        "targetUserId": target.id,
        "targetEmail": target.email,
        "targetWorkplaceId": membership.workplace_id,
        "targetWorkplaceName": membership.workplace.display_name if membership.workplace else None,
        "ownerUserId": auth.user_id,
        "ownerMembershipId": auth.membership_id,
        "startedAt": started,
        "reason": reason.strip(),
    }
    await set_json(f"impersonation:{auth.user_id}", payload, ttl_seconds=_IMPERSONATION_TTL)
    return ImpersonationSessionOut(
        active=True,
        target_user_id=target.id,
        target_email=target.email,
        target_workplace_id=membership.workplace_id,
        target_workplace_name=membership.workplace.display_name if membership.workplace else None,
        owner_user_id=auth.user_id,
        owner_membership_id=auth.membership_id,
        started_at=datetime.fromisoformat(started),
        reason=reason.strip(),
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )


async def end_impersonation(db: AsyncSession, auth: AuthContext) -> ImpersonationSessionOut:
    from app.services import auth_service

    key = f"impersonation:{auth.user_id}"
    stored = await get_json(key)
    await delete_key(key)
    owner = await db.get(User, auth.user_id)
    if owner is None:
        return ImpersonationSessionOut(active=False)
    result = await db.execute(
        select(Membership)
        .where(Membership.id == auth.membership_id, Membership.user_id == auth.user_id)
        .options(selectinload(Membership.workplace))
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        return ImpersonationSessionOut(active=False)
    tokens = await auth_service._issue_tokens(db, user=owner, membership=membership)
    await db.commit()
    return ImpersonationSessionOut(
        active=False,
        owner_user_id=auth.user_id,
        owner_membership_id=auth.membership_id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        reason=(stored or {}).get("reason") if isinstance(stored, dict) else None,
    )


async def get_impersonation(auth: AuthContext) -> ImpersonationSessionOut:
    stored = await get_json(f"impersonation:{auth.user_id}")
    if not stored or not stored.get("active"):
        return ImpersonationSessionOut(active=False)
    started = stored.get("startedAt")
    return ImpersonationSessionOut(
        active=True,
        target_user_id=stored.get("targetUserId"),
        target_email=stored.get("targetEmail"),
        target_workplace_id=stored.get("targetWorkplaceId"),
        target_workplace_name=stored.get("targetWorkplaceName"),
        owner_user_id=stored.get("ownerUserId"),
        owner_membership_id=stored.get("ownerMembershipId"),
        started_at=datetime.fromisoformat(started) if started else None,
        reason=stored.get("reason"),
    )


async def summarize_workplace(db: AsyncSession, workplace_id: str) -> dict:
    usage = await get_site_usage(db, workplace_id)
    billing = await get_site_billing(db, workplace_id)
    return {"usage": usage, "billing": billing}


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
