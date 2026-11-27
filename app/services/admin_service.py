from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.ids import new_id, new_opaque_token
from app.core.permissions import ASSIGNABLE_ROLES
from app.core.security import hash_password, hash_token
from app.db.models import (
    Department,
    Invite,
    Language,
    Membership,
    MembershipStatus,
    SitePlan,
    Tenant,
    User,
    Workplace,
    WorkplaceStatus,
    WorkplaceType,
)
from app.dependencies import AuthContext
from app.schemas.admin import (
    CreateDepartmentRequest,
    CreateUserRequest,
    CreateWorkplaceRequest,
    DashboardStatsOut,
    DepartmentAdminOut,
    MembershipOut,
    PaginatedUsers,
    UpdateWorkplaceRequest,
    UserAccountOut,
    WorkplaceAdminOut,
)
from app.services import notification_service
from app.services.subscription_service import get_subscription, to_subscription_out

settings = get_settings()


class AdminError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _is_owner_account(user: User) -> bool:
    return any("owner" in (m.roles or []) for m in user.memberships)


def _assert_may_manage(auth: AuthContext, user: User) -> None:
    if "owner" in auth.roles:
        return
    if _is_owner_account(user):
        raise AdminError(
            "Platform owner accounts are not managed from the Admin panel.",
            status_code=403,
        )


def _membership_out(m: Membership) -> MembershipOut:
    return MembershipOut(
        id=m.id,
        user_id=m.user_id,
        workplace_id=m.workplace_id,
        department_id=m.department_id,
        roles=list(m.roles or []),  # type: ignore[arg-type]
        status=m.status.value,  # type: ignore[arg-type]
        start_date=m.start_date,
        end_date=m.end_date,
    )


def _user_out(user: User, *, invite_url: str | None = None, sms_sent: bool | None = None) -> UserAccountOut:
    return UserAccountOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
        preferred_language=user.preferred_language.value,  # type: ignore[arg-type]
        status=user.status.value,  # type: ignore[arg-type]
        created_at=user.created_at,
        memberships=[_membership_out(m) for m in user.memberships],
        invite_url=invite_url,
        credentials_sms_sent=sms_sent,
    )


def _workplace_out(wp: Workplace) -> WorkplaceAdminOut:
    to_subscription_out(wp)  # ensure period defaults
    return WorkplaceAdminOut(
        id=wp.id,
        tenant_id=wp.tenant_id,
        type=wp.type.value,  # type: ignore[arg-type]
        legal_name=wp.legal_name,
        display_name=wp.display_name,
        status=wp.status.value,  # type: ignore[arg-type]
        timezone=wp.timezone,
        default_language=wp.default_language.value,  # type: ignore[arg-type]
        address=wp.address,
        contact_email=wp.contact_email,
        plan=wp.plan.value,  # type: ignore[arg-type]
        subscription_status=wp.subscription_status,  # type: ignore[arg-type]
        subscription_period_ends_at=wp.subscription_period_ends_at,
        onboarded_at=wp.onboarded_at,
    )


async def _load_user(db: AsyncSession, user_id: str) -> User:
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.memberships))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AdminError("User not found.", status_code=404)
    return user


