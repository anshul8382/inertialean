#!/usr/bin/env python3
"""
Point Agreement_sep2026 at the Sep 2026 Word master (not the PDF that was uploaded by mistake).

Master file (already on disk):
  static/uploads/templates/20260904_202045_Inertia_Agreement_template_1.docx

Run:
  python migrations/fix_agreement_sep2026_word_template.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORD_BASENAME = "20260904_202045_Inertia_Agreement_template_1.docx"
WORD_WEB_PATH = f"/static/uploads/templates/{WORD_BASENAME}"
TEMPLATE_NAME = "Agreement_sep2026"


def main() -> int:
    from main import create_app
    from models import AgreementTemplate, db
    from services.agreement_docx_service import extract_variables_from_docx_path
    from services.agreement_pdf_paths import template_file_abs_path

    app = create_app()
    with app.app_context():
        abs_word = template_file_abs_path(WORD_WEB_PATH)
        if not abs_word or not os.path.isfile(abs_word):
            print(f"ERROR: Word master not found at {WORD_WEB_PATH}")
            print(f"  Expected under: {os.path.join(app.root_path, 'static/uploads/templates', WORD_BASENAME)}")
            return 1

        tpl = AgreementTemplate.query.filter_by(name=TEMPLATE_NAME).first()
        if not tpl:
            print(f"ERROR: Template {TEMPLATE_NAME!r} not found")
            return 1

        variables = extract_variables_from_docx_path(abs_word)
        before = {
            "type": tpl.template_type,
            "path": tpl.template_file_path,
            "vars": tpl.variables,
        }
        tpl.template_type = "docx"
        tpl.template_file_path = WORD_WEB_PATH
        tpl.variables = json.dumps(variables)
        db.session.commit()

        print(f"Updated {TEMPLATE_NAME} (id={tpl.id})")
        print(f"  before: type={before['type']!r} path={before['path']!r}")
        print(f"  after:  type={tpl.template_type!r} path={tpl.template_file_path!r}")
        print(f"  placeholders ({len(variables)}): {variables}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
