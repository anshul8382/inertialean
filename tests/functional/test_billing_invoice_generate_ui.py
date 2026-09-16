from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from flask import Blueprint, Flask, render_template_string


@dataclass
class _Client:
    id: int
    name: str


@dataclass
class _Lead:
    client: _Client | None = None


@dataclass
class _Agreement:
    id: int
    status: str | None = None
    lead: _Lead | None = None


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

    @main_bp.route("/maintenance/billing")
    def maintenance_billing() -> str:
        return "ok"

    invoices_bp = Blueprint("invoices", __name__)

    @invoices_bp.route("/invoices/")
    def list_invoices() -> str:
        return "ok"

    app.register_blueprint(main_bp)
    app.register_blueprint(invoices_bp)
    return app


def test_generate_invoice_page_renders(app: Flask) -> None:
    tpl = '{% include "invoices/generate.html" %}'
    agreements = [
        _Agreement(id=1, status="active", lead=_Lead(client=_Client(id=10, name="Alice"))),
        _Agreement(id=2, status="draft", lead=_Lead(client=_Client(id=11, name="Bob"))),
    ]
    with app.test_request_context("/invoices/generate"):
        html = render_template_string(tpl, agreements=agreements)
    assert "Generate invoice" in html
    assert 'name="agreement_id"' in html

