"""Tests for health service and API."""
from __future__ import annotations


def test_liveness_payload_shape():
    from services.health_service import liveness_payload

    data = liveness_payload()
    assert data["status"] == "ok"
    assert data["service"] == "inertia"
    assert "version" in data
    assert "timestamp" in data


def test_health_endpoint_public(app, client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"


def test_readiness_endpoint(app, client):
    r = client.get("/api/v1/health/ready")
    assert r.status_code in (200, 503)
    body = r.get_json()
    assert "checks" in body["data"]
