from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import AuthContext, require_roles
from app.schemas.owner import SiteInviteCreateRequest
from app.services import admin_service, owner_service
from app.services.admin_service import AdminError
from app.services.owner_service import OwnerError

router = APIRouter(prefix="/owner", tags=["owner"])


def _raise_owner(exc: OwnerError | AdminError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/plans")
async def plans(_: AuthContext = Depends(require_roles("owner"))):
    return owner_service.list_plan_definitions()


@router.get("/health")
async def platform_health(
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.get_platform_health(db)


@router.get("/people")
async def people(
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.people_overview(db)


@router.get("/signups")
async def signups(
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.list_signup_requests(db)


@router.get("/sites")
async def sites(
    auth: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_workplaces(db, auth)


@router.get("/sites/{workplace_id}/usage")
async def site_usage(
    workplace_id: str,
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await owner_service.get_site_usage(db, workplace_id)
    except OwnerError as exc:
        _raise_owner(exc)


@router.get("/sites/{workplace_id}/billing")
async def site_billing(
    workplace_id: str,
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await owner_service.get_site_billing(db, workplace_id)
    except OwnerError as exc:
        _raise_owner(exc)


@router.get("/invoices")
async def invoices(
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.list_invoices(db)


@router.post("/sites/invites")
async def create_invite(
    payload: SiteInviteCreateRequest,
    auth: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await owner_service.create_site_invite(
            db,
            auth,
            workplace_id=payload.workplace_id,
            email=str(payload.email),
            display_name=payload.display_name,
            phone=payload.phone,
            password=payload.password,
            roles=payload.roles or ["admin"],
            delivery=payload.delivery,
        )
    except (OwnerError, AdminError) as exc:
        _raise_owner(exc)


@router.get("/sites/{workplace_id}/invites")
async def list_invites(
    workplace_id: str,
    _: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.list_site_invites(db, workplace_id)


@router.get("/security")
async def security(
    auth: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    return await owner_service.get_security_settings(db, auth)
