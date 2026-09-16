from datetime import datetime, date
import bcrypt
from flask_login import UserMixin
from extensions import db
from sqlalchemy import func
from sqlalchemy.orm import foreign

class Client(db.Model):
    __tablename__ = 'client'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    secondary_emails = db.Column(db.Text, nullable=True)  # Comma-separated CC addresses
    phone = db.Column(db.String(20))
    whatsapp_number = db.Column(db.String(20))  # WhatsApp Business number for notifications
    address = db.Column(db.String(200))
    risk_profile = db.Column(db.String(20))  # Conservative, Moderate, Aggressive
    risk_profile_updated_at = db.Column(db.DateTime)  # Track when risk profile was last updated
    date_of_birth = db.Column(db.Date)  # Client's date of birth
    starting_aua = db.Column(db.Numeric(15, 2), default=0)
    designation = db.Column(db.String(100))
    linkedin_profile_url = db.Column(db.String(500))
    date_of_joining = db.Column(db.Date)
    type_of_engagement = db.Column(db.String(200))
    portfolio_inherited = db.Column(db.Boolean, default=False)
    company_name = db.Column(db.String(200))
    industry = db.Column(db.String(100))
    planning_synopsis = db.Column(db.Text, nullable=True)  # Synopsis of latest planning for the client
    background_notes = db.Column(db.Text, nullable=True)
    other_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    advisor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    is_active = db.Column(db.Boolean, default=True, nullable=False)  # Active vs inactive client engagement
    
    # Relationships
    portfolios = db.relationship('Portfolio', back_populates='client')
    user = db.relationship('User', back_populates='clients', foreign_keys=[user_id])
    model_assignments = db.relationship('ModelAssignment', back_populates='client')
    transactions = db.relationship('Transaction', back_populates='client')
    monthly_investments = db.relationship('MonthlyInvestment', back_populates='client')
    holdings = db.relationship('Holding', back_populates='client')
    call_logs = db.relationship('CallLog', back_populates='client', lazy=True)
    documents = db.relationship('Document', back_populates='client', lazy=True)
    meetings = db.relationship('Meeting', back_populates='client', overlaps="meetings_list,client_meetings")
    lead = db.relationship('Lead', back_populates='client', uselist=False)
    portfolio_snapshots = db.relationship('PortfolioSnapshot', back_populates='client')
    recommendations = db.relationship('Recommendation', back_populates='client')
    recommendation_sessions = db.relationship('RecommendationSession', back_populates='client')
    reviews = db.relationship('Review', back_populates='client')
    advisor = db.relationship('User', back_populates='assigned_clients', foreign_keys=[advisor_id])
    client_assignments = db.relationship('ClientAdvisorAssignment', back_populates='client')
    agreement_variables = db.relationship('AgreementVariables', back_populates='client', cascade='all, delete-orphan')
    review_schedules = db.relationship('ReviewSchedule', back_populates='client', cascade='all, delete-orphan')
    review_workflows = db.relationship('ReviewWorkflow', back_populates='client', cascade='all, delete-orphan')
    monthly_investment_schedule = db.relationship('MonthlyInvestmentSchedule', back_populates='client', uselist=False, cascade='all, delete-orphan')
    bni_referral = db.relationship('ClientBniReferral', back_populates='client', uselist=False)

class ClientBniReferral(db.Model):
    """One BNI referral display name per client (maintenance lookup for BNI TY Notes)."""
    __tablename__ = 'client_bni_referral'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, unique=True)
    referral_name = db.Column(db.String(255), nullable=False, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship('Client', back_populates='bni_referral')

    def __repr__(self):
        return f'<ClientBniReferral client_id={self.client_id}>'


class ClientSecurityAdvisoryExclusion(db.Model):
    """
    Per-client security overlay: excluded from advisory AUA metrics (billing, practice dashboard).
    Does not affect holdings, recommendations, or reviews.
    """
    __tablename__ = 'client_security_advisory_exclusion'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False, index=True)
    exclude_from_billing = db.Column(db.Boolean, default=True, nullable=False)
    exclude_from_practice_aua = db.Column(db.Boolean, default=True, nullable=False)
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship('Client', backref=db.backref('advisory_exclusions', lazy='dynamic'))
    security = db.relationship('Security')
    creator = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.UniqueConstraint('client_id', 'security_id', name='uq_client_security_advisory_exclusion'),
    )


class ClientGroup(db.Model):
    """Household overlay — consolidated portfolio/review without merging billing or advice."""
    __tablename__ = 'client_group'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    primary_client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    primary_client = db.relationship('Client', foreign_keys=[primary_client_id])
    creator = db.relationship('User', foreign_keys=[created_by])
    members = db.relationship(
        'ClientGroupMember',
        back_populates='group',
        cascade='all, delete-orphan',
        lazy='joined',
    )


class ClientGroupMember(db.Model):
    __tablename__ = 'client_group_member'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('client_group.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, unique=True, index=True)
    role = db.Column(db.String(30), nullable=False, default='other')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    group = db.relationship('ClientGroup', back_populates='members')
    client = db.relationship('Client', backref=db.backref('household_membership', uselist=False))


class BniTyNotesState(db.Model):
    """Singleton (id=1): last successful BNI TY Notes email window end (UTC naive)."""
    __tablename__ = 'bni_ty_notes_state'
    id = db.Column(db.Integer, primary_key=True)
    last_success_end_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SecurityAllocation(db.Model):
    __tablename__ = 'security_allocation'
    id = db.Column(db.Integer, primary_key=True)
    security_model_id = db.Column(db.Integer, db.ForeignKey('security_allocation_model.id'), nullable=False)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    allocation_percentage = db.Column(db.Numeric(5, 2), nullable=False)
    min_allocation = db.Column(db.Numeric(5, 2))
    max_allocation = db.Column(db.Numeric(5, 2))
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
    
    # Relationships
    model = db.relationship('SecurityAllocationModel', back_populates='security_allocations')
    security = db.relationship('Security', back_populates='security_allocations')

class Security(db.Model):
    __tablename__ = 'security'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    security_type = db.Column(db.String(50), nullable=False)
    current_price = db.Column(db.Numeric(15, 2))
    last_updated = db.Column(db.DateTime)
    refresh_interval = db.Column(db.Integer, default=300)
    meta_data = db.Column(db.Text)
    is_hot_stock = db.Column(db.Boolean, default=False)  # Mark as preferred by research team (legacy)
    hot_stock_rating = db.Column(db.Integer, default=0)  # Hot stock rating 0-5 scale
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    asset_class = db.relationship('AssetClass', back_populates='securities')
    transactions = db.relationship('Transaction', back_populates='security')
    holdings = db.relationship('Holding', back_populates='security')
    security_allocations = db.relationship('SecurityAllocation', back_populates='security')
    recommendations = db.relationship('Recommendation', back_populates='security')
    model_allocations = db.relationship('ModelAllocation', back_populates='security')
    creator = db.relationship('User', back_populates='created_securities', foreign_keys=[created_by])
    mprofit_mappings = db.relationship('MProfitSymbolMap', back_populates='security')


class MProfitSymbolMap(db.Model):
    __tablename__ = 'mprofit_symbol_map'
    id = db.Column(db.Integer, primary_key=True)
    raw_name = db.Column(db.String(200), nullable=False, unique=True)
    normalized_name = db.Column(db.String(200), nullable=False, index=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=True)
    nse_symbol = db.Column(db.String(50), nullable=True)
    is_manual = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(500))
    last_used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    security = db.relationship('Security', back_populates='mprofit_mappings')

class Transaction(db.Model):
    __tablename__ = 'transaction'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    transaction_date = db.Column(db.DateTime, nullable=False)
    type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Numeric(15, 2), nullable=False)
    price = db.Column(db.Numeric(15, 2), nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    created_at = db.Column(db.DateTime)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolio.id'))
    notes = db.Column(db.String(1000))
    
    # Relationships
    client = db.relationship('Client', back_populates='transactions')
    security = db.relationship('Security', back_populates='transactions')
    portfolio = db.relationship('Portfolio', back_populates='transactions')

class Holding(db.Model):
    __tablename__ = 'holding'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolio.id'))
    quantity = db.Column(db.Numeric(15, 4), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    average_price = db.Column(db.Numeric(15, 2), nullable=False)
    
    # Relationships
    client = db.relationship('Client', back_populates='holdings')
    security = db.relationship('Security', back_populates='holdings')
    portfolio = db.relationship('Portfolio', back_populates='holdings')

class Recommendation(db.Model):
    __tablename__ = 'recommendation'
    id = db.Column(db.Integer, primary_key=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)  # NULL for market-wide recommendations
    action = db.Column(db.String(20), nullable=False)  # buy, sell, hold (hold = allocation line, not an executable trade)
    quantity = db.Column(db.Numeric(15, 4), nullable=True)  # NULL for general recommendations
    target_price = db.Column(db.Numeric(10, 2))
    actual_price = db.Column(db.Numeric(15, 2), nullable=True)  # Price when trade is executed
    status = db.Column(db.String(20), default='pending')  # pending, sent, executed, cancelled, active (for market-wide)
    expiry_date = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime, nullable=True)
    sent_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    executed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    executed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    security = db.relationship('Security', back_populates='recommendations')
    client = db.relationship('Client', back_populates='recommendations')
    creator = db.relationship('User', foreign_keys=[created_by])
    sender = db.relationship('User', foreign_keys=[sent_by])
    executor = db.relationship('User', foreign_keys=[executed_by])
    session_id = db.Column(db.Integer, db.ForeignKey('recommendation_session.id'), nullable=True)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=True)
    batch_created_at = db.Column(db.DateTime, nullable=True)
    is_user_modified = db.Column(db.Boolean, default=False)  # Track if user modified this recommendation
    
    # Relationships
    session = db.relationship('RecommendationSession', back_populates='recommendations')
    asset_class = db.relationship('AssetClass', foreign_keys=[asset_class_id])

    @property
    def calculated_status(self):
        """Calculate status based on client_id and expiry_date"""
        if self.client_id:
            # Client-specific recommendation - use stored status
            return self.status
        else:
            # Market-wide recommendation - calculate based on expiry_date
            if not self.expiry_date:
                return 'active'
            if datetime.utcnow() > self.expiry_date:
                return 'expired'
            return 'active'
    
    @property
    def total_amount(self):
        """Calculate total amount based on actual or target price"""
        if not self.quantity:
            return None
        price = self.actual_price if self.actual_price else self.target_price
        if price:
            # Convert both quantity and price to float to avoid decimal/float operations
            quantity_float = float(self.quantity) if self.quantity else 0.0
            price_float = float(price) if price else 0.0
            return quantity_float * price_float
        return None
    
    @property
    def status_color(self):
        """Return Bootstrap color class based on status"""
        status_colors = {
            'pending': 'warning',
            'sent': 'info',
            'executed': 'success',
            'cancelled': 'danger',
            'active': 'primary',
            'expired': 'secondary'
        }
        return status_colors.get(self.status, 'primary')
    
    @property
    def calculated_status_color(self):
        """Return Bootstrap color class based on calculated status"""
        status_colors = {
            'pending': 'warning',
            'executed': 'success',
            'cancelled': 'danger',
            'active': 'primary',
            'expired': 'secondary'
        }
        return status_colors.get(self.calculated_status, 'primary')
    
    @classmethod
    def executable_trade_sql_filters(cls, client_id=None):
        """
        SQL criteria for recommended-trades lists (buy/sell with quantity; excludes hold allocation lines).
        Pass client_id to scope to one client; omit for all clients.
        """
        cid = cls.client_id == client_id if client_id is not None else cls.client_id.isnot(None)
        return (
            cid,
            cls.quantity.isnot(None),
            func.lower(func.trim(cls.action)).in_(("buy", "sell")),
        )

    @classmethod
    def client_trade_list_sql_filters(cls, client_id):
        """
        Per-client record-trade-details page: buy/sell executable rows plus HOLD allocation lines.
        Unified recommendations often save equity as HOLD while debt buys are BUY.
        """
        return (
            cls.client_id == client_id,
            cls.quantity.isnot(None),
            func.lower(func.trim(cls.action)).in_(("buy", "sell", "hold")),
        )

    @property
    def is_hold_line(self):
        """Allocation / maintain-position line from unified recommendations (not an executable trade)."""
        return (self.action or "").strip().lower() == "hold"

    @property
    def is_trade_recommendation(self):
        """Client-specific buy/sell recommendation with quantity (executable trade, not a hold line)."""
        if self.client_id is None or self.quantity is None:
            return False
        a = (self.action or "").strip().lower()
        return a in ("buy", "sell")

