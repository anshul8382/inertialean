#!/usr/bin/env python3
"""
Enhanced ML Training for User Recommendation Modifications
"""

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from typing import Dict, List, Tuple
from datetime import datetime
import logging

class UserModificationMLTrainer:
    """Enhanced ML trainer that learns from user modifications to recommendations"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Models for different aspects of user behavior
        self.quantity_modification_model = RandomForestRegressor(n_estimators=100, random_state=42)
        self.action_change_model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.security_addition_model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.asset_allocation_preference_model = RandomForestRegressor(n_estimators=100, random_state=42)
        
        self.scaler = StandardScaler()
        
    def extract_modification_features(self, original_recommendation: Dict, user_modification: Dict) -> Dict:
        """Extract features that explain why user made specific modifications based on key behavioral patterns"""
        
        # Calculate key behavioral indicators
        current_weight = original_recommendation.get('current_weight', 0)
        target_weight = original_recommendation.get('target_weight', 0)
        weight_change = target_weight - current_weight
        
        features = {
            # Original recommendation features
            'original_quantity': original_recommendation.get('quantity', 0),
            'original_amount': original_recommendation.get('amount', 0),
            'original_action': 1 if original_recommendation.get('action') == 'BUY' else 0,
            'original_confidence': original_recommendation.get('ml_confidence', 50),
            
            # 🎯 KEY BEHAVIORAL PATTERN FEATURES
            # 1. Trade minimization preference
            'total_trades_in_recommendation': original_recommendation.get('total_trades', 0),
            'number_of_securities': original_recommendation.get('number_of_securities', 0),
            'trade_complexity_score': self._calculate_trade_complexity(original_recommendation),
            'user_trade_minimization_history': original_recommendation.get('user_trade_minimization_score', 0.5),
            
            # 2. Selling avoidance for overweight positions
            'is_sell_recommendation': 1 if original_recommendation.get('action') == 'SELL' else 0,
            'is_overweight_position': 1 if current_weight > target_weight else 0,
            'overweight_magnitude': max(0, current_weight - target_weight),
            'sell_avoidance_score': self._calculate_sell_avoidance_score(current_weight, target_weight),
            'user_sell_avoidance_history': original_recommendation.get('user_sell_avoidance_score', 0.3),
            
            # 3. Buying based on weight and outlook
            'is_buy_recommendation': 1 if original_recommendation.get('action') == 'BUY' else 0,
            'weight_change_magnitude': abs(weight_change),
            'is_underweight_position': 1 if current_weight < target_weight else 0,
            'stock_outlook_score': original_recommendation.get('stock_outlook_score', 50),
            'sector_outlook_score': original_recommendation.get('sector_outlook_score', 50),
            'user_buy_preference_score': original_recommendation.get('user_buy_preference_score', 0.6),
            
            # 4. Sector/Stock exposure desire
            'current_sector_exposure': original_recommendation.get('current_sector_exposure', 0),
            'target_sector_exposure': original_recommendation.get('target_sector_exposure', 0),
            'sector_exposure_change': original_recommendation.get('sector_exposure_change', 0),
            'user_sector_preference_score': original_recommendation.get('user_sector_preference_score', 0.5),
            'stock_attractiveness_score': original_recommendation.get('stock_attractiveness_score', 50),
            
            # 5. Diversification needs
            'portfolio_concentration': original_recommendation.get('concentration_risk', 0),
            'sector_diversification_score': original_recommendation.get('sector_diversification_score', 50),
            'stock_diversification_score': original_recommendation.get('stock_diversification_score', 50),
            'user_diversification_preference': original_recommendation.get('user_diversification_preference', 0.5),
            
            # 6. Price attractiveness
            'current_price_vs_fair_value': original_recommendation.get('price_vs_fair_value_ratio', 1.0),
            'price_momentum_score': original_recommendation.get('price_momentum_score', 50),
            'valuation_attractiveness': original_recommendation.get('valuation_attractiveness', 50),
            'user_price_sensitivity': original_recommendation.get('user_price_sensitivity', 0.5),
            
            # Security characteristics
            'security_market_cap': original_recommendation.get('market_cap', 0),
            'security_volatility': original_recommendation.get('volatility', 0),
            'security_sector': self._encode_sector(original_recommendation.get('sector', '')),
            'security_current_price': original_recommendation.get('current_price', 0),
            'security_pe_ratio': original_recommendation.get('pe_ratio', 0),
            
            # Portfolio context
            'portfolio_total_value': original_recommendation.get('portfolio_value', 0),
            'current_weight_in_portfolio': current_weight,
            'target_weight_recommended': target_weight,
            
            # Client characteristics
            'client_risk_profile': original_recommendation.get('client_risk_profile', 3),
            'client_age': original_recommendation.get('client_age', 45),
            'client_investment_experience': original_recommendation.get('experience_years', 5),
            'client_income_level': original_recommendation.get('income_level', 3),
            
            # Market conditions
            'market_volatility_index': original_recommendation.get('market_volatility', 0),
            'sector_performance': original_recommendation.get('sector_performance', 0),
            'interest_rate_environment': original_recommendation.get('interest_rate', 5.0),
            
            # Time-based features
            'time_of_month': datetime.now().day,
            'day_of_week': datetime.now().weekday(),
            'quarter': datetime.now().quarter,
            
            # Historical user behavior
            'user_follow_rate_history': original_recommendation.get('user_follow_rate', 0.5),
            'user_modification_frequency': original_recommendation.get('modification_frequency', 0.1),
            'user_preferred_sectors': len(original_recommendation.get('preferred_sectors', [])),
            
            # Modification context
            'modification_time_seconds': user_modification.get('time_spent_on_page', 0),
            'number_of_modifications': user_modification.get('total_modifications_in_session', 1),
            'modification_type': self._encode_modification_type(user_modification.get('type', 'quantity_change'))
        }
        
        return features
    
    def record_user_modification(self, client_id: int, recommendation_id: int, 
                               original_data: Dict, modified_data: Dict, 
                               modification_context: Dict) -> bool:
        """Record user modification for ML training"""
        
        try:
            # Extract features
            features = self.extract_modification_features(original_data, modification_context)
            
            # Calculate modification metrics
            quantity_change = modified_data.get('quantity', 0) - original_data.get('quantity', 0)
            amount_change = modified_data.get('amount', 0) - original_data.get('amount', 0)
            action_changed = modified_data.get('action') != original_data.get('action')
            
            # Store training data
            training_record = {
                'client_id': client_id,
                'recommendation_id': recommendation_id,
                'timestamp': datetime.now(),
                **features,
                'quantity_change_ratio': quantity_change / max(original_data.get('quantity', 1), 1),
                'amount_change_ratio': amount_change / max(original_data.get('amount', 1), 1),
                'action_changed': 1 if action_changed else 0,
                'modification_magnitude': abs(quantity_change) + abs(amount_change),
                'modification_reason': modification_context.get('reason', 'user_preference')
            }
            
            # Store in database (you'll need to create this table)
            self._store_training_record(training_record)
            
            self.logger.info(f"Recorded user modification for client {client_id}, recommendation {recommendation_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording user modification: {e}")
            return False
    
    def train_modification_models(self, training_data: pd.DataFrame) -> Dict:
        """Train models to predict user modification patterns"""
        
        try:
            results = {}
            
            # 1. Train quantity modification model
            quantity_features = [col for col in training_data.columns if col not in [
                'quantity_change_ratio', 'amount_change_ratio', 'action_changed', 
                'modification_magnitude', 'client_id', 'recommendation_id', 'timestamp'
            ]]
            
            X = training_data[quantity_features]
            y_quantity = training_data['quantity_change_ratio']
            
            # Scale features
            X_scaled = self.scaler.fit_transform(X)
            
            # Train quantity modification model
            self.quantity_modification_model.fit(X_scaled, y_quantity)
            results['quantity_model_score'] = self.quantity_modification_model.score(X_scaled, y_quantity)
            
            # 2. Train action change model
            y_action = training_data['action_changed']
            self.action_change_model.fit(X_scaled, y_action)
            results['action_model_score'] = self.action_change_model.score(X_scaled, y_action)
            
            # 3. Train asset allocation preference model
            y_amount = training_data['amount_change_ratio']
            self.asset_allocation_preference_model.fit(X_scaled, y_amount)
            results['allocation_model_score'] = self.asset_allocation_preference_model.score(X_scaled, y_amount)
            
            results['success'] = True
            results['training_samples'] = len(training_data)
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error training modification models: {e}")
            return {'success': False, 'error': str(e)}
    
    def predict_user_modifications(self, client_id: int, recommendations: List[Dict]) -> List[Dict]:
        """Predict how a user might modify recommendations"""
        
        predictions = []
        
        for rec in recommendations:
            # Extract features for prediction
            features = self.extract_modification_features(rec, {})
            feature_array = np.array(list(features.values())).reshape(1, -1)
            feature_array_scaled = self.scaler.transform(feature_array)
            
            # Predict modifications
            predicted_quantity_ratio = self.quantity_modification_model.predict(feature_array_scaled)[0]
            predicted_action_change_prob = self.action_change_model.predict_proba(feature_array_scaled)[0][1]
            predicted_amount_ratio = self.asset_allocation_preference_model.predict(feature_array_scaled)[0]
            
            # Calculate modified values
            original_quantity = rec.get('quantity', 0)
            original_amount = rec.get('amount', 0)
            
            predicted_quantity = original_quantity * (1 + predicted_quantity_ratio)
            predicted_amount = original_amount * (1 + predicted_amount_ratio)
            
            predictions.append({
                'recommendation_id': rec.get('id'),
                'original_quantity': original_quantity,
                'predicted_quantity': predicted_quantity,
                'original_amount': original_amount,
                'predicted_amount': predicted_amount,
                'action_change_probability': predicted_action_change_prob,
                'modification_confidence': min(abs(predicted_quantity_ratio) + abs(predicted_amount_ratio), 1.0),
                'predicted_modification_type': 'quantity_reduction' if predicted_quantity_ratio < 0 else 'quantity_increase'
            })
        
        return predictions
    
    def _encode_sector(self, sector: str) -> float:
        """Encode sector as numerical value"""
        sector_encodings = {
            'Technology': 1.0, 'Healthcare': 0.8, 'Financial': 0.7,
            'Consumer': 0.6, 'Industrial': 0.5, 'Energy': 0.4,
            'Utilities': 0.3, 'Materials': 0.2, 'Other': 0.1
        }
        return sector_encodings.get(sector, 0.1)
    
    def _encode_modification_type(self, mod_type: str) -> float:
        """Encode modification type"""
        type_encodings = {
            'quantity_change': 1.0,
            'action_change': 0.8,
            'amount_change': 0.6,
            'security_addition': 0.4,
            'security_removal': 0.2
        }
        return type_encodings.get(mod_type, 0.5)
    
    def _calculate_trade_complexity(self, recommendation: Dict) -> float:
        """Calculate trade complexity score (higher = more complex)"""
        try:
            # Factors that increase complexity:
            # - Number of securities involved
            # - Mix of BUY/SELL actions
            # - Small trade sizes (more trades for same amount)
            
            num_securities = recommendation.get('number_of_securities', 1)
            total_amount = recommendation.get('total_amount', 100000)
            avg_trade_size = total_amount / max(num_securities, 1)
            
            # Complexity score (0-100)
            complexity = min(100, (num_securities * 10) + (1000 / max(avg_trade_size, 1000) * 20))
            return complexity
            
        except Exception:
            return 50.0  # Default moderate complexity
    
    def _calculate_sell_avoidance_score(self, current_weight: float, target_weight: float) -> float:
        """Calculate how much user avoids selling overweight positions"""
        try:
            if current_weight <= target_weight:
                return 0.0  # No selling needed
            
            # Higher score = more likely to avoid selling
            overweight_magnitude = current_weight - target_weight
            avoidance_score = min(1.0, overweight_magnitude / 10.0)  # Max at 10% overweight
            
            return avoidance_score
            
        except Exception:
            return 0.5  # Default moderate avoidance
    
    def _encode_sector(self, sector: str) -> float:
        """Encode sector as numerical value"""
        sector_encodings = {
            'Technology': 1.0, 'Healthcare': 0.8, 'Financial': 0.7,
            'Consumer': 0.6, 'Industrial': 0.5, 'Energy': 0.4,
            'Utilities': 0.3, 'Materials': 0.2, 'Other': 0.1
        }
        return sector_encodings.get(sector, 0.1)
    
    def _encode_modification_type(self, mod_type: str) -> float:
        """Encode modification type"""
        type_encodings = {
            'quantity_change': 1.0,
            'action_change': 0.8,
            'amount_change': 0.6,
            'security_addition': 0.4,
            'security_removal': 0.2
        }
        return type_encodings.get(mod_type, 0.5)
    
    def _store_training_record(self, record: Dict):
        """Store training record in database"""
        # This would insert into a user_modification_training_data table
        # Implementation depends on your database setup
        pass

# Global instance
modification_trainer = UserModificationMLTrainer()
