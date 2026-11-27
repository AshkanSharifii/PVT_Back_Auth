from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.ids import new_id
from app.core.plans import get_plan, is_plan_id, plan_amount_rial
from app.core.zarinpal import ZarinPalError, zarinpal_client
from app.db.models import (
    SitePlan,
    SubscriptionPayment,
    SubscriptionPaymentStatus,
    Workplace,
)
from app.schemas.admin import AdminSubscriptionOut
from app.schemas.subscription import (
    SubscriptionCheckoutOut,
    SubscriptionVerifyOut,
)

settings = get_settings()


class SubscriptionError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _days_remaining(period_ends_at: datetime | None) -> int:
    if period_ends_at is None:
        return 0
    ends = period_ends_at if period_ends_at.tzinfo else period_ends_at.replace(tzinfo=UTC)
    ms = ends.timestamp() - datetime.now(UTC).timestamp()
    return max(0, int((ms + 86399) // 86400)) if ms > 0 else 0


def _derive_status(workplace: Workplace) -> str:
    if workplace.status.value != "active":
        return "canceled"
    if workplace.subscription_status == "canceled":
        return "canceled"
    days = _days_remaining(workplace.subscription_period_ends_at)
    if days <= 0:
        return "past_due"
    if days <= 3:
        return "past_due"
    return workplace.subscription_status or "active"


def ensure_period_defaults(workplace: Workplace) -> None:
    """Seed a demo period if missing so days-left always works."""
    if workplace.subscription_period_ends_at is None:
        offset = 22 if "hospital" in workplace.id else 14
        workplace.subscription_period_ends_at = datetime.now(UTC) + timedelta(days=offset)
    if workplace.subscription_last_payment_at is None:
        workplace.subscription_last_payment_at = datetime.now(UTC) - timedelta(days=16)
    if not workplace.subscription_status:
        workplace.subscription_status = "active"


def to_subscription_out(workplace: Workplace) -> AdminSubscriptionOut:
    ensure_period_defaults(workplace)
    plan = get_plan(workplace.plan.value) or {}
    status = _derive_status(workplace)
    workplace.subscription_status = status
    return AdminSubscriptionOut(
        workplace_id=workplace.id,
        workplace_name=workplace.display_name,
        plan=workplace.plan.value,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        amount_monthly_usd=float(plan.get("price_monthly_usd") or 0),
        amount_monthly_rial=int(plan.get("price_monthly_rial") or 0),
        period_ends_at=workplace.subscription_period_ends_at,
        days_remaining=_days_remaining(workplace.subscription_period_ends_at),
        last_payment_at=workplace.subscription_last_payment_at,
    )


async def get_workplace(db: AsyncSession, workplace_id: str) -> Workplace:
    result = await db.execute(select(Workplace).where(Workplace.id == workplace_id))
    workplace = result.scalar_one_or_none()
    if workplace is None:
        raise SubscriptionError("Workplace not found.", status_code=404)
    return workplace


async def get_subscription(db: AsyncSession, *, workplace_id: str) -> AdminSubscriptionOut:
    workplace = await get_workplace(db, workplace_id)
    out = to_subscription_out(workplace)
    await db.commit()
    return out


async def renew_or_change_plan(
    db: AsyncSession,
    *,
    workplace_id: str,
    requested_by_user_id: str,
    plan_id: str | None = None,
    email: str | None = None,
) -> AdminSubscriptionOut:
    """Admin UI renew / change-plan.

    Demo (empty merchant): checkout + verify immediately, return updated subscription.
    Live: return current subscription plus paymentUrl for the browser redirect.
    """
    checkout = await start_checkout(
        db,
        workplace_id=workplace_id,
        requested_by_user_id=requested_by_user_id,
        plan_id=plan_id,
        email=email,
    )
    if checkout.demo:
        await verify_checkout(db, authority=checkout.authority, gateway_status="OK")
        out = await get_subscription(db, workplace_id=workplace_id)
        out.payment_demo = True
        return out

    out = await get_subscription(db, workplace_id=workplace_id)
    out.payment_url = checkout.payment_url
    out.payment_authority = checkout.authority
    out.payment_id = checkout.payment_id
    out.payment_demo = False
    return out


async def start_checkout(
    db: AsyncSession,
    *,
    workplace_id: str,
    requested_by_user_id: str,
    plan_id: str | None,
    email: str | None = None,
    mobile: str | None = None,
) -> SubscriptionCheckoutOut:
    workplace = await get_workplace(db, workplace_id)
    target_plan = plan_id or workplace.plan.value
    if not is_plan_id(target_plan):
        raise SubscriptionError("Invalid plan.")
    amount = plan_amount_rial(target_plan)
    description = f"TebNote {target_plan} subscription — {workplace.display_name}"

    try:
        zp = await zarinpal_client.request_payment(
            amount_rial=amount,
            description=description,
            email=email,
            mobile=mobile,
            metadata={"workplace_id": workplace_id, "plan": target_plan},
        )
    except ZarinPalError as exc:
        raise SubscriptionError(exc.message, status_code=exc.status_code) from exc

    payment = SubscriptionPayment(
        id=new_id("pay"),
        workplace_id=workplace_id,
        requested_by_user_id=requested_by_user_id,
        plan=SitePlan(target_plan),
        amount_rial=amount,
        authority=zp["authority"],
        status=SubscriptionPaymentStatus.pending,
        description=description,
    )
    db.add(payment)
    await db.commit()

    return SubscriptionCheckoutOut(
        payment_id=payment.id,
        authority=zp["authority"],
        payment_url=zp["payment_url"],
        amount_rial=amount,
        plan=target_plan,  # type: ignore[arg-type]
        demo=bool(zp.get("demo")),
        workplace_id=workplace_id,
    )


async def verify_checkout(
    db: AsyncSession,
    *,
    authority: str,
    gateway_status: str | None = "OK",
) -> SubscriptionVerifyOut:
    result = await db.execute(
        select(SubscriptionPayment)
        .where(SubscriptionPayment.authority == authority)
        .options(selectinload(SubscriptionPayment.workplace))
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise SubscriptionError("Payment not found.", status_code=404)

    if payment.status == SubscriptionPaymentStatus.paid:
        workplace = payment.workplace
        ensure_period_defaults(workplace)
        return SubscriptionVerifyOut(
            ok=True,
            payment_id=payment.id,
            ref_id=payment.ref_id,
            plan=payment.plan.value,  # type: ignore[arg-type]
            workplace_id=payment.workplace_id,
            period_ends_at=workplace.subscription_period_ends_at,
            days_remaining=_days_remaining(workplace.subscription_period_ends_at),
            demo=bool(authority.startswith("DEMO")),
        )

    if gateway_status and gateway_status.upper() == "NOK":
        payment.status = SubscriptionPaymentStatus.canceled
        await db.commit()
        raise SubscriptionError("Payment canceled by user.", status_code=400)

    try:
        verified = await zarinpal_client.verify_payment(
            authority=authority,
            amount_rial=payment.amount_rial,
        )
    except ZarinPalError as exc:
        payment.status = SubscriptionPaymentStatus.failed
        await db.commit()
        raise SubscriptionError(exc.message, status_code=exc.status_code) from exc

    now = datetime.now(UTC)
    workplace = await get_workplace(db, payment.workplace_id)
    ensure_period_defaults(workplace)

    base = workplace.subscription_period_ends_at or now
    if base < now:
        base = now
    workplace.subscription_period_ends_at = base + timedelta(days=30)
    workplace.subscription_last_payment_at = now
    workplace.subscription_status = "active"
    workplace.plan = payment.plan

    payment.status = SubscriptionPaymentStatus.paid
    payment.ref_id = str(verified.get("ref_id") or "")
    payment.paid_at = now
    await db.commit()

    return SubscriptionVerifyOut(
        ok=True,
        payment_id=payment.id,
        ref_id=payment.ref_id,
        plan=payment.plan.value,  # type: ignore[arg-type]
        workplace_id=payment.workplace_id,
        period_ends_at=workplace.subscription_period_ends_at,
        days_remaining=_days_remaining(workplace.subscription_period_ends_at),
        demo=bool(verified.get("demo")),
    )
