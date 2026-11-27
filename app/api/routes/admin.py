from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ASSIGNABLE_ROLES, ROLE_PERMISSIONS
from app.db.session import get_db
from app.dependencies import AuthContext, require_permission, require_roles
from app.schemas.admin import (
    CreateDepartmentRequest,
    CreateUserRequest,
    CreateWorkplaceRequest,
    RolesCatalogOut,
    SetUserStatusRequest,
    UpdateRolesRequest,
    UpdateWorkplaceRequest,
)
from app.services import admin_service
from app.services.admin_service import AdminError

router = APIRouter(prefix="/admin", tags=["admin"])


def _raise(exc: AdminError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/roles")
async def roles_catalog(
    _: AuthContext = Depends(require_roles("admin", "owner")),
) -> RolesCatalogOut:
    return RolesCatalogOut(
        roles=ROLE_PERMISSIONS,
        assignable_roles=sorted(ASSIGNABLE_ROLES),
    )


@router.get("/dashboard")
async def dashboard(
    auth: AuthContext = Depends(require_roles("admin", "owner")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.dashboard_stats(db, auth)


@router.get("/users")
async def list_users(
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    status: str | None = None,
):
    return await admin_service.list_users(
        db, auth, page=page, page_size=page_size, search=search, status_filter=status
    )


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.get_user(db, auth, user_id)
    except AdminError as exc:
        _raise(exc)


@router.post("/users")
async def create_user(
    payload: CreateUserRequest,
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.create_user(db, auth, payload)
    except AdminError as exc:
        _raise(exc)


@router.patch("/users/{user_id}/status")
async def set_user_status(
    user_id: str,
    payload: SetUserStatusRequest,
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.set_user_status(db, auth, user_id, payload.status)
    except AdminError as exc:
        _raise(exc)


@router.patch("/users/{user_id}/memberships/{membership_id}/roles")
async def update_roles(
    user_id: str,
    membership_id: str,
    payload: UpdateRolesRequest,
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.update_membership_roles(
            db, auth, user_id, membership_id, list(payload.roles)
        )
    except AdminError as exc:
        _raise(exc)


@router.delete("/users/{user_id}/memberships/{membership_id}")
async def remove_membership(
    user_id: str,
    membership_id: str,
    auth: AuthContext = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.remove_membership(db, auth, user_id, membership_id)
    except AdminError as exc:
        _raise(exc)


@router.get("/workplaces")
async def list_workplaces(
    auth: AuthContext = Depends(require_roles("admin", "owner")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_workplaces(db, auth)


@router.get("/workplaces/{workplace_id}")
async def get_workplace(
    workplace_id: str,
    auth: AuthContext = Depends(require_roles("admin", "owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.get_workplace(db, auth, workplace_id)
    except AdminError as exc:
        _raise(exc)


@router.post("/workplaces")
async def create_workplace(
    payload: CreateWorkplaceRequest,
    auth: AuthContext = Depends(require_roles("owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.create_workplace(db, auth, payload)
    except AdminError as exc:
        _raise(exc)


@router.patch("/workplaces/{workplace_id}")
async def update_workplace(
    workplace_id: str,
    payload: UpdateWorkplaceRequest,
    auth: AuthContext = Depends(require_roles("admin", "owner")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.update_workplace(db, auth, workplace_id, payload)
    except AdminError as exc:
        _raise(exc)


@router.get("/departments")
async def list_departments(
    workplace_id: str | None = None,
    auth: AuthContext = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.list_departments(db, auth, workplace_id)
    except AdminError as exc:
        _raise(exc)


@router.post("/departments")
async def create_department(
    payload: CreateDepartmentRequest,
    auth: AuthContext = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await admin_service.create_department(db, auth, payload)
    except AdminError as exc:
        _raise(exc)


@router.get("/subscription")
async def get_subscription(
    auth: AuthContext = Depends(require_permission("subscription.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.admin_get_subscription(db, auth)
