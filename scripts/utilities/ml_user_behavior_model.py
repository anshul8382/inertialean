#!/usr/bin/env python3
"""
ML Model for predicting user behavior on investment recommendations
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import StandardScaler
import joblib
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import os

class UserBehaviorMLModel:
    """ML model to predict user behavior on investment recommendations"""
    
    def __init__(self, model_path: str = 'models/user_behavior_model.pkl'):
        self.model_path = model_path
        self.model = None
        self.scaler = StandardScaler()
        self.logger = logging.getLogger(__name__)
        self.feature_columns = [
            'security_market_cap', 'security_volatility', 'security_sector_score',
            'current_weight', 'target_weight', 'allocation_ratio',
            'portfolio_total_value', 'recommendation_amount',
            'days_since_last_trade', 'client_risk_profile',
            'market_condition_score', 'time_of_month'
        ]
        
    def load_model(self) -> bool:
        """Load pre-trained model if exists"""
        try:
            if os.path.exists(self.model_path):
                self.model = joblib.load(self.model_path)
                self.logger.info(f"Loaded ML model from {self.model_path}")
                return True
            else:
                self.logger.info("No pre-trained model found, will train new one")
                return False
        except Exception as e:
            self.logger.error(f"Error loading model: {e}")
            return False
    
    def save_model(self) -> bool:
        """Save trained model"""
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            joblib.dump(self.model, self.model_path)
            self.logger.info(f"Saved ML model to {self.model_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving model: {e}")
            return False
    
    def prepare_features(self, client_id: int, security_id: int, recommendation_data: Dict) -> np.ndarray:
        """Prepare features for ML model prediction"""
        try:
            # Get security and client data
            from models import Security, Client
            from sqlalchemy.orm import sessionmaker
            from extensions import db
            
            session = sessionmaker(bind=db.engine)()
            
            security = session.query(Security).get(security_id)
            client = session.query(Client).get(client_id)
            
            if not security or not client:
                return np.zeros(len(self.feature_columns))
            
            # Calculate features
            features = []
            
            # Security characteristics
            features.append(security.market_cap or 0)  # security_market_cap
            features.append(security.volatility or 0)  # security_volatility
            features.append(self._get_sector_score(security.sector))  # security_sector_score
            
            # Portfolio characteristics
            features.append(recommendation_data.get('current_weight', 0))  # current_weight
            features.append(recommendation_data.get('target_weight', 0))  # target_weight
            features.append(recommendation_data.get('allocation_ratio', 0))  # allocation_ratio
            
            # Portfolio context
            features.append(recommendation_data.get('portfolio_total_value', 0))  # portfolio_total_value
            features.append(abs(recommendation_data.get('amount', 0)))  # recommendation_amount
            
            # Time-based features
            features.append(self._get_days_since_last_trade(client_id, security_id))  # days_since_last_trade
            features.append(self._get_client_risk_profile(client))  # client_risk_profile
            
            # Market conditions
            features.append(self._get_market_condition_score())  # market_condition_score
            features.append(datetime.now().day)  # time_of_month
            
            session.close()
            
            return np.array(features).reshape(1, -1)
            
        except Exception as e:
            self.logger.error(f"Error preparing features: {e}")
            return np.zeros((1, len(self.feature_columns)))
    
    def predict_user_behavior(self, client_id: int, security_id: int, recommendation_data: Dict) -> Dict:
        """Predict user behavior for a recommendation"""
        try:
            if not self.model:
                # Return default prediction if no model
                return {
                    'confidence': 50.0,
                    'prediction': 'unknown',
                    'color': 'yellow',
                    'history': None
                }
            
            # Prepare features
            features = self.prepare_features(client_id, security_id, recommendation_data)
            
            # Scale features
            features_scaled = self.scaler.transform(features)
            
            # Make prediction
            prediction_proba = self.model.predict_proba(features_scaled)[0]
            confidence = max(prediction_proba) * 100
            
            # Get user history
            history = self._get_user_history(client_id, security_id, recommendation_data.get('action', ''))
            
            # Determine color based on confidence
            if confidence >= 70:
                color = 'green'
            elif confidence >= 50:
                color = 'yellow'
            else:
                color = 'red'
            
            return {
                'confidence': round(confidence, 1),
                'prediction': 'follow' if prediction_proba[1] > prediction_proba[0] else 'reject',
                'color': color,
                'history': history
            }
            
        except Exception as e:
            self.logger.error(f"Error predicting user behavior: {e}")
            return {
                'confidence': 50.0,
                'prediction': 'unknown',
                'color': 'yellow',
                'history': None
            }
    
    def train_model(self, training_data: pd.DataFrame) -> Dict:
        """Train the ML model on historical data"""
        try:
            if training_data.empty:
                self.logger.warning("No training data available")
                return {'success': False, 'message': 'No training data'}
            
            # Prepare features and target
            X = training_data[self.feature_columns]
            y = training_data['user_decision']
            
            # Split data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )
            
            # Scale features
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)
            
            # Train model
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                random_state=42,
                class_weight='balanced'
            )
            
            self.model.fit(X_train_scaled, y_train)
            
            # Evaluate
            y_pred = self.model.predict(X_test_scaled)
            accuracy = accuracy_score(y_test, y_pred)
            
            # Save model
            self.save_model()
            
            return {
                'success': True,
                'accuracy': round(accuracy, 3),
                'training_samples': len(training_data),
                'test_samples': len(X_test)
            }
            
        except Exception as e:
            self.logger.error(f"Error training model: {e}")
            return {'success': False, 'message': str(e)}
    
    def _get_sector_score(self, sector: str) -> float:
        """Get sector preference score (0-100)"""
        # This could be learned from user behavior or set manually
        sector_preferences = {
            'Technology': 80,
            'Healthcare': 75,
            'Financial': 70,
            'Consumer': 65,
            'Industrial': 60,
            'Energy': 55,
            'Utilities': 50
        }
        return sector_preferences.get(sector, 50)
    
    def _get_days_since_last_trade(self, client_id: int, security_id: int) -> int:
        """Get days since last trade for this security"""
        try:
            from models import Transaction
            from sqlalchemy.orm import sessionmaker
            from extensions import db
            
            session = sessionmaker(bind=db.engine)()
            
            last_trade = session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id
            ).order_by(Transaction.transaction_date.desc()).first()
            
            session.close()
            
            if last_trade:
                return (datetime.now() - last_trade.transaction_date).days
            else:
                return 365  # Default to 1 year if no previous trades
                
        except Exception as e:
            self.logger.error(f"Error getting days since last trade: {e}")
            return 365
    
    def _get_client_risk_profile(self, client) -> float:
        """Get client risk profile score (0-100)"""
        # This could be based on client's historical behavior or questionnaire
        # For now, return a default value
        return 60.0
    
    def _get_market_condition_score(self) -> float:
        """Get current market condition score (0-100)"""
        # This could be based on market indices, volatility, etc.
        # For now, return a default value
        return 50.0
    
    def _get_user_history(self, client_id: int, security_id: int, action: str) -> Optional[str]:
        """Get user's historical behavior for this security and action"""
        try:
            from sqlalchemy.orm import sessionmaker
            from extensions import db
            
            session = sessionmaker(bind=db.engine)()
            
            # Get historical feedback
            feedback_count = session.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN user_decision = 1 THEN 1 ELSE 0 END) as followed
                FROM user_recommendation_feedback 
                WHERE client_id = :client_id 
                AND security_id = :security_id 
                AND recommendation_type = :action
            """, {
                'client_id': client_id,
                'security_id': security_id,
                'action': action
            }).fetchone()
            
            session.close()
            
            if feedback_count and feedback_count.total > 0:
                followed = feedback_count.followed
                total = feedback_count.total
                avoided = total - followed
                
                if avoided > followed:
                    return f"{avoided}/{total} instances user avoided {action.lower()}ing this stock"
                elif followed > avoided:
                    return f"{followed}/{total} instances user followed {action.lower()} recommendation"
                else:
                    return f"Mixed behavior: {followed}/{total} followed"
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting user history: {e}")
            return None

# Global instance
ml_model = UserBehaviorMLModel()

