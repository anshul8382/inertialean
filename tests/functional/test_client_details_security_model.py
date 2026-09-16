from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from flask import Flask, render_template_string
from flask import Blueprint


@dataclass
class _Named:
    name: str


@dataclass
class _Client:
    id: int


@dataclass
class _ModelAssignment:
    asset_model: _Named | None = None
    stock_model: _Named | None = None
    assigned_at: datetime | None = None


@pytest.fixture()
def app() -> Flask:
    root = Path(__file__).resolve().parents[2]
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.config.update(SECRET_KEY="test")

    # Minimal endpoints needed by the partial (`url_for('clients.assign_client_model', ...)`).
    clients_bp = Blueprint("clients", __name__)

    @clients_bp.route("/clients/<int:client_id>/assign-model")
    def assign_client_model(client_id: int) -> str:
        return f"ok:{client_id}"

    app.register_blueprint(clients_bp)
    return app


def _render_partial(app: Flask, *, client: _Client, model_assignment, equity_security_model) -> str:
    # Render the extracted partial directly so this test is stable and does not
    # require populating the full client_details page context.
    tpl = '{% include "clients/partials/model_assignment_card.html" %}'
    with app.test_request_context("/"):
        return render_template_string(
            tpl,
            client=client,
            model_assignment=model_assignment,
            equity_security_model=equity_security_model,
        )


def test_security_model_prefers_equity_override(app: Flask) -> None:
    html = _render_partial(
        app,
        client=_Client(id=123),
        model_assignment=_ModelAssignment(
            asset_model=_Named("Asset A"),
            stock_model=_Named("Legacy Stock Model"),
            assigned_at=datetime(2026, 1, 1),
        ),
        equity_security_model=_Named("Equity Override Model"),
    )

    assert "Security Model" in html
    assert "Equity Override Model" in html
    assert "Legacy Stock Model" not in html


def test_security_model_falls_back_to_stock_model(app: Flask) -> None:
    html = _render_partial(
        app,
        client=_Client(id=123),
        model_assignment=_ModelAssignment(
            asset_model=_Named("Asset A"),
            stock_model=_Named("Legacy Stock Model"),
            assigned_at=datetime(2026, 1, 1),
        ),
        equity_security_model=None,
    )

    assert "Security Model" in html
    assert "Legacy Stock Model" in html


def test_security_model_shows_not_assigned(app: Flask) -> None:
    html = _render_partial(
        app,
        client=_Client(id=123),
        model_assignment=None,
        equity_security_model=None,
    )

    assert "Security Model" in html
    assert "Not assigned" in html

