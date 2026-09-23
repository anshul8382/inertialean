from __future__ import annotations
"""
Models package
Contains WhatsApp and other model definitions

This package exists alongside models.py. 
To import from models.py, use: from models import ModelName
To import from this package, use: from models.whatsapp_models import ModelName
"""

# Import everything from the parent models.py to maintain compatibility
# This allows both "from models import User" and "from models.whatsapp_models import WhatsAppMessage"
import sys
import os

# Use a lazy import approach - import models.py as a module with a different name
# to avoid conflicts, then re-export everything
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _parent_dir)

try:
    # Import models.py using importlib to avoid circular imports
    import importlib.util
    _models_py_path = os.path.join(_parent_dir, 'models.py')
    
    if os.path.exists(_models_py_path):
        # Load models.py as a separate module
        _spec = importlib.util.spec_from_file_location("_models_py_module", _models_py_path)
        _models_py_module = importlib.util.module_from_spec(_spec)
        # Don't add to sys.modules with 'models' name to avoid conflicts
        _spec.loader.exec_module(_models_py_module)
        
        # Re-export all public attributes (including db, User, Client, etc.)
        _current_module = sys.modules[__name__]
        _exclude_list = ['datetime', 'bcrypt', 'UserMixin', 'foreign']  # Exclude only imports, not models or db
        for _attr_name in dir(_models_py_module):
            if not _attr_name.startswith('_') and _attr_name not in _exclude_list:
                try:
                    setattr(_current_module, _attr_name, getattr(_models_py_module, _attr_name))
                except (AttributeError, TypeError):
                    pass
        # Explicitly ensure SQLQueryHistory is exported (used by database browser tool)
        if hasattr(_models_py_module, 'SQLQueryHistory'):
            setattr(_current_module, 'SQLQueryHistory', _models_py_module.SQLQueryHistory)
except Exception as e:
    # If import fails, at least allow whatsapp_models to be imported
    import warnings
    warnings.warn(f"Could not import models.py into models package: {e}")
finally:
    # Clean up sys.path
    if _parent_dir in sys.path:
        sys.path.remove(_parent_dir)

# Fallback: define SQLQueryHistory in-package if not re-exported (e.g. models.py failed to load)
if not hasattr(sys.modules[__name__], 'SQLQueryHistory'):
    try:
        from extensions import db
        _current = sys.modules[__name__]
        class SQLQueryHistory(db.Model):
            """History of SQL queries executed in the database browser tool."""
            __tablename__ = 'sql_query_history'
            id = db.Column(db.Integer, primary_key=True, autoincrement=True)
            user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
            query_text = db.Column(db.Text, nullable=False)
            query_hash = db.Column(db.String(64), nullable=False)
            created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.current_timestamp())
            last_executed_at = db.Column(db.DateTime, nullable=False, server_default=db.func.current_timestamp())
            execute_count = db.Column(db.Integer, nullable=False, default=1)
            last_success = db.Column(db.Boolean, nullable=False, default=True)
            last_error = db.Column(db.Text)
            last_row_count = db.Column(db.Integer)
            last_execution_ms = db.Column(db.Float)
            user = db.relationship('User', backref=db.backref('sql_query_history', lazy='dynamic'))
        setattr(_current, 'SQLQueryHistory', SQLQueryHistory)
    except Exception:
        pass  # Leave SQLQueryHistory undefined if fallback also fails