async def list_users(
    db: AsyncSession,
    auth: AuthContext,
    *,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
) -> PaginatedUsers:
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    result = await db.execute(
        select(User).options(selectinload(User.memberships)).order_by(User.created_at.desc())
    )
    users = list(result.scalars().unique().all())

    if "owner" not in auth.roles:
        users = [
            u
            for u in users
            if not _is_owner_account(u)
            and any(m.workplace_id == auth.workplace_id for m in u.memberships)
        ]

    if search:
        q = search.lower()
        users = [
            u
            for u in users
            if q in u.display_name.lower() or q in u.email.lower() or (u.phone and q in u.phone)
        ]
    if status_filter:
        users = [u for u in users if u.status.value == status_filter]

    total = len(users)
    start = (page - 1) * page_size
    items = users[start : start + page_size]
    return PaginatedUsers(
        items=[_user_out(u) for u in items],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_user(db: AsyncSession, auth: AuthContext, user_id: str) -> UserAccountOut:
    user = await _load_user(db, user_id)
    _assert_may_manage(auth, user)
    return _user_out(user)


async def create_user(
    db: AsyncSession,
    auth: AuthContext,
    payload: CreateUserRequest,
) -> UserAccountOut:
    if "owner" not in auth.roles and payload.workplace_id != auth.workplace_id:
        raise AdminError("Admins may only add users to their own site.", status_code=403)

    roles = list(payload.roles)
    if "owner" in roles:
        raise AdminError(
            "Owner is reserved for the platform creator. Assign Admin for clinic managers."
        )
    if not set(roles).issubset(ASSIGNABLE_ROLES):
        raise AdminError("Invalid role in request.")

    wp = await db.get(Workplace, payload.workplace_id)
    if wp is None:
        raise AdminError("Workplace not found.", status_code=404)

    if payload.department_id:
        dept = await db.get(Department, payload.department_id)
        if dept is None or dept.workplace_id != payload.workplace_id:
            raise AdminError("Department not found for this workplace.")

    email = payload.email.strip().lower()
    delivery = payload.delivery
    if delivery == "password" and (not payload.password or len(payload.password) < 8):
        raise AdminError("Password must be at least 8 characters.")
    # Phone is optional: when present, SMS credentials; otherwise admin shares password manually.

    existing = await db.execute(
        select(User).where(User.email == email).options(selectinload(User.memberships))
    )
    user = existing.scalar_one_or_none()
    sms_sent: bool | None = None
    invite_url: str | None = None

    if user:
        _assert_may_manage(auth, user)
        if any(m.workplace_id == payload.workplace_id for m in user.memberships):
            raise AdminError("This person is already a member of this site.")
        membership = Membership(
            id=new_id("mem"),
            user_id=user.id,
            workplace_id=payload.workplace_id,
            department_id=payload.department_id,
            roles=roles,
            status=MembershipStatus.active,
            start_date=date.today(),
        )
        db.add(membership)
        if payload.display_name.strip():
            user.display_name = payload.display_name.strip()
        if payload.phone and not user.phone:
            from app.core.sms import normalize_iran_phone

            user.phone = normalize_iran_phone(payload.phone)
        if payload.password and len(payload.password) >= 8:
            user.password_hash = hash_password(payload.password)
            if payload.phone:
                result = await notification_service.send_user_credentials_sms(
                    db,
                    phone=payload.phone,
                    email=user.email,
                    password=payload.password,
                )
                sms_sent = bool(result.get("sent") or result.get("demo"))
        await db.commit()
        user = await _load_user(db, user.id)
        return _user_out(user, sms_sent=sms_sent)

    invited = delivery == "invite"
    from app.core.sms import normalize_iran_phone

    phone = normalize_iran_phone(payload.phone) if payload.phone else None
    if phone:
        conflict = await db.execute(select(User).where(User.phone == phone))
        if conflict.scalar_one_or_none():
            raise AdminError("Phone number already registered.")

    plain_password = payload.password if not invited else new_opaque_token()[:16]
    user = User(
        id=new_id("user"),
        email=email,
        phone=phone,
        display_name=payload.display_name.strip(),
        preferred_language=Language(payload.preferred_language),
        status=MembershipStatus.invited if invited else MembershipStatus.active,
        password_hash=hash_password(plain_password or new_opaque_token()),
    )
    db.add(user)
    await db.flush()

    membership = Membership(
        id=new_id("mem"),
        user_id=user.id,
        workplace_id=payload.workplace_id,
        department_id=payload.department_id,
        roles=roles,
        status=MembershipStatus.invited if invited else MembershipStatus.active,
        start_date=date.today(),
    )
    db.add(membership)
    await db.flush()

    if invited:
        token = new_opaque_token()
        invite = Invite(
            id=new_id("inv"),
            user_id=user.id,
            membership_id=membership.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.invite_token_ttl_seconds),
        )
        db.add(invite)
        invite_url = f"/invite?token={token}"
        if phone:
            await notification_service.send_invite_sms(db, phone=phone, invite_url=invite_url)
    else:
        assert payload.password
        if payload.phone:
            result = await notification_service.send_user_credentials_sms(
                db,
                phone=payload.phone,
                email=email,
                password=payload.password,
            )
            sms_sent = bool(result.get("sent") or result.get("demo") or not settings.sms_live)
        else:
            sms_sent = False

    await db.commit()
    user = await _load_user(db, user.id)
    return _user_out(user, invite_url=invite_url, sms_sent=sms_sent)


def _normalize_status(raw: str) -> MembershipStatus:
    value = (raw or "").strip().lower()
    aliases = {
        "ban": "suspended",
        "banned": "suspended",
        "unban": "active",
        "unbanned": "active",
        "delete": "revoked",
        "deleted": "revoked",
    }
    value = aliases.get(value, value)
    try:
        return MembershipStatus(value)
    except ValueError as exc:
        raise AdminError(
            "Invalid status. Use active, invited, suspended (ban), expired, or revoked."
        ) from exc


