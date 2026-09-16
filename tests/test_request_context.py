"""Tests for request correlation ID."""
from __future__ import annotations


def test_request_id_header_on_response(app, client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "test-req-123"})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "test-req-123"


def test_request_id_generated_when_missing(app, client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid and len(rid) >= 8
