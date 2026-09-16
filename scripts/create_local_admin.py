#!/usr/bin/env python3
"""Create or update a local admin user. Run from repo root with venv active."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import create_app
from config import config

email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
username = os.environ.get("ADMIN_USERNAME", "admin")
password = os.environ.get("ADMIN_PASSWORD", "admin")

env = os.environ.get("FLASK_ENV", "development")
app = create_app(config.get(env, config["development"]))

with app.app_context():
    from extensions import db
    from models import User

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(username=username, email=email, is_admin=True, is_active=True)
        db.session.add(user)
        print(f"Created user {email}")
    else:
        print(f"Updating password for existing user {email}")

    user.set_password(password)
    db.session.commit()
    print("Done. Log in at http://localhost:5001 (development) with:")
    print(f"  Email: {email}")
    print(f"  Password: {password}")