def _assert_admin_site_scope(auth: AuthContext, user: User) -> None:
    """Site admins may only manage people who belong to their workplace."""
    if "owner" in auth.roles:
        return
    if not any(m.workplace_id == auth.workplace_id for m in user.memberships):
        raise AdminError("Admins may only manage users at their own site.", status_code=403)


async def _kick_sessions(db: AsyncSession, user_id: str) -> None:
    from app.services.auth_service import _revoke_all_refresh_tokens

    await _revoke_all_refresh_tokens(db, user_id)


async def set_user_status(
    db: AsyncSession,
    auth: AuthContext,
    user_id: str,
    status: str,
) -> UserAccountOut:
    if user_id == auth.user_id and _normalize_status(status) != MembershipStatus.active:
        raise AdminError("You cannot ban or revoke your own account.", status_code=400)
    user = await _load_user(db, user_id)
    _assert_may_manage(auth, user)
    _assert_admin_site_scope(auth, user)
    new_status = _normalize_status(status)
    user.status = new_status

    # Keep site memberships aligned when banning / revoking.
    if new_status in {MembershipStatus.suspended, MembershipStatus.revoked, MembershipStatus.expired}:
        for membership in user.memberships:
            if "owner" not in auth.roles and membership.workplace_id != auth.workplace_id:
                continue
            if "owner" in (membership.roles or []):
                continue
            membership.status = new_status
        await _kick_sessions(db, user.id)
    elif new_status == MembershipStatus.active:
        for membership in user.memberships:
            if "owner" not in auth.roles and membership.workplace_id != auth.workplace_id:
                continue
            if membership.status in {
                MembershipStatus.suspended,
                MembershipStatus.revoked,
                MembershipStatus.expired,
            }:
                membership.status = MembershipStatus.active

    await db.commit()
    user = await _load_user(db, user_id)
    return _user_out(user)


async def ban_user(db: AsyncSession, auth: AuthContext, user_id: str) -> UserAccountOut:
    return await set_user_status(db, auth, user_id, "suspended")


async def unban_user(db: AsyncSession, auth: AuthContext, user_id: str) -> UserAccountOut:
    return await set_user_status(db, auth, user_id, "active")


async def delete_user(db: AsyncSession, auth: AuthContext, user_id: str) -> dict:
    """Owner soft-deletes a user (revoked + sessions killed). Admins use ban instead."""
    if "owner" not in auth.roles:
        raise AdminError("Only the platform owner can delete users. Admins can ban instead.", status_code=403)
    if user_id == auth.user_id:
        raise AdminError("You cannot delete your own account.", status_code=400)
    user = await _load_user(db, user_id)
    _assert_may_manage(auth, user)
    user.status = MembershipStatus.revoked
    for membership in user.memberships:
        membership.status = MembershipStatus.revoked
    await _kick_sessions(db, user.id)
    await db.commit()
    return {"ok": True, "userId": user_id, "status": "revoked"}


async def update_membership_roles(
    db: AsyncSession,
    auth: AuthContext,
    user_id: str,
    membership_id: str,
    roles: list[str],
) -> UserAccountOut:
    if "owner" in roles:
        raise AdminError("Cannot assign owner role via admin panel.")
    if not set(roles).issubset(ASSIGNABLE_ROLES):
        raise AdminError("Invalid role in request.")
    user = await _load_user(db, user_id)
    _assert_may_manage(auth, user)
    membership = next((m for m in user.memberships if m.id == membership_id), None)
    if membership is None:
        raise AdminError("Membership not found.", status_code=404)
    if "owner" not in auth.roles and membership.workplace_id != auth.workplace_id:
        raise AdminError("Cannot manage memberships outside your site.", status_code=403)
    if "owner" in (membership.roles or []):
        raise AdminError("Platform owner memberships cannot be edited.", status_code=403)
    membership.roles = roles
    await db.commit()
    user = await _load_user(db, user_id)
    return _user_out(user)


async def remove_membership(
    db: AsyncSession,
    auth: AuthContext,
    user_id: str,
    membership_id: str,
) -> UserAccountOut:
    user = await _load_user(db, user_id)
    _assert_may_manage(auth, user)
    membership = next((m for m in user.memberships if m.id == membership_id), None)
    if membership is None:
        raise AdminError("Membership not found.", status_code=404)
    if "owner" not in auth.roles and membership.workplace_id != auth.workplace_id:
        raise AdminError("Cannot manage memberships outside your site.", status_code=403)
    if "owner" in (membership.roles or []):
        raise AdminError("Platform owner memberships cannot be removed.", status_code=403)
    membership.status = MembershipStatus.revoked
    # If no active memberships remain, suspend the account.
    if not any(m.status == MembershipStatus.active for m in user.memberships if m.id != membership_id):
        user.status = MembershipStatus.suspended
        await _kick_sessions(db, user.id)
    await db.commit()
    user = await _load_user(db, user_id)
    return _user_out(user)