class RecommendationSession(db.Model):
    """Groups related recommendations generated together for a client"""
    __tablename__ = 'recommendation_session'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    session_name = db.Column(db.String(100), nullable=True)  # e.g., "Portfolio Rebalancing - Jan 2024"
    session_type = db.Column(db.String(50), default='rebalancing')  # rebalancing, new_investment, withdrawal
    investment_amount = db.Column(db.Numeric(15, 2), nullable=True)  # Total amount being invested/withdrawn
    status = db.Column(db.String(20), default='draft')  # draft, active, completed, cancelled
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    client = db.relationship('Client', back_populates='recommendation_sessions')
    creator = db.relationship('User', back_populates='created_recommendation_sessions')
    recommendations = db.relationship('Recommendation', back_populates='session', cascade='all, delete-orphan')
    asset_distributions = db.relationship('AssetClassDistribution', back_populates='session', cascade='all, delete-orphan')
    
    @property
    def total_recommendations(self):
        """Get total number of recommendations in this session"""
        return len(self.recommendations)
    
    @property
    def total_amount(self):
        """Calculate total amount of all recommendations in this session"""
        total = 0
        for rec in self.recommendations:
            if rec.total_amount:
                total += rec.total_amount
        return total
    
    @property
    def status_color(self):
        """Return Bootstrap color class based on status"""
        status_colors = {
            'draft': 'secondary',
            'active': 'primary',
            'completed': 'success',
            'cancelled': 'danger'
        }
        return status_colors.get(self.status, 'secondary')

class CallLog(db.Model):
    __tablename__ = 'call_log'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    call_date = db.Column(db.DateTime, nullable=False)
    call_type = db.Column(db.String(20), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationships
    client = db.relationship('Client', back_populates='call_logs')

class Document(db.Model):
    __tablename__ = 'document'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    document_type = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='active')  # active, archived
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    
    # Relationships
    client = db.relationship('Client', back_populates='documents')

class Portfolio(db.Model):
    __tablename__ = 'portfolio'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime)
    model_id = db.Column(db.Integer, db.ForeignKey('asset_allocation_model.id'))
    current_value = db.Column(db.Numeric(15, 2), default=0.00)
    status = db.Column(db.String(20), default='active')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Relationships
    client = db.relationship('Client', back_populates='portfolios')
    model = db.relationship('AssetAllocationModel')
    user = db.relationship('User', back_populates='portfolios', foreign_keys=[created_by])
    transactions = db.relationship('Transaction', back_populates='portfolio')
    holdings = db.relationship('Holding', back_populates='portfolio')
    monthly_investments = db.relationship('MonthlyInvestment', back_populates='portfolio')

    @property
    def status_color(self):
        """Return Bootstrap color class based on status"""
        status_colors = {
            'active': 'success',
            'inactive': 'secondary',
            'archived': 'warning',
            'pending': 'info'
        }
        return status_colors.get(self.status, 'primary')

class AssetClass(db.Model):
    __tablename__ = 'asset_class'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Tax optimiser / capital gains (same row as portfolio class — replaces asset_class_tax_ruleset)
    tax_ruleset = db.Column(db.String(32), nullable=False, default='equity')
    tax_stcg_rate = db.Column(db.Float, nullable=False, default=0.20)
    tax_ltcg_rate = db.Column(db.Float, nullable=False, default=0.125)
    tax_ltcg_exemption_limit = db.Column(db.Float, nullable=False, default=125000.0)
    tax_ltcg_minimum_months = db.Column(db.Integer, nullable=False, default=12)
    tax_ltcg_eligibility_mode = db.Column(db.String(32), nullable=True)
    tax_ltcg_holding_days = db.Column(db.Integer, nullable=True)
    tax_rules_active = db.Column(db.Boolean, nullable=False, default=True)
    tax_notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    securities = db.relationship('Security', back_populates='asset_class')
    asset_allocations = db.relationship('AssetAllocation', back_populates='asset_class')
    customizations = db.relationship('AssetClassCustomization', back_populates='asset_class')
    creator = db.relationship('User', back_populates='created_asset_classes', foreign_keys=[created_by])
    security_allocation_models = db.relationship('SecurityAllocationModel', back_populates='asset_class')
    distributions = db.relationship('AssetClassDistribution', back_populates='asset_class')

    def tax_rules_api_dict(self):
        """Shape expected by enhanced tax optimiser UI / asset-classes API."""
        return {
            "id": self.id,
            "asset_class_id": self.id,
            "ruleset": (self.tax_ruleset or "equity").strip(),
            "asset_class_name": self.name or "",
            "stcg_rate": float(self.tax_stcg_rate or 0.0),
            "ltcg_rate": float(self.tax_ltcg_rate or 0.0),
            "ltcg_exemption_limit": float(self.tax_ltcg_exemption_limit or 0.0),
            "ltcg_minimum_months": int(self.tax_ltcg_minimum_months or 12),
            "ltcg_eligibility_mode": (self.tax_ltcg_eligibility_mode or "").strip() or None,
            "ltcg_holding_days": int(self.tax_ltcg_holding_days)
            if self.tax_ltcg_holding_days is not None
            else None,
            "is_active": bool(self.tax_rules_active),
            "notes": self.tax_notes or "",
        }

class AssetClassDistribution(db.Model):
    """Stores asset-level distribution summary for each asset class in a recommendation session"""
    __tablename__ = 'asset_class_distribution'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('recommendation_session.id'), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    
    # From model/portfolio analysis
    target_weight = db.Column(db.Numeric(5, 2))  # Target allocation % (e.g., 60.0 for 60%)
    current_weight = db.Column(db.Numeric(5, 2))  # Current allocation % in portfolio
    allocated_amount = db.Column(db.Numeric(15, 2))  # Total amount allocated to this asset class
    required_change = db.Column(db.Numeric(15, 2))  # Amount to buy/sell (+buy, -sell)
    
    # Aggregated from recommendations
    security_count = db.Column(db.Integer, default=0)  # Number of securities in Section 1
    total_recommended_amount = db.Column(db.Numeric(15, 2), default=0)  # Sum of all recommendation amounts
    
    # Metadata
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, onupdate=db.func.current_timestamp())
    
    # Relationships
    session = db.relationship('RecommendationSession', back_populates='asset_distributions')
    asset_class = db.relationship('AssetClass', back_populates='distributions')
    
    # Unique constraint: one distribution per asset class per session
    __table_args__ = (
        db.UniqueConstraint('session_id', 'asset_class_id', name='uq_session_asset_class'),
    )
    
    def __repr__(self):
        return f'<AssetClassDistribution session_id={self.session_id} asset_class_id={self.asset_class_id}>'

class AssetAllocationModel(db.Model):
    __tablename__ = 'asset_allocation_model'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    risk_profile = db.Column(db.String(50))  # Conservative, Moderate, Aggressive
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    asset_allocations = db.relationship('AssetAllocation', back_populates='model')
    creator = db.relationship('User', back_populates='created_models', foreign_keys=[created_by])

class AssetAllocation(db.Model):
    __tablename__ = 'asset_allocation'
    id = db.Column(db.Integer, primary_key=True)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    model_id = db.Column(db.Integer, db.ForeignKey('asset_allocation_model.id'), nullable=False)
    allocation_percentage = db.Column(db.Numeric(5, 2), nullable=False)
    min_allocation = db.Column(db.Numeric(5, 2))
    max_allocation = db.Column(db.Numeric(5, 2))
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
    
    # Relationships
    model = db.relationship('AssetAllocationModel', back_populates='asset_allocations')
    asset_class = db.relationship('AssetClass', back_populates='asset_allocations')

class SecurityAllocationModel(db.Model):
    __tablename__ = 'security_allocation_model'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    
    # Relationships
    security_allocations = db.relationship('SecurityAllocation', back_populates='model')
    model_assignments = db.relationship('ModelAssignment', back_populates='stock_model', foreign_keys='ModelAssignment.stock_model_id')
    creator = db.relationship('User', back_populates='created_security_models', foreign_keys=[created_by])
    asset_class = db.relationship('AssetClass', back_populates='security_allocation_models')

