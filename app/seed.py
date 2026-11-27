from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    Department,
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


DEMO_PHONES = {
    "user-doctor": "989121000001",
    "user-staff": "989121000002",
    "user-admin": "989121000003",
    "user-owner": "989121000004",
}


DEMO_PASSWORD = "12345678"


async def seed_demo_data(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == "user-doctor"))
    if existing.scalar_one_or_none():
        # Keep phones/password aligned for demos even if users already exist.
        password_hash = hash_password(DEMO_PASSWORD)
        result = await db.execute(select(User).where(User.id.in_(DEMO_PHONES.keys())))
        for user in result.scalars().all():
            if not user.phone:
                user.phone = DEMO_PHONES.get(user.id)
            user.password_hash = password_hash
            user.mfa_enabled = False
            user.mfa_secret = None
        wp_result = await db.execute(
            select(Workplace).where(Workplace.id.in_(["wp-clinic-a", "wp-hospital-b"]))
        )
        now = datetime.now(UTC)
        for wp in wp_result.scalars().all():
            if wp.subscription_period_ends_at is None:
                offset = 22 if "hospital" in wp.id else 14
                wp.subscription_period_ends_at = now + timedelta(days=offset)
            if wp.subscription_last_payment_at is None:
                wp.subscription_last_payment_at = now - timedelta(days=16)
            if not wp.subscription_status:
                wp.subscription_status = "active"
        await db.commit()
        return

    db.add(Tenant(id="tenant-1", name="TebNote Demo Tenant", region="ir"))

    now = datetime.now(UTC)
    db.add_all(
        [
            Workplace(
                id="wp-clinic-a",
                tenant_id="tenant-1",
                type=WorkplaceType.clinic,
                legal_name="Northwind Family Clinic",
                display_name="Clinic A",
                status=WorkplaceStatus.active,
                timezone="Asia/Tehran",
                default_language=Language.en,
                address="12 Healthcare Ave",
                contact_email="admin@clinic-a.example",
                plan=SitePlan.professional,
                subscription_status="active",
                subscription_period_ends_at=now + timedelta(days=14),
                subscription_last_payment_at=now - timedelta(days=16),
                onboarded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            ),
            Workplace(
                id="wp-hospital-b",
                tenant_id="tenant-1",
                type=WorkplaceType.hospital,
                legal_name="Caspian General Hospital",
                display_name="Hospital B",
                status=WorkplaceStatus.active,
                timezone="Asia/Tehran",
                default_language=Language.fa,
                address="88 Medical Blvd",
                contact_email="admin@hospital-b.example",
                plan=SitePlan.enterprise,
                subscription_status="active",
                subscription_period_ends_at=now + timedelta(days=22),
                subscription_last_payment_at=now - timedelta(days=16),
                onboarded_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
            ),
        ]
    )

    db.add_all(
        [
            Department(
                id="dept-radiology",
                workplace_id="wp-hospital-b",
                name="Radiology",
                code="RAD",
                status=WorkplaceStatus.active,
            ),
            Department(
                id="dept-cardiology",
                workplace_id="wp-hospital-b",
                name="Cardiology",
                code="CARD",
                status=WorkplaceStatus.active,
            ),
            Department(
                id="dept-records",
                workplace_id="wp-clinic-a",
                name="Records",
                code="REC",
                status=WorkplaceStatus.active,
            ),
            Department(
                id="dept-general",
                workplace_id="wp-clinic-a",
                name="General Clinic",
                code="GEN",
                status=WorkplaceStatus.active,
            ),
        ]
    )

    password_hash = hash_password(DEMO_PASSWORD)
    users = [
        User(
            id="user-doctor",
            email="doctor",
            phone=DEMO_PHONES["user-doctor"],
            display_name="Dr. Sara Nouri",
            preferred_language=Language.en,
            status=MembershipStatus.active,
            password_hash=password_hash,
        ),
        User(
            id="user-staff",
            email="staff",
            phone=DEMO_PHONES["user-staff"],
            display_name="Reza Karimi",
            preferred_language=Language.fa,
            status=MembershipStatus.active,
            password_hash=password_hash,
        ),
        User(
            id="user-admin",
            email="admin",
            phone=DEMO_PHONES["user-admin"],
            display_name="Mina Abbasi",
            preferred_language=Language.en,
            status=MembershipStatus.active,
            password_hash=password_hash,
        ),
        User(
            id="user-owner",
            email="owner",
            phone=DEMO_PHONES["user-owner"],
            display_name="Platform Owner",
            preferred_language=Language.en,
            status=MembershipStatus.active,
            password_hash=password_hash,
        ),
    ]
    db.add_all(users)

    db.add_all(
        [
            Membership(
                id="mem-doctor-a",
                user_id="user-doctor",
                workplace_id="wp-clinic-a",
                department_id="dept-general",
                roles=["doctor", "admin"],
                status=MembershipStatus.active,
                start_date=date(2026, 1, 10),
            ),
            Membership(
                id="mem-doctor-b",
                user_id="user-doctor",
                workplace_id="wp-hospital-b",
                department_id="dept-cardiology",
                roles=["doctor"],
                status=MembershipStatus.active,
                start_date=date(2026, 2, 1),
            ),
            Membership(
                id="mem-staff-a",
                user_id="user-staff",
                workplace_id="wp-clinic-a",
                department_id="dept-records",
                roles=["staff"],
                status=MembershipStatus.active,
                start_date=date(2026, 1, 12),
            ),
            Membership(
                id="mem-admin-a",
                user_id="user-admin",
                workplace_id="wp-clinic-a",
                roles=["admin"],
                status=MembershipStatus.active,
                start_date=date(2026, 1, 5),
            ),
            Membership(
                id="mem-owner-a",
                user_id="user-owner",
                workplace_id="wp-clinic-a",
                roles=["owner"],
                status=MembershipStatus.active,
                start_date=date(2026, 1, 1),
            ),
        ]
    )

    await db.commit()
