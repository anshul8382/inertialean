#!/usr/bin/env python3
"""
User Modification Tracking System for ML Training
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy.orm import sessionmaker
from extensions import db

class UserModificationTracker:
    """Track user modifications to recommendations for ML training"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def record_quantity_modification(self, client_id: int, recommendation_id: int, 
                                   original_quantity: int, new_quantity: int,
                                   original_amount: float, new_amount: float,
                                   context: Dict) -> bool:
        """Record when user modifies quantity/amount of a recommendation"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            modification_data = {
                'client_id': client_id,
                'recommendation_id': recommendation_id,
                'modification_type': 'quantity_change',
                'original_quantity': original_quantity,
                'new_quantity': new_quantity,
                'quantity_change_ratio': (new_quantity - original_quantity) / max(original_quantity, 1),
                'original_amount': original_amount,
                'new_amount': new_amount,
                'amount_change_ratio': (new_amount - original_amount) / max(original_amount, 1),
                'modification_timestamp': datetime.now(),
                'context_data': json.dumps(context),
                'user_session_id': context.get('session_id'),
                'time_spent_on_page': context.get('time_spent', 0),
                'modification_reason': context.get('reason', 'user_preference')
            }
            
            session.execute("""
                INSERT INTO user_modification_log 
                (client_id, recommendation_id, modification_type, original_quantity, new_quantity,
                 quantity_change_ratio, original_amount, new_amount, amount_change_ratio,
                 modification_timestamp, context_data, user_session_id, time_spent_on_page, modification_reason)
                VALUES (:client_id, :recommendation_id, :modification_type, :original_quantity, :new_quantity,
                        :quantity_change_ratio, :original_amount, :new_amount, :amount_change_ratio,
                        :modification_timestamp, :context_data, :user_session_id, :time_spent_on_page, :modification_reason)
            """, modification_data)
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded quantity modification for client {client_id}, recommendation {recommendation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording quantity modification: {e}")
            return False
    
    def record_action_modification(self, client_id: int, recommendation_id: int,
                                 original_action: str, new_action: str,
                                 context: Dict) -> bool:
        """Record when user changes BUY/SELL action"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            modification_data = {
                'client_id': client_id,
                'recommendation_id': recommendation_id,
                'modification_type': 'action_change',
                'original_action': original_action,
                'new_action': new_action,
                'action_changed': 1,
                'modification_timestamp': datetime.now(),
                'context_data': json.dumps(context),
                'user_session_id': context.get('session_id'),
                'time_spent_on_page': context.get('time_spent', 0),
                'modification_reason': context.get('reason', 'user_preference')
            }
            
            session.execute("""
                INSERT INTO user_modification_log 
                (client_id, recommendation_id, modification_type, original_action, new_action,
                 action_changed, modification_timestamp, context_data, user_session_id, 
                 time_spent_on_page, modification_reason)
                VALUES (:client_id, :recommendation_id, :modification_type, :original_action, :new_action,
                        :action_changed, :modification_timestamp, :context_data, :user_session_id,
                        :time_spent_on_page, :modification_reason)
            """, modification_data)
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded action modification for client {client_id}, recommendation {recommendation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording action modification: {e}")
            return False
    
    def record_security_addition(self, client_id: int, asset_class: str,
                               added_security: Dict, context: Dict) -> bool:
        """Record when user adds a new security"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            modification_data = {
                'client_id': client_id,
                'modification_type': 'security_addition',
                'asset_class': asset_class,
                'added_security_data': json.dumps(added_security),
                'modification_timestamp': datetime.now(),
                'context_data': json.dumps(context),
                'user_session_id': context.get('session_id'),
                'time_spent_on_page': context.get('time_spent', 0),
                'modification_reason': context.get('reason', 'user_preference')
            }
            
            session.execute("""
                INSERT INTO user_modification_log 
                (client_id, modification_type, asset_class, added_security_data,
                 modification_timestamp, context_data, user_session_id,
                 time_spent_on_page, modification_reason)
                VALUES (:client_id, :modification_type, :asset_class, :added_security_data,
                        :modification_timestamp, :context_data, :user_session_id,
                        :time_spent_on_page, :modification_reason)
            """, modification_data)
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded security addition for client {client_id}, asset class {asset_class}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording security addition: {e}")
            return False
    
    def record_security_deletion(self, client_id: int, recommendation_id: int,
                               deleted_security: Dict, context: Dict) -> bool:
        """Record when user deletes a security recommendation"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            modification_data = {
                'client_id': client_id,
                'recommendation_id': recommendation_id,
                'modification_type': 'security_deletion',
                'deleted_security_data': json.dumps(deleted_security),
                'modification_timestamp': datetime.now(),
                'context_data': json.dumps(context),
                'user_session_id': context.get('session_id'),
                'time_spent_on_page': context.get('time_spent', 0),
                'modification_reason': context.get('reason', 'user_preference')
            }
            
            session.execute("""
                INSERT INTO user_modification_log 
                (client_id, recommendation_id, modification_type, deleted_security_data,
                 modification_timestamp, context_data, user_session_id,
                 time_spent_on_page, modification_reason)
                VALUES (:client_id, :recommendation_id, :modification_type, :deleted_security_data,
                        :modification_timestamp, :context_data, :user_session_id,
                        :time_spent_on_page, :modification_reason)
            """, modification_data)
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded security deletion for client {client_id}, recommendation {recommendation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording security deletion: {e}")
            return False
    
    def record_asset_allocation_modification(self, client_id: int, asset_class: str,
                                           original_allocation: float, new_allocation: float,
                                           context: Dict) -> bool:
        """Record when user modifies asset allocation percentages"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            modification_data = {
                'client_id': client_id,
                'modification_type': 'asset_allocation_change',
                'asset_class': asset_class,
                'original_allocation': original_allocation,
                'new_allocation': new_allocation,
                'allocation_change_ratio': (new_allocation - original_allocation) / max(original_allocation, 1),
                'modification_timestamp': datetime.now(),
                'context_data': json.dumps(context),
                'user_session_id': context.get('session_id'),
                'time_spent_on_page': context.get('time_spent', 0),
                'modification_reason': context.get('reason', 'user_preference')
            }
            
            session.execute("""
                INSERT INTO user_modification_log 
                (client_id, modification_type, asset_class, original_allocation, new_allocation,
                 allocation_change_ratio, modification_timestamp, context_data, user_session_id,
                 time_spent_on_page, modification_reason)
                VALUES (:client_id, :modification_type, :asset_class, :original_allocation, :new_allocation,
                        :allocation_change_ratio, :modification_timestamp, :context_data, :user_session_id,
                        :time_spent_on_page, :modification_reason)
            """, modification_data)
            
            session.commit()
            session.close()
            
            self.logger.info(f"Recorded asset allocation modification for client {client_id}, {asset_class}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording asset allocation modification: {e}")
            return False
    
    def get_user_modification_patterns(self, client_id: int, limit: int = 100) -> List[Dict]:
        """Get user's modification patterns for ML training"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            result = session.execute("""
                SELECT * FROM user_modification_log 
                WHERE client_id = :client_id 
                ORDER BY modification_timestamp DESC 
                LIMIT :limit
            """, {'client_id': client_id, 'limit': limit})
            
            modifications = []
            for row in result:
                modifications.append({
                    'modification_type': row.modification_type,
                    'timestamp': row.modification_timestamp,
                    'context_data': json.loads(row.context_data) if row.context_data else {},
                    'quantity_change_ratio': row.quantity_change_ratio,
                    'amount_change_ratio': row.amount_change_ratio,
                    'action_changed': row.action_changed,
                    'modification_reason': row.modification_reason
                })
            
            session.close()
            return modifications
            
        except Exception as e:
            self.logger.error(f"Error getting user modification patterns: {e}")
            return []
    
    def get_training_data_for_ml(self, limit: int = 1000) -> List[Dict]:
        """Get aggregated training data for ML model training"""
        
        try:
            session = sessionmaker(bind=db.engine)()
            
            result = session.execute("""
                SELECT 
                    uml.client_id,
                    uml.modification_type,
                    uml.quantity_change_ratio,
                    uml.amount_change_ratio,
                    uml.action_changed,
                    uml.allocation_change_ratio,
                    uml.modification_reason,
                    uml.context_data,
                    c.risk_profile,
                    c.age,
                    c.investment_experience,
                    c.income_level
                FROM user_modification_log uml
                JOIN client c ON uml.client_id = c.id
                ORDER BY uml.modification_timestamp DESC
                LIMIT :limit
            """, {'limit': limit})
            
            training_data = []
            for row in result:
                context = json.loads(row.context_data) if row.context_data else {}
                
                training_data.append({
                    'client_id': row.client_id,
                    'modification_type': row.modification_type,
                    'quantity_change_ratio': row.quantity_change_ratio or 0,
                    'amount_change_ratio': row.amount_change_ratio or 0,
                    'action_changed': row.action_changed or 0,
                    'allocation_change_ratio': row.allocation_change_ratio or 0,
                    'client_risk_profile': row.risk_profile or 3,
                    'client_age': row.age or 45,
                    'client_experience': row.investment_experience or 5,
                    'client_income_level': row.income_level or 3,
                    'modification_reason': row.modification_reason,
                    'context': context
                })
            
            session.close()
            return training_data
            
        except Exception as e:
            self.logger.error(f"Error getting training data: {e}")
            return []

# Global instance
modification_tracker = UserModificationTracker()
