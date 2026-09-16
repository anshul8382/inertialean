"""PWA static routes (manifest, favicon, service worker)."""
from __future__ import annotations

import os

from flask import current_app, send_file


def register(main):

    @main.route("/manifest.json")
    def pwa_manifest_json():
        """Many clients still request /manifest.json at site root; static file lives under /static/."""
        path = os.path.join(current_app.root_path, "static", "manifest.json")
        return send_file(path, mimetype="application/json")


    @main.route("/favicon.ico")
    def favicon():
        """Browsers request /favicon.ico by default; avoid 404 console noise."""
        path = os.path.join(current_app.root_path, "static", "favicon-32.png")
        return send_file(path, mimetype="image/png")


    @main.route("/service-worker.js")
    def pwa_service_worker_js():
        """Serve SW from root with broad scope (script under /static/ alone cannot scope /)."""
        path = os.path.join(current_app.root_path, "static", "js", "service-worker.js")
        resp = send_file(path, mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp


