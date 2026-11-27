import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.seed import DEMO_PASSWORD


@pytest.mark.asyncio
async def test_health_and_login():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "doctor", "password": DEMO_PASSWORD},
        )
        assert login.status_code == 200
        body = login.json()
        assert "accessToken" in body
        assert body["user"]["email"] == "doctor"

        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['accessToken']}"},
        )
        assert me.status_code == 200
        assert me.json()["membershipId"] == "mem-doctor-a"


@pytest.mark.asyncio
async def test_otp_demo_without_kavenegar_key():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sent = await client.post(
            "/api/v1/auth/otp/send",
            json={"phone": "+989121234567"},
        )
        assert sent.status_code == 200
        payload = sent.json()
        assert payload["ok"] is True
        assert payload["smsSent"] is False
        assert payload["demoCode"]

        verify = await client.post(
            "/api/v1/auth/otp/verify",
            json={"phone": "+989121234567", "code": payload["demoCode"]},
        )
        assert verify.status_code == 200
        assert verify.json()["ok"] is True


@pytest.mark.asyncio
async def test_full_signup_flow():
    import uuid

    from app.core.redis import set_json
    from app.core.sms import normalize_iran_phone

    transport = ASGITransport(app=app)
    suffix = uuid.uuid4().hex[:8]
    phone = f"+98912{int(suffix, 16) % 10_000_000:07d}"
    email = f"admin_{suffix}@example.com"
    normalized = normalize_iran_phone(phone)
    await set_json(
        f"otp_verified:{normalized}",
        {"phone": normalized, "verified_at": "test"},
        ttl_seconds=600,
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        plans = await client.get("/api/v1/auth/signup/plans")
        assert plans.status_code == 200
        assert len(plans.json()) == 3

        registered = await client.post(
            "/api/v1/auth/signup/register",
            json={
                "fullName": "New Admin",
                "email": email,
                "phone": phone,
                "password": "secret12",
                "organization": "Sunrise Clinic",
                "orgType": "clinic",
                "city": "Tehran",
                "country": "IR",
                "planId": "professional",
            },
        )
        assert registered.status_code == 200, registered.text
        body = registered.json()
        assert body["paymentStatus"] == "awaiting_payment"
        assert body["checkout"]["checkoutId"]

        paid = await client.post(
            "/api/v1/auth/signup/payment/confirm",
            json={
                "signupId": body["signupId"],
                "checkoutId": body["checkout"]["checkoutId"],
            },
        )
        assert paid.status_code == 200, paid.text
        session = paid.json()
        assert session["accessToken"]
        assert session["user"]["roles"] == ["admin"]
