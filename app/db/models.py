from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Role(str, enum.Enum):
    doctor = "doctor"
    staff = "staff"
    admin = "admin"
    owner = "owner"


class MembershipStatus(str, enum.Enum):
    invited = "invited"
    active = "active"
    suspended = "suspended"
    expired = "expired"
    revoked = "revoked"


class WorkplaceType(str, enum.Enum):
    clinic = "clinic"
    hospital = "hospital"


class WorkplaceStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class SitePlan(str, enum.Enum):
    starter = "starter"
    professional = "professional"
    enterprise = "enterprise"


class Language(str, enum.Enum):
    en = "en"
    fa = "fa"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="ir")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workplaces: Mapped[list[Workplace]] = relationship(back_populates="tenant")


class Workplace(Base):
    __tablename__ = "workplaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("workplaces.id"), nullable=True)
    type: Mapped[WorkplaceType] = mapped_column(Enum(WorkplaceType), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[WorkplaceStatus] = mapped_column(
        Enum(WorkplaceStatus), nullable=False, default=WorkplaceStatus.active
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Tehran")
    default_language: Mapped[Language] = mapped_column(
        Enum(Language), nullable=False, default=Language.en
    )
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[SitePlan] = mapped_column(
        Enum(SitePlan), nullable=False, default=SitePlan.starter
    )
    subscription_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )  # trialing|active|past_due|canceled
    subscription_period_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_last_payment_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="workplaces")
    departments: Mapped[list[Department]] = relationship(back_populates="workplace")
    memberships: Mapped[list[Membership]] = relationship(back_populates="workplace")
    payments: Mapped[list[SubscriptionPayment]] = relationship(back_populates="workplace")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("workplace_id", "code", name="uq_department_code"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workplace_id: Mapped[str] = mapped_column(
        ForeignKey("workplaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WorkplaceStatus] = mapped_column(
        Enum(WorkplaceStatus), nullable=False, default=WorkplaceStatus.active
    )

    workplace: Mapped[Workplace] = relationship(back_populates="departments")
    memberships: Mapped[list[Membership]] = relationship(back_populates="department")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_language: Mapped[Language] = mapped_column(
        Enum(Language), nullable=False, default=Language.en
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus), nullable=False, default=MembershipStatus.active
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    invites: Mapped[list[Invite]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("ix_memberships_user_workplace", "user_id", "workplace_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    workplace_id: Mapped[str] = mapped_column(
        ForeignKey("workplaces.id"), nullable=False, index=True
    )
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus), nullable=False, default=MembershipStatus.active
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    user: Mapped[User] = relationship(back_populates="memberships")
    workplace: Mapped[Workplace] = relationship(back_populates="memberships")
    department: Mapped[Department | None] = relationship(back_populates="memberships")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    membership_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    membership_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="invites")


class AuthAuditEvent(Base):
    __tablename__ = "auth_audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SignupPaymentStatus(str, enum.Enum):
    draft = "draft"
    awaiting_payment = "awaiting_payment"
    paid = "paid"
    failed = "failed"


class SubscriptionPaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    canceled = "canceled"


class SubscriptionPayment(Base):
    __tablename__ = "subscription_payments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workplace_id: Mapped[str] = mapped_column(
        ForeignKey("workplaces.id"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan: Mapped[SitePlan] = mapped_column(Enum(SitePlan), nullable=False)
    amount_rial: Mapped[int] = mapped_column(Integer, nullable=False)
    authority: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[SubscriptionPaymentStatus] = mapped_column(
        Enum(SubscriptionPaymentStatus),
        nullable=False,
        default=SubscriptionPaymentStatus.pending,
    )
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workplace: Mapped[Workplace] = relationship(back_populates="payments")


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="sms")
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SignupRequest(Base):
    """Public self-serve signup / access request (SMS-verified)."""

    __tablename__ = "signup_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    organization: Mapped[str] = mapped_column(String(255), nullable=False)
    org_type: Mapped[str] = mapped_column(String(32), nullable=False, default="clinic")
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan: Mapped[SitePlan] = mapped_column(Enum(SitePlan), nullable=False)
    payment_status: Mapped[SignupPaymentStatus] = mapped_column(
        Enum(SignupPaymentStatus),
        nullable=False,
        default=SignupPaymentStatus.awaiting_payment,
    )
    checkout_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    workplace_id: Mapped[str | None] = mapped_column(ForeignKey("workplaces.id"), nullable=True)
    membership_id: Mapped[str | None] = mapped_column(ForeignKey("memberships.id"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

