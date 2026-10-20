"""TEMP local lab: upload / preload trade files and compare image vs extracted fills.

Enable with TRADE_PARSER_LAB=1 (or Flask debug). Remove when parsers are stable.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

trade_parser_lab_bp = Blueprint(
    "trade_parser_lab", __name__, url_prefix="/hub/trade-parser-lab"
)


def lab_enabled() -> bool:
    if os.environ.get("TRADE_PARSER_LAB", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(current_app.debug)
    except Exception:
        return False


def _can_use() -> bool:
    u = current_user
    return bool(
        getattr(u, "is_admin", False)
        or getattr(u, "is_manager", False)
        or getattr(u, "is_ops_manager", False)
        or getattr(u, "is_advisor", False)
    )


@trade_parser_lab_bp.before_request
def _gate():
    if not lab_enabled():
        flash("Trade parser lab is disabled (set TRADE_PARSER_LAB=1).", "warning")
        return redirect(url_for("hub.dashboard"))
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.path))
    if not _can_use():
        flash("Access denied.", "error")
        return redirect(url_for("hub.dashboard"))


@trade_parser_lab_bp.route("/", methods=["GET", "POST"])
@login_required
def lab_home():
    from services.reconciliation.trade_parser_lab import (
        list_preload_samples,
        parse_trade_bytes,
        resolve_fixture,
        split_passwords,
    )
    from services.reconciliation.trade_parsers.ocr_env import ocr_status

    samples = list_preload_samples()
    result = None
    preview = None  # {kind, url, name}
    passwords = split_passwords(request.form.get("passwords") or request.args.get("passwords") or "")

    if request.method == "POST":
        action = (request.form.get("action") or "upload").strip()
        if action == "fixture":
            rel = (request.form.get("fixture_id") or "").strip()
            path = resolve_fixture(rel)
            if not path:
                flash("Fixture not found.", "error")
            else:
                data = path.read_bytes()
                result = parse_trade_bytes(data, filename=path.name, passwords=passwords)
                preview = {
                    "kind": result["kind"],
                    "name": path.name,
                    "url": url_for(
                        "trade_parser_lab.lab_fixture_file",
                        rel=rel,
                    ),
                    "fixture_id": rel,
                }
        else:
            f = request.files.get("trade_file")
            if not f or not f.filename:
                flash("Choose a file to upload.", "warning")
            else:
                data = f.read()
                name = Path(f.filename).name
                # stash upload for preview
                up_dir = Path(current_app.root_path) / "var" / "trade_parser_lab" / "uploads"
                up_dir.mkdir(parents=True, exist_ok=True)
                safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)[:120]
                dest = up_dir / safe
                dest.write_bytes(data)
                result = parse_trade_bytes(data, filename=name, passwords=passwords)
                preview = {
                    "kind": result["kind"],
                    "name": name,
                    "url": url_for("trade_parser_lab.lab_upload_file", name=safe),
                    "fixture_id": None,
                }
    elif request.args.get("fixture"):
        rel = request.args.get("fixture", "").strip()
        path = resolve_fixture(rel)
        if path:
            data = path.read_bytes()
            result = parse_trade_bytes(data, filename=path.name, passwords=passwords)
            preview = {
                "kind": result["kind"],
                "name": path.name,
                "url": url_for("trade_parser_lab.lab_fixture_file", rel=rel),
                "fixture_id": rel,
            }

    return render_template(
        "hub/trade_parser_lab.html",
        samples=samples,
        result=result,
        preview=preview,
        ocr=ocr_status(),
        passwords=",".join(passwords),
        lab_temp=True,
    )


@trade_parser_lab_bp.route("/fixture/<path:rel>")
@login_required
def lab_fixture_file(rel):
    from services.reconciliation.trade_parser_lab import mime_for, resolve_fixture

    path = resolve_fixture(rel)
    if not path:
        flash("Not found.", "error")
        return redirect(url_for("trade_parser_lab.lab_home"))
    return send_file(path, mimetype=mime_for(path), conditional=True)


@trade_parser_lab_bp.route("/upload/<path:name>")
@login_required
def lab_upload_file(name):
    from services.reconciliation.trade_parser_lab import mime_for

    up_dir = Path(current_app.root_path) / "var" / "trade_parser_lab" / "uploads"
    path = (up_dir / Path(name).name).resolve()
    if not path.is_file() or up_dir.resolve() not in path.parents:
        flash("Upload not found.", "error")
        return redirect(url_for("trade_parser_lab.lab_home"))
    return send_file(path, mimetype=mime_for(path), conditional=True)
