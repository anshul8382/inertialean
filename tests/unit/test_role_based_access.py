"""Unit tests for role-based nav registry and permission helpers."""
import pytest

pytestmark = pytest.mark.no_app


def test_permission_catalog_excludes_universal_items():
    from nav_registry import NAV_GROUPS, get_permission_catalog

    catalog = get_permission_catalog()
    assert "Clients" in catalog
    assert "Advisor" in catalog
    assert "Help" not in catalog  # lean: user-manual-only, universal, not in roles catalog
    assert "tasks.list_tasks" in {ep for ep, _ in catalog["Advisor"]}
    assert "parrva.submit_page" in {ep for ep, _ in catalog["Maintenance"]}
    assert "tasks.list_tasks" not in {ep for ep, _ in catalog["Clients"]}
    universal = {
        item.endpoint
        for group in NAV_GROUPS
        for item in group.items
        if item.policy == "universal"
    }
    listed = {ep for rows in catalog.values() for ep, _ in rows}
    assert universal.isdisjoint(listed)


def test_menu_endpoints_contains_clients_list():
    from nav_registry import MENU_ENDPOINTS

    assert "clients_v2.list_clients" in MENU_ENDPOINTS
    assert "assistant.assistant_hub" not in MENU_ENDPOINTS


class _PermRole:
    def __init__(self, permissions):
        self._permissions = permissions

    def get_all_permissions(self):
        return self._permissions


class _User:
    def __init__(
        self,
        *,
        is_admin=False,
        role=None,
        role_obj=None,
        route_access=None,
    ):
        self.is_authenticated = True
        self.is_admin = is_admin
        self.role = role
        self.role_obj = role_obj
        self._route_access = route_access or {}

    def has_route_access(self, endpoint):
        return self._route_access.get(endpoint, False)


def test_role_is_menu_restricted_only_for_route_rows():
    from services.permission_service import role_is_menu_restricted

    cap_only = _User(role="research", role_obj=_PermRole({"capability:foo": True}))
    route_user = _User(role="research", role_obj=_PermRole({"main.securities": True}))
    assert role_is_menu_restricted(cap_only) is False
    assert role_is_menu_restricted(route_user) is True


def test_role_is_menu_restricted_false_for_manager_with_stale_routes():
    """Manager seeded with old clients.* rows must not enter allowlist mode."""
    from services.permission_service import role_is_menu_restricted, nav_can

    class _Mgr:
        is_authenticated = True
        is_admin = False
        is_manager = True
        is_advisor = False
        role = "manager"
        role_obj = _PermRole(
            {
                "clients.list_clients": True,
                "clients.view_client": True,
            }
        )

        def has_route_access(self, endpoint):
            return endpoint in {"clients.list_clients", "clients.view_client"}

    manager = _Mgr()
    assert role_is_menu_restricted(manager) is False
    assert nav_can(manager, "clients_v2.list_clients") is True


def test_role_is_menu_restricted_false_for_manager_with_stale_routes_legacy_user():
    """Manager via role string + stale rows must not enter allowlist mode."""
    from services.permission_service import role_is_menu_restricted, nav_can

    manager = _User(
        role="manager",
        role_obj=_PermRole(
            {
                "clients.list_clients": True,
                "clients.view_client": True,
            }
        ),
        route_access={"clients.list_clients": True, "clients_v2.list_clients": False},
    )
    assert role_is_menu_restricted(manager) is False
    assert nav_can(manager, "clients_v2.list_clients") is True


def test_nav_can_allowlist_for_restricted_role():
    from services.permission_service import nav_can

    user = _User(
        role="research",
        role_obj=_PermRole({"main.securities": True}),
        route_access={"main.securities": True, "clients_v2.list_clients": False},
    )
    assert nav_can(user, "main.securities") is True
    assert nav_can(user, "clients_v2.list_clients") is False


def test_nav_can_legacy_pass_for_unrestricted_role():
    from services.permission_service import nav_can

    user = _User(role="advisor", role_obj=_PermRole({"capability:client_data_ops": True}))
    assert nav_can(user, "clients_v2.list_clients") is True


def test_redact_client_dict_masks_pii():
    from services.client_data_redaction import redact_client_dict

    payload = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "9876543210",
        "date_of_birth": "1990-05-15",
        "holdings": [{"symbol": "INFY", "amount": 1000}],
    }
    out = redact_client_dict(payload, can_view_sensitive=False)
    assert out["email"] != payload["email"]
    assert out["phone"] == "98xxxxxx10"
    assert out["holdings"] == []


