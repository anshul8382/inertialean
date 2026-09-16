#!/usr/bin/env python3
"""
Enhanced Recommendation API with ML-based user behavior insights
"""

import logging
from typing import Dict, List
from recommendation_api import recommendation_api

# Import ML components with fallback
try:
    from ml_user_behavior_model import ml_model
    from user_feedback_tracker import feedback_tracker
    ML_AVAILABLE = True
except ImportError as e:
    print(f"ML components not available: {e}")
    ML_AVAILABLE = False
    ml_model = None
    feedback_tracker = None

class EnhancedRecommendationAPI:
    """Enhanced recommendation API with ML insights and user behavior tracking"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.base_api = recommendation_api
        self.ml_model = ml_model
        self.feedback_tracker = feedback_tracker
        self.ml_available = ML_AVAILABLE
        
        # Load ML model if available
        if self.ml_available and self.ml_model:
            try:
                self.ml_model.load_model()
            except Exception as e:
                self.logger.warning(f"Could not load ML model: {e}")
                self.ml_available = False
    
    def generate_enhanced_security_recommendations(
        self,
        client_id: int,
        asset_class: str,
        current_asset_value: float,
        change_amount: float
    ) -> Dict:
        """Generate security recommendations with ML insights"""
        try:
            self.logger.info(f"Generating enhanced recommendations for client {client_id}, asset class: {asset_class}")
            
            # Get base recommendations from the original API
            base_result = self.base_api.generate_security_recommendations_only(
                client_id=client_id,
                asset_class=asset_class,
                current_asset_value=current_asset_value,
                change_amount=change_amount
            )
            
            if not base_result.get('success'):
                return base_result
            
            # Enhance each recommendation with ML insights if available
            enhanced_recommendations = []
            for rec in base_result.get('security_recommendations', []):
                if self.ml_available:
                    enhanced_rec = self._enhance_recommendation_with_ml(client_id, rec)
                else:
                    # Add basic ML structure without actual ML
                    enhanced_rec = self._add_basic_ml_structure(rec)
                enhanced_recommendations.append(enhanced_rec)
            
            # Update the result with enhanced recommendations
            base_result['security_recommendations'] = enhanced_recommendations
            base_result['ml_insights_enabled'] = self.ml_available
            
            return base_result
            
        except Exception as e:
            self.logger.error(f"Error generating enhanced recommendations: {e}")
            return {
                'success': False,
                'error': str(e),
                'ml_insights_enabled': False
            }
    
    def _enhance_recommendation_with_ml(self, client_id: int, recommendation: Dict) -> Dict:
        """Enhance a single recommendation with ML insights"""
        try:
            security_id = recommendation.get('security_id')
            action = recommendation.get('action', '')
            
            # Get ML prediction
            ml_insight = self.ml_model.predict_user_behavior(
                client_id=client_id,
                security_id=security_id,
                recommendation_data=recommendation
            )
            
            # Add ML insights to recommendation
            recommendation['ml_insights'] = {
                'confidence': ml_insight['confidence'],
                'prediction': ml_insight['prediction'],
                'color': ml_insight['color'],
                'history': ml_insight['history']
            }
            
            # Add visual indicators
            recommendation['highlight_class'] = f"recommendation-{ml_insight['color']}"
            recommendation['confidence_badge'] = self._get_confidence_badge(ml_insight['confidence'])
            
            return recommendation
            
        except Exception as e:
            self.logger.error(f"Error enhancing recommendation with ML: {e}")
            # Return original recommendation if ML enhancement fails
            recommendation['ml_insights'] = {
                'confidence': 50.0,
                'prediction': 'unknown',
                'color': 'yellow',
                'history': None
            }
            recommendation['highlight_class'] = 'recommendation-yellow'
            recommendation['confidence_badge'] = 'Medium'
            return recommendation
    
    def _get_confidence_badge(self, confidence: float) -> str:
        """Get confidence badge text based on confidence score"""
        if confidence >= 80:
            return 'High'
        elif confidence >= 60:
            return 'Medium'
        else:
            return 'Low'
    
    def _add_basic_ml_structure(self, recommendation: Dict) -> Dict:
        """Add basic ML structure when ML components are not available"""
        recommendation['ml_insights'] = {
            'confidence': 50.0,
            'prediction': 'unknown',
            'color': 'yellow',
            'history': None
        }
        recommendation['highlight_class'] = 'recommendation-yellow'
        recommendation['confidence_badge'] = 'Medium'
        return recommendation
    
    def record_user_feedback(
        self,
        client_id: int,
        security_id: int,
        recommendation_type: str,
        user_decision: bool,
        amount: float,
        reason: str = None
    ) -> bool:
        """Record user's decision on a recommendation"""
        try:
            self.logger.info(f"Recording feedback: client_id={client_id}, security_id={security_id}, decision={user_decision}")
            self.logger.info(f"ML available: {self.ml_available}, feedback_tracker: {self.feedback_tracker is not None}")
            
            if self.ml_available and self.feedback_tracker:
                result = self.feedback_tracker.record_user_decision(
                    client_id=client_id,
                    security_id=security_id,
                    recommendation_type=recommendation_type,
                    model_recommended=True,  # Assuming this is from model recommendation
                    user_decision=user_decision,
                    amount=amount,
                    reason=reason
                )
                self.logger.info(f"Feedback recording result: {result}")
                return result
            else:
                self.logger.info("ML feedback tracking not available, skipping feedback recording")
                return True  # Return success but don't actually record
        except Exception as e:
            self.logger.error(f"Error recording user feedback: {e}")
            return False
    
    def get_user_behavior_summary(self, client_id: int) -> Dict:
        """Get user's behavior summary and insights"""
        try:
            if self.ml_available and self.feedback_tracker:
                return self.feedback_tracker.get_user_behavior_summary(client_id)
            else:
                return {
                    'total_recommendations': 0,
                    'followed_count': 0,
                    'follow_rate': 0.0,
                    'avg_confidence': 0.0,
                    'top_avoided_stocks': [],
                    'top_followed_stocks': []
                }
        except Exception as e:
            self.logger.error(f"Error getting user behavior summary: {e}")
            return {}
    
    def retrain_ml_model(self) -> Dict:
        """Retrain the ML model with latest user feedback data"""
        try:
            # Get training data
            training_data = self.feedback_tracker.get_training_data()
            
            if not training_data:
                return {
                    'success': False,
                    'message': 'No training data available'
                }
            
            # Convert to DataFrame (you might need to install pandas)
            import pandas as pd
            df = pd.DataFrame(training_data)
            
            # Train model
            result = self.ml_model.train_model(df)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error retraining ML model: {e}")
            return {
                'success': False,
                'message': str(e)
            }
    
    def get_recommendation_with_feedback_form(
        self,
        client_id: int,
        asset_class: str,
        current_asset_value: float,
        change_amount: float
    ) -> Dict:
        """Get recommendations with feedback form data for frontend"""
        try:
            # Get enhanced recommendations
            result = self.generate_enhanced_security_recommendations(
                client_id=client_id,
                asset_class=asset_class,
                current_asset_value=current_asset_value,
                change_amount=change_amount
            )
            
            if not result.get('success'):
                return result
            
            # Add feedback form data
            result['feedback_form'] = {
                'client_id': client_id,
                'asset_class': asset_class,
                'timestamp': str(datetime.now()),
                'total_recommendations': len(result.get('security_recommendations', []))
            }
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error getting recommendations with feedback form: {e}")
            return {
                'success': False,
                'error': str(e)
            }

# Global instance
enhanced_recommendation_api = EnhancedRecommendationAPI()
