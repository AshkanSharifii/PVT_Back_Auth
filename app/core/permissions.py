from __future__ import annotations

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "doctor": [
        "patients.read",
        "patients.create",
        "patients.update",
        "encounters.read",
        "encounters.create",
        "encounters.update",
        "audio.record",
        "transcript.review",
        "transcript.approve",
        "clinical.confirm",
        "document.create",
        "document.finalize",
        "document.route",
        "document.read",
    ],
    "staff": [
        "patients.read",
        "patients.create",
        "encounters.read",
        "encounters.create",
        "document.route",
        "document.read",
        "inbox.manage",
    ],
    "admin": [
        "patients.read",
        "patients.create",
        "patients.update",
        "encounters.read",
        "encounters.create",
        "encounters.update",
        "document.route",
        "document.read",
        "inbox.manage",
        "users.manage",
        "departments.manage",
        "organizations.manage",
        "templates.manage",
        "audit.read",
        "organization.manage",
        "governance.manage",
        "settings.manage",
        "subscription.manage",
        "billing.read",
    ],
    # Platform / tenant owner — sites, people, org governance. No clinical chart access.
    "owner": [
        "users.manage",
        "departments.manage",
        "organizations.manage",
        "templates.manage",
        "audit.read",
        "organization.manage",
        "governance.manage",
        "settings.manage",
        "subscription.manage",
        "billing.read",
        "billing.manage",
        "sites.manage",
        "platform.read",
    ],
}

VALID_ROLES = frozenset(ROLE_PERMISSIONS.keys())
ASSIGNABLE_ROLES = frozenset({"doctor", "staff", "admin"})  # owner is platform-reserved


def permissions_for_roles(roles: list[str]) -> list[str]:
    granted: set[str] = set()
    for role in roles:
        for permission in ROLE_PERMISSIONS.get(role, []):
            granted.add(permission)
    return sorted(granted)


def has_permission(granted: list[str], required: str | list[str]) -> bool:
    needed = [required] if isinstance(required, str) else list(required)
    return all(item in granted for item in needed)


def has_any_role(roles: list[str], required: str | list[str]) -> bool:
    needed = [required] if isinstance(required, str) else list(required)
    return any(item in roles for item in needed)
