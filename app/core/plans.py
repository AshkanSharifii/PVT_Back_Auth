from __future__ import annotations

from typing import Any

# price_monthly_rial is what ZarinPal charges (Iranian Rial).
PUBLIC_PLANS: list[dict[str, Any]] = [
    {
        "id": "starter",
        "name": "Starter",
        "price_monthly_usd": 99,
        "price_monthly_rial": 4_200_000,
        "user_limit": 10,
        "department_limit": 3,
        "template_limit": 5,
        "storage_gb": 25,
        "featured": False,
    },
    {
        "id": "professional",
        "name": "Professional",
        "price_monthly_usd": 249,
        "price_monthly_rial": 10_500_000,
        "user_limit": 50,
        "department_limit": 15,
        "template_limit": 25,
        "storage_gb": 200,
        "featured": True,
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "price_monthly_usd": 799,
        "price_monthly_rial": 33_600_000,
        "user_limit": 500,
        "department_limit": 100,
        "template_limit": 200,
        "storage_gb": 2000,
        "featured": False,
    },
]


def get_plan(plan_id: str) -> dict[str, Any] | None:
    return next((plan for plan in PUBLIC_PLANS if plan["id"] == plan_id), None)


def is_plan_id(plan_id: str) -> bool:
    return plan_id in {"starter", "professional", "enterprise"}


def plan_amount_rial(plan_id: str) -> int:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_id}")
    return int(plan["price_monthly_rial"])