class ModelAllocation(db.Model):
    __tablename__ = 'model_allocation'
    id = db.Column(db.Integer, primary_key=True)
    customization_id = db.Column(db.Integer, db.ForeignKey('asset_class_customization.id'), nullable=False)
    security_model_id = db.Column(db.Integer, db.ForeignKey('security_allocation_model.id'), nullable=False)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    allocation_percentage = db.Column(db.Numeric(5, 2), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    security = db.relationship('Security', back_populates='model_allocations')
    model_assignments = db.relationship('ModelAssignment', back_populates='model_allocation')

class Role(db.Model):
    """Dynamic roles that can be created and customized"""
    __tablename__ = 'role'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # e.g., 'senior_advisor', 'analyst', 'admin'
    display_name = db.Column(db.String(100), nullable=False)  # e.g., 'Senior Advisor'
    description = db.Column(db.Text)
    is_system_role = db.Column(db.Boolean, default=False)  # admin, manager, advisor are system roles
    parent_role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=True)  # For inheritance
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    parent_role = db.relationship('Role', remote_side=[id], backref='child_roles')
    permissions = db.relationship('RolePermission', back_populates='role', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Role {self.name}>'
    
    def get_all_permissions(self):
        """Get all permissions including inherited ones"""
        permissions = {}
        
        # Get direct permissions
        for perm in self.permissions:
            permissions[perm.route_endpoint] = perm.has_access
        
        # Get inherited permissions from parent (if any)
        if self.parent_role:
            parent_perms = self.parent_role.get_all_permissions()
            # Merge: direct permissions override inherited ones
            for route, has_access in parent_perms.items():
                if route not in permissions:
                    permissions[route] = has_access
        
        return permissions

class RolePermission(db.Model):
    """Permissions for each role"""
    __tablename__ = 'role_permission'
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    route_endpoint = db.Column(db.String(200), nullable=False)  # e.g., 'main.clients'
    has_access = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    role = db.relationship('Role', back_populates='permissions')
    
    # Unique constraint: one permission per route per role
    __table_args__ = (db.UniqueConstraint('role_id', 'route_endpoint', name='_role_route_uc'),)
    
    def __repr__(self):
        return f'<RolePermission {self.role.name}:{self.route_endpoint}>'

class ModelAssignment(db.Model):
    __tablename__ = 'model_assignment'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    asset_model_id = db.Column(db.Integer, db.ForeignKey('asset_allocation_model.id'))
    stock_model_id = db.Column(db.Integer, db.ForeignKey('security_allocation_model.id'))
    assigned_at = db.Column(db.DateTime)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    model_allocation_id = db.Column(db.Integer, db.ForeignKey('model_allocation.id'))
    
    # Relationships
    client = db.relationship('Client', back_populates='model_assignments')
    asset_model = db.relationship('AssetAllocationModel', foreign_keys=[asset_model_id])
    stock_model = db.relationship('SecurityAllocationModel', back_populates='model_assignments', foreign_keys=[stock_model_id])
    user = db.relationship('User', back_populates='model_assignments', foreign_keys=[assigned_by])
    model_allocation = db.relationship('ModelAllocation', back_populates='model_assignments')
    asset_class_security_models = db.relationship(
        'ModelAssignmentSecurityModel',
        back_populates='model_assignment',
        cascade='all, delete-orphan'
    )


class ModelAssignmentSecurityModel(db.Model):
    """
    Stores the per-asset-class SecurityAllocationModel selection for a given client ModelAssignment.
    This enables having a different "security distribution model" per asset class (Equity, Fixed Income, etc.).
    """
    __tablename__ = 'model_assignment_security_model'

    id = db.Column(db.Integer, primary_key=True)
    model_assignment_id = db.Column(db.Integer, db.ForeignKey('model_assignment.id', ondelete='CASCADE'), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    security_model_id = db.Column(db.Integer, db.ForeignKey('security_allocation_model.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())

    __table_args__ = (
        db.UniqueConstraint('model_assignment_id', 'asset_class_id', name='uq_model_assignment_asset_class'),
        db.Index('idx_masm_model_assignment_id', 'model_assignment_id'),
        db.Index('idx_masm_asset_class_id', 'asset_class_id'),
    )

    model_assignment = db.relationship('ModelAssignment', back_populates='asset_class_security_models')
    asset_class = db.relationship('AssetClass')
    security_model = db.relationship('SecurityAllocationModel')

class User(db.Model, UserMixin):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    is_active = db.Column(db.Boolean, default=True)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='advisor')  # manager, advisor, admin (deprecated, use role_id)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=True)  # New: foreign key to Role table
    email_verified = db.Column(db.Boolean, default=False)
    password_reset_token = db.Column(db.String(255), unique=True)
    password_reset_expires = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # 2FA fields
    two_factor_enabled = db.Column(db.Boolean, default=False)
    two_factor_secret = db.Column(db.String(32))  # TOTP secret key
    two_factor_backup_codes = db.Column(db.Text)  # JSON array of backup codes
    two_factor_required = db.Column(db.Boolean, default=False)  # Force 2FA for this user
    two_factor_setup_reminder = db.Column(db.DateTime)  # Last reminder sent
    # Google Calendar (OAuth): used to create/update events when user is assignee on OpsTasks
    google_calendar_refresh_token = db.Column(db.Text, nullable=True)

    # Relationships
    clients = db.relationship('Client', back_populates='user', foreign_keys='Client.user_id')
    assigned_clients = db.relationship('Client', back_populates='advisor', foreign_keys='Client.advisor_id')
    client_assignments = db.relationship('ClientAdvisorAssignment', back_populates='advisor', foreign_keys='ClientAdvisorAssignment.advisor_id')
    portfolios = db.relationship('Portfolio', back_populates='user', foreign_keys='Portfolio.created_by')
    monthly_investments = db.relationship('MonthlyInvestment', back_populates='creator', foreign_keys='MonthlyInvestment.created_by')
    workflow_actions = db.relationship('WorkflowAction', back_populates='user', foreign_keys='WorkflowAction.user_id')
    model_assignments = db.relationship('ModelAssignment', back_populates='user', foreign_keys='ModelAssignment.assigned_by')
    created_asset_classes = db.relationship('AssetClass', back_populates='creator', foreign_keys='AssetClass.created_by')
    created_securities = db.relationship('Security', back_populates='creator', foreign_keys='Security.created_by')
    created_models = db.relationship('AssetAllocationModel', back_populates='creator', foreign_keys='AssetAllocationModel.created_by')
    created_security_models = db.relationship('SecurityAllocationModel', back_populates='creator', foreign_keys='SecurityAllocationModel.created_by')
    created_workflows = db.relationship('Workflow', back_populates='creator', foreign_keys='Workflow.created_by')
    created_recommendation_sessions = db.relationship('RecommendationSession', back_populates='creator', foreign_keys='RecommendationSession.created_by')
    requested_reviews = db.relationship('Review', back_populates='user', foreign_keys='Review.requested_by')
    shared_reviews = db.relationship('ReviewShare', back_populates='user', foreign_keys='ReviewShare.shared_by')
    created_templates = db.relationship('ReviewTemplate', back_populates='user', foreign_keys='ReviewTemplate.created_by')
    ml_feedback = db.relationship('MLRecommendationFeedback', back_populates='user')
    ml_training_history = db.relationship('MLModelTrainingHistory', back_populates='user')
    recommendation_preferences = db.relationship('UserRecommendationPreference', back_populates='user')
    created_agreement_templates = db.relationship('AgreementTemplate', back_populates='creator', foreign_keys='AgreementTemplate.created_by')
    created_agreements = db.relationship('Agreement', back_populates='creator', foreign_keys='Agreement.created_by')
    created_review_schedules = db.relationship('ReviewSchedule', back_populates='creator', foreign_keys='ReviewSchedule.created_by')
    created_review_workflows = db.relationship('ReviewWorkflow', back_populates='creator', foreign_keys='ReviewWorkflow.created_by')
    attendance_records = db.relationship('Attendance', back_populates='user')
    stipend_config = db.relationship('UserStipend', back_populates='user', uselist=False)
    claims = db.relationship('UserClaim', back_populates='user', foreign_keys='UserClaim.user_id')
    processed_claims = db.relationship('UserClaim', back_populates='processor', foreign_keys='UserClaim.processed_by')
    monthly_salaries = db.relationship('MonthlySalary', back_populates='user', foreign_keys='MonthlySalary.user_id')
    processed_salaries = db.relationship('MonthlySalary', back_populates='processor', foreign_keys='MonthlySalary.paid_by')
    role_obj = db.relationship('Role', backref='users', foreign_keys=[role_id])  # Relationship to Role model

    @property
    def is_active_property(self):
        return self.is_active

    @property
    def is_manager(self):
        """Check if user is a manager"""
        # Check new role system first, then fall back to old system
        if self.role_obj:
            return self.role_obj.name == 'manager' or self.is_admin
        return self.role == 'manager' or self.is_admin

    @property
    def is_advisor(self):
        """Check if user is an advisor"""
        # Check new role system first, then fall back to old system
        if self.role_obj:
            return self.role_obj.name == 'advisor'
        return self.role == 'advisor'

    @property
    def is_ops_manager(self):
        """True for ops_manager or sales_ops_manager (see access_control.user_is_ops_manager)."""
        from access_control import user_is_ops_manager

        return user_is_ops_manager(self)

    @property
    def can_access_holdings_report(self):
        from access_control import user_can_access_holdings_report

        return user_can_access_holdings_report(self)

    def has_route_access(self, route_endpoint):
        """Check if user has access to a route"""
        # Admins always have access
        if self.is_admin:
            return True
        
        # Check new role system
        if self.role_obj:
            return self.role_obj.get_all_permissions().get(route_endpoint, False)
        
        # Fall back to old role-based system for backward compatibility
        # This can be removed once all users are migrated
        return False

    def can_access_client(self, client):
        """Check if user can access a specific client"""
        if self.is_admin or self.is_manager or self.is_ops_manager:
            return True
        return client.advisor_id == self.id

    def get_accessible_clients(self):
        """Get clients that this user can access"""
        try:
            if self.is_admin or self.is_manager or self.is_ops_manager:
                clients = Client.query.all()
            else:
                clients = Client.query.filter_by(advisor_id=self.id).all()
            return list(clients) if clients else []
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error getting accessible clients for user {self.id}: {str(e)}", exc_info=True)
            return []


    def set_password(self, password):
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        self.password_hash = hashed.decode('utf-8')

    def check_password(self, password):
        if not self.password_hash:
            return False
        try:
            stored_hash = self.password_hash.encode('utf-8')
            return bcrypt.checkpw(password.encode('utf-8'), stored_hash)
        except Exception as e:
            print(f"Error checking password: {e}")
            return False

class Cashflow(db.Model):
    __tablename__ = 'cashflow'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # INFLOW or OUTFLOW
    description = db.Column(db.Text)
    date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Relationships
    client = db.relationship('Client', backref=db.backref('cashflows', lazy=True))
    creator = db.relationship('User', backref=db.backref('created_cashflows', lazy=True))

    def __repr__(self):
        return f'<Cashflow {self.id}: {self.amount} {self.type}>'

class Meeting(db.Model):
    __tablename__ = 'meeting'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id', ondelete='SET NULL'), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    meeting_date = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.Integer)
    status = db.Column(db.String(20))
    created_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    google_calendar_event_id = db.Column(db.String(280), nullable=True)
    google_calendar_organizer_user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True
    )

    # Relationships
    client = db.relationship('Client', back_populates='meetings')
    lead = db.relationship('Lead', back_populates='meetings')
    creator = db.relationship('User', foreign_keys=[created_by], backref='meetings_created')
    calendar_organizer = db.relationship(
        'User', foreign_keys=[google_calendar_organizer_user_id], backref='meetings_calendar_hosted'
    )
    participants_assoc = db.relationship(
        'MeetingParticipant',
        back_populates='meeting',
        cascade='all, delete-orphan',
        lazy='selectin',
    )

    def __repr__(self):
        return f'<Meeting {self.title}>'


class MeetingParticipant(db.Model):
    """Internal team members invited to a meeting (shown in app + Google Calendar attendees)."""
    __tablename__ = 'meeting_participant'

    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)

    meeting = db.relationship('Meeting', back_populates='participants_assoc')
    user = db.relationship('User', backref=db.backref('meeting_participant_rows', lazy='dynamic'))

class ServiceTicket(db.Model):
    """Service ticket system for client support and issue tracking"""
    __tablename__ = 'service_ticket'
    
    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(50), unique=True, nullable=False)  # Auto-generated ticket number
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    
    # Ticket details
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    alert_type = db.Column(db.String(50), nullable=False)  # Links to alert system type
    priority = db.Column(db.String(20), nullable=False, default='medium')  # low, medium, high, critical
    status = db.Column(db.String(20), nullable=False, default='open')  # open, in_progress, snoozed, resolved, closed, cancelled
    
    # SLA tracking
    sla_hours = db.Column(db.Integer, nullable=True)  # SLA in hours from SLAConfiguration
    sla_deadline = db.Column(db.DateTime, nullable=True)  # Calculated deadline
    sla_breached = db.Column(db.Boolean, default=False)  # Whether SLA has been breached
    
    # Assignment
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    
    # Resolution
    resolution_notes = db.Column(db.Text, nullable=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Snooze (see scripts/migration/add_snooze_fields_to_service_ticket.sql)
    pre_snooze_status = db.Column(db.String(20), nullable=True)
    snoozed_until = db.Column(db.DateTime, nullable=True)
    snoozed_at = db.Column(db.DateTime, nullable=True)
    snoozed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    snooze_reason = db.Column(db.Text, nullable=True)
    
    # Relationships
    client = db.relationship('Client', backref='service_tickets')
    assigned_user = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_tickets')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_tickets')
    resolver = db.relationship('User', foreign_keys=[resolved_by], backref='resolved_tickets')
    snooze_user = db.relationship('User', foreign_keys=[snoozed_by], backref='tickets_snoozed_by_me')
    
    @property
    def is_overdue(self):
        """Check if ticket is overdue based on SLA"""
        if not self.sla_deadline or self.status in ['resolved', 'closed', 'cancelled']:
            return False
        return datetime.utcnow() > self.sla_deadline
    
    @property
    def hours_remaining(self):
        """Get hours remaining until SLA deadline"""
        if not self.sla_deadline or self.status in ['resolved', 'closed', 'cancelled']:
            return None
        remaining = (self.sla_deadline - datetime.utcnow()).total_seconds() / 3600
        return max(0, remaining)
    
    @property
    def age_hours(self):
        """Get ticket age in hours"""
        return int((datetime.utcnow() - self.created_at).total_seconds() / 3600)
    
    def __repr__(self):
        return f'<ServiceTicket {self.ticket_number}>'

