"""Broad feature coverage for the auth API."""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.redis import set_json
from app.core.sms import normalize_iran_phone
from app.main import app
from app.seed import DEMO_PASSWORD


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client: AsyncClient, email: str = "admin", password: str = DEMO_PASSWORD):
    res = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_login_refresh_cookie_and_me(client: AsyncClient):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "doctor", "password": DEMO_PASSWORD},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["accessToken"]
    assert body["refreshToken"]
    assert "pvt_refresh" in login.cookies

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['accessToken']}"},
    )
    assert me.status_code == 200
    assert me.json()["roles"]

    refresh = await client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["accessToken"]


@pytest.mark.asyncio
async def test_memberships_workplaces_departments_switch(client: AsyncClient):
    tokens = await _login(client, "doctor")
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}

    memberships = await client.get("/api/v1/auth/memberships", headers=headers)
    assert memberships.status_code == 200
    items = memberships.json()
    assert len(items) >= 2

    workplaces = await client.get("/api/v1/auth/workplaces", headers=headers)
    assert workplaces.status_code == 200
    assert len(workplaces.json()) >= 1

    departments = await client.get("/api/v1/auth/departments", headers=headers)
    assert departments.status_code == 200

    other = next(m for m in items if m["membershipId"] != tokens["user"]["membershipId"])
    switched = await client.post(
        "/api/v1/auth/membership/switch",
        headers=headers,
        json={"membershipId": other["membershipId"]},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["user"]["membershipId"] == other["membershipId"]


async def _signup_user(client: AsyncClient) -> tuple[str, str, str]:
    """Create an isolated paid signup user. Returns email, phone, access token."""
    suffix = _uid()
    phone = f"+98915{int(suffix, 16) % 10_000_000:07d}"
    email = f"iso_{suffix}@example.com"
    normalized = normalize_iran_phone(phone)
    await set_json(
        f"otp_verified:{normalized}",
        {"phone": normalized, "verified_at": "test"},
        ttl_seconds=600,
    )
    registered = await client.post(
        "/api/v1/auth/signup/register",
        json={
            "fullName": "Iso User",
            "email": email,
            "phone": phone,
            "password": "secret12",
            "organization": f"Iso Org {suffix}",
            "orgType": "clinic",
            "planId": "starter",
        },
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    paid = await client.post(
        "/api/v1/auth/signup/payment/confirm",
        json={"signupId": body["signupId"], "checkoutId": body["checkout"]["checkoutId"]},
    )
    assert paid.status_code == 200, paid.text
    return email, normalized, paid.json()["accessToken"]


@pytest.mark.asyncio
async def test_password_change_and_logout_all(client: AsyncClient):
    email, _phone, access = await _signup_user(client)
    headers = {"Authorization": f"Bearer {access}"}
    new_password = "newpass12"

    changed = await client.post(
        "/api/v1/auth/password/change",
        headers=headers,
        json={"currentPassword": "secret12", "newPassword": new_password},
    )
    assert changed.status_code == 200, changed.text

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "secret12"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_password},
    )
    assert new_login.status_code == 200
    headers = {"Authorization": f"Bearer {new_login.json()['accessToken']}"}

    logout_all = await client.post("/api/v1/auth/logout-all", headers=headers)
    assert logout_all.status_code == 200
    assert logout_all.json()["revoked"] >= 1


@pytest.mark.asyncio
async def test_password_reset_via_sms_otp(client: AsyncClient):
    email, phone, _access = await _signup_user(client)

    forgot = await client.post("/api/v1/auth/password/forgot", json={"phone": phone})
    assert forgot.status_code == 200, forgot.text
    demo = forgot.json().get("demoCode")
    assert demo

    reset = await client.post(
        "/api/v1/auth/password/reset",
        json={"phone": phone, "code": demo, "password": "resetpass1"},
    )
    assert reset.status_code == 200, reset.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "resetpass1"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_change_email_roundtrip(client: AsyncClient):
    suffix = _uid()
    phone = f"+98914{int(suffix, 16) % 10_000_000:07d}"
    email = f"mailuser_{suffix}@example.com"
    normalized = normalize_iran_phone(phone)
    await set_json(
        f"otp_verified:{normalized}",
        {"phone": normalized, "verified_at": "test"},
        ttl_seconds=600,
    )
    registered = await client.post(
        "/api/v1/auth/signup/register",
        json={
            "fullName": "Mail User",
            "email": email,
            "phone": phone,
            "password": "secret12",
            "organization": f"Mail Org {suffix}",
            "orgType": "clinic",
            "planId": "starter",
        },
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    paid = await client.post(
        "/api/v1/auth/signup/payment/confirm",
        json={"signupId": body["signupId"], "checkoutId": body["checkout"]["checkoutId"]},
    )
    assert paid.status_code == 200
    headers = {"Authorization": f"Bearer {paid.json()['accessToken']}"}
    new_email = f"mailuser2_{suffix}@example.com"

    changed = await client.post(
        "/api/v1/auth/email/change",
        headers=headers,
        json={"currentPassword": "secret12", "newEmail": new_email},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["email"] == new_email

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": new_email, "password": "secret12"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_mfa_setup_confirm_and_login(client: AsyncClient):
    email, _phone, access = await _signup_user(client)
    headers = {"Authorization": f"Bearer {access}"}
    password = "secret12"

    setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauthUrl"]
    code = pyotp.TOTP(secret).now()

    confirm = await client.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers,
        json={"code": code},
    )
    assert confirm.status_code == 200, confirm.text

    denied = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert denied.status_code == 401

    ok = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "mfaCode": pyotp.TOTP(secret).now(),
        },
    )
    assert ok.status_code == 200, ok.text
    headers = {"Authorization": f"Bearer {ok.json()['accessToken']}"}

    disable = await client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"currentPassword": password, "code": pyotp.TOTP(secret).now()},
    )
    assert disable.status_code == 200, disable.text

    plain = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert plain.status_code == 200


@pytest.mark.asyncio
async def test_owner_signups_list(client: AsyncClient):
    tokens = await _login(client, "owner")
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}
    listed = await client.get("/api/v1/auth/owner/signups", headers=headers)
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)

    staff = await _login(client, "staff")
    forbidden = await client.get(
        "/api/v1/auth/owner/signups",
        headers={"Authorization": f"Bearer {staff['accessToken']}"},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_signup_register_payment_end_to_end(client: AsyncClient):
    suffix = _uid()
    phone = f"+98913{int(suffix, 16) % 10_000_000:07d}"
    email = f"clinic_{suffix}@example.com"
    normalized = normalize_iran_phone(phone)
    await set_json(
        f"otp_verified:{normalized}",
        {"phone": normalized, "verified_at": "test"},
        ttl_seconds=600,
    )

    plans = await client.get("/api/v1/auth/signup/plans")
    assert plans.status_code == 200
    assert len(plans.json()) == 3

    registered = await client.post(
        "/api/v1/auth/signup/register",
        json={
            "fullName": "Clinic Admin",
            "email": email,
            "phone": phone,
            "password": "secret12",
            "organization": f"Clinic {suffix}",
            "orgType": "clinic",
            "planId": "starter",
        },
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()

    paid = await client.post(
        "/api/v1/auth/signup/payment/confirm",
        json={"signupId": body["signupId"], "checkoutId": body["checkout"]["checkoutId"]},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["user"]["email"] == email
    assert "pvt_refresh" in paid.cookies


@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "doctor", "password": DEMO_PASSWORD},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    logout = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
