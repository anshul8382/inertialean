from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from flask import Blueprint, Flask, render_template_string


@dataclass
class _Row:
    client_id: int
    client_name: str
    agreement_id: int | None
    agreement_status: str | None = None
    signed_date: object | None = None
    billing_frequency: str | None = None
    has_active_billing_schedule: bool = False
    has_active_billing_rates: bool = False
    flags: list[dict[str, str]] = None  # type: ignore[assignment]


@pytest.fixture()
def app() -> Flask:
    root = Path(__file__).resolve().parents[2]
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.config.update(SECRET_KEY="test")

    # base.html expects these globals
    app.jinja_env.globals["csrf_token"] = lambda: "t"
    app.jinja_env.globals["current_user"] = type("U", (), {"is_authenticated": False})()
    app.jinja_env.globals["has_endpoint"] = lambda _name: True
    app.jinja_env.globals["has_route_access"] = lambda _name: True
    app.jinja_env.globals["user_can_access_incentive_simulator"] = lambda: False

    # Minimal endpoints referenced by the template.
    main_bp = Blueprint("main", __name__)
    clients_bp = Blueprint("clients", __name__)
    agreements_bp = Blueprint("agreements", __name__)

    @main_bp.route("/maintenance/billing")
    def maintenance_billing() -> str:
        return "ok"

    @main_bp.route("/dashboard")
    def dashboard() -> str:
        return "ok"

    @clients_bp.route("/clients/<int:client_id>")
    def client_details(client_id: int) -> str:
        return f"ok:{client_id}"

    @agreements_bp.route("/agreements/<int:agreement_id>")
    def view_agreement(agreement_id: int) -> str:
        return f"ok:{agreement_id}"

    @agreements_bp.route("/agreements/overview")
    def agreements_overview() -> str:
        return "ok"

    app.register_blueprint(main_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(agreements_bp)
    return app


def test_overview_row_missing_agreement(app: Flask) -> None:
    tpl = '{% include "agreements/overview.html" %}'
    rows = [
        _Row(
            client_id=1,
            client_name="Alice",
            agreement_id=None,
            flags=[{"code": "MISSING_AGREEMENT", "severity": "hard", "message": "No agreement on file."}],
        )
    ]
    with app.test_request_context("/agreements/agreements/overview"):
        html = render_template_string(
            tpl,
            rows=rows,
            summary={"clients_scanned": 1, "missing_or_incomplete": 1},
            missing_only=False,
            q="",
        )
    assert "Agreements overview" in html
    assert "No agreement on file" in html
    assert ">None<" in html

