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

        renewed = await client.post(
            "/api/v1/admin/subscription/renew",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["daysRemaining"] >= 30

        changed = await client.post(
            "/api/v1/admin/subscription/change-plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "enterprise"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["plan"] == "enterprise"


@pytest.mark.asyncio
async def test_admin_ban_change_role_and_owner_delete():
    transport = ASGITransport(app=app)
    suffix = uuid.uuid4().hex[:8]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_token = await _login(client, "admin")
        created = await client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "email": f"temp_{suffix}@example.com",
                "displayName": "Temp User",
                "phone": f"+98913{int(suffix, 16) % 10_000_000:07d}",
                "password": "TempPass99",
                "roles": ["staff"],
                "workplaceId": "wp-clinic-a",
                "delivery": "password",
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        membership_id = created.json()["memberships"][0]["id"]

        roles = await client.patch(
            f"/api/v1/admin/users/{user_id}/memberships/{membership_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"roles": ["doctor", "staff"]},
        )
        assert roles.status_code == 200, roles.text
        assert set(roles.json()["memberships"][0]["roles"]) == {"doctor", "staff"}

        banned = await client.post(
            f"/api/v1/admin/users/{user_id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={},
        )
        assert banned.status_code == 200, banned.text
        assert banned.json()["status"] == "suspended"

        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": f"temp_{suffix}@example.com", "password": "TempPass99"},
        )
        assert blocked.status_code == 403

        unbanned = await client.post(
            f"/api/v1/admin/users/{user_id}/unban",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert unbanned.status_code == 200
        assert unbanned.json()["status"] == "active"

        owner_token = await _login(client, "owner")
        deleted = await client.delete(
            f"/api/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "revoked"

        admin_delete = await client.delete(
            f"/api/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_delete.status_code == 403


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
