"""
FinVantage Rules & Products API - Admin-editable rules, approved products, insurance questionnaire.
Routes: /finvantage/rules, /finvantage/products, /finvantage/insurance-questions, /finvantage/insurance-assess
Uses app User model for admin auth - app admins can manage FinVantage rules/products.
"""

import logging
import re
from flask import Blueprint, request
from flask_login import current_user
from flask_mail import Message
from extensions import mail

logger = logging.getLogger(__name__)
from functools import wraps
from models.finvantage_models import (
    FinVantageUser,
    FinVantageRule,
    FinVantageProduct,
    FinVantageRuleProduct,
    FinVantageInsuranceQuestion,
    FinVantageUserInsuranceAssessment,
    FinVantageProfile,
    FinVantageSyncBlock,
)
from models import db
from api.core.response import APIResponse
from sqlalchemy import func


finvantage_rules_bp = Blueprint('finvantage_rules', __name__)


def admin_required(f):
    """Require app admin (Flask-Login current_user.is_admin). Returns 401 JSON for API."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return APIResponse.error('Login required', status_code=401)
        if not getattr(current_user, 'is_admin', False):
            return APIResponse.error('Admin access required', status_code=403)
        return f(*args, **kwargs)
    return decorated


def _get_user(email: str):
    if not email:
        return None
    return FinVantageUser.query.filter(func.lower(FinVantageUser.email) == email.lower()).first()


def _rule_to_json(r):
    return {
        'id': r.id,
        'code': r.code,
        'name': r.name,
        'description': r.description,
        'category': r.category,
        'priority': r.priority,
        'isActive': r.is_active,
        'productIds': [p.id for p in r.products],
    }


def _product_to_json(p):
    return {
        'id': p.id,
        'name': p.name,
        'productType': p.product_type,
        'provider': p.provider,
        'description': p.description,
        'minCoverage': float(p.min_coverage) if p.min_coverage else None,
        'maxCoverage': float(p.max_coverage) if p.max_coverage else None,
        'productUrl': getattr(p, 'product_url', None) or None,
        'metadata': p.metadata_json,
        'isActive': p.is_active,
    }


def _question_to_json(q):
    return {
        'id': q.id,
        'text': q.text,
        'questionType': q.question_type,
        'options': q.options or [],
        'category': q.category,
        'orderIdx': q.order_idx,
        'ruleCode': q.rule_code,
    }


# --- Admin check (uses app User.is_admin) ---


@finvantage_rules_bp.route('/finvantage/admin/check', methods=['GET'])
def admin_check():
    """
    Check if current session user is app admin.
    Used by FinVantage admin UI - redirect to /auth/login if not admin.
    Returns user info when logged in as admin for header display.
    """
    ok = current_user.is_authenticated and getattr(current_user, 'is_admin', False)
    out = {'isAdmin': ok}
    if ok:
        out['user'] = {'email': getattr(current_user, 'email', None) or '', 'username': getattr(current_user, 'username', None) or ''}
    return APIResponse.success(out)


# --- Rules CRUD ---


@finvantage_rules_bp.route('/finvantage/rules', methods=['GET'])
def list_rules():
    """List all rules (optional: ?category=insurance)."""
    category = request.args.get('category', '').strip()
    q = FinVantageRule.query
    if category:
        q = q.filter_by(category=category)
    rules = q.order_by(FinVantageRule.priority.desc(), FinVantageRule.id).all()
    return APIResponse.success([_rule_to_json(r) for r in rules])


@finvantage_rules_bp.route('/finvantage/rules', methods=['POST'])
@admin_required
def create_rule():
    """Create a new rule."""
    body = request.get_json() or {}
    code = (body.get('code') or '').strip()
    name = (body.get('name') or '').strip()
    if not code or not name:
        return APIResponse.error('code and name required', status_code=400)
    if FinVantageRule.query.filter_by(code=code).first():
        return APIResponse.error(f'Rule with code {code} already exists', status_code=400)
    r = FinVantageRule(
        code=code,
        name=name,
        description=body.get('description'),
        category=body.get('category'),
        priority=int(body.get('priority') or 0),
        is_active=body.get('isActive', True),
    )
    db.session.add(r)
    db.session.commit()
    db.session.refresh(r)
    product_ids = body.get('productIds') or []
    for pid in product_ids:
        db.session.add(FinVantageRuleProduct(rule_id=r.id, product_id=int(pid)))
    db.session.commit()
    db.session.commit()
    return APIResponse.success(_rule_to_json(r))


@finvantage_rules_bp.route('/finvantage/rules/<int:rid>', methods=['PUT'])
@admin_required
def update_rule(rid):
    """Update a rule."""
    body = request.get_json() or {}
    r = FinVantageRule.query.get(rid)
    if not r:
        return APIResponse.error('Rule not found', status_code=404)
    if 'name' in body:
        r.name = (body['name'] or '').strip() or r.name
    if 'description' in body:
        r.description = body['description']
    if 'category' in body:
        r.category = body['category']
    if 'priority' in body:
        r.priority = int(body['priority'])
    if 'isActive' in body:
        r.is_active = bool(body['isActive'])
    if 'productIds' in body:
        FinVantageRuleProduct.query.filter_by(rule_id=rid).delete()
        for pid in body['productIds']:
            db.session.add(FinVantageRuleProduct(rule_id=rid, product_id=int(pid)))
    db.session.commit()
    return APIResponse.success(_rule_to_json(r))


@finvantage_rules_bp.route('/finvantage/rules/<int:rid>', methods=['DELETE'])
@admin_required
def delete_rule(rid):
    """Delete a rule."""
    r = FinVantageRule.query.get(rid)
    if not r:
        return APIResponse.error('Rule not found', status_code=404)
    FinVantageRuleProduct.query.filter_by(rule_id=rid).delete()
    db.session.delete(r)
    db.session.commit()
    return APIResponse.success({'ok': True})


# --- Products CRUD ---


@finvantage_rules_bp.route('/finvantage/products', methods=['GET'])
def list_products():
    """List products (optional: ?ruleCode=INSURANCE_FIRST, ?productType=health_insurance)."""
    rule_code = request.args.get('ruleCode', '').strip()
    product_type = request.args.get('productType', '').strip()
    q = FinVantageProduct.query.filter_by(is_active=True)
    if product_type:
        q = q.filter_by(product_type=product_type)
    if rule_code:
        rule = FinVantageRule.query.filter_by(code=rule_code).first()
        if rule:
            product_ids = [p.id for p in rule.products]
            q = q.filter(FinVantageProduct.id.in_(product_ids))
    products = q.order_by(FinVantageProduct.name).all()
    return APIResponse.success([_product_to_json(p) for p in products])


@finvantage_rules_bp.route('/finvantage/products', methods=['POST'])
@admin_required
def create_product():
    """Create a product."""
    body = request.get_json() or {}
    name = (body.get('name') or '').strip()
    product_type = (body.get('productType') or '').strip()
    if not name or not product_type:
        return APIResponse.error('name and productType required', status_code=400)
    p = FinVantageProduct(
        name=name,
        product_type=product_type,
        provider=body.get('provider'),
        description=body.get('description'),
        min_coverage=float(body['minCoverage']) if body.get('minCoverage') is not None else None,
        max_coverage=float(body['maxCoverage']) if body.get('maxCoverage') is not None else None,
        product_url=(body.get('productUrl') or '').strip() or None,
        metadata_json=body.get('metadata'),
        is_active=body.get('isActive', True),
    )
    db.session.add(p)
    db.session.commit()
    db.session.refresh(p)
    rule_ids = body.get('ruleIds') or []
    for rid in rule_ids:
        db.session.add(FinVantageRuleProduct(rule_id=int(rid), product_id=p.id))
    db.session.commit()
    return APIResponse.success(_product_to_json(p))


@finvantage_rules_bp.route('/finvantage/products/<int:pid>', methods=['PUT'])
@admin_required
def update_product(pid):
    """Update a product."""
    body = request.get_json() or {}
    p = FinVantageProduct.query.get(pid)
    if not p:
        return APIResponse.error('Product not found', status_code=404)
    if 'name' in body:
        p.name = (body['name'] or '').strip() or p.name
    if 'productType' in body:
        p.product_type = body['productType']
    if 'provider' in body:
        p.provider = body['provider']
    if 'description' in body:
        p.description = body['description']
    if 'minCoverage' in body:
        p.min_coverage = float(body['minCoverage']) if body.get('minCoverage') is not None else None
    if 'maxCoverage' in body:
        p.max_coverage = float(body['maxCoverage']) if body.get('maxCoverage') is not None else None
    if 'productUrl' in body:
        p.product_url = (body.get('productUrl') or '').strip() or None
    if 'metadata' in body:
        p.metadata_json = body['metadata']
    if 'isActive' in body:
        p.is_active = bool(body['isActive'])
    if 'ruleIds' in body:
        FinVantageRuleProduct.query.filter_by(product_id=pid).delete()
        for rid in body['ruleIds']:
            db.session.add(FinVantageRuleProduct(rule_id=int(rid), product_id=pid))
    db.session.commit()
    return APIResponse.success(_product_to_json(p))


# --- Insurance Questions ---


@finvantage_rules_bp.route('/finvantage/insurance-questions', methods=['GET'])
def list_insurance_questions():
    """List insurance questionnaire questions (ordered)."""
    category = request.args.get('category', '').strip()
    q = FinVantageInsuranceQuestion.query.filter_by(is_active=True)
    if category:
        q = q.filter_by(category=category)
    questions = q.order_by(FinVantageInsuranceQuestion.order_idx, FinVantageInsuranceQuestion.id).all()
    return APIResponse.success([_question_to_json(q) for q in questions])


@finvantage_rules_bp.route('/finvantage/insurance-questions', methods=['POST'])
@admin_required
def create_insurance_question():
    """Create questionnaire question (admin)."""
    body = request.get_json() or {}
    text = (body.get('text') or '').strip()
    question_type = (body.get('questionType') or 'single_choice').strip()
    if not text:
        return APIResponse.error('text required', status_code=400)
    q = FinVantageInsuranceQuestion(
        text=text,
        question_type=question_type,
        options=body.get('options'),
        category=body.get('category'),
        order_idx=int(body.get('orderIdx') or 0),
        rule_code=body.get('ruleCode'),
    )
    db.session.add(q)
    db.session.commit()
    db.session.refresh(q)
    return APIResponse.success(_question_to_json(q))


@finvantage_rules_bp.route('/finvantage/insurance-questions/<int:qid>', methods=['PUT'])
@admin_required
def update_insurance_question(qid):
    """Update question (admin)."""
    body = request.get_json() or {}
    q = FinVantageInsuranceQuestion.query.get(qid)
    if not q:
        return APIResponse.error('Question not found', status_code=404)
    if 'text' in body:
        q.text = (body['text'] or '').strip() or q.text
    if 'questionType' in body:
        q.question_type = body['questionType']
    if 'options' in body:
        q.options = body['options']
    if 'category' in body:
        q.category = body['category']
    if 'orderIdx' in body:
        q.order_idx = int(body['orderIdx'])
    if 'ruleCode' in body:
        q.rule_code = body['ruleCode']
    db.session.commit()
    return APIResponse.success(_question_to_json(q))


# --- Insurance Assessment & Suggestions ---


@finvantage_rules_bp.route('/finvantage/insurance-assess', methods=['POST'])
def submit_insurance_assessment():
    """
    Submit questionnaire answers, get suggested products.
    Body: { email, answers: { questionId: value } }
    """
    body = request.get_json() or {}
    email = (body.get('email') or '').strip()
    answers = body.get('answers') or {}
    if not email:
        return APIResponse.error('email required', status_code=400)
    user = _get_user(email)
    if not user:
        return APIResponse.error('Profile required first', status_code=400)

    profile = FinVantageProfile.query.filter_by(user_id=user.id).first()
    current_coverage = float(profile.health_insurance_coverage or 0) if profile else 0

    suggested_ids = []
    products = []
    rule = FinVantageRule.query.filter_by(code='INSURANCE_FIRST', is_active=True).first()
    if rule:
        product_ids = [p.id for p in rule.products]
        products = FinVantageProduct.query.filter(
            FinVantageProduct.id.in_(product_ids),
            FinVantageProduct.is_active == True,
        ).all()
        for p in products:
            min_cov = float(p.min_coverage or 0)
            max_cov = float(p.max_coverage or 999999999)
            if current_coverage < 500000 and (min_cov <= 500000 or not min_cov):
                suggested_ids.append(p.id)

    if not suggested_ids and products:
        suggested_ids = [p.id for p in products[:5]]

    assessment = FinVantageUserInsuranceAssessment.query.filter_by(user_id=user.id).first()
    if assessment:
        assessment.answers = {str(k): v for k, v in answers.items()}
        assessment.suggested_product_ids = suggested_ids
    else:
        assessment = FinVantageUserInsuranceAssessment(
            user_id=user.id, answers={str(k): v for k, v in answers.items()}, suggested_product_ids=suggested_ids
        )
        db.session.add(assessment)
    db.session.commit()

    suggested = FinVantageProduct.query.filter(FinVantageProduct.id.in_(suggested_ids)).all() if suggested_ids else []
    return APIResponse.success({
        'answers': assessment.answers,
        'suggestedProducts': [_product_to_json(p) for p in suggested],
        'currentCoverage': current_coverage,
        'targetMin': 500000,
    })


@finvantage_rules_bp.route('/finvantage/insurance-assess', methods=['GET'])
def get_insurance_assessment():
    """Get user's insurance assessment and suggested products."""
    email = (request.args.get('email') or '').strip()
    if not email:
        return APIResponse.error('email required', status_code=400)
    user = _get_user(email)
    if not user:
        return APIResponse.error('Profile required first', status_code=400)
    assessment = FinVantageUserInsuranceAssessment.query.filter_by(user_id=user.id).first()
    if not assessment:
        return APIResponse.success({'answers': {}, 'suggestedProducts': [], 'currentCoverage': 0, 'targetMin': 500000})
    profile = FinVantageProfile.query.filter_by(user_id=user.id).first()
    current_coverage = float(profile.health_insurance_coverage or 0) if profile else 0
    ids = assessment.suggested_product_ids or []
    products = FinVantageProduct.query.filter(FinVantageProduct.id.in_(ids)).all() if ids else []
    return APIResponse.success({
        'answers': assessment.answers or {},
        'suggestedProducts': [_product_to_json(p) for p in products],
        'currentCoverage': current_coverage,
        'targetMin': 500000,
    })


