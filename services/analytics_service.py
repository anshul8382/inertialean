#!/usr/bin/env python3
"""
Analytics Service
ML and analytics for user behavior tracking and predictions
"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class AnalyticsService:
    """ML and analytics service for recommendations"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        # Import ML components (they may not exist yet)
        try:
            from behavioral_prediction_engine import behavioral_predictor
            from user_modification_tracker import modification_tracker
            from ml_user_modification_training import modification_trainer
            
            self.behavioral_predictor = behavioral_predictor
            self.modification_tracker = modification_tracker
            self.ml_trainer = modification_trainer
            self.ml_available = True
        except ImportError:
            self.logger.warning("ML components not available - analytics will be limited")
            self.ml_available = False
    
    def get_behavioral_predictions(self, client_id: int, recommendations: List[Dict]) -> List[Dict]:
        """
        Get ML predictions for how user will modify recommendations
        
        Args:
            client_id: Client ID
            recommendations: List of recommendation dicts
            
        Returns:
            List of prediction dicts with modification forecasts
        """
        if not self.ml_available:
            # Return empty predictions if ML not available
            return []
        
        try:
            predictions = self.behavioral_predictor.predict_user_modifications(
                client_id, recommendations
            )
            
            # Calculate overall confidence
            overall_confidence = sum(p.get('modification_confidence', 0) for p in predictions) / max(len(predictions), 1)
            
            return {
                'predictions': predictions,
                'overall_confidence': round(overall_confidence, 1),
                'model_status': 'ready' if overall_confidence > 70 else 'training'
            }
        except Exception as e:
            self.logger.error(f"Error getting behavioral predictions: {e}")
            return []
    
    def track_modification(self, client_id: int, session_id: int,
                          modification_type: str, data: Dict) -> bool:
        """
        Track user modification for ML training
        
        Args:
            client_id: Client ID
            session_id: RecommendationSession ID
            modification_type: 'quantity_change', 'action_change', 'security_addition', 'security_deletion'
            data: Modification data
            
        Returns:
            bool: Success status
        """
        if not self.ml_available:
            return False
        
        try:
            if modification_type == 'quantity_change':
                return self.modification_tracker.record_quantity_modification(
                    client_id=client_id,
                    recommendation_id=data.get('recommendation_id'),
                    original_quantity=data.get('original_quantity'),
                    new_quantity=data.get('new_quantity'),
                    original_amount=data.get('original_amount'),
                    new_amount=data.get('new_amount'),
                    context={
                        'session_id': session_id,
                        'time_spent': data.get('time_spent', 0),
                        'reason': data.get('reason', 'user_preference')
                    }
                )
            elif modification_type == 'action_change':
                return self.modification_tracker.record_action_modification(
                    client_id=client_id,
                    recommendation_id=data.get('recommendation_id'),
                    original_action=data.get('original_action'),
                    new_action=data.get('new_action'),
                    context={
                        'session_id': session_id,
                        'time_spent': data.get('time_spent', 0),
                        'reason': data.get('reason', 'user_preference')
                    }
                )
            elif modification_type == 'security_addition':
                return self.modification_tracker.record_security_addition(
                    client_id=client_id,
                    asset_class=data.get('asset_class'),
                    added_security=data.get('added_security'),
                    context={
                        'session_id': session_id,
                        'time_spent': data.get('time_spent', 0),
                        'reason': data.get('reason', 'user_preference')
                    }
                )
            elif modification_type == 'security_deletion':
                return self.modification_tracker.record_security_deletion(
                    client_id=client_id,
                    recommendation_id=data.get('recommendation_id'),
                    deleted_security=data.get('deleted_security'),
                    context={
                        'session_id': session_id,
                        'time_spent': data.get('time_spent', 0),
                        'reason': data.get('reason', 'user_preference')
                    }
                )
            else:
                self.logger.warning(f"Unknown modification type: {modification_type}")
                return False
        except Exception as e:
            self.logger.error(f"Error tracking modification: {e}")
            return False
    
    def record_user_feedback(self, client_id: int, security_id: int,
                            recommendation_type: str, user_decision: str,
                            amount: float, reason: str = '') -> bool:
        """
        Record user feedback on recommendations
        
        Args:
            client_id: Client ID
            security_id: Security ID
            recommendation_type: Type of recommendation
            user_decision: 'accepted', 'rejected', 'modified'
            amount: Amount involved
            reason: Reason for decision
            
        Returns:
            bool: Success status
        """
        # This can be implemented to store feedback in database
        # For now, just log it
        self.logger.info(f"User feedback: client={client_id}, security={security_id}, decision={user_decision}, reason={reason}")
        return True
    
    def train_models(self) -> Dict:
        """
        Train ML models on collected data
        
        Returns:
            Dict with training results
        """
        if not self.ml_available:
            return {
                'success': False,
                'error': 'ML components not available'
            }
        
        try:
            # Get training data
            training_data = self.modification_tracker.get_training_data_for_ml()
            
            if len(training_data) < 100:
                return {
                    'success': False,
                    'error': f'Insufficient training data: {len(training_data)} samples (need at least 100)'
                }
            
            # Convert to DataFrame and train
            import pandas as pd
            df = pd.DataFrame(training_data)
            
            # Train models
            results = self.ml_trainer.train_modification_models(df)
            
            # Save models
            self.ml_trainer.save_models()
            
            return {
                'success': True,
                'results': results,
                'training_samples': len(training_data)
            }
        except Exception as e:
            self.logger.error(f"Error training models: {e}")
            return {
                'success': False,
                'error': str(e)
            }