async def list_workplaces(db: AsyncSession, auth: AuthContext) -> list[WorkplaceAdminOut]:
    if "owner" in auth.roles:
        result = await db.execute(select(Workplace).order_by(Workplace.display_name))
        workplaces = list(result.scalars().all())
    else:
        wp = await db.get(Workplace, auth.workplace_id)
        workplaces = [wp] if wp else []
    outs = [_workplace_out(wp) for wp in workplaces if wp]
    await db.commit()
    return outs


async def get_workplace(db: AsyncSession, auth: AuthContext, workplace_id: str) -> WorkplaceAdminOut:
    if "owner" not in auth.roles and workplace_id != auth.workplace_id:
        raise AdminError("Forbidden.", status_code=403)
    wp = await db.get(Workplace, workplace_id)
    if wp is None:
        raise AdminError("Workplace not found.", status_code=404)
    out = _workplace_out(wp)
    await db.commit()
    return out


async def create_workplace(
    db: AsyncSession,
    auth: AuthContext,
    payload: CreateWorkplaceRequest,
) -> WorkplaceAdminOut:
    if "owner" not in auth.roles:
        raise AdminError("Only owners can create workplaces.", status_code=403)
    tenant_id = auth.tenant_id or "tenant-1"
    if not await db.get(Tenant, tenant_id):
        db.add(Tenant(id=tenant_id, name="TebNote Tenant", region="ir"))
        await db.flush()
    wp = Workplace(
        id=new_id("wp"),
        tenant_id=tenant_id,
        type=WorkplaceType(payload.type),
        legal_name=payload.legal_name.strip(),
        display_name=payload.display_name.strip(),
        status=WorkplaceStatus.active,
        timezone=payload.timezone,
        default_language=Language(payload.default_language),
        address=payload.address,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        plan=SitePlan(payload.plan),
        subscription_status="active",
        subscription_period_ends_at=datetime.now(UTC) + timedelta(days=30),
        subscription_last_payment_at=datetime.now(UTC),
        onboarded_at=datetime.now(UTC),
    )
    db.add(wp)
    await db.commit()
    return _workplace_out(wp)


async def delete_workplace(db: AsyncSession, auth: AuthContext, workplace_id: str) -> dict:
    if "owner" not in auth.roles:
        raise AdminError("Only owners can delete workplaces.", status_code=403)
    wp = await db.get(Workplace, workplace_id)
    if wp is None:
        raise AdminError("Workplace not found.", status_code=404)
    # Soft-delete: deactivate site and revoke memberships (keep history).
    wp.status = WorkplaceStatus.inactive
    wp.subscription_status = "canceled"
    result = await db.execute(select(Membership).where(Membership.workplace_id == workplace_id))
    for membership in result.scalars().all():
        membership.status = MembershipStatus.revoked
    await db.commit()
    return {"ok": True, "workplaceId": workplace_id, "status": "inactive"}


