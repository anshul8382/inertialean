#!/usr/bin/env python3
"""
Seed FinVantage rules, products, and insurance questions.
Run after alter_finvantage_v3_rules.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models.finvantage_models import (
    FinVantageRule,
    FinVantageProduct,
    FinVantageRuleProduct,
    FinVantageInsuranceQuestion,
)


def seed():
    app = create_app()
    with app.app_context():
        # --- Rules ---
        rules_data = [
            {
                'code': 'INSURANCE_FIRST',
                'name': 'Insurance First',
                'description': 'Never recommend equity investments if the user does not have a private health insurance policy (independent of employer).',
                'category': 'insurance',
                'priority': 100,
            },
            {
                'code': 'EMERGENCY_BUFFER',
                'name': 'Emergency Buffer',
                'description': 'Emergency Fund must cover: Rent/EMI + Utilities + Groceries + Children\'s Fees + Insurance Premiums.',
                'category': 'emergency',
                'priority': 90,
            },
            {
                'code': 'RULE_3_6_12',
                'name': '3-6-12 Rule',
                'description': '3 months if single/stable job. 6 months if married/dependents. 12 months if supporting parents or irregular income.',
                'category': 'emergency',
                'priority': 85,
            },
            {
                'code': 'INVESTMENT_HORIZON',
                'name': 'Investment Horizon',
                'description': '< 2 years: Liquid/Debt Funds only. 2-5 years: Hybrid/Balanced Funds. > 5 years: Equity/PPF/NPS.',
                'category': 'horizon',
                'priority': 80,
            },
        ]
        for r in rules_data:
            if not FinVantageRule.query.filter_by(code=r['code']).first():
                rule = FinVantageRule(**r)
                db.session.add(rule)
                print(f"✓ Added rule {r['code']}")
        db.session.commit()

        # --- Sample products (placeholders - admin can edit) ---
        products_data = [
            {'name': 'Star Comprehensive Health', 'product_type': 'health_insurance', 'provider': 'Star Health', 'min_coverage': 500000, 'max_coverage': 2500000},
            {'name': 'HDFC Optima Restore', 'product_type': 'health_insurance', 'provider': 'HDFC ERGO', 'min_coverage': 500000, 'max_coverage': 5000000},
            {'name': 'Care Health Policy', 'product_type': 'health_insurance', 'provider': 'Care Health', 'min_coverage': 500000, 'max_coverage': 10000000},
            {'name': 'ICICI Lombard Complete Health', 'product_type': 'health_insurance', 'provider': 'ICICI Lombard', 'min_coverage': 500000, 'max_coverage': 10000000},
        ]
        rule_ins = FinVantageRule.query.filter_by(code='INSURANCE_FIRST').first()
        for pdata in products_data:
            if not FinVantageProduct.query.filter_by(name=pdata['name']).first():
                p = FinVantageProduct(**pdata)
                db.session.add(p)
                db.session.flush()
                if rule_ins:
                    db.session.add(FinVantageRuleProduct(rule_id=rule_ins.id, product_id=p.id))
                print(f"✓ Added product {pdata['name']}")
        db.session.commit()

        # --- Insurance questionnaire ---
        questions_data = [
            {'text': 'Do you have private health insurance (independent of employer)?', 'question_type': 'single_choice', 'options': [{'value': 'yes', 'label': 'Yes'}, {'value': 'no', 'label': 'No'}, {'value': 'employer_only', 'label': 'Only through employer'}], 'category': 'health', 'order_idx': 1, 'rule_code': 'INSURANCE_FIRST'},
            {'text': 'What is your current family floater sum assured?', 'question_type': 'single_choice', 'options': [{'value': 'none', 'label': 'None'}, {'value': 'under_5l', 'label': 'Under ₹5L'}, {'value': '5_to_10l', 'label': '₹5L–10L'}, {'value': 'above_10l', 'label': 'Above ₹10L'}], 'category': 'health', 'order_idx': 2, 'rule_code': 'INSURANCE_FIRST'},
            {'text': 'Number of family members to be covered?', 'question_type': 'single_choice', 'options': [{'value': '1', 'label': 'Just me'}, {'value': '2', 'label': '2 (e.g. spouse)'}, {'value': '3_4', 'label': '3–4'}, {'value': '5_plus', 'label': '5+'}], 'category': 'health', 'order_idx': 3},
            {'text': 'Any pre-existing conditions in the family?', 'question_type': 'single_choice', 'options': [{'value': 'no', 'label': 'No'}, {'value': 'yes', 'label': 'Yes'}], 'category': 'health', 'order_idx': 4},
            {'text': 'Your age bracket?', 'question_type': 'single_choice', 'options': [{'value': 'under_30', 'label': 'Under 30'}, {'value': '30_40', 'label': '30–40'}, {'value': '40_50', 'label': '40–50'}, {'value': '50_plus', 'label': '50+'}], 'category': 'health', 'order_idx': 5},
            {'text': 'Monthly budget for health insurance premium?', 'question_type': 'single_choice', 'options': [{'value': 'under_1k', 'label': 'Under ₹1,000'}, {'value': '1k_3k', 'label': '₹1,000–3,000'}, {'value': '3k_5k', 'label': '₹3,000–5,000'}, {'value': '5k_plus', 'label': '₹5,000+'}], 'category': 'health', 'order_idx': 6},
            {'text': 'Do you need term life insurance coverage?', 'question_type': 'single_choice', 'options': [{'value': 'yes', 'label': 'Yes'}, {'value': 'no', 'label': 'No'}, {'value': 'have_already', 'label': 'Already have'}], 'category': 'term', 'order_idx': 10},
            {'text': 'Approximate income replacement cover needed (years of annual income)?', 'question_type': 'single_choice', 'options': [{'value': '5', 'label': '5 years'}, {'value': '10', 'label': '10 years'}, {'value': '15', 'label': '15 years'}, {'value': '20_plus', 'label': '20+ years'}], 'category': 'term', 'order_idx': 11},
        ]
        for q in questions_data:
            if not FinVantageInsuranceQuestion.query.filter_by(text=q['text']).first():
                q_obj = FinVantageInsuranceQuestion(**q)
                db.session.add(q_obj)
                print(f"✓ Added question: {q['text'][:50]}...")
        db.session.commit()
        print("\n✅ Seed complete!")


if __name__ == '__main__':
    seed()
