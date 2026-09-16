#!/usr/bin/env python3
"""Create campaign_studio_campaign table for Campaign Studio persistence."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from main import create_app
    from config import Config
    from extensions import db

    app = create_app(Config)
    with app.app_context():
        db.create_all()
        print("campaign_studio_campaign table created (or already exists).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