async def list_audit(
    db: AsyncSession,
    auth: AuthContext,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    from app.db.models import AuthAuditEvent

    page = max(1, page)
    page_size = min(200, max(1, page_size))
    total = (await db.execute(select(func.count()).select_from(AuthAuditEvent))).scalar_one()
    result = await db.execute(
        select(AuthAuditEvent)
        .order_by(AuthAuditEvent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": row.id,
            "userId": row.user_id,
            "eventType": row.event_type,
            "payload": row.payload or {},
            "ipAddress": row.ip_address,
            "createdAt": row.created_at,
        }
        for row in result.scalars().all()
    ]
    return {"items": items, "total": int(total or 0), "page": page, "pageSize": page_size}


async def update_workplace(
    db: AsyncSession,
    auth: AuthContext,
    workplace_id: str,
    payload: UpdateWorkplaceRequest,
) -> WorkplaceAdminOut:
    if "owner" not in auth.roles and workplace_id != auth.workplace_id:
        raise AdminError("Forbidden.", status_code=403)
    wp = await db.get(Workplace, workplace_id)
    if wp is None:
        raise AdminError("Workplace not found.", status_code=404)
    data = payload.model_dump(exclude_unset=True)
    if "plan" in data and "owner" not in auth.roles:
        # Site admins change plan via ZarinPal subscription flow, not free update.
        raise AdminError("Use subscription checkout to change plan.", status_code=403)
    for key, value in data.items():
        if value is None:
            continue
        if key == "type":
            wp.type = WorkplaceType(value)
        elif key == "status":
            wp.status = WorkplaceStatus(value)
        elif key == "default_language":
            wp.default_language = Language(value)
        elif key == "plan":
            wp.plan = SitePlan(value)
        elif key == "contact_email":
            wp.contact_email = str(value)
        elif key == "display_name":
            wp.display_name = value
        elif key == "legal_name":
            wp.legal_name = value
        elif key == "timezone":
            wp.timezone = value
        elif key == "address":
            wp.address = value
    await db.commit()
    return _workplace_out(wp)


async def list_departments(
    db: AsyncSession,
    auth: AuthContext,
    workplace_id: str | None = None,
) -> list[DepartmentAdminOut]:
    target = workplace_id or auth.workplace_id
    if "owner" not in auth.roles and target != auth.workplace_id:
        raise AdminError("Forbidden.", status_code=403)
    result = await db.execute(
        select(Department).where(Department.workplace_id == target).order_by(Department.name)
    )
    return [
        DepartmentAdminOut(
            id=d.id,
            workplace_id=d.workplace_id,
            name=d.name,
            code=d.code,
            status=d.status.value,  # type: ignore[arg-type]
        )
        for d in result.scalars().all()
    ]


async def create_department(
    db: AsyncSession,
    auth: AuthContext,
    payload: CreateDepartmentRequest,
) -> DepartmentAdminOut:
    if "owner" not in auth.roles and payload.workplace_id != auth.workplace_id:
        raise AdminError("Forbidden.", status_code=403)
    wp = await db.get(Workplace, payload.workplace_id)
    if wp is None:
        raise AdminError("Workplace not found.", status_code=404)
    code = payload.code.strip().upper()
    existing = await db.execute(
        select(Department).where(
            Department.workplace_id == payload.workplace_id,
            Department.code == code,
        )
    )
    if existing.scalar_one_or_none():
        raise AdminError("Department code already exists.")
    dept = Department(
        id=new_id("dept"),
        workplace_id=payload.workplace_id,
        name=payload.name.strip(),
        code=code,
        status=WorkplaceStatus.active,
    )
    db.add(dept)
    await db.commit()
    return DepartmentAdminOut(
        id=dept.id,
        workplace_id=dept.workplace_id,
        name=dept.name,
        code=dept.code,
        status=dept.status.value,  # type: ignore[arg-type]
    )


async def dashboard_stats(db: AsyncSession, auth: AuthContext) -> DashboardStatsOut:
    if "owner" in auth.roles:
        total_users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        active_users = (
            await db.execute(
                select(func.count()).select_from(User).where(User.status == MembershipStatus.active)
            )
        ).scalar_one()
        organizations = (
            await db.execute(select(func.count()).select_from(Workplace))
        ).scalar_one()
        departments = (
            await db.execute(select(func.count()).select_from(Department))
        ).scalar_one()
        pending = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(User.status == MembershipStatus.invited)
            )
        ).scalar_one()
    else:
        mem_q = select(Membership.user_id).where(Membership.workplace_id == auth.workplace_id)
        total_users = (
            await db.execute(select(func.count()).select_from(User).where(User.id.in_(mem_q)))
        ).scalar_one()
        active_users = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(User.id.in_(mem_q), User.status == MembershipStatus.active)
            )
        ).scalar_one()
        organizations = 1
        departments = (
            await db.execute(
                select(func.count())
                .select_from(Department)
                .where(Department.workplace_id == auth.workplace_id)
            )
        ).scalar_one()
        pending = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(User.id.in_(mem_q), User.status == MembershipStatus.invited)
            )
        ).scalar_one()

    return DashboardStatsOut(
        total_users=int(total_users or 0),
        active_users=int(active_users or 0),
        organizations=int(organizations or 0),
        departments=int(departments or 0),
        pending_admin_actions=int(pending or 0),
    )


async def admin_get_subscription(db: AsyncSession, auth: AuthContext):
    return await get_subscription(db, workplace_id=auth.workplace_id)


async def admin_renew_subscription(db: AsyncSession, auth: AuthContext):
    from app.services.subscription_service import renew_or_change_plan

    return await renew_or_change_plan(
        db,
        workplace_id=auth.workplace_id,
        requested_by_user_id=auth.user_id,
        plan_id=None,
        email=auth.email,
    )


async def admin_change_subscription_plan(db: AsyncSession, auth: AuthContext, plan: str):
    from app.services.subscription_service import renew_or_change_plan

    return await renew_or_change_plan(
        db,
        workplace_id=auth.workplace_id,
        requested_by_user_id=auth.user_id,
        plan_id=plan,
        email=auth.email,
    )