class Lead(db.Model):
    __tablename__ = 'lead'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    source = db.Column(db.String(50))
    status = db.Column(db.String(50))
    notes = db.Column(db.Text)  # Changed from notes_text to notes to match database
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    billing_created = db.Column(db.Boolean, default=False)
    investment_schedule_created = db.Column(db.Boolean, default=False)
    review_schedule_created = db.Column(db.Boolean, default=False)
    name = db.Column(db.String(100), nullable=False, default='')
    email = db.Column(db.String(120), nullable=False, default='')  # Fixed to match database NOT NULL
    phone = db.Column(db.String(50), nullable=False, default='')
    referral_by = db.Column(db.String(100), nullable=True)  # Name of person who gave referral
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, default=1)
    is_active = db.Column(db.Boolean, default=True)  # Track if lead is active or inactive
    temperature = db.Column(db.Integer, default=3)  # Lead temperature rating (1-5)

    # Relationships
    client = db.relationship('Client', back_populates='lead', foreign_keys=[client_id])
    meetings = db.relationship('Meeting', back_populates='lead', cascade='all, delete-orphan')
    call_logs = db.relationship('LeadCallLog', back_populates='lead', order_by='desc(LeadCallLog.call_date)', cascade='all, delete-orphan')
    user = db.relationship('User', backref=db.backref('leads', lazy=True))
    agreements = db.relationship('Agreement', back_populates='lead', cascade='all, delete-orphan')
    agreement_variables = db.relationship('AgreementVariables', back_populates='lead', cascade='all, delete-orphan')
    
    # Workflow relationship
    @property
    def workflow(self):
        """Get the workflow for this lead"""
        from workflow_service import WorkflowService
        return WorkflowService.get_workflow('lead', self.id)
    
    @property
    def latest_note(self):
        """Get the latest note for this lead from call logs"""
        if self.call_logs and len(self.call_logs) > 0:
            latest_call = self.call_logs[0]  # Already ordered by desc(call_date)
            return latest_call.notes if latest_call.notes else None
        return None

    def __repr__(self):
        return f'<Lead {self.name}>'

class MonthlyInvestment(db.Model):
    __tablename__ = 'monthly_investment'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolio.id'), nullable=False)
    planned_amount = db.Column(db.Numeric(precision=15, scale=2), nullable=False)
    investment_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    client = db.relationship('Client', back_populates='monthly_investments')
    portfolio = db.relationship('Portfolio', back_populates='monthly_investments')
    creator = db.relationship('User', back_populates='monthly_investments', foreign_keys=[created_by])
    workflow = db.relationship('Workflow', back_populates='monthly_investment', uselist=False)

    @property
    def status_color(self):
        """Return Bootstrap color class based on status"""
        status_colors = {
            'pending': 'warning',
            'PENDING': 'warning',
            'completed': 'success',
            'COMPLETED': 'success',
            'cancelled': 'danger',
            'CANCELLED': 'danger',
            'active': 'info',
            'ACTIVE': 'info'
        }
        return status_colors.get(self.status, 'secondary')

class MonthlyInvestmentSchedule(db.Model):
    """Configuration table for recurring monthly investments per client"""
    __tablename__ = 'monthly_investment_schedule'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, unique=True)
    planned_amount = db.Column(db.Numeric(precision=15, scale=2), nullable=False)
    day_of_month = db.Column(db.Integer, nullable=False, default=1)  # Day of month (1-31) when investment should be created
    is_active = db.Column(db.Boolean, default=True)
    start_date = db.Column(db.Date, nullable=False)  # When this schedule should start
    end_date = db.Column(db.Date, nullable=True)  # Optional end date (None = ongoing)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # New cadence options
    # Portfolio change cadence: every N months (1=monthly)
    change_frequency_months = db.Column(db.Integer, nullable=False, default=1)
    # Withdrawal cadence: AD_HOC, MONTHLY, or N_MONTHS
    withdrawal_mode = db.Column(db.String(20), nullable=False, default='AD_HOC')
    # Used only when withdrawal_mode == 'N_MONTHS'
    withdrawal_frequency_months = db.Column(db.Integer, nullable=False, default=0)
    # Flexible amounts
    allow_zero_amount = db.Column(db.Boolean, nullable=False, default=True)
    allow_negative_amount = db.Column(db.Boolean, nullable=False, default=True)
    
    # Relationships
    client = db.relationship('Client', back_populates='monthly_investment_schedule')
    creator = db.relationship('User', foreign_keys=[created_by])
    
    def __repr__(self):
        return f'<MonthlyInvestmentSchedule Client {self.client_id}: ₹{self.planned_amount} on day {self.day_of_month}>'

class Workflow(db.Model):
    __tablename__ = 'workflow'
    id = db.Column(db.Integer, primary_key=True)
    monthly_investment_id = db.Column(db.Integer, db.ForeignKey('monthly_investment.id'), nullable=False)
    current_stage = db.Column(db.String(20), default='FUNDS')
    planned_amount = db.Column(db.Numeric(precision=15, scale=2), nullable=False)
    actual_amount = db.Column(db.Numeric(precision=15, scale=2))
    investment_date = db.Column(db.Date, nullable=False)
    target_completion_date = db.Column(db.Date, nullable=False)
    actual_completion_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    # Snapshot of MonthlyInvestmentSchedule.notes at workflow creation time
    schedule_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Archive fields
    is_archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime)
    archived_reason = db.Column(db.String(200))
    
    # Relationships
    monthly_investment = db.relationship('MonthlyInvestment', back_populates='workflow')
    creator = db.relationship('User', back_populates='created_workflows', foreign_keys=[created_by])
    actions = db.relationship('WorkflowAction', back_populates='workflow', cascade='all, delete-orphan')

    def get_stage_status(self):
        stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
        # Handle legacy stages: ICR and INVESTMENT/CHANGES/REDEMPTION
        current_stage = self.current_stage
        if current_stage == 'ICR' or current_stage == 'INVESTMENT/CHANGES/REDEMPTION':
            current_stage = 'FUNDS'
        
        current_index = stages.index(current_stage)
        return {
            'current_stage': current_stage,
            'completed_stages': stages[:current_index],
            'pending_stages': stages[current_index + 1:]
        }
    
    def __repr__(self):
        return f'<Workflow {self.current_stage}>'

class WorkflowAction(db.Model):
    __tablename__ = 'workflow_action'
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    action_date = db.Column(db.DateTime, nullable=False)
    amount = db.Column(db.Numeric(precision=15, scale=2), default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    workflow = db.relationship('Workflow', back_populates='actions')
    user = db.relationship('User', back_populates='workflow_actions', foreign_keys=[user_id])

    def __repr__(self):
        return f'<WorkflowAction {self.action_type}>'

class AssetClassCustomization(db.Model):
    __tablename__ = 'asset_class_customization'
    id = db.Column(db.Integer, primary_key=True)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    client_assignment_id = db.Column(db.Integer, db.ForeignKey('model_assignment.id'), nullable=False)
    min_allocation = db.Column(db.Float)
    max_allocation = db.Column(db.Float)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    asset_class = db.relationship('AssetClass', back_populates='customizations')

class LeadCallLog(db.Model):
    __tablename__ = 'lead_call_log'
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False)
    call_date = db.Column(db.DateTime, nullable=False)
    call_type = db.Column(db.String(20), nullable=False)  # incoming, outgoing
    duration = db.Column(db.Integer)  # in seconds
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    
    # Relationships
    lead = db.relationship('Lead', back_populates='call_logs')



