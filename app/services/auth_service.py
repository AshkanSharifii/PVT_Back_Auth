from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.ids import new_id, new_opaque_token
from app.core.kafka import publish_auth_event
from app.core.mfa import generate_totp_secret, provisioning_uri, verify_totp
from app.core.permissions import permissions_for_roles
from app.core.plans import PUBLIC_PLANS, get_plan, is_plan_id
from app.core.redis import delete_key, delete_prefix, get_json, rate_limit, set_json
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    refresh_expiry,
    verify_password,
)
from app.core.sms import SmsError, normalize_iran_phone, sms_client
from app.db.models import (
    AuthAuditEvent,
    Department,
    Invite,
    Language,
    Membership,
    MembershipStatus,
    RefreshToken,
    SignupPaymentStatus,
    SignupRequest,
    SitePlan,
    Tenant,
    User,
    Workplace,
    WorkplaceStatus,
    WorkplaceType,
)
from app.schemas.auth import (
    CheckoutSession,
    DepartmentOut,
    MembershipOption,
    PlanOut,
    SessionUser,
    SignupOut,
    SignupRegisterResponse,
    TokenPair,
    WorkplaceOut,
)

settings = get_settings()


class AuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_phone(phone: str) -> str:
    # Canonical storage / redis key form for Iran mobiles.
    return normalize_iran_phone(phone)


def _generate_otp_code() -> str:
    if settings.allow_otp_demo:
        return settings.otp_demo_code
    return str(int.from_bytes(new_opaque_token().encode("utf-8")[:4], "big") % 1_000_000).zfill(6)


async def _revoke_all_refresh_tokens(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    count = 0
    now = datetime.now(UTC)
    for row in result.scalars().all():
        row.revoked_at = now
        count += 1
    await delete_key(f"session:{user_id}")
    await delete_prefix(f"me:{user_id}")
    return count


async def _store_and_send_otp(
    *,
    phone: str,
    purpose: str,
    ip_address: str | None = None,
) -> dict:
    allowed = await rate_limit(
        f"rl:otp:{ip_address or 'unknown'}",
        limit=settings.rate_limit_otp_per_minute,
    )
    if not allowed:
        raise AuthError("Too many OTP requests. Try again later.", status_code=429)

    normalized = _normalize_phone(phone)
    if len(normalized) < 10:
        raise AuthError("Invalid phone number.", status_code=400)

    code = _generate_otp_code()
    await set_json(
        f"otp:{purpose}:{normalized}",
        {"phone": normalized, "code": code, "attempts": 0, "purpose": purpose},
        ttl_seconds=settings.otp_ttl_seconds,
    )
    try:
        send_result = await sms_client.send_otp(phone=normalized, code=code)
    except SmsError as exc:
        await delete_key(f"otp:{purpose}:{normalized}")
        raise AuthError(exc.message, status_code=exc.status_code) from exc

    await publish_auth_event(
        "otp.sent",
        {"phone": normalized, "purpose": purpose, "sent": send_result.get("sent")},
    )
    demo_code = None
    if settings.allow_otp_demo and not send_result.get("sent"):
        # Local-only: never returned in production or when SMS is live.
        demo_code = code
    return {
        "ok": True,
        "expires_in_sec": settings.otp_ttl_seconds,
        "demo_code": demo_code,
        "sms_sent": bool(send_result.get("sent")),
    }


async def _consume_otp(*, phone: str, code: str, purpose: str) -> str:
    normalized = _normalize_phone(phone)
    key = f"otp:{purpose}:{normalized}"
    challenge = await get_json(key)
    if not challenge or challenge.get("phone") != normalized:
        raise AuthError("No active verification for this phone. Resend the code.", status_code=400)
    attempts = int(challenge.get("attempts", 0))
    if attempts >= settings.otp_max_attempts:
        await delete_key(key)
        raise AuthError("Too many attempts. Resend a new code.", status_code=400)
    entered = "".join(ch for ch in code if ch.isdigit())
    if entered != challenge.get("code"):
        challenge["attempts"] = attempts + 1
        await set_json(key, challenge, ttl_seconds=settings.otp_ttl_seconds)
        raise AuthError("Incorrect code.", status_code=400)
    await delete_key(key)
    await publish_auth_event("otp.verified", {"phone": normalized, "purpose": purpose})
    return normalized


async def _audit(
    db: AsyncSession,
    *,
    event_type: str,
    user_id: str | None,
    payload: dict,
    ip_address: str | None = None,
) -> None:
    db.add(
        AuthAuditEvent(
            id=new_id("aud"),
            user_id=user_id,
            event_type=event_type,
            payload=payload,
            ip_address=ip_address,
        )
    )
    await publish_auth_event(event_type, {"user_id": user_id, **payload})


def to_session_user(user: User, membership: Membership) -> SessionUser:
    workplace = membership.workplace
    if workplace is None:
        raise AuthError("Workplace not found.", status_code=500)
    return SessionUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        preferred_language=user.preferred_language.value,  # type: ignore[arg-type]
        roles=list(membership.roles),  # type: ignore[arg-type]
        permissions=permissions_for_roles(list(membership.roles)),
        membership_id=membership.id,
        workplace_id=membership.workplace_id,
        workplace_name=workplace.display_name,
        tenant_id=workplace.tenant_id,
        department_id=membership.department_id,
    )


