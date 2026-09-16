#!/usr/bin/env python3
"""
Send a WhatsApp text message using the app configuration.

Usage:
  python3 scripts/send_whatsapp_message.py --to 919881127924 --message "Hello From Inertia"
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from config import ProductionConfig
from main import create_app
from services.whatsapp_service import WhatsAppService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a WhatsApp text message.")
    parser.add_argument("--to", required=True, help="Recipient phone in E.164 without +")
    parser.add_argument("--message", required=True, help="Message text to send")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    app = create_app(ProductionConfig)
    with app.app_context():
        service = WhatsAppService()
        result = service.send_text_message(args.to, args.message)

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