# New Generic Workflow System
class GenericWorkflow(db.Model):
    __tablename__ = 'generic_workflow'
    id = db.Column(db.Integer, primary_key=True)
    module_type = db.Column(db.String(50), nullable=False)  # 'lead', 'client_onboarding', 'portfolio', 'transaction'
    record_id = db.Column(db.Integer, nullable=False)  # ID of the record in the respective module
    current_stage = db.Column(db.String(50), nullable=False)
    target_completion_date = db.Column(db.Date, nullable=False)
    actual_completion_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='active')  # active, completed, cancelled, paused
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    creator = db.relationship('User', backref='created_generic_workflows', foreign_keys=[created_by])
    actions = db.relationship('GenericWorkflowAction', back_populates='workflow', order_by='GenericWorkflowAction.action_date.desc()', cascade='all, delete-orphan')

    def get_stage_status(self):
        """Get status based on target completion date"""
        if self.status == 'completed':
            return 'COMPLETED'
        if self.target_completion_date and datetime.now().date() > self.target_completion_date:
            return 'OVERDUE'
        return 'ON_TIME'
    
    def __repr__(self):
        return f'<GenericWorkflow {self.module_type}:{self.record_id} - {self.current_stage}>'

class GenericWorkflowAction(db.Model):
    __tablename__ = 'generic_workflow_action'
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('generic_workflow.id'), nullable=False)
    action_type = db.Column(db.String(100), nullable=False)
    action_date = db.Column(db.DateTime, nullable=False)
    amount = db.Column(db.Numeric(precision=15, scale=2), default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    workflow = db.relationship('GenericWorkflow', back_populates='actions')
    user = db.relationship('User', backref='generic_workflow_actions', foreign_keys=[user_id])

    def __repr__(self):
        return f'<GenericWorkflowAction {self.action_type}>'

# Performance Tracking Models
class PortfolioSnapshot(db.Model):
    __tablename__ = 'portfolio_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    total_value = db.Column(db.Numeric(15, 2), nullable=False)
    total_invested = db.Column(db.Numeric(15, 2), nullable=False)
    total_withdrawn = db.Column(db.Numeric(15, 2), nullable=False)
    net_investment = db.Column(db.Numeric(15, 2), nullable=False)
    absolute_return = db.Column(db.Numeric(15, 2), nullable=False)
    absolute_return_percent = db.Column(db.Numeric(10, 4), nullable=False)
    xirr = db.Column(db.Numeric(10, 4), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    client = db.relationship('Client', back_populates='portfolio_snapshots')
    holdings_snapshots = db.relationship('HoldingSnapshot', back_populates='portfolio_snapshot')

class HoldingSnapshot(db.Model):
    __tablename__ = 'holding_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    portfolio_snapshot_id = db.Column(db.Integer, db.ForeignKey('portfolio_snapshot.id'), nullable=False)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    quantity = db.Column(db.Numeric(15, 4), nullable=False)
    average_price = db.Column(db.Numeric(15, 4), nullable=False)
    current_price = db.Column(db.Numeric(15, 4), nullable=False)
    current_value = db.Column(db.Numeric(15, 2), nullable=False)
    unrealized_pnl = db.Column(db.Numeric(15, 2), nullable=False)
    unrealized_pnl_percent = db.Column(db.Numeric(10, 4), nullable=False)
    allocation_percent = db.Column(db.Numeric(10, 4), nullable=False)
    
    # Relationships
    portfolio_snapshot = db.relationship('PortfolioSnapshot', back_populates='holdings_snapshots')
    security = db.relationship('Security')

class Benchmark(db.Model):
    __tablename__ = 'benchmark'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    benchmark_data = db.relationship('BenchmarkData', back_populates='benchmark')

class BenchmarkData(db.Model):
    __tablename__ = 'benchmark_data'
    id = db.Column(db.Integer, primary_key=True)
    benchmark_id = db.Column(db.Integer, db.ForeignKey('benchmark.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    price = db.Column(db.Numeric(15, 4), nullable=False)
    return_percent = db.Column(db.Numeric(10, 4), nullable=True)
    cumulative_return = db.Column(db.Numeric(10, 4), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    benchmark = db.relationship('Benchmark', back_populates='benchmark_data')

class ModelPerformance(db.Model):
    __tablename__ = 'model_performance'
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, nullable=False)  # Can be asset_model_id or security_model_id
    model_type = db.Column(db.String(20), nullable=False)  # 'asset' or 'security'
    date = db.Column(db.Date, nullable=False)
    total_value = db.Column(db.Numeric(15, 2), nullable=False)
    return_percent = db.Column(db.Numeric(10, 4), nullable=True)
    cumulative_return = db.Column(db.Numeric(10, 4), nullable=True)
    benchmark_id = db.Column(db.Integer, db.ForeignKey('benchmark.id'), nullable=True)
    benchmark_return = db.Column(db.Numeric(10, 4), nullable=True)
    excess_return = db.Column(db.Numeric(10, 4), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    
    # Relationships
    benchmark = db.relationship('Benchmark')

class SecurityDailyUpdate(db.Model):
    __tablename__ = 'security_daily_update'
    id = db.Column(db.Integer, primary_key=True)
    update_date = db.Column(db.Date, nullable=False, unique=True)
    update_type = db.Column(db.String(50), nullable=False)  # 'market_close', 'manual', 'scheduled'
    updated_securities_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    creator = db.relationship('User', backref='security_daily_updates', foreign_keys=[created_by])
    
    def __repr__(self):
        return f'<SecurityDailyUpdate {self.update_date}: {self.updated_securities_count} securities>'

class ClientAdvisorAssignment(db.Model):
    __tablename__ = 'client_advisor_assignment'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    advisor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    
    # Relationships
    client = db.relationship('Client', back_populates='client_assignments')
    advisor = db.relationship('User', back_populates='client_assignments', foreign_keys=[advisor_id])
    assigned_by_user = db.relationship('User', foreign_keys=[assigned_by])

    def __repr__(self):
        return f'<ClientAdvisorAssignment {self.client_id} -> {self.advisor_id}>' 

# Review Models
class Review(db.Model):
    """Client review reports"""
    __tablename__ = 'review'
    
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    
    # Review metadata
    review_type = db.Column(db.String(50), nullable=False)  # comprehensive, performance, allocation, etc.
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    
    # Review data
    review_data = db.Column(db.JSON, nullable=True)  # Full review data as JSON
    
    # Status
    status = db.Column(db.String(20), default='generating')  # generating, completed, failed
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # User who requested the review
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Error information
    error_message = db.Column(db.Text, nullable=True)
    
    # Relationships
    client = db.relationship('Client', back_populates='reviews')
    user = db.relationship('User', back_populates='requested_reviews')
    sections = db.relationship('ReviewSection', back_populates='review')
    shares = db.relationship('ReviewShare', back_populates='review')
    ml_feedback = db.relationship('MLRecommendationFeedback', back_populates='review')

class ReviewSection(db.Model):
    """Individual sections of a review"""
    __tablename__ = 'review_section'
    
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('review.id'), nullable=False)
    
    # Section metadata
    section_type = db.Column(db.String(50), nullable=False)  # performance, allocation, transactions, etc.
    section_data = db.Column(db.JSON, nullable=True)  # Section data as JSON
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Error information
    error_message = db.Column(db.Text, nullable=True)
    
    # Relationships
    review = db.relationship('Review', back_populates='sections')

class ReviewShare(db.Model):
    """Client-shared versions of reviews"""
    __tablename__ = 'review_share'
    
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('review.id'), nullable=False)
    
    # Share metadata
    shared_at = db.Column(db.DateTime, default=datetime.utcnow)
    shared_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Client-shared data (filtered version)
    shared_data = db.Column(db.JSON, nullable=True)  # Filtered review data for client
    
    # Delivery information
    delivery_method = db.Column(db.String(20), default='email')  # email, download, etc.
    delivery_status = db.Column(db.String(20), default='pending')  # pending, sent, delivered
    
    # Client access
    access_token = db.Column(db.String(100), nullable=True)  # For secure client access
    expires_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    review = db.relationship('Review', back_populates='shares')
    user = db.relationship('User', back_populates='shared_reviews')

class ReviewTemplate(db.Model):
    """Review templates for different types"""
    __tablename__ = 'review_template'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    # Template configuration
    template_type = db.Column(db.String(50), nullable=False)  # comprehensive, performance, etc.
    sections = db.Column(db.JSON, nullable=True)  # Which sections to include
    
    # Template content
    template_data = db.Column(db.JSON, nullable=True)  # Template structure and styling
    
    # Metadata
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    user = db.relationship('User', back_populates='created_templates') 

class MLRecommendationFeedback(db.Model):
    """User feedback on ML recommendations"""
    __tablename__ = 'ml_recommendation_feedback'
    
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('review.id'), nullable=False)
    recommendation_id = db.Column(db.String(100), nullable=False)  # Unique ID within review
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Feedback data
    rating = db.Column(db.Integer)  # 1-5 scale
    implemented = db.Column(db.Boolean, default=False)  # Was recommendation implemented?
    ignored = db.Column(db.Boolean, default=False)  # Was recommendation ignored?
    feedback_text = db.Column(db.Text)  # User comments
    
    # Outcome tracking
    outcome_rating = db.Column(db.Integer)  # 1-5 scale for actual outcome
    outcome_notes = db.Column(db.Text)  # Notes on actual outcome
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    review = db.relationship('Review', back_populates='ml_feedback')
    user = db.relationship('User', back_populates='ml_feedback')

class MLModelTrainingHistory(db.Model):
    """History of ML model training sessions"""
    __tablename__ = 'ml_model_training_history'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Training session info
    training_date = db.Column(db.DateTime, default=datetime.utcnow)
    model_type = db.Column(db.String(50), nullable=False)  # 'risk', 'performance', 'allocation'
    
    # Training metrics
    training_samples = db.Column(db.Integer)
    validation_accuracy = db.Column(db.Float)
    test_accuracy = db.Column(db.Float)
    
    # Model performance
    model_version = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    
    # Training details
    training_duration = db.Column(db.Float)  # seconds
    hyperparameters = db.Column(db.JSON)
    feature_importance = db.Column(db.JSON)
    
    # User who initiated training
    trained_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Relationships
    user = db.relationship('User', back_populates='ml_training_history')

class UserRecommendationPreference(db.Model):
    """User preferences for recommendation types and styles"""
    __tablename__ = 'user_recommendation_preference'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Preference settings
    risk_tolerance = db.Column(db.String(20))  # 'conservative', 'moderate', 'aggressive'
    preferred_categories = db.Column(db.JSON)  # ['allocation', 'risk', 'timing']
    notification_preferences = db.Column(db.JSON)  # Email, SMS, etc.
    
    # Recommendation style preferences
    detail_level = db.Column(db.String(20))  # 'brief', 'detailed', 'technical'
    priority_threshold = db.Column(db.String(20))  # 'low', 'medium', 'high'
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='recommendation_preferences') 

class AgreementTemplate(db.Model):
    __tablename__ = 'agreement_template'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    template_file_path = db.Column(db.String(500), nullable=False)  # Path to uploaded template file
    template_type = db.Column(db.String(50), nullable=False)  # 'doc', 'pdf', 'html'
    variables = db.Column(db.Text)  # JSON string of variables found in template
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    creator = db.relationship('User', back_populates='created_agreement_templates', foreign_keys=[created_by])
    agreements = db.relationship('Agreement', back_populates='template')

class Agreement(db.Model):
    __tablename__ = 'agreement'
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey('agreement_template.id'), nullable=True)  # Nullable for existing agreements
    agreement_data = db.Column(db.Text)
    generated_pdf_path = db.Column(db.String(500))
    docx_path = db.Column(db.String(500))  # Path to generated DOCX file
    status = db.Column(db.String(50), default='draft')
    sent_date = db.Column(db.DateTime)
    signed_date = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    lead = db.relationship('Lead', back_populates='agreements')
    template = db.relationship('AgreementTemplate', back_populates='agreements')
    creator = db.relationship('User', back_populates='created_agreements', foreign_keys=[created_by])
    variables = db.relationship('AgreementVariables', back_populates='agreement', cascade='all, delete-orphan')