async def _load_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.memberships)
            .selectinload(Membership.workplace)
            .selectinload(Workplace.tenant),
            selectinload(User.memberships).selectinload(Membership.department),
        )
        .where(func.lower(User.email) == _normalize_email(email))
    )
    return result.scalar_one_or_none()


async def _load_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.memberships)
            .selectinload(Membership.workplace)
            .selectinload(Workplace.tenant),
            selectinload(User.memberships).selectinload(Membership.department),
        )
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


def _pick_membership(user: User, membership_id: str | None) -> Membership:
    active = [m for m in user.memberships if m.status == MembershipStatus.active]
    if membership_id:
        match = next((m for m in active if m.id == membership_id), None)
        if not match:
            raise AuthError("Membership not found or inactive.", status_code=400)
        return match
    if not active:
        raise AuthError("No active workplace membership for this account.", status_code=403)
    return active[0]


async def _issue_tokens(
    db: AsyncSession,
    *,
    user: User,
    membership: Membership,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> TokenPair:
    access = create_access_token(
        subject=user.id,
        membership_id=membership.id,
        extra={
            "tenant_id": membership.workplace.tenant_id if membership.workplace else "",
            "workplace_id": membership.workplace_id,
            "roles": list(membership.roles or []),
            "department_id": membership.department_id,
        },
    )
    refresh = new_opaque_token()
    db.add(
        RefreshToken(
            id=new_id("rt"),
            user_id=user.id,
            membership_id=membership.id,
            token_hash=hash_token(refresh),
            expires_at=refresh_expiry(),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    session = to_session_user(user, membership)
    # Store session pointer in Redis for fast currentSession lookups / revoke.
    await set_json(
        f"session:{user.id}",
        {
            "user_id": user.id,
            "membership_id": membership.id,
            "email": user.email,
        },
        ttl_seconds=settings.refresh_token_expire_days * 24 * 3600,
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        user=session,
    )


async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    membership_id: str | None = None,
    mfa_code: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    allowed = await rate_limit(
        f"rl:login:{ip_address or 'unknown'}",
        limit=settings.rate_limit_login_per_minute,
    )
    if not allowed:
        raise AuthError("Too many login attempts. Try again later.", status_code=429)

    user = await _load_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash):
        await _audit(
            db,
            event_type="login.failed",
            user_id=user.id if user else None,
            payload={"email": _normalize_email(email)},
            ip_address=ip_address,
        )
        await db.commit()
        raise AuthError("Invalid email or password.", status_code=401)

    if user.status != MembershipStatus.active:
        raise AuthError("Account is not active.", status_code=403)

    if user.mfa_enabled:
        if not mfa_code or not user.mfa_secret or not verify_totp(user.mfa_secret, mfa_code):
            await _audit(
                db,
                event_type="login.mfa_failed",
                user_id=user.id,
                payload={},
                ip_address=ip_address,
            )
            await db.commit()
            raise AuthError("MFA code required or invalid.", status_code=401)

    membership = _pick_membership(user, membership_id)
    tokens = await _issue_tokens(
        db,
        user=user,
        membership=membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await _audit(
        db,
        event_type="login.succeeded",
        user_id=user.id,
        payload={"membership_id": membership.id},
        ip_address=ip_address,
    )
    await db.commit()
    return tokens


async def logout(
    db: AsyncSession,
    *,
    user_id: str,
    refresh_token: str | None = None,
) -> None:
    if refresh_token:
        token_h = hash_token(refresh_token)
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_h,
                RefreshToken.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
    await delete_key(f"session:{user_id}")
    await delete_prefix(f"me:{user_id}")
    await _audit(db, event_type="logout", user_id=user_id, payload={})
    await db.commit()


async def logout_all(db: AsyncSession, *, user_id: str) -> dict:
    revoked = await _revoke_all_refresh_tokens(db, user_id)
    await _audit(
        db,
        event_type="logout.all",
        user_id=user_id,
        payload={"revoked": revoked},
    )
    await db.commit()
    return {"ok": True, "revoked": revoked}

async def refresh_session(
    db: AsyncSession,
    *,
    refresh_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    token_h = hash_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_h))
    row = result.scalar_one_or_none()
    if not row or row.revoked_at is not None or row.expires_at < datetime.now(UTC):
        raise AuthError("Invalid or expired refresh token.", status_code=401)

    row.revoked_at = datetime.now(UTC)
    user = await _load_user_by_id(db, row.user_id)
    if not user or user.status != MembershipStatus.active:
        raise AuthError("Account is not active.", status_code=403)
    membership = _pick_membership(user, row.membership_id)
    tokens = await _issue_tokens(
        db,
        user=user,
        membership=membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await db.commit()
    return tokens


async def current_session(db: AsyncSession, *, user_id: str, membership_id: str) -> SessionUser:
    cache_key = f"me:{user_id}:{membership_id}"
    if settings.me_cache_ttl_seconds > 0:
        cached = await get_json(cache_key)
        if cached:
            return SessionUser.model_validate(cached)

    user = await _load_user_by_id(db, user_id)
    if not user or user.status != MembershipStatus.active:
        raise AuthError("Not authenticated.", status_code=401)
    membership = _pick_membership(user, membership_id)
    session = to_session_user(user, membership)
    if settings.me_cache_ttl_seconds > 0:
        await set_json(
            cache_key,
            session.model_dump(by_alias=False),
            ttl_seconds=settings.me_cache_ttl_seconds,
        )
    return session


async def _load_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    normalized = _normalize_phone(phone)
    # Match common stored forms.
    candidates = {normalized}
    if normalized.startswith("98") and len(normalized) == 12:
        candidates.add("0" + normalized[2:])
        candidates.add("+" + normalized)
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.memberships)
            .selectinload(Membership.workplace)
            .selectinload(Workplace.tenant),
            selectinload(User.memberships).selectinload(Membership.department),
        )
        .where(User.phone.in_(candidates))
    )
    return result.scalar_one_or_none()