def test_redact_contact_pii_keeps_financials():
    from services.client_data_redaction import redact_contact_pii_dict

    payload = {
        "email": "jane@example.com",
        "phone": "9876543210",
        "client_email": "jane@example.com",
        "holdings": [{"symbol": "INFY", "amount": 1000}],
        "amount": 50000,
    }
    out = redact_contact_pii_dict(payload, can_view_contact_pii=False)
    assert out["email"] == "j***@example.com"
    assert out["phone"] == "98xxxxxx10"
    assert out["client_email"] == "j***@example.com"
    assert out["holdings"] == payload["holdings"]
    assert out["amount"] == 50000


def test_user_can_view_client_contact_pii_admin_manager_only():
    from services.permission_service import user_can_view_client_contact_pii

    class _U:
        def __init__(self, *, is_admin=False, is_manager=False, is_advisor=False):
            self.is_authenticated = True
            self.is_admin = is_admin
            self.is_manager = is_manager
            self.is_advisor = is_advisor

    assert user_can_view_client_contact_pii(_U(is_admin=True)) is True
    assert user_can_view_client_contact_pii(_U(is_manager=True)) is True
    assert user_can_view_client_contact_pii(_U(is_advisor=True)) is False


def test_mask_phone_formats():
    from services.client_data_redaction import mask_phone

    assert mask_phone("9876543210") == "98xxxxxx10"


def test_user_can_view_all_clients_admin_and_manager_only():
    from access_control import user_can_view_all_clients
    from unittest.mock import patch

    class _U:
        def __init__(self, *, is_admin=False, is_manager=False):
            self.is_authenticated = True
            self.is_admin = is_admin
            self.is_manager = is_manager
            self.id = 1
            self.role = "advisor"
            self.role_obj = None

    assert user_can_view_all_clients(_U(is_admin=True)) is True
    assert user_can_view_all_clients(_U(is_manager=True)) is True
    assert user_can_view_all_clients(_U()) is False
    assert user_can_view_all_clients(None) is False

    class _Ops:
        is_authenticated = True
        is_admin = False
        is_manager = False
        id = 12
        role = "Ops manager"
        role_obj = type("R", (), {"name": "Ops manager"})()

    assert user_can_view_all_clients(_Ops()) is True


def test_scope_clients_query_filters_advisor_book():
    from access_control import scope_clients_query

    class _Advisor:
        is_authenticated = True
        is_admin = False
        is_manager = False
        id = 15

    class _Manager:
        is_authenticated = True
        is_admin = False
        is_manager = True
        id = 3

    class _Query:
        def __init__(self):
            self.filters = []

        def filter(self, *args):
            self.filters.append(args)
            return self

    q = _Query()
    out = scope_clients_query(q, user=_Advisor())
    assert out is q
    assert len(q.filters) == 1

    q2 = _Query()
    out2 = scope_clients_query(q2, user=_Manager())
    assert out2 is q2
    assert q2.filters == []


def test_accessible_client_ids_none_for_manager():
    from access_control import accessible_client_ids, user_can_view_all_clients
    from unittest.mock import patch

    class _Manager:
        is_authenticated = True
        is_admin = False
        is_manager = True
        id = 3

    class _Advisor:
        is_authenticated = True
        is_admin = False
        is_manager = False
        id = 15

        def get_accessible_clients(self):
            class C:
                id = 99
            return [C()]

    class _Anon:
        is_authenticated = False

    with patch("access_control.current_user", _Manager()):
        assert user_can_view_all_clients() is True
        assert accessible_client_ids() is None

    with patch("access_control.current_user", _Advisor()):
        assert user_can_view_all_clients() is False
        assert accessible_client_ids() == [99]

    # Batch jobs / email cron: no logged-in user → do not empty the report
    with patch("access_control.current_user", _Anon()):
        assert accessible_client_ids() is None


def test_scope_query_to_accessible_clients_empty_book():
    from access_control import scope_query_to_accessible_clients
    from unittest.mock import MagicMock, patch

    class _Advisor:
        is_authenticated = True
        is_admin = False
        is_manager = False
        id = 15

        def get_accessible_clients(self):
            return []

    class _Query:
        def __init__(self):
            self.filters = []

        def filter(self, *args):
            self.filters.append(args)
            return self

    col = MagicMock()
    col.in_.return_value = "FILTER_EXPR"

    with patch("access_control.current_user", _Advisor()):
        q = _Query()
        out = scope_query_to_accessible_clients(q, col)
        assert out is q
        col.in_.assert_called_once_with([-1])
        assert q.filters == [("FILTER_EXPR",)]
