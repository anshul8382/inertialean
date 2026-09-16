#!/usr/bin/env python3
"""Build static/docs/inertia_user_manual.pdf from docs/USER_MANUAL.md."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.user_manual_pdf_service import build_user_manual_pdf, pdf_output_path


def main() -> int:
    path = build_user_manual_pdf(force=True)
    if not path:
        print("Failed to build user manual PDF.", file=sys.stderr)
        return 1
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)")
    expected = pdf_output_path()
    return 0 if path.resolve() == expected.resolve() else 1


if __name__ == "__main__":
    raise SystemExit(main())
