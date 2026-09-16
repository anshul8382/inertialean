#!/usr/bin/env python3
"""
Historical Price Model
Stores historical stock prices for performance analysis
"""

from extensions import db
from datetime import datetime

class HistoricalPrice(db.Model):
    """Historical stock prices"""
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


