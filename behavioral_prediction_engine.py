#!/usr/bin/env python3
"""
Behavioral Prediction Engine based on user investment patterns
"""

import numpy as np
from typing import Dict, List, Tuple
from datetime import datetime
import logging

class BehavioralPredictionEngine:
    """Predict user modifications based on key behavioral patterns"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Behavioral pattern weights (learned from user data)
        self.pattern_weights = {
            'trade_minimization': 0.25,
            'sell_avoidance': 0.20,
            'buy_preference': 0.20,
            'sector_exposure': 0.15,
            'diversification': 0.10,
            'price_sensitivity': 0.10
        }
    
    def predict_user_modifications(self, client_id: int, recommendations: List[Dict]) -> List[Dict]:
        """Predict how user will modify recommendations based on behavioral patterns"""
        
        predictions = []
        
        for rec in recommendations:
            # Get behavioral scores for this recommendation
            behavioral_scores = self._calculate_behavioral_scores(rec, client_id)
            
            # Predict modifications based on each pattern
            predictions.append({
                'recommendation_id': rec.get('id'),
                'security_id': rec.get('security_id'),
                'symbol': rec.get('symbol'),
                'original_action': rec.get('action'),
                'original_quantity': rec.get('quantity', 0),
                'original_amount': rec.get('amount', 0),
                
                # Behavioral pattern predictions
                'predicted_modifications': {
                    'trade_minimization_impact': behavioral_scores['trade_minimization'],
                    'sell_avoidance_likelihood': behavioral_scores['sell_avoidance'],
                    'buy_preference_strength': behavioral_scores['buy_preference'],
                    'sector_exposure_desire': behavioral_scores['sector_exposure'],
                    'diversification_need': behavioral_scores['diversification'],
                    'price_sensitivity_score': behavioral_scores['price_sensitivity']
                },
                
                # Predicted outcomes
                'predicted_quantity': self._predict_quantity_modification(rec, behavioral_scores),
                'predicted_amount': self._predict_amount_modification(rec, behavioral_scores),
                'predicted_action': self._predict_action_modification(rec, behavioral_scores),
                'modification_confidence': self._calculate_overall_confidence(behavioral_scores),
                
                # Recommendations
                'suggested_actions': self._generate_suggestions(rec, behavioral_scores)
            })
        
        return predictions
    
    def _calculate_behavioral_scores(self, recommendation: Dict, client_id: int) -> Dict:
        """Calculate behavioral pattern scores for a recommendation"""
        
        current_weight = recommendation.get('current_weight', 0)
        target_weight = recommendation.get('target_weight', 0)
        action = recommendation.get('action', 'HOLD')
        
        scores = {
            # 1. Trade Minimization Preference
            'trade_minimization': self._score_trade_minimization(recommendation),
            
            # 2. Selling Avoidance for Overweight
            'sell_avoidance': self._score_sell_avoidance(current_weight, target_weight, action),
            
            # 3. Buying Based on Weight and Outlook
            'buy_preference': self._score_buy_preference(recommendation, current_weight, target_weight),
            
            # 4. Sector/Stock Exposure Desire
            'sector_exposure': self._score_sector_exposure_desire(recommendation),
            
            # 5. Diversification Needs
            'diversification': self._score_diversification_need(recommendation),
            
            # 6. Price Attractiveness
            'price_sensitivity': self._score_price_sensitivity(recommendation)
        }
        
        return scores
    
    def _score_trade_minimization(self, recommendation: Dict) -> float:
        """Score how much user wants to minimize trades"""
        
        # Higher score = more likely to reduce trades
        total_trades = recommendation.get('total_trades', 1)
        num_securities = recommendation.get('number_of_securities', 1)
        avg_trade_size = recommendation.get('total_amount', 100000) / max(total_trades, 1)
        
        # Small trades and many securities = high minimization desire
        if total_trades > 5 or avg_trade_size < 10000:
            return 0.8  # High desire to minimize
        elif total_trades > 3 or avg_trade_size < 25000:
            return 0.6  # Medium desire
        else:
            return 0.3  # Low desire
    
    def _score_sell_avoidance(self, current_weight: float, target_weight: float, action: str) -> float:
        """Score how much user avoids selling overweight positions"""
        
        if action != 'SELL' or current_weight <= target_weight:
            return 0.0  # No selling involved
        
        # Higher overweight = higher avoidance
        overweight_magnitude = current_weight - target_weight
        
        if overweight_magnitude > 5.0:
            return 0.9  # Very high avoidance
        elif overweight_magnitude > 2.0:
            return 0.7  # High avoidance
        elif overweight_magnitude > 0.5:
            return 0.5  # Medium avoidance
        else:
            return 0.2  # Low avoidance
    
    def _score_buy_preference(self, recommendation: Dict, current_weight: float, target_weight: float) -> float:
        """Score buying preference based on weight and outlook"""
        
        if recommendation.get('action') != 'BUY':
            return 0.0
        
        # Calculate underweight magnitude
        underweight = max(0, target_weight - current_weight)
        
        # Get outlook scores
        stock_outlook = recommendation.get('stock_outlook_score', 50)
        sector_outlook = recommendation.get('sector_outlook_score', 50)
        avg_outlook = (stock_outlook + sector_outlook) / 2
        
        # Higher underweight + better outlook = higher buy preference
        if underweight > 2.0 and avg_outlook > 70:
            return 0.9  # Very high buy preference
        elif underweight > 1.0 and avg_outlook > 60:
            return 0.7  # High buy preference
        elif underweight > 0.5 and avg_outlook > 50:
            return 0.5  # Medium buy preference
        else:
            return 0.3  # Low buy preference
    
    def _score_sector_exposure_desire(self, recommendation: Dict) -> float:
        """Score desire for sector/stock exposure"""
        
        current_exposure = recommendation.get('current_sector_exposure', 0)
        target_exposure = recommendation.get('target_sector_exposure', 0)
        exposure_change = target_exposure - current_exposure
        
        stock_attractiveness = recommendation.get('stock_attractiveness_score', 50)
        sector_preference = recommendation.get('user_sector_preference_score', 0.5)
        
        # Higher exposure change + attractiveness = higher desire
        if exposure_change > 5.0 and stock_attractiveness > 70 and sector_preference > 0.7:
            return 0.9  # Very high desire
        elif exposure_change > 2.0 and stock_attractiveness > 60 and sector_preference > 0.6:
            return 0.7  # High desire
        elif exposure_change > 0.5 and stock_attractiveness > 50:
            return 0.5  # Medium desire
        else:
            return 0.3  # Low desire
    
    def _score_diversification_need(self, recommendation: Dict) -> float:
        """Score diversification needs"""
        
        concentration = recommendation.get('concentration_risk', 0)
        sector_diversification = recommendation.get('sector_diversification_score', 50)
        stock_diversification = recommendation.get('stock_diversification_score', 50)
        
        # High concentration + low diversification = high need
        if concentration > 30 and (sector_diversification < 40 or stock_diversification < 40):
            return 0.8  # High diversification need
        elif concentration > 20 and (sector_diversification < 50 or stock_diversification < 50):
            return 0.6  # Medium diversification need
        elif concentration > 10:
            return 0.4  # Low diversification need
        else:
            return 0.2  # Very low diversification need
    
    def _score_price_sensitivity(self, recommendation: Dict) -> float:
        """Score price sensitivity/attractiveness"""
        
        price_vs_fair_value = recommendation.get('price_vs_fair_value_ratio', 1.0)
        price_momentum = recommendation.get('price_momentum_score', 50)
        valuation_attractiveness = recommendation.get('valuation_attractiveness', 50)
        
        # Low price vs fair value + good momentum + attractive valuation = high sensitivity
        if price_vs_fair_value < 0.9 and price_momentum > 60 and valuation_attractiveness > 70:
            return 0.9  # Very price sensitive (good price)
        elif price_vs_fair_value < 1.0 and price_momentum > 50 and valuation_attractiveness > 60:
            return 0.7  # High price sensitivity
        elif price_vs_fair_value < 1.1 and valuation_attractiveness > 50:
            return 0.5  # Medium price sensitivity
        else:
            return 0.3  # Low price sensitivity (poor price)
    
    def _predict_quantity_modification(self, recommendation: Dict, scores: Dict) -> int:
        """Predict how user will modify quantity"""
        
        original_quantity = recommendation.get('quantity', 0)
        
        # Trade minimization: reduce quantity
        trade_min_impact = scores['trade_minimization'] * 0.2  # Max 20% reduction
        
        # Sell avoidance: reduce sell quantity significantly
        sell_avoid_impact = scores['sell_avoidance'] * 0.5  # Max 50% reduction for sells
        
        # Buy preference: may increase quantity
        buy_pref_impact = scores['buy_preference'] * 0.15  # Max 15% increase
        
        # Price sensitivity: reduce if poor price
        price_impact = (1 - scores['price_sensitivity']) * 0.1  # Reduce up to 10% for poor prices
        
        # Calculate net modification
        total_modification = -trade_min_impact - sell_avoid_impact + buy_pref_impact - price_impact
        
        predicted_quantity = int(original_quantity * (1 + total_modification))
        return max(0, predicted_quantity)  # Ensure non-negative
    
    def _predict_amount_modification(self, recommendation: Dict, scores: Dict) -> float:
        """Predict how user will modify amount"""
        
        original_amount = recommendation.get('amount', 0)
        
        # Similar logic to quantity but with different weights
        trade_min_impact = scores['trade_minimization'] * 0.15
        sell_avoid_impact = scores['sell_avoidance'] * 0.4
        buy_pref_impact = scores['buy_preference'] * 0.2
        price_impact = (1 - scores['price_sensitivity']) * 0.1
        
        total_modification = -trade_min_impact - sell_avoid_impact + buy_pref_impact - price_impact
        
        predicted_amount = original_amount * (1 + total_modification)
        return max(0, predicted_amount)
    
    def _predict_action_modification(self, recommendation: Dict, scores: Dict) -> str:
        """Predict if user will change BUY/SELL action"""
        
        original_action = recommendation.get('action', 'HOLD')
        
        # High sell avoidance might change SELL to HOLD
        if original_action == 'SELL' and scores['sell_avoidance'] > 0.7:
            return 'HOLD'
        
        # High buy preference might change HOLD to BUY
        if original_action == 'HOLD' and scores['buy_preference'] > 0.7:
            return 'BUY'
        
        # Price sensitivity might change BUY to HOLD
        if original_action == 'BUY' and scores['price_sensitivity'] < 0.4:
            return 'HOLD'
        
        return original_action  # No change predicted
    
    def _calculate_overall_confidence(self, scores: Dict) -> float:
        """Calculate overall confidence in predictions"""
        
        # Weight the scores by their importance
        weighted_score = sum(
            scores[pattern] * weight 
            for pattern, weight in self.pattern_weights.items()
        )
        
        # Convert to confidence percentage
        confidence = min(95, max(5, weighted_score * 100))
        return round(confidence, 1)
    
    def _generate_suggestions(self, recommendation: Dict, scores: Dict) -> List[str]:
        """Generate actionable suggestions based on behavioral patterns"""
        
        suggestions = []
        
        # Trade minimization suggestions
        if scores['trade_minimization'] > 0.6:
            suggestions.append("Consider consolidating trades to minimize transaction complexity")
        
        # Sell avoidance suggestions
        if scores['sell_avoidance'] > 0.7:
            suggestions.append("User typically avoids selling overweight positions - consider HOLD instead")
        
        # Buy preference suggestions
        if scores['buy_preference'] > 0.7:
            suggestions.append("Strong buy preference - user likely to increase quantity")
        
        # Sector exposure suggestions
        if scores['sector_exposure'] > 0.7:
            suggestions.append("High sector exposure desire - user may add more of this security")
        
        # Diversification suggestions
        if scores['diversification'] > 0.6:
            suggestions.append("Diversification needed - consider spreading across more securities")
        
        # Price sensitivity suggestions
        if scores['price_sensitivity'] < 0.4:
            suggestions.append("Poor price attractiveness - user may reduce quantity or skip")
        
        return suggestions

# Global instance
behavioral_predictor = BehavioralPredictionEngine()
