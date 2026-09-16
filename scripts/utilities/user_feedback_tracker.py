#!/usr/bin/env python3
"""
User Feedback Tracking System for Investment Recommendations
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from extensions import db

class UserFeedbackTracker:
    """Track and manage user feedback on investment recommendations"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def record_user_decision(
        self,
        client_id: int,
        security_id: int,
        recommendation_type: str,
        model_recommended: bool,
        user_decision: bool,
        amount: float,
        reason: str = None,
        ml_confidence: float = None
    ) -> bool:
        """Record user's decision on a recommendation"""
        try:
            session = sessionmaker(bind=db.engine)()
            
            # Insert feedback record
            session.execute("""
                INSERT INTO user_recommendation_feedback 
                (client_id, security_id, recommendation_type, model_recommended, 
                 user_decision, amount, reason, ml_confidence, created_at)
                VALUES (:client_id, :security_id, :recommendation_type, :model_recommended,
                        :user_decision, :amount, :reason, :ml_confidence, :created_at)
            """, {
                'client_id': client_id,
                'security_id': security_id,
                'recommendation_type': recommendation_type,
                'model_recommended': model_recommended,
                'user_decision': user_decision,
                'amount': amount,
                'reason': reason,
                'ml_confidence': ml_confidence,
                'created_at': datetime.now()
            })
            
            # Update behavior patterns
            self._update_behavior_patterns(
                session, client_id, security_id, recommendation_type, user_decision
            )
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded user decision: Client {client_id}, Security {security_id}, Decision: {user_decision}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording user decision: {e}")
            return False
    
    def _update_behavior_patterns(
        self, session, client_id: int, security_id: int, recommendation_type: str, user_decision: bool
    ):
        """Update aggregated behavior patterns"""
        try:
            # Get current pattern
            pattern = session.execute("""
                SELECT total_recommendations, user_followed_count, user_rejected_count
                FROM user_behavior_patterns
                WHERE client_id = :client_id 
                AND security_id = :security_id 
                AND recommendation_type = :recommendation_type
            """, {
                'client_id': client_id,
                'security_id': security_id,
                'recommendation_type': recommendation_type
            }).fetchone()
            
            if pattern:
                # Update existing pattern
                total = pattern.total_recommendations + 1
                followed = pattern.user_followed_count + (1 if user_decision else 0)
                rejected = pattern.user_rejected_count + (0 if user_decision else 1)
                follow_rate = (followed / total) * 100
                
                session.execute("""
                    UPDATE user_behavior_patterns
                    SET total_recommendations = :total,
                        user_followed_count = :followed,
                        user_rejected_count = :rejected,
                        follow_rate = :follow_rate,
                        last_updated = :last_updated
                    WHERE client_id = :client_id 
                    AND security_id = :security_id 
                    AND recommendation_type = :recommendation_type
                """, {
                    'total': total,
                    'followed': followed,
                    'rejected': rejected,
                    'follow_rate': follow_rate,
                    'last_updated': datetime.now(),
                    'client_id': client_id,
                    'security_id': security_id,
                    'recommendation_type': recommendation_type
                })
            else:
                # Create new pattern
                followed = 1 if user_decision else 0
                rejected = 0 if user_decision else 1
                follow_rate = (followed / 1) * 100
                
                session.execute("""
                    INSERT INTO user_behavior_patterns
                    (client_id, security_id, recommendation_type, total_recommendations,
                     user_followed_count, user_rejected_count, follow_rate, last_updated)
                    VALUES (:client_id, :security_id, :recommendation_type, 1,
                            :followed, :rejected, :follow_rate, :last_updated)
                """, {
                    'client_id': client_id,
                    'security_id': security_id,
                    'recommendation_type': recommendation_type,
                    'followed': followed,
                    'rejected': rejected,
                    'follow_rate': follow_rate,
                    'last_updated': datetime.now()
                })
                
        except Exception as e:
            self.logger.error(f"Error updating behavior patterns: {e}")
    
    def get_user_behavior_summary(self, client_id: int) -> Dict:
        """Get summary of user's behavior patterns"""
        try:
            session = sessionmaker(bind=db.engine)()
            
            # Get overall statistics
            stats = session.execute("""
                SELECT 
                    COUNT(*) as total_recommendations,
                    SUM(CASE WHEN user_decision = 1 THEN 1 ELSE 0 END) as followed,
                    AVG(ml_confidence) as avg_confidence
                FROM user_recommendation_feedback
                WHERE client_id = :client_id
            """, {'client_id': client_id}).fetchone()
            
            # Get top avoided stocks
            avoided_stocks = session.execute("""
                SELECT s.symbol, s.name, COUNT(*) as avoid_count
                FROM user_recommendation_feedback f
                JOIN security s ON f.security_id = s.id
                WHERE f.client_id = :client_id 
                AND f.user_decision = 0
                GROUP BY f.security_id, s.symbol, s.name
                ORDER BY avoid_count DESC
                LIMIT 5
            """, {'client_id': client_id}).fetchall()
            
            # Get top followed stocks
            followed_stocks = session.execute("""
                SELECT s.symbol, s.name, COUNT(*) as follow_count
                FROM user_recommendation_feedback f
                JOIN security s ON f.security_id = s.id
                WHERE f.client_id = :client_id 
                AND f.user_decision = 1
                GROUP BY f.security_id, s.symbol, s.name
                ORDER BY follow_count DESC
                LIMIT 5
            """, {'client_id': client_id}).fetchall()
            
            session.close()
            
            return {
                'total_recommendations': stats.total_recommendations or 0,
                'followed_count': stats.followed or 0,
                'follow_rate': round((stats.followed / stats.total_recommendations * 100) if stats.total_recommendations > 0 else 0, 1),
                'avg_confidence': round(stats.avg_confidence or 0, 1),
                'top_avoided_stocks': [
                    {'symbol': row.symbol, 'name': row.name, 'avoid_count': row.avoid_count}
                    for row in avoided_stocks
                ],
                'top_followed_stocks': [
                    {'symbol': row.symbol, 'name': row.name, 'follow_count': row.follow_count}
                    for row in followed_stocks
                ]
            }
            
        except Exception as e:
            self.logger.error(f"Error getting user behavior summary: {e}")
            return {}
    
    def get_training_data(self, limit: int = 1000) -> List[Dict]:
        """Get training data for ML model"""
        try:
            session = sessionmaker(bind=db.engine)()
            
            # Get feedback data with security and client information
            training_data = session.execute("""
                SELECT 
                    f.client_id,
                    f.security_id,
                    f.recommendation_type,
                    f.user_decision,
                    f.amount,
                    f.ml_confidence,
                    s.market_cap,
                    s.volatility,
                    s.sector,
                    c.risk_profile
                FROM user_recommendation_feedback f
                JOIN security s ON f.security_id = s.id
                JOIN client c ON f.client_id = c.id
                ORDER BY f.created_at DESC
                LIMIT :limit
            """, {'limit': limit}).fetchall()
            
            session.close()
            
            return [
                {
                    'client_id': row.client_id,
                    'security_id': row.security_id,
                    'recommendation_type': row.recommendation_type,
                    'user_decision': row.user_decision,
                    'amount': row.amount,
                    'ml_confidence': row.ml_confidence,
                    'market_cap': row.market_cap,
                    'volatility': row.volatility,
                    'sector': row.sector,
                    'risk_profile': row.risk_profile
                }
                for row in training_data
            ]
            
        except Exception as e:
            self.logger.error(f"Error getting training data: {e}")
            return []

# Global instance
feedback_tracker = UserFeedbackTracker()

