"""Invoice snapshots should live outside public static files."""
import json

import pytest
from flask import Flask

from routes.invoices import _load_invoice_portfolio_snapshot

pytestmark = pytest.mark.no_app


def test_loads_snapshot_from_upload_folder(tmp_path):
    app = Flask(__name__)
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    folder = tmp_path / "invoices" / "portfolio_snapshots"
    folder.mkdir(parents=True)
    payload = {"as_of": "2026-09-09", "total_value": 500}
    (folder / "invoice_41.json").write_text(json.dumps(payload), encoding="utf-8")

    with app.app_context():
        assert _load_invoice_portfolio_snapshot(41) == payload


def test_ignores_legacy_static_snapshot_location(tmp_path):
    app = Flask(__name__, root_path=str(tmp_path))
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    legacy = tmp_path / "static" / "invoices" / "portfolio_snapshots"
    legacy.mkdir(parents=True)
    (legacy / "invoice_41_asof_2026-09-09.json").write_text('{"legacy": true}', encoding="utf-8")

    with app.app_context():
        assert _load_invoice_portfolio_snapshot(41) is None
