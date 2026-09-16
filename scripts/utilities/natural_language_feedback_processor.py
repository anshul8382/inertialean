#!/usr/bin/env python3
"""
Natural Language Feedback Processor - Understands and learns from user comments
"""
import logging
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)

class NaturalLanguageFeedbackProcessor:
    """Processes natural language feedback and extracts actionable insights"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Keywords for different types of feedback
        self.feedback_patterns = {
            'accuracy': [
                'wrong', 'incorrect', 'inaccurate', 'not right', 'misleading',
                'correct', 'accurate', 'right', 'good analysis', 'spot on'
            ],
            'detail_level': [
                'too generic', 'too detailed', 'too technical', 'too simple',
                'need more detail', 'too much detail', 'just right'
            ],
            'focus_areas': [
                'missing', 'add more', 'focus on', 'emphasize', 'highlight',
                'ignore', 'less focus on', 'too much focus on'
            ],
            'tone': [
                'too conservative', 'too aggressive', 'too optimistic', 'too pessimistic',
                'balanced', 'appropriate tone', 'wrong tone'
            ],
            'specificity': [
                'too vague', 'too specific', 'general', 'specific',
                'broad', 'narrow', 'detailed', 'high-level'
            ],
            'relevance': [
                'not relevant', 'irrelevant', 'relevant', 'useful',
                'helpful', 'not helpful', 'important', 'unimportant'
            ]
        }
    
    def process_feedback(self, feedback_text: str, context: Dict = None) -> Dict:
        """
        Process natural language feedback and extract insights
        
        Args:
            feedback_text: User's feedback in natural language
            context: Additional context (review_id, user_id, etc.)
        
        Returns:
            Dict containing processed feedback insights
        """
        try:
            feedback_lower = feedback_text.lower()
            
            # Extract different types of insights
            insights = {
                'feedback_type': self._classify_feedback_type(feedback_lower),
                'sentiment': self._analyze_sentiment(feedback_lower),
                'specific_issues': self._extract_specific_issues(feedback_text),
                'suggested_actions': self._extract_suggested_actions(feedback_text),
                'stock_mentions': self._extract_stock_mentions(feedback_text),
                'sector_mentions': self._extract_sector_mentions(feedback_text),
                'priority': self._determine_priority(feedback_lower),
                'learning_points': self._extract_learning_points(feedback_text),
                'processed_at': datetime.utcnow().isoformat()
            }
            
            # Add context if provided
            if context:
                insights['context'] = context
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error processing feedback: {str(e)}")
            return {}
    
    def _classify_feedback_type(self, feedback_text: str) -> str:
        """Classify the type of feedback"""
        if any(word in feedback_text for word in ['wrong', 'incorrect', 'inaccurate']):
            return 'accuracy_correction'
        elif any(word in feedback_text for word in ['too generic', 'too detailed', 'need more']):
            return 'detail_level'
        elif any(word in feedback_text for word in ['missing', 'add more', 'focus on']):
            return 'content_addition'
        elif any(word in feedback_text for word in ['tone', 'conservative', 'aggressive']):
            return 'tone_adjustment'
        elif any(word in feedback_text for word in ['relevant', 'useful', 'helpful']):
            return 'relevance_feedback'
        else:
            return 'general_feedback'
    
    def _analyze_sentiment(self, feedback_text: str) -> str:
        """Analyze the sentiment of the feedback"""
        positive_words = ['good', 'great', 'excellent', 'accurate', 'right', 'helpful', 'useful', 'correct']
        negative_words = ['wrong', 'bad', 'poor', 'inaccurate', 'useless', 'unhelpful', 'incorrect', 'misleading']
        
        positive_count = sum(1 for word in positive_words if word in feedback_text)
        negative_count = sum(1 for word in negative_words if word in feedback_text)
        
        if positive_count > negative_count:
            return 'positive'
        elif negative_count > positive_count:
            return 'negative'
        else:
            return 'neutral'
    
    def _extract_specific_issues(self, feedback_text: str) -> List[str]:
        """Extract specific issues mentioned in feedback"""
        issues = []
        
        # Look for specific problem statements
        problem_patterns = [
            r'(?:the|this)\s+(\w+)\s+(?:is|are)\s+(?:wrong|incorrect|inaccurate)',
            r'(?:missing|lack of)\s+(\w+)',
            r'(?:too much|too little)\s+(\w+)',
            r'(?:need more|add more)\s+(\w+)',
            r'(?:should|must)\s+(?:be|have)\s+(\w+)'
        ]
        
        for pattern in problem_patterns:
            matches = re.findall(pattern, feedback_text.lower())
            issues.extend(matches)
        
        return list(set(issues))  # Remove duplicates
    
    def _extract_suggested_actions(self, feedback_text: str) -> List[str]:
        """Extract suggested actions from feedback"""
        actions = []
        
        # Look for action-oriented phrases
        action_patterns = [
            r'(?:should|must|need to|have to)\s+(\w+(?:\s+\w+)*)',
            r'(?:add|include|mention|cover)\s+(\w+(?:\s+\w+)*)',
            r'(?:remove|delete|exclude)\s+(\w+(?:\s+\w+)*)',
            r'(?:focus on|emphasize|highlight)\s+(\w+(?:\s+\w+)*)',
            r'(?:change|modify|adjust)\s+(\w+(?:\s+\w+)*)'
        ]
        
        for pattern in action_patterns:
            matches = re.findall(pattern, feedback_text.lower())
            actions.extend(matches)
        
        return list(set(actions))
    
    def _extract_stock_mentions(self, feedback_text: str) -> List[str]:
        """Extract stock symbols/names mentioned in feedback"""
        # Common Indian stock patterns
        stock_patterns = [
            r'\b[A-Z]{2,10}\b',  # Stock symbols like RELIANCE, TCS, HDFC
            r'\b(?:reliance|tcs|hdfc|infy|itc|bharti|axis|icici|sbi|wipro)\b',
            r'\b(?:bank|industries|technologies|corporation|limited|ltd)\b'
        ]
        
        stocks = []
        for pattern in stock_patterns:
            matches = re.findall(pattern, feedback_text, re.IGNORECASE)
            stocks.extend(matches)
        
        return list(set(stocks))
    
    def _extract_sector_mentions(self, feedback_text: str) -> List[str]:
        """Extract sector mentions in feedback"""
        sectors = [
            'technology', 'banking', 'finance', 'consumer', 'healthcare',
            'energy', 'automobile', 'pharma', 'real estate', 'infrastructure',
            'telecom', 'media', 'retail', 'manufacturing', 'it'
        ]
        
        mentioned_sectors = []
        for sector in sectors:
            if sector in feedback_text.lower():
                mentioned_sectors.append(sector)
        
        return mentioned_sectors
    
    def _determine_priority(self, feedback_text: str) -> str:
        """Determine the priority of the feedback"""
        high_priority_words = ['critical', 'urgent', 'important', 'major', 'significant']
        low_priority_words = ['minor', 'small', 'slight', 'minor', 'trivial']
        
        if any(word in feedback_text for word in high_priority_words):
            return 'high'
        elif any(word in feedback_text for word in low_priority_words):
            return 'low'
        else:
            return 'medium'
    
    def _extract_learning_points(self, feedback_text: str) -> List[str]:
        """Extract specific learning points for the AI model"""
        learning_points = []
        
        # Look for specific corrections
        correction_patterns = [
            r'(?:actually|in fact|the truth is)\s+(.+)',
            r'(?:should be|is really|really is)\s+(.+)',
            r'(?:correction|correctly|properly)\s+(.+)'
        ]
        
        for pattern in correction_patterns:
            matches = re.findall(pattern, feedback_text, re.IGNORECASE)
            learning_points.extend(matches)
        
        return learning_points
    
    def generate_learning_summary(self, feedback_insights: Dict) -> str:
        """Generate a summary of what the model learned from feedback"""
        try:
            summary_parts = []
            
            if feedback_insights.get('feedback_type') == 'accuracy_correction':
                summary_parts.append("Model needs to improve accuracy in specific areas")
            
            if feedback_insights.get('specific_issues'):
                issues = feedback_insights['specific_issues']
                summary_parts.append(f"Address issues: {', '.join(issues)}")
            
            if feedback_insights.get('suggested_actions'):
                actions = feedback_insights['suggested_actions']
                summary_parts.append(f"Implement actions: {', '.join(actions)}")
            
            if feedback_insights.get('stock_mentions'):
                stocks = feedback_insights['stock_mentions']
                summary_parts.append(f"Pay more attention to: {', '.join(stocks)}")
            
            if feedback_insights.get('sector_mentions'):
                sectors = feedback_insights['sector_mentions']
                summary_parts.append(f"Focus on sectors: {', '.join(sectors)}")
            
            return ". ".join(summary_parts) if summary_parts else "General feedback received"
            
        except Exception as e:
            self.logger.error(f"Error generating learning summary: {str(e)}")
            return "Feedback processed successfully"
    
    def apply_feedback_to_model(self, feedback_insights: Dict) -> Dict:
        """
        Apply feedback insights to improve the AI model
        
        Args:
            feedback_insights: Processed feedback insights
        
        Returns:
            Dict containing model improvement actions
        """
        try:
            improvements = {
                'model_adjustments': [],
                'content_changes': [],
                'tone_adjustments': [],
                'focus_areas': [],
                'priority_updates': []
            }
            
            feedback_type = feedback_insights.get('feedback_type', '')
            sentiment = feedback_insights.get('sentiment', '')
            specific_issues = feedback_insights.get('specific_issues', [])
            suggested_actions = feedback_insights.get('suggested_actions', [])
            
            # Apply feedback based on type and sentiment
            if feedback_type == 'accuracy_correction' and sentiment == 'negative':
                improvements['model_adjustments'].append({
                    'type': 'accuracy_improvement',
                    'issues': specific_issues,
                    'action': 'Increase fact-checking and data validation'
                })
            
            elif feedback_type == 'detail_level':
                if 'too generic' in str(specific_issues):
                    improvements['content_changes'].append({
                        'type': 'increase_detail',
                        'action': 'Add more specific analysis and examples'
                    })
                elif 'too detailed' in str(specific_issues):
                    improvements['content_changes'].append({
                        'type': 'decrease_detail',
                        'action': 'Provide more concise, high-level insights'
                    })
            
            elif feedback_type == 'content_addition':
                improvements['focus_areas'].extend(suggested_actions)
            
            elif feedback_type == 'tone_adjustment':
                improvements['tone_adjustments'].append({
                    'current_tone': 'detected_from_feedback',
                    'suggested_tone': 'balanced',
                    'action': 'Adjust tone based on user preference'
                })
            
            return improvements
            
        except Exception as e:
            self.logger.error(f"Error applying feedback to model: {str(e)}")
            return {}
