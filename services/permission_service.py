"""
Capability-based permissions using RolePermission.route_endpoint.

Capability keys use prefix ``capability:`` (e.g. ``capability:incentive_simulator``).
Admins always pass. Legacy role slugs remain as fallback until DB is seeded.
"""
from __future__ import annotations

from typing import FrozenSet

# Well-known capability keys (stored as capability:<name> in role_permission.route_endpoint)
CAP_INCENTIVE_SIMULATOR = "capability:incentive_simulator"
CAP_HOLDINGS_REPORT = "capability:holdings_report"
CAP_PRACTICE_ANALYTICS = "capability:practice_analytics"
CAP_CLIENT_DATA_OPS = "capability:client_data_ops"
CAP_CLIENT_DATA_SENSITIVE = "capability:client_data_sensitive"

_OPS_MANAGER_SLUGS: FrozenSet[str] = frozenset({"ops_manager", "sales_ops_manager"})
_INCENTIVE_SALES_SLUGS: FrozenSet[str] = frozenset(
    {
        "sales",
        "sales_executive",
        "bd",
        "business_development",
        "sales_ops_manager",
    }
)


def _role_slug(user) -> str:
    if not user:
        return ""
    ro = getattr(user, "role_obj", None)
    if ro is not None and getattr(ro, "name", None):
        return (ro.name or "").strip().lower().replace(" ", "_").replace("-", "_")
    leg = getattr(user, "role", None)
    if isinstance(leg, str):
        return leg.strip().lower().replace(" ", "_").replace("-", "_")
    return ""


def user_has_capability(user, capability: str) -> bool:
    """True if user has explicit capability or admin."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if hasattr(user, "has_route_access") and user.has_route_access(capability):
        return True
    return False


def user_is_ops_manager(user) -> bool:
    if not user:
        return False
    return _role_slug(user) in _OPS_MANAGER_SLUGS


def user_sees_incentive_sales_simulator(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user_has_capability(user, CAP_INCENTIVE_SIMULATOR):
        return True
    if (
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
    ):
        return True
    slug = _role_slug(user)
    if slug and slug in _INCENTIVE_SALES_SLUGS:
        return True
    # Legacy single-user fallback — remove after seed_permission_capabilities assigns roles
    from access_control import user_is_sharveen_legacy

    return user_is_sharveen_legacy(user)


def user_can_access_holdings_report(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user_has_capability(user, CAP_HOLDINGS_REPORT):
        return True
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
        or user_is_ops_manager(user)
    )


# Coarse roles keep legacy navbar visibility. Stale RolePermission rows from early
# seeds (e.g. clients.list_clients) must NOT put them in allowlist mode — that hides
# current endpoints like clients_v2.list_clients. Research / custom roles stay allowlisted.
_LEGACY_COARSE_MENU_SLUGS: FrozenSet[str] = frozenset(
    {
        "admin",
        "manager",
        "advisor",
        "ops_manager",
        "sales_ops_manager",
    }
)


def role_is_menu_restricted(user) -> bool:
    """True when the role has explicit non-capability route permissions (allowlist mode).

    Manager / advisor / ops keep legacy coarse menus even if old route rows exist.
    """
    if not user:
        return False
    if getattr(user, "is_admin", False):
        return False
    if (
        getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
        or user_is_ops_manager(user)
    ):
        return False
    if _role_slug(user) in _LEGACY_COARSE_MENU_SLUGS:
        return False
    role_obj = getattr(user, "role_obj", None)
    if not role_obj:
        return False
    for endpoint, allowed in role_obj.get_all_permissions().items():
        if allowed and not str(endpoint).startswith("capability:"):
            return True
    return False


def nav_can(user, endpoint: str, policy: str = "permission") -> bool:
    """Whether a navbar item should be visible for this user."""
    if policy == "universal":
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if role_is_menu_restricted(user):
        return bool(getattr(user, "has_route_access", lambda _e: False)(endpoint))
    # Unrestricted roles keep legacy coarse visibility (no regression).
    return True


def nav_group_visible(user, group_key: str, config: dict | None = None) -> bool:
    """True when any item in the nav group should be shown."""
    from nav_registry import get_nav_group

    group = get_nav_group(group_key)
    if not group:
        return False
    cfg = config or {}
    if group.nav_flag and not cfg.get(group.nav_flag):
        return False
    for item in group.items:
        if item.nav_flag and not cfg.get(item.nav_flag):
            continue
        if nav_can(user, item.endpoint, item.policy):
            return True
    return False


def user_can_view_client_ops(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user_has_capability(user, CAP_CLIENT_DATA_OPS):
        return True
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
    )


def user_can_view_sensitive_client_data(user) -> bool:
    """Financial / portfolio detail (holdings amounts, trade qty, etc.).

    Advisors keep this so they can do advisory work. Contact PII (email/phone)
    is gated separately via ``user_can_view_client_contact_pii``.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user_has_capability(user, CAP_CLIENT_DATA_SENSITIVE):
        return True
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
    )


def user_can_view_client_contact_pii(user) -> bool:
    """Email, phone, DOB, address, PAN — admin / manager only.

    Advisors and other roles see masked contact details on display surfaces.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_admin", False) or getattr(user, "is_manager", False)
    )