async def request_password_reset(
    db: AsyncSession,
    *,
    phone: str,
    ip_address: str | None = None,
) -> dict:
    """Send password-reset OTP over SMS (Kavenegar). Always returns ok to avoid enumeration."""
    user = await _load_user_by_phone(db, phone)
    if not user:
        # Do not reveal whether the phone exists.
        return {
            "ok": True,
            "expires_in_sec": settings.otp_ttl_seconds,
            "demo_code": None,
            "sms_sent": False,
        }

    result = await _store_and_send_otp(
        phone=phone,
        purpose="password_reset",
        ip_address=ip_address,
    )
    await _audit(
        db,
        event_type="password.reset_requested",
        user_id=user.id,
        payload={"phone": _normalize_phone(phone)},
        ip_address=ip_address,
    )
    await db.commit()
    return result


async def reset_password(
    db: AsyncSession,
    *,
    phone: str,
    code: str,
    password: str,
) -> None:
    if len(password) < settings.password_min_length:
        raise AuthError(
            f"Password must be at least {settings.password_min_length} characters.",
            status_code=400,
        )
    normalized = await _consume_otp(phone=phone, code=code, purpose="password_reset")
    user = await _load_user_by_phone(db, normalized)
    if not user:
        raise AuthError("Invalid or expired reset code.", status_code=400)
    user.password_hash = hash_password(password)
    revoked = await _revoke_all_refresh_tokens(db, user.id)
    await _audit(
        db,
        event_type="password.reset_completed",
        user_id=user.id,
        payload={"phone": normalized, "revoked_sessions": revoked},
    )
    await db.commit()