class AgreementVariables(db.Model):
    __tablename__ = 'agreement_variables'
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey('agreement.id'), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    variable_name = db.Column(db.String(100), nullable=False)
    variable_value = db.Column(db.Text)
    variable_type = db.Column(db.String(50), default='text')  # text, number, date, email, phone, etc.
    is_required = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agreement = db.relationship('Agreement', back_populates='variables')
    lead = db.relationship('Lead', back_populates='agreement_variables')
    client = db.relationship('Client', back_populates='agreement_variables') 

class ReviewSchedule(db.Model):
    __tablename__ = 'review_schedule'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    first_review_date = db.Column(db.Date, nullable=False)
    frequency = db.Column(db.String(20), nullable=False)  # 'half_yearly' or 'yearly'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    client = db.relationship('Client', back_populates='review_schedules')
    creator = db.relationship('User', back_populates='created_review_schedules', foreign_keys=[created_by])
    workflows = db.relationship('ReviewWorkflow', back_populates='schedule', cascade='all, delete-orphan')

class ReviewWorkflow(db.Model):
    __tablename__ = 'review_workflow'
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('review_schedule.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    review_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='initiated')  # 'initiated', 'sent', 'meeting', 'closed'
    notes = db.Column(db.Text)
    meeting_date = db.Column(db.DateTime)
    meeting_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Relationships
    schedule = db.relationship('ReviewSchedule', back_populates='workflows')
    client = db.relationship('Client', back_populates='review_workflows')
    creator = db.relationship('User', back_populates='created_review_workflows', foreign_keys=[created_by])
    assigned_user = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_review_workflows')


class TaskAssignmentRule(db.Model):
    """Maps workflow stages, issue categories, or review statuses to a default assignee."""
    __tablename__ = 'task_assignment_rule'

    id = db.Column(db.Integer, primary_key=True)
    rule_type = db.Column(db.String(50), nullable=False)
    match_value = db.Column(db.String(100), nullable=False)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    priority = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assigned_user = db.relationship('User', foreign_keys=[assigned_user_id], backref='task_assignment_rules')

    __table_args__ = (
        db.Index('ix_task_assignment_rule_type', 'rule_type'),
        db.Index('ix_task_assignment_rule_active', 'is_active'),
    )


class OpsTask(db.Model):
    """Operational tasks (/tasks/) including auto-created links to issues and reviews."""
    __tablename__ = 'ops_task'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    deadline = db.Column(db.DateTime, nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    notes = db.Column(db.Text)
    priority = db.Column(db.String(20), default='medium')
    status = db.Column(db.String(20), nullable=False, default='pending')
    pre_snooze_status = db.Column(db.String(20))
    snoozed_until = db.Column(db.DateTime)
    snoozed_at = db.Column(db.DateTime)
    snoozed_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'))
    snooze_reason = db.Column(db.Text)
    reminder_at = db.Column(db.DateTime)
    reminder_sent = db.Column(db.Boolean, default=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id', ondelete='SET NULL'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    completed_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'))
    data_integrity_issue_id = db.Column(db.Integer, db.ForeignKey('data_integrity_issue.id', ondelete='SET NULL'))
    review_workflow_id = db.Column(db.Integer, db.ForeignKey('review_workflow.id', ondelete='SET NULL'))
    google_calendar_event_id = db.Column(db.String(280), nullable=True)
    google_calendar_reminder_event_id = db.Column(db.String(280), nullable=True)
    google_calendar_sync_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    google_tasks_task_id = db.Column(db.String(280), nullable=True)
    google_tasks_sync_user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    client = db.relationship('Client', backref=db.backref('ops_tasks', lazy='dynamic'))
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref='ops_tasks_assigned')
    creator = db.relationship('User', foreign_keys=[created_by], backref='ops_tasks_created')
    completer = db.relationship('User', foreign_keys=[completed_by], backref='ops_tasks_completed')
    snoozed_by_user = db.relationship('User', foreign_keys=[snoozed_by])
    data_integrity_issue = db.relationship('DataIntegrityIssue', backref=db.backref('ops_tasks', lazy='dynamic'))
    review_workflow = db.relationship('ReviewWorkflow', backref=db.backref('ops_tasks', lazy='dynamic'))
    google_calendar_sync_user = db.relationship(
        'User',
        foreign_keys=[google_calendar_sync_user_id],
        backref=db.backref('ops_tasks_gcal_hosted', lazy='dynamic'),
    )
    google_tasks_sync_user = db.relationship(
        'User',
        foreign_keys=[google_tasks_sync_user_id],
        backref=db.backref('ops_tasks_gtasks_hosted', lazy='dynamic'),
    )

    @property
    def is_overdue(self):
        if self.status in ('completed', 'cancelled'):
            return False
        return datetime.utcnow() > self.deadline

    @property
    def assigned_user(self):
        """Template alias for assignee."""
        return self.assignee

    @property
    def completed_by_user(self):
        """Template alias for completer."""
        return self.completer

    __table_args__ = (
        db.Index('ix_ops_task_assigned_to', 'assigned_to'),
        db.Index('ix_ops_task_status', 'status'),
        db.Index('ix_ops_task_deadline', 'deadline'),
        db.Index('ix_ops_task_reminder_at', 'reminder_at'),
        db.Index('ix_ops_task_data_integrity_issue_id', 'data_integrity_issue_id'),
        db.Index('ix_ops_task_review_workflow_id', 'review_workflow_id'),
    )


# Billing System Models

class BillingSchedule(db.Model):
    __tablename__ = 'billing_schedule'
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey('agreement.id'), nullable=False)
    billing_start_date = db.Column(db.Date, nullable=False)
    last_billing_date = db.Column(db.Date)
    next_billing_date = db.Column(db.Date, nullable=False)
    billing_cycle_number = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agreement = db.relationship('Agreement', backref='billing_schedules')

class BillingRateStructure(db.Model):
    __tablename__ = 'billing_rate_structure'
    id = db.Column(db.Integer, primary_key=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey('agreement.id'), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    min_amount = db.Column(db.Numeric(15, 2), default=0.00)
    max_amount = db.Column(db.Numeric(15, 2))
    rate_percentage = db.Column(db.Numeric(5, 4), nullable=False)  # 0.0125 = 1.25%
    min_fee = db.Column(db.Numeric(10, 2), default=0.00)
    max_fee = db.Column(db.Numeric(10, 2))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agreement = db.relationship('Agreement', backref='billing_rates')
    asset_class = db.relationship('AssetClass', backref='billing_rates')

class Invoice(db.Model):
    __tablename__ = 'invoice'
    id = db.Column(db.Integer, primary_key=True)
    # Nullable for historical imports when client has no signed agreement yet
    agreement_id = db.Column(db.Integer, db.ForeignKey('agreement.id'), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    billing_period_start = db.Column(db.Date, nullable=False)
    billing_period_end = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    tax_rate = db.Column(db.Numeric(5, 4), default=0.1800)  # 18% GST
    tax_amount = db.Column(db.Numeric(12, 2), default=0.00)
    net_amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(20), default='draft')  # draft, sent, paid, overdue, cancelled
    due_date = db.Column(db.Date, nullable=False)
    paid_date = db.Column(db.Date)
    payment_reference = db.Column(db.String(100))
    payment_method = db.Column(db.String(50))
    notes = db.Column(db.Text)
    pdf_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agreement = db.relationship('Agreement', backref='invoices')
    client = db.relationship('Client', backref='invoices')
    creator = db.relationship('User', backref='created_invoices', foreign_keys=[created_by])
    line_items = db.relationship('InvoiceLineItem', back_populates='invoice', cascade='all, delete-orphan')

class InvoiceLineItem(db.Model):
    __tablename__ = 'invoice_line_item'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    asset_class_id = db.Column(db.Integer, db.ForeignKey('asset_class.id'), nullable=False)
    asset_class_name = db.Column(db.String(100), nullable=False)
    portfolio_value = db.Column(db.Numeric(15, 2), nullable=False)
    rate_percentage = db.Column(db.Numeric(5, 4), nullable=False)
    calculated_fee = db.Column(db.Numeric(10, 2), nullable=False)
    min_fee_applied = db.Column(db.Numeric(10, 2), default=0.00)
    max_fee_applied = db.Column(db.Numeric(10, 2), default=0.00)
    final_fee = db.Column(db.Numeric(10, 2), nullable=False)
    fee_breakdown = db.Column(db.Text)  # Explanation of fee calculation
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    invoice = db.relationship('Invoice', back_populates='line_items')
    asset_class = db.relationship('AssetClass', backref='invoice_line_items')

class BillingConfiguration(db.Model):
    __tablename__ = 'billing_configuration'
    id = db.Column(db.Integer, primary_key=True)
    config_key = db.Column(db.String(100), unique=True, nullable=False)
    config_value = db.Column(db.Text, nullable=False)
    config_type = db.Column(db.String(20), default='string')  # string, number, boolean, json
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ReportRecipient(db.Model):
    __tablename__ = 'report_recipient'
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<ReportRecipient {self.email} for {self.job_id}>'

class CronSchedule(db.Model):
    __tablename__ = 'cron_schedule'
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(100), nullable=False, unique=True)
    schedule = db.Column(db.String(100), nullable=False)  # Cron expression
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<CronSchedule {self.job_id}: {self.schedule}>'