# Enhanced tax optimiser models (kept in-package so they work even if models.py layout changes)
try:
    from datetime import datetime
    from sqlalchemy.dialects.mysql import JSON as MYSQL_JSON
    from extensions import db

    class TaxOptimiserSettings(db.Model):
        """Singleton (id=1): editable defaults for tax optimiser tooling."""

        __tablename__ = "tax_optimiser_settings"

        id = db.Column(db.Integer, primary_key=True)
        default_stcg_rate = db.Column(db.Float, nullable=False, default=0.20)
        default_ltcg_rate = db.Column(db.Float, nullable=False, default=0.125)
        default_ltcg_exemption_limit = db.Column(db.Float, nullable=False, default=125000.0)
        ltcg_holding_days = db.Column(db.Integer, nullable=False, default=365)
        ltcg_eligibility_mode = db.Column(db.String(32), nullable=False, default="twelve_months")
        trade_charges_pct = db.Column(db.Float, nullable=False, default=0.1)
        section_94_8_months_before_record = db.Column(db.Integer, nullable=False, default=3)
        section_94_8_months_after_record = db.Column(db.Integer, nullable=False, default=9)
        stcg_loss_carryforward_years = db.Column(db.Integer, nullable=False, default=8)
        notes = db.Column(db.Text, nullable=True)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class TaxOptimiserTaxRule(db.Model):
        """Named global tax parameter sets; exactly one row should be is_active for engine defaults."""

        __tablename__ = "tax_optimiser_tax_rule"

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        name = db.Column(db.String(128), nullable=False, default="Default")
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
        default_stcg_rate = db.Column(db.Float, nullable=False, default=0.20)
        default_ltcg_rate = db.Column(db.Float, nullable=False, default=0.125)
        default_ltcg_exemption_limit = db.Column(db.Float, nullable=False, default=125000.0)
        ltcg_holding_days = db.Column(db.Integer, nullable=False, default=365)
        ltcg_eligibility_mode = db.Column(db.String(32), nullable=False, default="twelve_months")
        trade_charges_pct = db.Column(db.Float, nullable=False, default=0.1)
        section_94_8_months_before_record = db.Column(db.Integer, nullable=False, default=3)
        section_94_8_months_after_record = db.Column(db.Integer, nullable=False, default=9)
        stcg_loss_carryforward_years = db.Column(db.Integer, nullable=False, default=8)
        notes = db.Column(db.Text, nullable=True)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class TaxOptimiserReviewSession(db.Model):
        """Persisted interactive tax optimiser session; see migrations for schema."""

        __tablename__ = "tax_optimiser_review_session"

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        created_by_user_id = db.Column(db.Integer, nullable=False, index=True)
        advisor_user_id = db.Column(db.Integer, nullable=False, index=True)
        # NULL = FY-wide session (all clients); else scoped client from interactive start_payload
        client_id = db.Column(db.Integer, nullable=True, index=True)
        fy_label = db.Column(db.String(64), nullable=False, default="", index=True)
        status = db.Column(db.String(32), nullable=False, default="in_progress")
        payload_json = db.Column(MYSQL_JSON, nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class TaxOptimiserClientReviewStatus(db.Model):
        """Per-advisor FY+client: interactive review saved vs client email sent (report badges)."""

        __tablename__ = "tax_optimiser_client_review_status"
        __table_args__ = (
            db.UniqueConstraint(
                "advisor_user_id",
                "client_id",
                "fy_start_year",
                name="uq_txo_crs_scope",
            ),
        )

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        advisor_user_id = db.Column(db.Integer, nullable=False, index=True)
        client_id = db.Column(db.Integer, nullable=False, index=True)
        fy_start_year = db.Column(db.Integer, nullable=False, index=True)
        review_status = db.Column(db.String(24), nullable=False, default="saved")
        saved_at = db.Column(db.DateTime, nullable=True)
        email_sent_at = db.Column(db.DateTime, nullable=True)
        last_saved_review_session_id = db.Column(db.Integer, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class TaxOptimiserLiveSession(db.Model):
        """Ephemeral interactive queue shared across Gunicorn workers; see migrations/add_tax_optimiser_live_session_table.py."""

        __tablename__ = "tax_optimiser_live_session"

        session_key = db.Column(db.String(80), primary_key=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)
        payload_json = db.Column(MYSQL_JSON, nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    class TaxOptimiserStrategy(db.Model):
        """Configurable strategy row (S1–S9); params JSON drives engine thresholds."""

        __tablename__ = "tax_optimiser_strategy"

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        code = db.Column(db.String(64), nullable=False, unique=True, index=True)
        enabled = db.Column(db.Boolean, nullable=False, default=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        params = db.Column(MYSQL_JSON, nullable=False)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    class TaxOptimiserFollowupBatch(db.Model):
        """T+N buy-back reminder batch: price snapshot + draft HTML + optional OpsTask link."""

        __tablename__ = "tax_optimiser_followup_batch"

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        created_by_user_id = db.Column(db.Integer, nullable=False, index=True)
        fy_start_year = db.Column(db.Integer, nullable=False)
        run_after = db.Column(db.DateTime, nullable=False, index=True)
        status = db.Column(db.String(32), nullable=False, default="pending")
        buyback_delay_days = db.Column(db.Integer, nullable=False, default=2)
        price_snapshot_json = db.Column(MYSQL_JSON, nullable=False)
        approved_items_json = db.Column(MYSQL_JSON, nullable=False)
        draft_html = db.Column(db.Text, nullable=True)
        draft_html_refreshed = db.Column(db.Text, nullable=True)
        ops_task_id = db.Column(db.Integer, nullable=True)
        session_meta_json = db.Column(MYSQL_JSON, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    setattr(sys.modules[__name__], "TaxOptimiserSettings", TaxOptimiserSettings)
    setattr(sys.modules[__name__], "TaxOptimiserTaxRule", TaxOptimiserTaxRule)
    setattr(sys.modules[__name__], "TaxOptimiserReviewSession", TaxOptimiserReviewSession)
    setattr(sys.modules[__name__], "TaxOptimiserClientReviewStatus", TaxOptimiserClientReviewStatus)
    setattr(sys.modules[__name__], "TaxOptimiserLiveSession", TaxOptimiserLiveSession)
    setattr(sys.modules[__name__], "TaxOptimiserStrategy", TaxOptimiserStrategy)
    setattr(sys.modules[__name__], "TaxOptimiserFollowupBatch", TaxOptimiserFollowupBatch)
except Exception:
    # Do not block app startup on model import edge cases.
    pass
try:
    from models.audit_log import AuditLog

    setattr(sys.modules[__name__], "AuditLog", AuditLog)
except Exception:
    pass
try:
    from models.finding_notification_decision import FindingNotificationDecision

    setattr(sys.modules[__name__], "FindingNotificationDecision", FindingNotificationDecision)
except Exception:
    pass
try:
    from models.user_peer_message import UserPeerMessage

    setattr(sys.modules[__name__], "UserPeerMessage", UserPeerMessage)
except Exception:
    pass
try:
    from models.advisory_register import AdvisoryRegisterEntry

    setattr(sys.modules[__name__], "AdvisoryRegisterEntry", AdvisoryRegisterEntry)
except Exception:
    pass
try:
    from models.lead_onboarding import LeadProposal, RiskAssessmentSubmission, LeadKycProfile

    setattr(sys.modules[__name__], "LeadProposal", LeadProposal)
    setattr(sys.modules[__name__], "RiskAssessmentSubmission", RiskAssessmentSubmission)
    setattr(sys.modules[__name__], "LeadKycProfile", LeadKycProfile)
except Exception:
    pass
