"""
Integration tests for architecture phase deliverables (health, main_sections, foundation, permissions).
"""
from __future__ import annotations

import importlib


def test_app_factory_loads(app):
    assert app is not None
    assert "main.dashboard" in app.view_functions


def test_main_sections_endpoints_registered(app):
    endpoints = [
        "main.favicon",
        "main.pwa_manifest_json",
        "main.upload_securities",
        "main.securities",
        "main.database_browser",
        "main.download_document",
    ]
    for ep in endpoints:
        assert ep in app.view_functions, f"missing {ep}"


def test_foundation_layer_imports():
    from services.foundation import ClientService, PriceService, SecurityService

    assert ClientService is not None
    assert PriceService is not None
    assert SecurityService is not None


def test_domain_service_packages_import():
    importlib.import_module("services.portfolio")
    importlib.import_module("services.client_crm")
    importlib.import_module("services.platform")


def test_health_service_readiness_shape(app):
    from services.health_service import readiness_payload

    with app.app_context():
        payload = readiness_payload()
    assert payload["service"] == "inertia"
    assert "checks" in payload
    assert "database" in payload["checks"]


def test_permission_service_no_circular_import():
    from access_control import user_sees_incentive_sales_simulator, user_can_access_holdings_report
    from services.permission_service import user_has_capability, CAP_INCENTIVE_SIMULATOR
    from types import SimpleNamespace

    admin = SimpleNamespace(is_authenticated=True, is_admin=True, role_obj=None, role=None)
    admin.has_route_access = lambda ep: False
    assert user_has_capability(admin, CAP_INCENTIVE_SIMULATOR) is True
    assert user_sees_incentive_sales_simulator(admin) is True
    assert user_can_access_holdings_report(admin) is True


def test_api_v1_health_unauthenticated(client):
    for path in ("/api/v1/health", "/api/v1/health/ready"):
        r = client.get(path)
        assert r.status_code in (200, 503), path
        body = r.get_json()
        assert body.get("success") is True


def test_trace_id_on_api_error(client):
    r = client.get("/api/v1/clients/", headers={"X-Request-ID": "trace-integration-1"})
    assert r.status_code == 401
    body = r.get_json()
    assert body.get("trace_id") == "trace-integration-1"


def test_favicon_route(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.mimetype in ("image/png", "image/x-icon", "image/vnd.microsoft.icon")


def test_manifest_route(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert "application/json" in (r.mimetype or "")


def test_service_worker_route(client):
    r = client.get("/service-worker.js")
    assert r.status_code == 200
    assert "javascript" in (r.mimetype or "")