# AI Models Section Database Models

class AIModel(db.Model):
    """AI Model for storing model metadata and configurations"""
    __tablename__ = 'ai_model'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    model_type = db.Column(db.String(50), nullable=False)  # content_generation, equity_research, marketing_strategy, etc.
    description = db.Column(db.Text)
    version = db.Column(db.String(20), default='1.0.0')
    status = db.Column(db.String(20), default='draft')  # draft, training, trained, deployed, archived
    model_path = db.Column(db.String(255))  # Local path to model file
    config = db.Column(db.Text)  # JSON configuration
    training_data_path = db.Column(db.String(255))  # Path to training data
    performance_metrics = db.Column(db.Text)  # JSON performance metrics
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_trained_at = db.Column(db.DateTime)
    
    # Relationships
    creator = db.relationship('User', backref='created_ai_models')
    training_sessions = db.relationship('AIModelTrainingSession', back_populates='model', cascade='all, delete-orphan')
    tasks = db.relationship('AIModelTask', back_populates='model', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<AIModel {self.name} v{self.version}>'

class AIModelTrainingSession(db.Model):
    """Training session for AI models"""
    __tablename__ = 'ai_model_training_session'
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('ai_model.id'), nullable=False)
    session_name = db.Column(db.String(100), nullable=False)
    training_data_size = db.Column(db.Integer)
    training_parameters = db.Column(db.Text)  # JSON training parameters
    status = db.Column(db.String(20), default='pending')  # pending, running, completed, failed
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer)
    accuracy = db.Column(db.Float)
    loss = db.Column(db.Float)
    training_log = db.Column(db.Text)  # Training logs
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    model = db.relationship('AIModel', back_populates='training_sessions')
    
    def __repr__(self):
        return f'<AIModelTrainingSession {self.session_name}>'

class AIModelTask(db.Model):
    """AI Model task execution records"""
    __tablename__ = 'ai_model_task'
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('ai_model.id'), nullable=False)
    task_type = db.Column(db.String(50), nullable=False)  # content_generation, equity_analysis, marketing_strategy
    task_name = db.Column(db.String(100), nullable=False)
    input_data = db.Column(db.Text)  # JSON input data
    output_data = db.Column(db.Text)  # JSON output data
    status = db.Column(db.String(20), default='pending')  # pending, running, completed, failed
    execution_time_seconds = db.Column(db.Float)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    
    # Relationships
    model = db.relationship('AIModel', back_populates='tasks')
    creator = db.relationship('User', backref='ai_model_tasks')
    
    def __repr__(self):
        return f'<AIModelTask {self.task_name}>'

class AIModelTrainingData(db.Model):
    """Training data for AI models"""
    __tablename__ = 'ai_model_training_data'
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('ai_model.id'), nullable=False)
    data_type = db.Column(db.String(50), nullable=False)  # text, structured, image, etc.
    input_data = db.Column(db.Text, nullable=False)  # JSON input data
    output_data = db.Column(db.Text, nullable=False)  # JSON expected output
    data_source = db.Column(db.String(100))  # manual, imported, generated
    quality_score = db.Column(db.Float)  # Data quality score
    is_validated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    model = db.relationship('AIModel', backref='training_data')
    creator = db.relationship('User', backref='ai_training_data')
    
    def __repr__(self):
        return f'<AIModelTrainingData {self.id}>'

class AIModelVersion(db.Model):
    """Version control for AI models"""
    __tablename__ = 'ai_model_version'
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('ai_model.id'), nullable=False)
    version_number = db.Column(db.String(20), nullable=False)
    model_path = db.Column(db.String(255), nullable=False)
    config_snapshot = db.Column(db.Text)  # JSON config at this version
    performance_metrics = db.Column(db.Text)  # JSON metrics
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    model = db.relationship('AIModel', backref='versions')
    creator = db.relationship('User', backref='ai_model_versions')
    
    def __repr__(self):
        return f'<AIModelVersion {self.version_number}>'


# Historical Price Model
class HistoricalPrice(db.Model):
    """Historical stock prices for performance analysis"""
    __tablename__ = 'historical_price'
    
    id = db.Column(db.Integer, primary_key=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    
    # Price data
    open_price = db.Column(db.Numeric(15, 4), nullable=True)
    high_price = db.Column(db.Numeric(15, 4), nullable=True)
    low_price = db.Column(db.Numeric(15, 4), nullable=True)
    close_price = db.Column(db.Numeric(15, 4), nullable=False)  # Main price we use
    volume = db.Column(db.BigInteger, nullable=True)
    
    # Data source tracking
    source = db.Column(db.String(50), nullable=False)  # 'cron', 'transaction', 'googlefinance', 'manual'
    confidence = db.Column(db.Numeric(3, 2), default=1.0)  # 0.0 to 1.0
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    security = db.relationship('Security', backref='historical_prices')
    
    # Composite unique constraint (one price per security per date)
    __table_args__ = (
        db.UniqueConstraint('security_id', 'date', name='uq_security_date'),
        db.Index('idx_security_date', 'security_id', 'date'),
    )
    
    def __repr__(self):
        return f'<HistoricalPrice {self.security.symbol if self.security else "?"} {self.date}: ₹{self.close_price}>'


class PriceAccuracyRun(db.Model):
    """Latest batch job output for hub price-accuracy dashboard (historical spikes, gaps, zeros)."""
    __tablename__ = "price_accuracy_run"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    threshold_pct = db.Column(db.Numeric(6, 2), nullable=False, default=10.00)
    securities_scanned = db.Column(db.Integer, nullable=False, default=0)
    spike_count = db.Column(db.Integer, nullable=False, default=0)
    missing_count = db.Column(db.Integer, nullable=False, default=0)
    zero_count = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.String(500), nullable=True)

    findings = db.relationship(
        "PriceAccuracyFinding",
        backref="run",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class PriceAccuracyFinding(db.Model):
    """Single anomaly row produced by PriceAccuracyRun."""
    __tablename__ = "price_accuracy_finding"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("price_accuracy_run.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # SPIKE, MISSING, ZERO
    security_id = db.Column(db.Integer, db.ForeignKey("security.id"), nullable=False, index=True)
    symbol = db.Column(db.String(40), nullable=False)
    date_prev = db.Column(db.Date, nullable=True)
    date_next = db.Column(db.Date, nullable=True)
    close_prev = db.Column(db.Numeric(18, 6), nullable=True)
    close_next = db.Column(db.Numeric(18, 6), nullable=True)
    pct_change = db.Column(db.Numeric(12, 6), nullable=True)
    missing_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PriceAccuracySpikeAck(db.Model):
    """Verified genuine gap between two consecutive historical rows; excluded from future SPIKE reports."""
    __tablename__ = "price_accuracy_spike_ack"

    id = db.Column(db.Integer, primary_key=True)
    security_id = db.Column(db.Integer, db.ForeignKey("security.id"), nullable=False)
    date_prev = db.Column(db.Date, nullable=False)
    date_next = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(30), nullable=False, default="ui")
    notes = db.Column(db.String(500), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("security_id", "date_prev", "date_next", name="uq_spike_ack"),
    )


# Corporate Action Model
class CorporateAction(db.Model):
    """Corporate actions like splits, bonuses, dividends, mergers"""
    __tablename__ = 'corporate_actions'
    
    id = db.Column(db.Integer, primary_key=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=False)
    action_type = db.Column(db.String(20), nullable=False)  # SPLIT, BONUS, DIVIDEND, MERGER, etc.
    action_date = db.Column(db.Date, nullable=False)
    ratio = db.Column(db.Numeric(10, 6), nullable=False)  # Split ratio, bonus ratio, conversion ratio, etc.
    description = db.Column(db.String(255), nullable=True)
    source = db.Column(db.String(50), nullable=False, default='MANUAL')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    # For MERGER/CONVERSION actions
    source_security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=True)  # Security being merged FROM
    source_quantity = db.Column(db.Numeric(15, 4), nullable=True)  # Quantity being converted
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    security = db.relationship('Security', foreign_keys=[security_id], backref='corporate_actions')
    source_security = db.relationship('Security', foreign_keys=[source_security_id])
    
    # Composite unique constraint (one action per security per date per type)
    __table_args__ = (
        db.UniqueConstraint('security_id', 'action_date', 'action_type', name='uq_security_date_type'),
        db.Index('idx_security_date', 'security_id', 'action_date'),
        db.Index('idx_action_date', 'action_date'),
        db.Index('idx_action_type', 'action_type')
    )
    
    def __repr__(self):
        return f'<CorporateAction {self.security.symbol if self.security else "?"} {self.action_date}: {self.action_type} {self.ratio}>'

# Import WhatsApp models
try:
    from models.whatsapp_models import (
        WhatsAppMessage, WhatsAppNotification, ClientCommunication, 
        WhatsAppTemplate, WhatsAppWebhookLog, MessageStatus, 
        MessageType, NotificationType
    )
    print("WhatsApp models imported successfully")
except ImportError as e:
    print(f"Warning: Could not import WhatsApp models: {e}")
    # Define placeholder classes to prevent errors
    class WhatsAppMessage:
        pass
    class WhatsAppNotification:
        pass
    class ClientCommunication:
        pass
    class WhatsAppTemplate:
        pass
    class WhatsAppWebhookLog:
        pass
    class MessageStatus:
        pass
    class MessageType:
        pass
    class NotificationType:
        pass

# Attendance Management Models
class Attendance(db.Model):
    """User attendance tracking for daily stipend calculation"""
    __tablename__ = 'attendance'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    attendance_type = db.Column(db.String(20), nullable=False)  # 'full_day', 'half_day', 'leave'
    stipend_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='attendance_records')
    
    # Unique constraint to prevent duplicate entries for same user and date
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='unique_user_date'),)
    
    def __repr__(self):
        return f'<Attendance {self.user.username} - {self.date} - {self.attendance_type}>'

class UserStipend(db.Model):
    """User daily stipend configuration"""
    __tablename__ = 'user_stipend'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    full_day_stipend = db.Column(db.Numeric(10, 2), nullable=False, default=1000.00)
    half_day_stipend = db.Column(db.Numeric(10, 2), nullable=False, default=500.00)
    leave_stipend = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='stipend_config')
    
    def __repr__(self):
        return f'<UserStipend {self.user.username} - Full: {self.full_day_stipend}>'

