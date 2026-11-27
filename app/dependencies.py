from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import has_any_role, has_permission, permissions_for_roles
from app.core.security import decode_access_token
from app.db.models import Membership, MembershipStatus, User
from app.db.session import get_db
from app.services import auth_service

bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user_id: str
    membership_id: str
    token: str
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    workplace_id: str = ""
    tenant_id: str = ""
    email: str = ""
    display_name: str = ""


async def get_current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    subject = payload.get("sub")
    membership_id = payload.get("membership_id")
    if not subject or not membership_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    result = await db.execute(
        select(User)
        .where(User.id == str(subject))
        .options(
            selectinload(User.memberships).selectinload(Membership.workplace),
        )
    )
    user = result.scalar_one_or_none()
    if user is None or user.status != MembershipStatus.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive.")

    membership = next(
        (
            m
            for m in user.memberships
            if m.id == str(membership_id) and m.status == MembershipStatus.active
        ),
        None,
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Membership inactive or not found.",
        )

    roles = list(membership.roles or [])
    return AuthContext(
        user_id=user.id,
        membership_id=membership.id,
        token=credentials.credentials,
        roles=roles,
        permissions=permissions_for_roles(roles),
        workplace_id=membership.workplace_id,
        tenant_id=membership.workplace.tenant_id if membership.workplace else "",
        email=user.email,
        display_name=user.display_name,
    )


async def get_optional_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthContext | None:
    if credentials is None:
        return None
    try:
        return await get_current_auth(credentials, db)
    except HTTPException:
        return None


def require_roles(*roles: str) -> Callable:
    async def _dep(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if not has_any_role(auth.roles, list(roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return auth

    return _dep


def require_permission(*permissions: str) -> Callable:
    async def _dep(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if not has_permission(auth.permissions, list(permissions)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {', '.join(permissions)}",
            )
        return auth

    return _dep


RequireAdminOrOwner = Depends(require_roles("admin", "owner"))
RequireOwner = Depends(require_roles("owner"))
RequireUsersManage = Depends(require_permission("users.manage"))
RequireSubscriptionManage = Depends(require_permission("subscription.manage"))

DbDep = Depends(get_db)
AuthDep = Depends(get_current_auth)


def client_ip(x_forwarded_for: str | None = Header(default=None)) -> str | None:
    if not x_forwarded_for:
        return None
    return x_forwarded_for.split(",")[0].strip()


def raise_auth_error(exc: auth_service.AuthError) -> None:
    """Always raises — declared for call-site clarity."""
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
