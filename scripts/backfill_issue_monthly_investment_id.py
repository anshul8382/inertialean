#!/usr/bin/env python
"""
Backfill monthly_investment_id in DataIntegrityIssue.details for RECOMMENDATION_EXECUTION issues.
Run after deploying the agent change that adds monthly_investment_id to new issues.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import DataIntegrityIssue, Workflow


def backfill():
    app = create_app()
    with app.app_context():
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.details.isnot(None)
        ).all()
        
        updated = 0
        for issue in issues:
            details = issue.details or {}
            if details.get('monthly_investment_id'):
                continue  # Already has it
            wid = details.get('workflow_id')
            if wid is None:
                continue
            
            try:
                wid = int(wid)
            except (TypeError, ValueError):
                continue
            
            workflow = Workflow.query.get(wid)
            if not workflow:
                continue
            
            details['monthly_investment_id'] = workflow.monthly_investment_id
            issue.details = details
            updated += 1
        
        db.session.commit()
        print(f"Backfilled monthly_investment_id for {updated} issues (total checked: {len(issues)})")


if __name__ == '__main__':
    backfill()
