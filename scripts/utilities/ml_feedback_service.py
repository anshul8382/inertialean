#!/usr/bin/env python3
"""
ML Feedback Service - Handles user feedback on ML recommendations
"""
from models import MLRecommendationFeedback, MLModelTrainingHistory, UserRecommendationPreference, Review, User
from extensions import db
from datetime import datetime, timedelta
import logging
import json
from typing import Dict, List, Optional, Tuple
from natural_language_feedback_processor import NaturalLanguageFeedbackProcessor

logger = logging.getLogger(__name__)

class MLFeedbackService:
    """Service for handling ML recommendation feedback and learning"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.nlp_processor = NaturalLanguageFeedbackProcessor()
    
    def record_recommendation_feedback(self, review_id: int, recommendation_id: str, 
                                     user_id: int, rating: int = None, 
                                     implemented: bool = None, ignored: bool = None,
                                     feedback_text: str = None) -> bool:
        """
        Record user feedback on a specific recommendation
        
        Args:
            review_id: ID of the review containing the recommendation
            recommendation_id: Unique identifier for the recommendation
            user_id: ID of the user providing feedback
            rating: 1-5 rating of the recommendation quality
            implemented: Whether the recommendation was implemented
            ignored: Whether the recommendation was ignored
            feedback_text: Additional text feedback
        
        Returns:
            bool: Success status
        """
        try:
            # Check if feedback already exists
            existing_feedback = MLRecommendationFeedback.query.filter_by(
                review_id=review_id,
                recommendation_id=recommendation_id,
                user_id=user_id
            ).first()
            
            if existing_feedback:
                # Update existing feedback
                if rating is not None:
                    existing_feedback.rating = rating
                if implemented is not None:
                    existing_feedback.implemented = implemented
                if ignored is not None:
                    existing_feedback.ignored = ignored
                if feedback_text is not None:
                    existing_feedback.feedback_text = feedback_text
                existing_feedback.updated_at = datetime.utcnow()
            else:
                # Create new feedback
                feedback = MLRecommendationFeedback(
                    review_id=review_id,
                    recommendation_id=recommendation_id,
                    user_id=user_id,
                    rating=rating,
                    implemented=implemented,
                    ignored=ignored,
                    feedback_text=feedback_text
                )
                db.session.add(feedback)
            
            db.session.commit()
            self.logger.info(f"Feedback recorded for recommendation {recommendation_id} in review {review_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording feedback: {str(e)}")
            db.session.rollback()
            return False
    
    def record_outcome_feedback(self, review_id: int, recommendation_id: str, 
                              user_id: int, outcome_rating: int, 
                              outcome_notes: str = None) -> bool:
        """
        Record outcome feedback after recommendation implementation
        
        Args:
            review_id: ID of the review
            recommendation_id: ID of the recommendation
            user_id: ID of the user
            outcome_rating: 1-5 rating of actual outcome
            outcome_notes: Notes about the outcome
        
        Returns:
            bool: Success status
        """
        try:
            feedback = MLRecommendationFeedback.query.filter_by(
                review_id=review_id,
                recommendation_id=recommendation_id,
                user_id=user_id
            ).first()
            
            if feedback:
                feedback.outcome_rating = outcome_rating
                feedback.outcome_notes = outcome_notes
                feedback.updated_at = datetime.utcnow()
                db.session.commit()
                return True
            else:
                self.logger.warning(f"No existing feedback found for recommendation {recommendation_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error recording outcome feedback: {str(e)}")
            db.session.rollback()
            return False
    
    def get_feedback_analytics(self, days: int = 30) -> Dict:
        """
        Get analytics on recommendation feedback
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dict containing feedback analytics
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Get all feedback in the period
            feedback_data = MLRecommendationFeedback.query.filter(
                MLRecommendationFeedback.created_at >= cutoff_date
            ).all()
            
            total_feedback = len(feedback_data)
            if total_feedback == 0:
                return {
                    'total_feedback': 0,
                    'average_rating': 0,
                    'implementation_rate': 0,
                    'ignored_rate': 0,
                    'outcome_satisfaction': 0,
                    'category_performance': {},
                    'trends': {}
                }
            
            # Calculate metrics
            ratings = [f.rating for f in feedback_data if f.rating is not None]
            average_rating = sum(ratings) / len(ratings) if ratings else 0
            
            implemented_count = sum(1 for f in feedback_data if f.implemented)
            ignored_count = sum(1 for f in feedback_data if f.ignored)
            implementation_rate = (implemented_count / total_feedback) * 100
            ignored_rate = (ignored_count / total_feedback) * 100
            
            outcome_ratings = [f.outcome_rating for f in feedback_data if f.outcome_rating is not None]
            outcome_satisfaction = sum(outcome_ratings) / len(outcome_ratings) if outcome_ratings else 0
            
            # Category performance analysis
            category_performance = self._analyze_category_performance(feedback_data)
            
            # Trends analysis
            trends = self._analyze_feedback_trends(feedback_data, days)
            
            return {
                'total_feedback': total_feedback,
                'average_rating': round(average_rating, 2),
                'implementation_rate': round(implementation_rate, 2),
                'ignored_rate': round(ignored_rate, 2),
                'outcome_satisfaction': round(outcome_satisfaction, 2),
                'category_performance': category_performance,
                'trends': trends
            }
            
        except Exception as e:
            self.logger.error(f"Error getting feedback analytics: {str(e)}")
            return {}
    
    def _analyze_category_performance(self, feedback_data: List[MLRecommendationFeedback]) -> Dict:
        """Analyze performance by recommendation category"""
        try:
            category_stats = {}
            
            for feedback in feedback_data:
                # Get recommendation category from review data
                review = Review.query.get(feedback.review_id)
                if not review or not review.review_data:
                    continue
                
                ml_data = review.review_data.get('ml_recommendations', {})
                recommendations = ml_data.get('recommendations', [])
                
                # Find the specific recommendation
                for rec in recommendations:
                    if rec.get('id') == feedback.recommendation_id:
                        category = rec.get('category', 'unknown')
                        
                        if category not in category_stats:
                            category_stats[category] = {
                                'total': 0,
                                'ratings': [],
                                'implemented': 0,
                                'ignored': 0,
                                'outcome_ratings': []
                            }
                        
                        stats = category_stats[category]
                        stats['total'] += 1
                        
                        if feedback.rating:
                            stats['ratings'].append(feedback.rating)
                        if feedback.implemented:
                            stats['implemented'] += 1
                        if feedback.ignored:
                            stats['ignored'] += 1
                        if feedback.outcome_rating:
                            stats['outcome_ratings'].append(feedback.outcome_rating)
                        break
            
            # Calculate averages
            for category, stats in category_stats.items():
                if stats['ratings']:
                    stats['avg_rating'] = round(sum(stats['ratings']) / len(stats['ratings']), 2)
                else:
                    stats['avg_rating'] = 0
                
                if stats['outcome_ratings']:
                    stats['avg_outcome'] = round(sum(stats['outcome_ratings']) / len(stats['outcome_ratings']), 2)
                else:
                    stats['avg_outcome'] = 0
                
                stats['implementation_rate'] = round((stats['implemented'] / stats['total']) * 100, 2) if stats['total'] > 0 else 0
                
                # Remove raw lists for cleaner output
                del stats['ratings']
                del stats['outcome_ratings']
            
            return category_stats
            
        except Exception as e:
            self.logger.error(f"Error analyzing category performance: {str(e)}")
            return {}
    
    def _analyze_feedback_trends(self, feedback_data: List[MLRecommendationFeedback], days: int) -> Dict:
        """Analyze feedback trends over time"""
        try:
            trends = {
                'daily_ratings': {},
                'daily_implementations': {},
                'rating_trend': [],
                'implementation_trend': []
            }
            
            # Group by date
            for feedback in feedback_data:
                date_str = feedback.created_at.strftime('%Y-%m-%d')
                
                if date_str not in trends['daily_ratings']:
                    trends['daily_ratings'][date_str] = []
                if date_str not in trends['daily_implementations']:
                    trends['daily_implementations'][date_str] = 0
                
                if feedback.rating:
                    trends['daily_ratings'][date_str].append(feedback.rating)
                if feedback.implemented:
                    trends['daily_implementations'][date_str] += 1
            
            # Calculate daily averages
            for date_str, ratings in trends['daily_ratings'].items():
                if ratings:
                    trends['daily_ratings'][date_str] = round(sum(ratings) / len(ratings), 2)
                else:
                    trends['daily_ratings'][date_str] = 0
            
            # Convert to sorted lists for trend analysis
            trends['rating_trend'] = sorted(trends['daily_ratings'].items())
            trends['implementation_trend'] = sorted(trends['daily_implementations'].items())
            
            return trends
            
        except Exception as e:
            self.logger.error(f"Error analyzing feedback trends: {str(e)}")
            return {}
    
    def get_user_preferences(self, user_id: int) -> Optional[UserRecommendationPreference]:
        """Get user's recommendation preferences"""
        try:
            return UserRecommendationPreference.query.filter_by(user_id=user_id).first()
        except Exception as e:
            self.logger.error(f"Error getting user preferences: {str(e)}")
            return None
    
    def update_user_preferences(self, user_id: int, preferences: Dict) -> bool:
        """Update user's recommendation preferences"""
        try:
            user_pref = self.get_user_preferences(user_id)
            
            if user_pref:
                # Update existing preferences
                for key, value in preferences.items():
                    if hasattr(user_pref, key):
                        setattr(user_pref, key, value)
                user_pref.updated_at = datetime.utcnow()
            else:
                # Create new preferences
                user_pref = UserRecommendationPreference(
                    user_id=user_id,
                    **preferences
                )
                db.session.add(user_pref)
            
            db.session.commit()
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating user preferences: {str(e)}")
            db.session.rollback()
            return False
    
    def get_learning_insights(self) -> Dict:
        """Get insights for model improvement based on feedback"""
        try:
            # Get recent feedback (last 90 days)
            cutoff_date = datetime.utcnow() - timedelta(days=90)
            recent_feedback = MLRecommendationFeedback.query.filter(
                MLRecommendationFeedback.created_at >= cutoff_date
            ).all()
            
            insights = {
                'low_rated_recommendations': [],
                'high_implementation_recommendations': [],
                'ignored_recommendations': [],
                'improvement_areas': [],
                'success_patterns': []
            }
            
            # Analyze patterns
            for feedback in recent_feedback:
                if feedback.rating and feedback.rating <= 2:
                    insights['low_rated_recommendations'].append({
                        'review_id': feedback.review_id,
                        'recommendation_id': feedback.recommendation_id,
                        'rating': feedback.rating,
                        'feedback_text': feedback.feedback_text
                    })
                
                if feedback.implemented and feedback.outcome_rating and feedback.outcome_rating >= 4:
                    insights['high_implementation_recommendations'].append({
                        'review_id': feedback.review_id,
                        'recommendation_id': feedback.recommendation_id,
                        'outcome_rating': feedback.outcome_rating
                    })
                
                if feedback.ignored:
                    insights['ignored_recommendations'].append({
                        'review_id': feedback.review_id,
                        'recommendation_id': feedback.recommendation_id,
                        'feedback_text': feedback.feedback_text
                    })
            
            # Generate improvement suggestions
            if len(insights['low_rated_recommendations']) > 0:
                insights['improvement_areas'].append({
                    'area': 'Recommendation Quality',
                    'description': f"{len(insights['low_rated_recommendations'])} recommendations rated poorly",
                    'suggestion': 'Review and improve recommendation generation logic'
                })
            
            if len(insights['ignored_recommendations']) > 0:
                insights['improvement_areas'].append({
                    'area': 'Recommendation Relevance',
                    'description': f"{len(insights['ignored_recommendations'])} recommendations ignored",
                    'suggestion': 'Improve recommendation filtering and personalization'
                })
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error getting learning insights: {str(e)}")
            return {}
    
    def should_retrain_models(self) -> Tuple[bool, str]:
        """
        Determine if models should be retrained based on feedback
        
        Returns:
            Tuple of (should_retrain, reason)
        """
        try:
            # Get recent feedback
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            recent_feedback = MLRecommendationFeedback.query.filter(
                MLRecommendationFeedback.created_at >= cutoff_date
            ).all()
            
            if len(recent_feedback) < 50:
                return False, "Insufficient feedback data for retraining"
            
            # Check average rating
            ratings = [f.rating for f in recent_feedback if f.rating is not None]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)
                if avg_rating < 3.0:
                    return True, f"Low average rating ({avg_rating:.2f}) indicates need for improvement"
            
            # Check implementation rate
            implemented_count = sum(1 for f in recent_feedback if f.implemented)
            implementation_rate = implemented_count / len(recent_feedback)
            if implementation_rate < 0.3:
                return True, f"Low implementation rate ({implementation_rate:.2%}) suggests recommendations need improvement"
            
            return False, "Models performing adequately"
            
        except Exception as e:
            self.logger.error(f"Error checking retrain conditions: {str(e)}")
            return False, "Error checking retrain conditions"
    
    def process_natural_language_feedback(self, feedback_text: str, context: Dict = None) -> Dict:
        """
        Process natural language feedback and extract actionable insights
        
        Args:
            feedback_text: User's feedback in natural language
            context: Additional context (review_id, user_id, etc.)
        
        Returns:
            Dict containing processed feedback insights and learning summary
        """
        try:
            # Process the feedback using NLP processor
            insights = self.nlp_processor.process_feedback(feedback_text, context)
            
            # Generate learning summary
            learning_summary = self.nlp_processor.generate_learning_summary(insights)
            insights['learning_summary'] = learning_summary
            
            # Store the processed feedback for future learning
            self._store_processed_feedback(insights)
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error processing natural language feedback: {str(e)}")
            return {'error': str(e)}
    
    def _store_processed_feedback(self, insights: Dict) -> None:
        """Store processed feedback for model learning"""
        try:
            # This could be stored in a separate table or used to update model parameters
            # For now, we'll log it for demonstration
            self.logger.info(f"Stored processed feedback: {json.dumps(insights, indent=2)}")
            
            # In a real implementation, you might:
            # 1. Store in a feedback_insights table
            # 2. Update model weights based on feedback
            # 3. Trigger model retraining if needed
            # 4. Update user preferences based on feedback
            
        except Exception as e:
            self.logger.error(f"Error storing processed feedback: {str(e)}")
    
    def get_feedback_learning_insights(self) -> Dict:
        """
        Get insights from processed natural language feedback
        
        Returns:
            Dict containing learning insights and recommendations
        """
        try:
            # This would typically query stored processed feedback
            # For now, return a sample structure
            insights = {
                'common_issues': [
                    'Too generic commentary',
                    'Missing sector-specific analysis',
                    'Incorrect stock performance data'
                ],
                'user_preferences': [
                    'More detailed technical analysis',
                    'Focus on Indian market context',
                    'Include regulatory impact analysis'
                ],
                'improvement_areas': [
                    'Stock-specific commentary accuracy',
                    'Sector rotation timing',
                    'Risk assessment precision'
                ],
                'learning_recommendations': [
                    'Increase fact-checking for stock data',
                    'Add more sector-specific insights',
                    'Improve regulatory impact analysis'
                ]
            }
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error getting feedback learning insights: {str(e)}")
            return {}
