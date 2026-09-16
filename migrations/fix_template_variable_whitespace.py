"""
One-time fix: strip leading/trailing whitespace from variable names stored
in AgreementTemplate.variables JSON arrays.

Template placeholders like << special_note>> were extracted with a leading
space, causing SYSTEM_VARS filtering and pre-fill lookups to silently fail.

Run: python migrations/fix_template_variable_whitespace.py
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import AgreementTemplate

app = create_app()

with app.app_context():
    templates = AgreementTemplate.query.all()
    fixed_count = 0
    for tmpl in templates:
        if not tmpl.variables:
            continue
        try:
            raw = json.loads(tmpl.variables)
        except (json.JSONDecodeError, TypeError):
            continue
        stripped = [v.strip() for v in raw if isinstance(v, str)]
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for v in stripped:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        if deduped != raw:
            tmpl.variables = json.dumps(deduped)
            fixed_count += 1
            print(f"  Fixed template id={tmpl.id} name='{tmpl.name}': {raw} → {deduped}")

    if fixed_count:
        db.session.commit()
        print(f"\nFixed {fixed_count} template(s).")
    else:
        print("All template variable names are already clean — nothing to do.")
