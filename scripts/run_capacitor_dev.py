#!/usr/bin/env python3
"""Flask dev server bound to 0.0.0.0 for Android/iOS physical devices on LAN."""
import os
import socket
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

env = os.environ.get("FLASK_ENV", "development")
for env_file in (f".env.{env}", ".env"):
    path = os.path.join(ROOT, env_file)
    if os.path.isfile(path):
        load_dotenv(path)
        break

from config import config
from main import create_app

host = os.environ.get("CAPACITOR_DEV_HOST", "0.0.0.0")
port = int(os.environ.get("CAPACITOR_DEV_PORT", "5001"))

app = create_app(config.get(env, config["default"]))

lan_ip = "127.0.0.1"
try:
    lan_ip = socket.gethostbyname(socket.gethostname())
except OSError:
    pass

print(f"Starting INERTIA for Capacitor dev on http://{host}:{port}")
print(f"Phone .env: CAP_SERVER_URL=http://{lan_ip}:{port}")
print("Press Ctrl+C to stop")

# use_reloader=False — debug reloader breaks when launched via inline -c / subprocess
app.run(host=host, port=port, debug=True, threaded=True, use_reloader=False)