# --- Admin: Delete user planning data ---


@finvantage_rules_bp.route('/finvantage/admin/users/delete', methods=['POST'])
@admin_required
def admin_delete_user():
    """
    Delete entire FinVantage profile and all associated data (user, profile, assets, goals, earmarkings, persona, savings).
    Blocks re-sync of planning data from Inertia (holdings still sync). Admin only.
    """
    body = request.get_json() or {}
    email = (body.get('email') or '').strip()
    if not email:
        return APIResponse.error('email is required', status_code=400)
    user = _get_user(email)
    if not user:
        return APIResponse.success({'deleted': False, 'message': 'User not found'})
    try:
        db.session.delete(user)
        # Block re-sync from Inertia so data stays deleted
        block = FinVantageSyncBlock.query.filter(func.lower(FinVantageSyncBlock.email) == email.lower()).first()
        if not block:
            block = FinVantageSyncBlock(email=email.lower())
            db.session.add(block)
        db.session.commit()
        return APIResponse.success({'deleted': True, 'email': email})
    except Exception as e:
        db.session.rollback()
        return APIResponse.error(str(e), status_code=500)


@finvantage_rules_bp.route('/finvantage/admin/users/unblock', methods=['POST'])
@admin_required
def admin_unblock_user():
    """Remove email from sync block so Inertia can resync. Admin only."""
    body = request.get_json() or {}
    email = (body.get('email') or '').strip()
    if not email:
        return APIResponse.error('email is required', status_code=400)
    block = FinVantageSyncBlock.query.filter(func.lower(FinVantageSyncBlock.email) == email.lower()).first()
    if not block:
        return APIResponse.success({'unblocked': False, 'message': 'Not blocked'})
    try:
        db.session.delete(block)
        db.session.commit()
        return APIResponse.success({'unblocked': True, 'email': email})
    except Exception as e:
        db.session.rollback()
        return APIResponse.error(str(e), status_code=500)


def _is_valid_email(email_str):
    """Validate email format."""
    if not email_str or not isinstance(email_str, str):
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email_str.strip()))


@finvantage_rules_bp.route('/finvantage/email-plan', methods=['POST'])
def email_plan():
    """
    Email the FinVantage plan to the user.
    Uses Inertia app's existing email config (MAIL_SERVER, MAIL_USERNAME, etc. from main.py).
    Body: { email, planHtml }
    """
    body = request.get_json() or {}
    email = (body.get('email') or '').strip()
    plan_html = body.get('planHtml') or ''
    if not email:
        return APIResponse.error('email is required', status_code=400)
    if not _is_valid_email(email):
        return APIResponse.error('invalid email address', status_code=400)
    if not plan_html:
        return APIResponse.error('planHtml is required', status_code=400)
    try:
        msg = Message(
            subject='Your FinVantage Plan',
            recipients=[email],
            html=plan_html,
        )
        mail.send(msg)
        logger.info(f"FinVantage plan emailed to {email}")
        return APIResponse.success({'sent': True, 'email': email})
    except Exception as e:
        logger.error(f"Error sending FinVantage plan email: {e}")
        return APIResponse.error(f'Failed to send email: {str(e)}', status_code=500)
