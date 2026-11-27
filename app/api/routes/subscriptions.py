from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.dependencies import AuthContext, require_permission
from app.schemas.subscription import SubscriptionCheckoutRequest, SubscriptionVerifyRequest
from app.services import subscription_service
from app.services.subscription_service import SubscriptionError

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
settings = get_settings()


def _raise(exc: SubscriptionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/current")
async def current_subscription(
    auth: AuthContext = Depends(require_permission("subscription.manage")),
    db: AsyncSession = Depends(get_db),
    workplace_id: str | None = None,
):
    target = workplace_id or auth.workplace_id
    if "owner" not in auth.roles and target != auth.workplace_id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    try:
        return await subscription_service.get_subscription(db, workplace_id=target)
    except SubscriptionError as exc:
        _raise(exc)


@router.post("/checkout")
async def checkout(
    payload: SubscriptionCheckoutRequest,
    auth: AuthContext = Depends(require_permission("subscription.manage")),
    db: AsyncSession = Depends(get_db),
):
    target = payload.workplace_id or auth.workplace_id
    if "owner" not in auth.roles and target != auth.workplace_id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    try:
        return await subscription_service.start_checkout(
            db,
            workplace_id=target,
            requested_by_user_id=auth.user_id,
            plan_id=payload.plan,
            email=auth.email,
        )
    except SubscriptionError as exc:
        _raise(exc)


@router.post("/verify")
async def verify(
    payload: SubscriptionVerifyRequest,
    auth: AuthContext = Depends(require_permission("subscription.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await subscription_service.verify_checkout(
            db,
            authority=payload.authority,
            gateway_status=payload.status,
        )
    except SubscriptionError as exc:
        _raise(exc)


@router.get("/zarinpal/callback")
async def zarinpal_callback(
    Authority: str = Query(...),
    Status: str = Query("OK"),
    db: AsyncSession = Depends(get_db),
):
    """Browser return URL from ZarinPal. Verifies and redirects to front (or returns JSON in demo)."""
    try:
        result = await subscription_service.verify_checkout(
            db,
            authority=Authority,
            gateway_status=Status,
        )
    except SubscriptionError as exc:
        # Soft failure page payload for local/demo.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    front = settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:3000"
    if result.demo or settings.app_env != "production":
        return result
    return RedirectResponse(
        url=f"{front}/admin/subscription?paid=1&ref={result.ref_id or ''}",
        status_code=302,
    )
