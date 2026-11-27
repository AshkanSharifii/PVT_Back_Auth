import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.seed import DEMO_PASSWORD


async def _login(client: AsyncClient, email: str) -> str:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": DEMO_PASSWORD},
    )
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


@pytest.mark.asyncio
async def test_roles_catalog_and_rbac():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        doctor_token = await _login(client, "doctor")
        # doctor has admin role on clinic-a via mem-doctor-a
        roles = await client.get(
            "/api/v1/admin/roles",
            headers={"Authorization": f"Bearer {doctor_token}"},
        )
        assert roles.status_code == 200
        body = roles.json()
        assert "admin" in body["roles"]
        assert "owner" not in body["assignableRoles"]

        staff_token = await _login(client, "staff")
        denied = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {staff_token}"},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_admin_create_user_sends_credentials_sms_demo():
    transport = ASGITransport(app=app)
    suffix = uuid.uuid4().hex[:8]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin")
        created = await client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "email": f"nurse_{suffix}@example.com",
                "displayName": "New Nurse",
                "phone": f"+98912{int(suffix, 16) % 10_000_000:07d}",
                "password": "TempPass99",
                "roles": ["staff"],
                "workplaceId": "wp-clinic-a",
                "departmentId": "dept-records",
                "delivery": "password",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["email"] == f"nurse_{suffix}@example.com"
        assert body["credentialsSmsSent"] is True
        assert any(m["roles"] == ["staff"] for m in body["memberships"])


@pytest.mark.asyncio
async def test_admin_subscription_and_zarinpal_demo_checkout():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin")
        sub = await client.get(
            "/api/v1/admin/subscription",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sub.status_code == 200, sub.text
        data = sub.json()
        assert data["workplaceId"] == "wp-clinic-a"
        assert data["daysRemaining"] >= 0
        assert data["amountMonthlyRial"] > 0

        checkout = await client.post(
            "/api/v1/subscriptions/checkout",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "professional"},
        )
        assert checkout.status_code == 200, checkout.text
        pay = checkout.json()
        assert pay["demo"] is True
        assert pay["authority"].startswith("DEMO")
        assert pay["paymentUrl"]

        verified = await client.post(
            "/api/v1/subscriptions/verify",
            headers={"Authorization": f"Bearer {token}"},
            json={"authority": pay["authority"], "status": "OK"},
        )
        assert verified.status_code == 200, verified.text
        done = verified.json()
        assert done["ok"] is True
        assert done["daysRemaining"] >= 30


@pytest.mark.asyncio
async def test_owner_panel_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "owner")
        health = await client.get(
            "/api/v1/owner/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert health.status_code == 200
        assert "activeSites" in health.json()

        sites = await client.get(
            "/api/v1/owner/sites",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sites.status_code == 200
        assert len(sites.json()) >= 2

        people = await client.get(
            "/api/v1/owner/people",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert people.status_code == 200
        assert people.json()["totalUsers"] >= 4

        usage = await client.get(
            "/api/v1/owner/sites/wp-clinic-a/usage",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert usage.status_code == 200
        assert usage.json()["users"]["used"] >= 1

        # staff cannot access owner panel
        staff = await _login(client, "staff")
        blocked = await client.get(
            "/api/v1/owner/health",
            headers={"Authorization": f"Bearer {staff}"},
        )
        assert blocked.status_code == 403
