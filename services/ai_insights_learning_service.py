"""
AI Insights Learning Service
Captures user edits to AI insights and uses them to improve future AI generation
"""

import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from models import db
from flask import has_app_context

logger = logging.getLogger(__name__)


class AIInsightsLearningService:
    """Service to capture and use learning patterns from user edits"""
    
    @staticmethod
    def save_user_edit(user_id: int, client_id: Optional[int], start_date: str, end_date: str,
                      period_key: str, original_insights: Dict[str, Any], 
                      edited_insights: Dict[str, Any], performance_context: Dict[str, Any]) -> bool:
        """
        Save user edits to AI insights for learning
        
        Args:
            user_id: ID of user who made edits
            client_id: ID of client (optional)
            start_date: Period start date
            end_date: Period end date
            period_key: Period identifier (period1, period2, period3)
            original_insights: Original AI-generated insights
            edited_insights: User-edited insights
            performance_context: Performance metrics context for pattern matching
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            from datetime import datetime as dt
            
            # Check if there are actual edits (not just the same content)
            has_edits = False
            for field in ['summary', 'performance_summary', 'good_aspects', 'areas_for_improvement']:
                original = original_insights.get(field, '')
                edited = edited_insights.get(field, '')
                if original != edited and edited:
                    has_edits = True
                    break
            
            if not has_edits:
                logger.info("No actual edits detected, skipping save")
                return False
            
            # Import model here to avoid circular imports
            from models import AIInsightsLearning
            
            # Create learning record
            learning_record = AIInsightsLearning(
                user_id=user_id,
                client_id=client_id,
                start_date=dt.strptime(start_date, '%Y-%m-%d').date() if isinstance(start_date, str) else start_date,
                end_date=dt.strptime(end_date, '%Y-%m-%d').date() if isinstance(end_date, str) else end_date,
                period_key=period_key,
                original_summary=original_insights.get('summary', ''),
                original_performance_summary=original_insights.get('performance_summary', ''),
                original_good_aspects=original_insights.get('good_aspects', ''),
                original_areas_for_improvement=original_insights.get('areas_for_improvement', ''),
                edited_summary=edited_insights.get('summary', ''),
                edited_performance_summary=edited_insights.get('performance_summary', ''),
                edited_good_aspects=edited_insights.get('good_aspects', ''),
                edited_areas_for_improvement=edited_insights.get('areas_for_improvement', ''),
                performance_context=json.dumps(performance_context) if performance_context else None
            )
            
            db.session.add(learning_record)
            db.session.commit()
            
            logger.info(f"Saved AI insights learning record for user {user_id}, period {period_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving AI insights learning: {str(e)}", exc_info=True)
            db.session.rollback()
            return False
    
    @staticmethod
    def get_learning_patterns(performance_context: Dict[str, Any], 
                             user_id: Optional[int] = None,
                             limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get similar learning patterns based on performance context
        
        Args:
            performance_context: Current performance metrics to match against
            user_id: Optional user ID to get user-specific patterns
            limit: Maximum number of patterns to return
            
        Returns:
            List of learning patterns (similar past edits)
        """
        try:
            # If called outside Flask app context (e.g., CLI scripts), skip DB access gracefully
            if not has_app_context():
                return []

            from sqlalchemy import func, or_
            import json as json_lib
            
            # Extract key performance metrics for matching
            total_return = performance_context.get('total_return_percent', 0) or 0
            excess_return = performance_context.get('excess_return', 0) or 0
            xirr = performance_context.get('xirr', 0) or 0
            
            # Import model here to avoid circular imports
            from models import AIInsightsLearning
            
            # Build query
            query = AIInsightsLearning.query
            
            # Filter by user if specified
            if user_id:
                query = query.filter(AIInsightsLearning.user_id == user_id)
            
            # Get all learning records (we'll match by similarity in Python)
            # For simplicity, get recent records and match by performance ranges
            all_records = query.order_by(AIInsightsLearning.created_at.desc()).limit(50).all()
            
            # Score and match records by performance similarity
            scored_records = []
            for record in all_records:
                if not record.performance_context:
                    continue
                
                try:
                    record_context = json_lib.loads(record.performance_context)
                    record_return = record_context.get('total_return_percent', 0) or 0
                    record_excess = record_context.get('excess_return', 0) or 0
                    record_xirr = record_context.get('xirr', 0) or 0
                    
                    # Calculate similarity score (simpler matching)
                    return_diff = abs(total_return - record_return)
                    excess_diff = abs(excess_return - record_excess)
                    xirr_diff = abs(xirr - record_xirr)
                    
                    # Score based on similarity (lower difference = higher score)
                    score = 100 - (return_diff * 0.5 + excess_diff * 0.3 + xirr_diff * 0.2)
                    
                    if score > 50:  # Only include reasonably similar patterns
                        scored_records.append({
                            'score': score,
                            'record': record
                        })
                except:
                    continue
            
            # Sort by score and return top matches
            scored_records.sort(key=lambda x: x['score'], reverse=True)
            patterns = [item['record'] for item in scored_records[:limit]]
            
            # Convert to dictionary format
            result = []
            for pattern in patterns:
                result.append({
                    'id': pattern.id,
                    'original': {
                        'summary': pattern.original_summary,
                        'performance_summary': pattern.original_performance_summary,
                        'good_aspects': pattern.original_good_aspects,
                        'areas_for_improvement': pattern.original_areas_for_improvement
                    },
                    'edited': {
                        'summary': pattern.edited_summary,
                        'performance_summary': pattern.edited_performance_summary,
                        'good_aspects': pattern.edited_good_aspects,
                        'areas_for_improvement': pattern.edited_areas_for_improvement
                    },
                    'performance_context': json_lib.loads(pattern.performance_context) if pattern.performance_context else {},
                    'score': next((item['score'] for item in scored_records if item['record'].id == pattern.id), 0)
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting learning patterns: {str(e)}", exc_info=True)
            return []
    
    @staticmethod
    def enhance_prompt_with_learning(base_prompt: str, performance_context: Dict[str, Any],
                                    user_id: Optional[int] = None) -> str:
        """
        Enhance AI prompt with learning patterns from user edits
        
        Args:
            base_prompt: Original prompt for AI generation
            performance_context: Current performance metrics
            user_id: Optional user ID for user-specific learning
            
        Returns:
            Enhanced prompt with learning context
        """
        try:
            # Get similar learning patterns
            patterns = AIInsightsLearningService.get_learning_patterns(
                performance_context, user_id, limit=3
            )
            
            if not patterns:
                return base_prompt
            
            # Build learning context from patterns
            learning_context = "\n\nBased on previous user preferences for similar performance:\n"
            
            for i, pattern in enumerate(patterns[:2], 1):  # Use top 2 patterns
                learning_context += f"\nPattern {i} (similarity: {pattern['score']:.1f}%):\n"
                
                # Show what was changed (key differences)
                for field in ['summary', 'performance_summary', 'good_aspects', 'areas_for_improvement']:
                    original = pattern['original'].get(field, '')
                    edited = pattern['edited'].get(field, '')
                    
                    if original != edited and edited:
                        # Extract key differences (simplified - show edited version)
                        learning_context += f"- User preferred this style for {field}: {edited[:200]}...\n"
            
            learning_context += "\nConsider these preferences when generating insights.\n"
            
            enhanced_prompt = base_prompt + learning_context
            return enhanced_prompt
            
        except Exception as e:
            logger.error(f"Error enhancing prompt with learning: {str(e)}")
            return base_prompt