class UserClaim(db.Model):
    """User claims for additional expenses or reimbursements"""
    __tablename__ = 'user_claim'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text, nullable=False)
    claim_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    admin_notes = db.Column(db.Text)
    processed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    processed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='claims', foreign_keys=[user_id])
    processor = db.relationship('User', back_populates='processed_claims', foreign_keys=[processed_by])
    
    def __repr__(self):
        return f'<UserClaim {self.user.username} - {self.amount} - {self.status}>'

class MonthlySalary(db.Model):
    """Monthly salary calculation and payment tracking"""
    __tablename__ = 'monthly_salary'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)  # 1-12
    total_days = db.Column(db.Integer, default=0)
    full_days = db.Column(db.Integer, default=0)
    half_days = db.Column(db.Integer, default=0)
    leave_days = db.Column(db.Integer, default=0)
    base_stipend = db.Column(db.Numeric(10, 2), default=0.00)
    total_stipend = db.Column(db.Numeric(10, 2), default=0.00)
    approved_claims = db.Column(db.Numeric(10, 2), default=0.00)
    sales_incentive = db.Column(db.Numeric(10, 2), default=0.00)
    internal_incentive = db.Column(db.Numeric(10, 2), default=0.00)
    total_salary = db.Column(db.Numeric(10, 2), default=0.00)
    status = db.Column(db.String(20), default='calculated')  # calculated, paid, closed
    paid_at = db.Column(db.DateTime)
    paid_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    admin_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='monthly_salaries', foreign_keys=[user_id])
    processor = db.relationship('User', back_populates='processed_salaries', foreign_keys=[paid_by])
    
    __table_args__ = (db.UniqueConstraint('user_id', 'year', 'month', name='unique_user_month'),)
    
    def __repr__(self):
        return f'<MonthlySalary {self.user.username} - {self.year}/{self.month:02d} - {self.status}>'

class EmailLog(db.Model):
    """Track all emails sent from the system"""
    __tablename__ = 'email_log'
    
    id = db.Column(db.Integer, primary_key=True)
    # Email details
    recipient_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    email_type = db.Column(db.String(50), nullable=False)  # recommendation, notification, report, etc.
    
    # Relationships
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    sent_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Email content (stored for reference)
    email_html = db.Column(db.Text, nullable=True)
    custom_message = db.Column(db.Text, nullable=True)
    
    # Status tracking
    status = db.Column(db.String(20), default='sent')  # sent, failed, pending
    error_message = db.Column(db.Text, nullable=True)
    
    # Timestamps
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    client = db.relationship('Client', backref='email_logs')
    sent_by_user = db.relationship('User', backref='sent_emails', foreign_keys=[sent_by_user_id])
    
    def __repr__(self):
        return f'<EmailLog {self.id}: {self.recipient_email} - {self.subject} - {self.status}>'


# =============================================================================
# AGENT SYSTEM MODELS
# =============================================================================

class DataIntegrityIssue(db.Model):
    """
    Stores issues found by agents (Data Integrity Manager, Recommendation Execution Monitor, etc.).
    """
    __tablename__ = 'data_integrity_issue'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)

    check_category = db.Column(db.String(50), nullable=False)
    check_name = db.Column(db.String(100), nullable=False)
    severity = db.Column(db.String(20), nullable=False, default='warning')  # critical, warning, info

    message = db.Column(db.Text, nullable=False)
    details = db.Column(db.JSON)
    suggested_action = db.Column(db.Text)

    group_key = db.Column(db.String(100), index=True)

    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey('recommendation.id'), nullable=True)
    cashflow_id = db.Column(db.Integer, db.ForeignKey('cashflow.id'), nullable=True)
    reference_date = db.Column(db.Date)
    date_range_start = db.Column(db.Date)
    date_range_end = db.Column(db.Date)

    status = db.Column(db.String(20), nullable=False, default='open')  # open, baseline, resolved, false_positive

    resolution_type = db.Column(db.String(20))
    resolved_at = db.Column(db.DateTime)
    resolved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolution_notes = db.Column(db.Text)

    detected_at = db.Column(db.DateTime, default=datetime.utcnow)
    run_id = db.Column(db.String(50))
    alert_id = db.Column(db.Integer, db.ForeignKey('alert.id'), nullable=True, index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True, index=True)

    client = db.relationship('Client', backref=db.backref('integrity_issues', lazy='dynamic'))
    security = db.relationship('Security')
    transaction = db.relationship('Transaction')
    recommendation = db.relationship('Recommendation')
    resolver = db.relationship('User', foreign_keys=[resolved_by])
    assigned_user = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_integrity_issues')

    __table_args__ = (
        db.Index('idx_issue_client_status', 'client_id', 'status'),
        db.Index('idx_issue_category_status', 'check_category', 'status'),
        db.Index('idx_issue_run', 'run_id'),
    )


class DataIntegrityException(db.Model):
    """One-time exceptions for specific issue instances."""
    __tablename__ = 'data_integrity_exception'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)

    check_category = db.Column(db.String(50), nullable=False)
    check_name = db.Column(db.String(100), nullable=False)

    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey('recommendation.id'), nullable=True)
    cashflow_id = db.Column(db.Integer, db.ForeignKey('cashflow.id'), nullable=True)
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=True)
    reference_date = db.Column(db.Date)

    exception_hash = db.Column(db.String(64), unique=True, index=True)
    reason = db.Column(db.Text)
    original_issue_id = db.Column(db.Integer, db.ForeignKey('data_integrity_issue.id'))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    client = db.relationship('Client', backref=db.backref('integrity_exceptions', lazy='dynamic'))
    original_issue = db.relationship('DataIntegrityIssue')
    creator = db.relationship('User', foreign_keys=[created_by])


class ClientBehaviorPattern(db.Model):
    """Persistent per-client overrides/patterns that modify agent checks."""
    __tablename__ = 'client_behavior_pattern'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)

    check_category = db.Column(db.String(50), nullable=False)
    pattern_type = db.Column(db.String(50), nullable=False)

    applies_to = db.Column(db.String(20), default='all')
    security_id = db.Column(db.Integer, db.ForeignKey('security.id'), nullable=True)

    pattern_value = db.Column(db.JSON, nullable=False)

    occurrences = db.Column(db.Integer, default=1)
    confidence = db.Column(db.Float, default=0.5)
    last_used_at = db.Column(db.DateTime)

    learned_from_issue_id = db.Column(db.Integer, db.ForeignKey('data_integrity_issue.id'))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship('Client', backref=db.backref('behavior_patterns', lazy='dynamic'))
    security = db.relationship('Security')
    learned_from_issue = db.relationship('DataIntegrityIssue')
    creator = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.UniqueConstraint('client_id', 'check_category', 'pattern_type', 'security_id', name='uq_client_pattern'),
        db.Index('idx_pattern_client_category', 'client_id', 'check_category'),
    )


class AgentRun(db.Model):
    """Tracks each run of an agent for audit/monitoring."""
    __tablename__ = 'agent_run'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.String(50), unique=True, nullable=False, index=True)

    agent_name = db.Column(db.String(50), nullable=False)
    agent_version = db.Column(db.String(20))

    run_mode = db.Column(db.String(20), nullable=False)
    scope = db.Column(db.JSON)

    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Float)

    status = db.Column(db.String(20), default='running')
    clients_processed = db.Column(db.Integer, default=0)
    issues_found = db.Column(db.Integer, default=0)
    issues_by_severity = db.Column(db.JSON)
    issues_by_category = db.Column(db.JSON)

    error_message = db.Column(db.Text)
    error_traceback = db.Column(db.Text)

    triggered_by = db.Column(db.String(20))
    triggered_by_user = db.Column(db.Integer, db.ForeignKey('user.id'))

    user = db.relationship('User', foreign_keys=[triggered_by_user])


class AgentFeedback(db.Model):
    """User feedback for improving agent checks."""
    __tablename__ = 'agent_feedback'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    related_check = db.Column(db.String(50))
    related_issue_id = db.Column(db.Integer, db.ForeignKey('data_integrity_issue.id'))

    title = db.Column(db.String(200))
    description = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(20), default='open')
    response = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    addressed_at = db.Column(db.DateTime)
    addressed_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    creator = db.relationship('User', foreign_keys=[created_by], backref='agent_feedbacks')
    resolver = db.relationship('User', foreign_keys=[addressed_by])
    related_issue = db.relationship('DataIntegrityIssue')


class OrchestratorFeedback(db.Model):
    """Feedback on orchestrator decisions (per alert/client)."""
    __tablename__ = 'orchestrator_feedback'

    id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(db.Integer, db.ForeignKey('alert.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)

    feedback_type = db.Column(db.String(50), nullable=False)
    feedback_text = db.Column(db.Text)
    logic_documentation = db.Column(db.Text)
    suggested_improvement = db.Column(db.Text)

    status = db.Column(db.String(20), default='open')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    addressed_at = db.Column(db.DateTime)
    addressed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    response = db.Column(db.Text)

    client = db.relationship('Client')
    alert = db.relationship('Alert', foreign_keys=[alert_id])
    creator = db.relationship('User', foreign_keys=[created_by])
    resolver = db.relationship('User', foreign_keys=[addressed_by])


class AccountHead(db.Model):
    """Credit/debit (income/expense) classification head for bank statement lines."""
    __tablename__ = 'account_head'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    head_type = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    creator = db.relationship('User', foreign_keys=[created_by])
    transactions = db.relationship('BankStatementTransaction', back_populates='income_expense_head')


class BankStatement(db.Model):
    __tablename__ = 'bank_statement'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    account_name = db.Column(db.String(200))
    upload_date = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notes = db.Column(db.Text)

    creator = db.relationship('User', foreign_keys=[created_by])
    transactions = db.relationship(
        'BankStatementTransaction',
        back_populates='bank_statement',
        lazy='dynamic',
        cascade='all, delete-orphan',
    )


class BankStatementTransaction(db.Model):
    __tablename__ = 'bank_statement_transaction'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id', ondelete='CASCADE'), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    value_date = db.Column(db.Date)
    reference_no = db.Column(db.String(200))
    description = db.Column(db.Text)
    description_sanitized = db.Column(db.Text)
    withdrawal_amount = db.Column(db.Numeric(15, 2), default=0)
    deposit_amount = db.Column(db.Numeric(15, 2), default=0)
    running_balance = db.Column(db.Numeric(15, 2))
    income_expense_head_id = db.Column(db.Integer, db.ForeignKey('account_head.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())

    bank_statement = db.relationship('BankStatement', back_populates='transactions')
    income_expense_head = db.relationship('AccountHead', back_populates='transactions')


class CampaignStudioCampaign(db.Model):
    """Campaign Studio weekly campaigns — article + social posts in JSON payload."""
    __tablename__ = 'campaign_studio_campaign'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False, default='Untitled campaign')
    payload = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
    )

    user = db.relationship('User', backref=db.backref('campaign_studio_campaigns', lazy='dynamic'))