async def accept_invite(
    db: AsyncSession,
    *,
    token: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    if len(password) < settings.password_min_length:
        raise AuthError(
            f"Password must be at least {settings.password_min_length} characters.",
            status_code=400,
        )
    token_h = hash_token(token)
    result = await db.execute(select(Invite).where(Invite.token_hash == token_h))
    invite = result.scalar_one_or_none()
    if (
        not invite
        or invite.revoked_at is not None
        or invite.accepted_at is not None
        or invite.expires_at < datetime.now(UTC)
    ):
        raise AuthError("Invalid or expired invite token.", status_code=400)

    user = await _load_user_by_id(db, invite.user_id)
    if not user:
        raise AuthError("Invite not found.", status_code=404)

    membership = next((m for m in user.memberships if m.id == invite.membership_id), None)
    if not membership:
        raise AuthError("No membership available for invite.", status_code=400)

    user.password_hash = hash_password(password)
    user.status = MembershipStatus.active
    membership.status = MembershipStatus.active
    invite.accepted_at = datetime.now(UTC)

    tokens = await _issue_tokens(
        db,
        user=user,
        membership=membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await _audit(
        db,
        event_type="invite.accepted",
        user_id=user.id,
        payload={"membership_id": membership.id},
        ip_address=ip_address,
    )
    await db.commit()
    return tokens


async def switch_membership(
    db: AsyncSession,
    *,
    user_id: str,
    membership_id: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    membership = _pick_membership(user, membership_id)
    tokens = await _issue_tokens(
        db,
        user=user,
        membership=membership,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await _audit(
        db,
        event_type="membership.switched",
        user_id=user.id,
        payload={"membership_id": membership.id},
        ip_address=ip_address,
    )
    await db.commit()
    return tokens


async def change_email(
    db: AsyncSession,
    *,
    user_id: str,
    membership_id: str,
    current_password: str,
    new_email: str,
) -> SessionUser:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.", status_code=400)
    normalized = _normalize_email(new_email)
    if normalized == user.email.lower():
        raise AuthError("New email is the same as the current email.", status_code=400)
    existing = await _load_user_by_email(db, normalized)
    if existing and existing.id != user.id:
        raise AuthError("That email is already in use.", status_code=409)
    user.email = normalized
    membership = _pick_membership(user, membership_id)
    await _audit(
        db,
        event_type="email.changed",
        user_id=user.id,
        payload={"email": normalized},
    )
    await db.commit()
    return to_session_user(user, membership)


async def change_password(
    db: AsyncSession,
    *,
    user_id: str,
    current_password: str,
    new_password: str,
) -> None:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.", status_code=400)
    if len(new_password) < settings.password_min_length:
        raise AuthError(
            f"New password must be at least {settings.password_min_length} characters.",
            status_code=400,
        )
    if new_password == current_password:
        raise AuthError("New password must be different from the current password.", status_code=400)
    user.password_hash = hash_password(new_password)
    revoked = await _revoke_all_refresh_tokens(db, user.id)
    await _audit(
        db,
        event_type="password.changed",
        user_id=user.id,
        payload={"revoked_sessions": revoked},
    )
    await db.commit()


def _user_has_privileged_role(user: User) -> bool:
    return any(
        m.status == MembershipStatus.active
        and any(role in {"admin", "owner"} for role in (m.roles or []))
        for m in user.memberships
    )


async def mfa_setup_start(db: AsyncSession, *, user_id: str) -> dict:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    if not _user_has_privileged_role(user):
        raise AuthError("MFA setup is available for admin/owner accounts.", status_code=403)
    secret = generate_totp_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False
    await delete_prefix(f"me:{user.id}")
    await db.commit()
    return {
        "secret": secret,
        "otpauth_url": provisioning_uri(
            secret=secret,
            email=user.email,
            issuer=settings.mfa_issuer,
        ),
    }


async def mfa_setup_confirm(db: AsyncSession, *, user_id: str, code: str) -> dict:
    user = await _load_user_by_id(db, user_id)
    if not user or not user.mfa_secret:
        raise AuthError("Start MFA setup first.", status_code=400)
    if not verify_totp(user.mfa_secret, code):
        raise AuthError("Invalid MFA code.", status_code=400)
    user.mfa_enabled = True
    await _revoke_all_refresh_tokens(db, user.id)
    await _audit(db, event_type="mfa.enabled", user_id=user.id, payload={})
    await db.commit()
    return {"ok": True, "mfa_enabled": True}


async def mfa_disable(
    db: AsyncSession,
    *,
    user_id: str,
    current_password: str,
    code: str,
) -> dict:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Current password is incorrect.", status_code=400)
    if not user.mfa_enabled or not user.mfa_secret or not verify_totp(user.mfa_secret, code):
        raise AuthError("Invalid MFA code.", status_code=400)
    user.mfa_enabled = False
    user.mfa_secret = None
    await _revoke_all_refresh_tokens(db, user.id)
    await _audit(db, event_type="mfa.disabled", user_id=user.id, payload={})
    await db.commit()
    return {"ok": True, "mfa_enabled": False}


async def list_membership_options(db: AsyncSession, *, user_id: str) -> list[MembershipOption]:
    user = await _load_user_by_id(db, user_id)
    if not user:
        return []
    options: list[MembershipOption] = []
    for membership in user.memberships:
        if membership.status != MembershipStatus.active:
            continue
        workplace = membership.workplace
        department = membership.department
        options.append(
            MembershipOption(
                membership_id=membership.id,
                workplace_id=membership.workplace_id,
                workplace_name=workplace.display_name if workplace else membership.workplace_id,
                workplace_type=(
                    workplace.type.value if workplace else "clinic"  # type: ignore[arg-type]
                ),
                department_id=membership.department_id,
                department_name=department.name if department else None,
                roles=list(membership.roles),  # type: ignore[arg-type]
                status=membership.status.value,
            )
        )
    return options


async def list_my_workplaces(db: AsyncSession, *, user_id: str) -> list[WorkplaceOut]:
    user = await _load_user_by_id(db, user_id)
    if not user:
        return []
    seen: set[str] = set()
    out: list[WorkplaceOut] = []
    for membership in user.memberships:
        if membership.status != MembershipStatus.active:
            continue
        wp = membership.workplace
        if not wp or wp.id in seen:
            continue
        seen.add(wp.id)
        out.append(
            WorkplaceOut(
                id=wp.id,
                tenant_id=wp.tenant_id,
                parent_id=wp.parent_id,
                type=wp.type.value,  # type: ignore[arg-type]
                legal_name=wp.legal_name,
                display_name=wp.display_name,
                status=wp.status.value,  # type: ignore[arg-type]
                timezone=wp.timezone,
                default_language=wp.default_language.value,  # type: ignore[arg-type]
                address=wp.address,
                contact_email=wp.contact_email,
                plan=wp.plan.value,  # type: ignore[arg-type]
                onboarded_at=wp.onboarded_at,
            )
        )
    return out


async def list_my_departments(
    db: AsyncSession, *, workplace_id: str
) -> list[DepartmentOut]:
    result = await db.execute(
        select(Department).where(
            Department.workplace_id == workplace_id,
            Department.status == WorkplaceStatus.active,
        )
    )
    return [
        DepartmentOut(
            id=item.id,
            workplace_id=item.workplace_id,
            name=item.name,
            code=item.code,
            status=item.status.value,  # type: ignore[arg-type]
        )
        for item in result.scalars().all()
    ]


async def send_otp(*, phone: str, ip_address: str | None = None) -> dict:
    """Signup / generic phone verification OTP via Kavenegar."""
    result = await _store_and_send_otp(
        phone=phone,
        purpose="verify",
        ip_address=ip_address,
    )
    return result


async def verify_otp(*, phone: str, code: str) -> None:
    normalized = await _consume_otp(phone=phone, code=code, purpose="verify")
    await set_json(
        f"otp_verified:{normalized}",
        {"phone": normalized, "verified_at": datetime.now(UTC).isoformat()},
        ttl_seconds=1800,
    )


async def create_invite(
    db: AsyncSession,
    *,
    user_id: str,
    membership_id: str,
) -> str:
    token = new_opaque_token()
    db.add(
        Invite(
            id=new_id("inv"),
            user_id=user_id,
            membership_id=membership_id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.invite_token_ttl_seconds),
        )
    )
    await db.commit()
    return token


async def send_invite_sms(
    *,
    phone: str,
    invite_token: str,
    invite_url: str | None = None,
) -> dict:
    link = invite_url or f"/invite?token={invite_token}"
    message = f"TebNote invite: {link}"
    try:
        result = await sms_client.send_text(phone=phone, message=message)
    except SmsError as exc:
        raise AuthError(exc.message, status_code=exc.status_code) from exc
    await publish_auth_event(
        "invite.sms_sent",
        {"phone": _normalize_phone(phone), "sent": result.get("sent")},
    )
    return {"ok": True, "sms_sent": bool(result.get("sent"))}


def list_plans() -> list[PlanOut]:
    return [
        PlanOut(
            id=plan["id"],
            name=plan["name"],
            price_monthly_usd=plan["price_monthly_usd"],
            user_limit=plan["user_limit"],
            department_limit=plan["department_limit"],
            template_limit=plan["template_limit"],
            storage_gb=plan["storage_gb"],
            featured=bool(plan.get("featured")),
        )
        for plan in PUBLIC_PLANS
    ]


async def _require_verified_phone(phone: str) -> str:
    normalized = _normalize_phone(phone)
    verified = await get_json(f"otp_verified:{normalized}")
    if not verified:
        raise AuthError("Phone is not verified. Complete SMS OTP first.", status_code=400)
    return normalized


async def signup_register(
    db: AsyncSession,
    *,
    full_name: str,
    email: str,
    phone: str,
    password: str,
    organization: str,
    org_type: str,
    plan_id: str,
    city: str | None = None,
    country: str | None = None,
    preferred_language: str = "en",
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SignupRegisterResponse:
    if len(password) < settings.password_min_length:
        raise AuthError(
            f"Password must be at least {settings.password_min_length} characters.",
            status_code=400,
        )
    if not is_plan_id(plan_id):
        raise AuthError("Invalid plan.", status_code=400)
    plan = get_plan(plan_id)
    assert plan is not None

    normalized_phone = await _require_verified_phone(phone)
    normalized_email = _normalize_email(str(email))

    if await _load_user_by_email(db, normalized_email):
        raise AuthError("That email is already in use.", status_code=409)
    if await _load_user_by_phone(db, normalized_phone):
        raise AuthError("That phone is already in use.", status_code=409)

    wp_type = WorkplaceType.hospital if org_type == "hospital" else WorkplaceType.clinic
    lang = Language.fa if preferred_language == "fa" else Language.en
    site_plan = SitePlan(plan_id)

    tenant_id = new_id("tenant")
    workplace_id = new_id("wp")
    user_id = new_id("user")
    membership_id = new_id("mem")
    signup_id = new_id("signup")
    checkout_id = new_id("chk")

    db.add(
        Tenant(
            id=tenant_id,
            name=organization.strip(),
            region=(country or "ir").lower()[:32],
        )
    )
    db.add(
        Workplace(
            id=workplace_id,
            tenant_id=tenant_id,
            type=wp_type,
            legal_name=organization.strip(),
            display_name=organization.strip(),
            status=WorkplaceStatus.inactive,  # activated after payment
            timezone="Asia/Tehran",
            default_language=lang,
            contact_email=normalized_email,
            plan=site_plan,
            onboarded_at=datetime.now(UTC),
            address=", ".join([p for p in [city, country] if p]) or None,
        )
    )
    db.add(
        User(
            id=user_id,
            email=normalized_email,
            phone=normalized_phone,
            display_name=full_name.strip(),
            preferred_language=lang,
            status=MembershipStatus.active,
            password_hash=hash_password(password),
        )
    )
    db.add(
        Membership(
            id=membership_id,
            user_id=user_id,
            workplace_id=workplace_id,
            roles=["admin"],
            status=MembershipStatus.active,
            start_date=date.today(),
        )
    )
    # Ensure parents exist before signup_requests FK insert.
    await db.flush()

    db.add(
        SignupRequest(
            id=signup_id,
            full_name=full_name.strip(),
            email=normalized_email,
            phone=normalized_phone,
            organization=organization.strip(),
            org_type=org_type,
            city=city,
            country=country,
            plan=site_plan,
            payment_status=SignupPaymentStatus.awaiting_payment,
            checkout_id=checkout_id,
            user_id=user_id,
            workplace_id=workplace_id,
            membership_id=membership_id,
        )
    )

    await set_json(
        f"checkout:{checkout_id}",
        {
            "signup_id": signup_id,
            "plan_id": plan_id,
            "amount_usd": plan["price_monthly_usd"],
            "email": normalized_email,
        },
        ttl_seconds=60 * 60 * 24,
    )
    await delete_key(f"otp_verified:{normalized_phone}")

    await _audit(
        db,
        event_type="signup.registered",
        user_id=user_id,
        payload={
            "signup_id": signup_id,
            "plan_id": plan_id,
            "workplace_id": workplace_id,
        },
        ip_address=ip_address,
    )
    await db.commit()
    _ = user_agent

    return SignupRegisterResponse(
        signup_id=signup_id,
        payment_status="awaiting_payment",
        user_id=user_id,
        workplace_id=workplace_id,
        checkout=CheckoutSession(
            checkout_id=checkout_id,
            client_secret=f"sms_checkout_{checkout_id}",
            amount_usd=plan["price_monthly_usd"],
            plan_id=plan_id,  # type: ignore[arg-type]
        ),
    )


async def signup_confirm_payment(
    db: AsyncSession,
    *,
    signup_id: str,
    checkout_id: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    result = await db.execute(select(SignupRequest).where(SignupRequest.id == signup_id))
    signup = result.scalar_one_or_none()
    if not signup:
        raise AuthError("Signup not found.", status_code=404)
    if signup.checkout_id != checkout_id:
        raise AuthError("Invalid checkout session.", status_code=400)
    if signup.payment_status == SignupPaymentStatus.paid:
        # Idempotent — just issue session.
        user = await _load_user_by_id(db, signup.user_id or "")
        if not user:
            raise AuthError("Signup user missing.", status_code=500)
        membership = _pick_membership(user, signup.membership_id)
        tokens = await _issue_tokens(
            db, user=user, membership=membership, ip_address=ip_address, user_agent=user_agent
        )
        await db.commit()
        return tokens

    checkout = await get_json(f"checkout:{checkout_id}")
    if not checkout or checkout.get("signup_id") != signup_id:
        raise AuthError("Checkout expired or invalid.", status_code=400)

    workplace = await db.get(Workplace, signup.workplace_id)
    if workplace:
        workplace.status = WorkplaceStatus.active

    signup.payment_status = SignupPaymentStatus.paid
    signup.paid_at = datetime.now(UTC)
    await delete_key(f"checkout:{checkout_id}")

    user = await _load_user_by_id(db, signup.user_id or "")
    if not user:
        raise AuthError("Signup user missing.", status_code=500)
    membership = _pick_membership(user, signup.membership_id)

    tokens = await _issue_tokens(
        db,
        user=user,
        membership=membership,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await _audit(
        db,
        event_type="signup.paid",
        user_id=user.id,
        payload={"signup_id": signup_id, "checkout_id": checkout_id},
        ip_address=ip_address,
    )
    await db.commit()
    return tokens


def _signup_out(item: SignupRequest) -> SignupOut:
    return SignupOut(
        id=item.id,
        full_name=item.full_name,
        email=item.email,
        phone=item.phone,
        organization=item.organization,
        org_type=item.org_type,
        city=item.city,
        country=item.country,
        plan=item.plan.value,  # type: ignore[arg-type]
        payment_status=item.payment_status.value,  # type: ignore[arg-type]
        checkout_id=item.checkout_id,
        user_id=item.user_id,
        workplace_id=item.workplace_id,
        paid_at=item.paid_at,
        created_at=item.created_at,
    )


async def list_signups_for_owner(db: AsyncSession, *, user_id: str) -> list[SignupOut]:
    user = await _load_user_by_id(db, user_id)
    if not user:
        raise AuthError("Not authenticated.", status_code=401)
    is_owner = any(
        m.status == MembershipStatus.active and "owner" in (m.roles or [])
        for m in user.memberships
    )
    if not is_owner:
        raise AuthError("Only the platform Owner can view signups.", status_code=403)
    result = await db.execute(
        select(SignupRequest).order_by(SignupRequest.created_at.desc())
    )
    return [_signup_out(item) for item in result.scalars().all()]