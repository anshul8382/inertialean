#!/usr/bin/env python3
"""
ML-Based Recommendation Engine for Portfolio Analysis
Provides intelligent recommendations based on portfolio data, market conditions, and client preferences
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, classification_report
from sklearn.cluster import KMeans
import joblib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)

class PortfolioRecommendationEngine:
    """ML-based recommendation engine for portfolio analysis and recommendations"""
    
    def __init__(self):
        self.risk_model = None
        self.performance_model = None
        self.allocation_model = None
        self.recommendation_model = None
        self.scaler = StandardScaler()
        self.label_encoder = LabelEncoder()
        self.is_trained = False
        
    def extract_portfolio_features(self, portfolio_data: Dict) -> Dict:
        """Extract features from portfolio data for ML models"""
        try:
            features = {}
            
            # Basic portfolio metrics
            features['total_value'] = portfolio_data.get('total_value', 0)
            features['total_return'] = portfolio_data.get('total_return_percent', 0)
            features['volatility'] = portfolio_data.get('volatility', 0)
            features['sharpe_ratio'] = portfolio_data.get('sharpe_ratio', 0)
            features['max_drawdown'] = portfolio_data.get('max_drawdown', 0)
            
            # Allocation features
            allocation = portfolio_data.get('allocation', {})
            features['equity_allocation'] = allocation.get('equity', 0)
            features['debt_allocation'] = allocation.get('debt', 0)
            features['cash_allocation'] = allocation.get('cash', 0)
            features['international_allocation'] = allocation.get('international', 0)
            
            # Diversification metrics
            features['num_securities'] = portfolio_data.get('num_securities', 0)
            features['concentration_risk'] = portfolio_data.get('concentration_risk', 0)
            features['sector_diversification'] = portfolio_data.get('sector_diversification', 0)
            
            # Market timing features
            features['market_timing_score'] = portfolio_data.get('market_timing_score', 0)
            features['trend_following'] = portfolio_data.get('trend_following', 0)
            
            # Client-specific features
            features['client_age'] = portfolio_data.get('client_age', 45)
            features['risk_tolerance'] = portfolio_data.get('risk_tolerance', 3)
            features['investment_horizon'] = portfolio_data.get('investment_horizon', 10)
            features['income_level'] = portfolio_data.get('income_level', 2)
            
            return features
            
        except Exception as e:
            logger.error(f"Error extracting portfolio features: {str(e)}")
            return {}
    
    def calculate_risk_metrics(self, portfolio_data: Dict) -> Dict:
        """Calculate comprehensive risk metrics"""
        try:
            risk_metrics = {}
            
            # Value at Risk (VaR)
            returns = portfolio_data.get('returns', [])
            if returns:
                returns_array = np.array(returns)
                risk_metrics['var_95'] = np.percentile(returns_array, 5)
                risk_metrics['var_99'] = np.percentile(returns_array, 1)
            
            # Volatility
            if returns:
                risk_metrics['volatility'] = np.std(returns_array) * np.sqrt(252)  # Annualized
            
            # Beta calculation (if benchmark data available)
            benchmark_returns = portfolio_data.get('benchmark_returns', [])
            if returns and benchmark_returns:
                portfolio_returns = np.array(returns)
                benchmark_returns = np.array(benchmark_returns)
                covariance = np.cov(portfolio_returns, benchmark_returns)[0, 1]
                benchmark_variance = np.var(benchmark_returns)
                risk_metrics['beta'] = covariance / benchmark_variance if benchmark_variance > 0 else 1.0
            
            # Maximum drawdown
            cumulative_returns = portfolio_data.get('cumulative_returns', [])
            if cumulative_returns:
                peak = np.maximum.accumulate(cumulative_returns)
                drawdown = (cumulative_returns - peak) / peak
                risk_metrics['max_drawdown'] = np.min(drawdown)
            
            # Sharpe ratio
            risk_free_rate = 0.05  # 5% risk-free rate
            if returns:
                excess_returns = np.array(returns) - risk_free_rate/252
                risk_metrics['sharpe_ratio'] = np.mean(excess_returns) / np.std(excess_returns) if np.std(excess_returns) > 0 else 0
            
            return risk_metrics
            
        except Exception as e:
            logger.error(f"Error calculating risk metrics: {str(e)}")
            return {}
    
    def generate_portfolio_recommendations(self, portfolio_data: Dict, market_data: Dict, user_preferences: Dict = None, user_feedback: Dict = None) -> List[Dict]:
        """Generate ML-based portfolio recommendations with user feedback integration"""
        try:
            recommendations = []
            
            # Extract features
            features = self.extract_portfolio_features(portfolio_data)
            risk_metrics = self.calculate_risk_metrics(portfolio_data)
            
            # 1. Risk Assessment Recommendations
            risk_recommendations = self._generate_risk_recommendations(features, risk_metrics, user_preferences, user_feedback)
            recommendations.extend(risk_recommendations)
            
            # 2. Allocation Optimization Recommendations
            allocation_recommendations = self._generate_allocation_recommendations(features, market_data, user_preferences, user_feedback)
            recommendations.extend(allocation_recommendations)
            
            # 3. Security Selection Recommendations
            security_recommendations = self._generate_security_recommendations(portfolio_data, market_data, user_preferences, user_feedback)
            recommendations.extend(security_recommendations)
            
            # 4. Market Timing Recommendations
            timing_recommendations = self._generate_timing_recommendations(features, market_data, user_preferences, user_feedback)
            recommendations.extend(timing_recommendations)
            
            # 5. Rebalancing Recommendations
            rebalancing_recommendations = self._generate_rebalancing_recommendations(features, portfolio_data, user_preferences, user_feedback)
            recommendations.extend(rebalancing_recommendations)
            
            # Personalize recommendations based on user feedback and preferences
            recommendations = self._personalize_recommendations(recommendations, user_preferences, user_feedback)
            
            # Sort by priority and confidence
            recommendations.sort(key=lambda x: (x.get('priority', 'medium'), x.get('confidence', 0)), reverse=True)
            
            return recommendations[:10]  # Return top 10 recommendations
            
        except Exception as e:
            logger.error(f"Error generating recommendations: {str(e)}")
            return []
    
    def _generate_risk_recommendations(self, features: Dict, risk_metrics: Dict, user_preferences: Dict = None, user_feedback: Dict = None) -> List[Dict]:
        """Generate risk-based recommendations with user feedback integration"""
        recommendations = []
        
        # Adjust thresholds based on user preferences
        volatility_threshold = 0.25  # Default 25% annualized volatility
        if user_preferences and user_preferences.get('risk_tolerance') == 'conservative':
            volatility_threshold = 0.15
        elif user_preferences and user_preferences.get('risk_tolerance') == 'aggressive':
            volatility_threshold = 0.35
        
        # High volatility warning
        if risk_metrics.get('volatility', 0) > volatility_threshold:
            # Check if user has historically ignored similar recommendations
            if user_feedback and self._has_user_ignored_category(user_feedback, 'risk_management'):
                confidence = 0.6  # Lower confidence if user ignores risk recommendations
            else:
                confidence = 0.85
            
            recommendations.append({
                'type': 'risk_management',
                'title': 'High Portfolio Volatility',
                'description': f"Portfolio volatility is {risk_metrics['volatility']:.1%}, which is above recommended levels.",
                'action': 'Consider reducing exposure to high-volatility assets',
                'priority': 'high',
                'confidence': confidence,
                'impact': 'risk_reduction'
            })
        
        # Low diversification warning
        min_securities = 15  # Default minimum
        if user_preferences and user_preferences.get('risk_tolerance') == 'conservative':
            min_securities = 20
        elif user_preferences and user_preferences.get('risk_tolerance') == 'aggressive':
            min_securities = 10
        
        if features.get('num_securities', 0) < min_securities:
            recommendations.append({
                'type': 'diversification',
                'title': 'Low Portfolio Diversification',
                'description': f"Portfolio contains only {features['num_securities']} securities.",
                'action': 'Consider adding more securities to improve diversification',
                'priority': 'high',
                'confidence': 0.90,
                'impact': 'risk_reduction'
            })
        
        # Concentration risk
        if features.get('concentration_risk', 0) > 0.20:  # 20% concentration
            recommendations.append({
                'type': 'concentration',
                'title': 'High Concentration Risk',
                'description': 'Portfolio is heavily concentrated in a few securities.',
                'action': 'Consider reducing position sizes in top holdings',
                'priority': 'high',
                'confidence': 0.88,
                'impact': 'risk_reduction'
            })
        
        return recommendations
    
    def _generate_allocation_recommendations(self, features: Dict, market_data: Dict, user_preferences: Dict = None, user_feedback: Dict = None) -> List[Dict]:
        """Generate asset allocation recommendations"""
        recommendations = []
        
        # Age-based allocation
        client_age = features.get('client_age', 45)
        risk_tolerance = features.get('risk_tolerance', 3)
        
        # Target equity allocation based on age and risk tolerance
        target_equity = max(100 - client_age - (5 - risk_tolerance) * 5, 20)
        current_equity = features.get('equity_allocation', 0)
        
        if abs(current_equity - target_equity) > 10:
            action = 'increase' if current_equity < target_equity else 'decrease'
            recommendations.append({
                'type': 'allocation',
                'title': f'Adjust Equity Allocation',
                'description': f"Current equity allocation is {current_equity:.1f}%, target is {target_equity:.1f}%.",
                'action': f'Consider {action}ing equity exposure by {abs(target_equity - current_equity):.1f}%',
                'priority': 'medium',
                'confidence': 0.75,
                'impact': 'allocation_optimization'
            })
        
        # International diversification
        if features.get('international_allocation', 0) < 20:
            recommendations.append({
                'type': 'international',
                'title': 'Increase International Exposure',
                'description': 'Portfolio has low international diversification.',
                'action': 'Consider adding international equity funds',
                'priority': 'medium',
                'confidence': 0.70,
                'impact': 'diversification'
            })
        
        return recommendations
    
    def _generate_security_recommendations(self, portfolio_data: Dict, market_data: Dict) -> List[Dict]:
        """Generate security-specific recommendations"""
        recommendations = []
        
        # Analyze underperforming securities
        securities = portfolio_data.get('securities', [])
        for security in securities:
            if security.get('unrealized_pnl_percent', 0) < -15:  # 15% loss
                recommendations.append({
                    'type': 'security',
                    'title': f"Review {security.get('symbol', 'Security')}",
                    'description': f"{security.get('symbol', 'Security')} is down {abs(security.get('unrealized_pnl_percent', 0)):.1f}%.",
                    'action': 'Consider reviewing position or averaging down',
                    'priority': 'medium',
                    'confidence': 0.65,
                    'impact': 'performance_improvement',
                    'security_symbol': security.get('symbol')
                })
        
        # Sector rotation opportunities
        sector_performance = market_data.get('sector_performance', {})
        portfolio_sectors = portfolio_data.get('sector_allocation', {})
        
        for sector, performance in sector_performance.items():
            if performance > 0.15:  # 15% sector outperformance
                if portfolio_sectors.get(sector, 0) < 0.10:  # Less than 10% allocation
                    recommendations.append({
                        'type': 'sector',
                        'title': f'Consider {sector} Sector',
                        'description': f'{sector} sector is outperforming with {performance:.1f}% return.',
                        'action': f'Consider adding {sector} sector exposure',
                        'priority': 'low',
                        'confidence': 0.60,
                        'impact': 'performance_improvement'
                    })
        
        return recommendations
    
    def _generate_timing_recommendations(self, features: Dict, market_data: Dict) -> List[Dict]:
        """Generate market timing recommendations"""
        recommendations = []
        
        # Market sentiment analysis
        market_sentiment = market_data.get('sentiment', 'neutral')
        volatility = market_data.get('volatility', 0)
        
        if market_sentiment == 'bearish' and volatility > 0.20:
            recommendations.append({
                'type': 'timing',
                'title': 'Market Volatility Alert',
                'description': 'Market is showing bearish sentiment with high volatility.',
                'action': 'Consider defensive positioning or hedging strategies',
                'priority': 'medium',
                'confidence': 0.70,
                'impact': 'risk_management'
            })
        
        # Trend following signals
        if features.get('trend_following', 0) < 0.5:
            recommendations.append({
                'type': 'timing',
                'title': 'Trend Following Opportunity',
                'description': 'Market is showing clear trends that could be followed.',
                'action': 'Consider trend-following strategies',
                'priority': 'low',
                'confidence': 0.55,
                'impact': 'performance_improvement'
            })
        
        return recommendations
    
    def _generate_rebalancing_recommendations(self, features: Dict, portfolio_data: Dict) -> List[Dict]:
        """Generate rebalancing recommendations"""
        recommendations = []
        
        # Check for significant drift
        drift_analysis = portfolio_data.get('drift_analysis', {})
        for asset_class, drift in drift_analysis.items():
            if abs(drift) > 5:  # 5% drift threshold
                action = 'reduce' if drift > 0 else 'increase'
                recommendations.append({
                    'type': 'rebalancing',
                    'title': f'Rebalance {asset_class}',
                    'description': f'{asset_class} is {abs(drift):.1f}% {"overweight" if drift > 0 else "underweight"}.',
                    'action': f'Consider {action}ing {asset_class} exposure',
                    'priority': 'medium',
                    'confidence': 0.80,
                    'impact': 'rebalancing'
                })
        
        return recommendations
    
    def train_models(self, training_data: List[Dict]):
        """Train ML models with historical data"""
        try:
            logger.info("Starting ML model training...")
            
            # Prepare training data
            X = []
            y_risk = []
            y_performance = []
            y_allocation = []
            
            for data_point in training_data:
                features = self.extract_portfolio_features(data_point)
                if features:
                    X.append(list(features.values()))
                    y_risk.append(data_point.get('risk_score', 0))
                    y_performance.append(data_point.get('performance_score', 0))
                    y_allocation.append(data_point.get('allocation_score', 0))
            
            if len(X) < 10:
                logger.warning("Insufficient training data")
                return False
            
            X = np.array(X)
            X_scaled = self.scaler.fit_transform(X)
            
            # Train risk model
            self.risk_model = RandomForestRegressor(n_estimators=100, random_state=42)
            self.risk_model.fit(X_scaled, y_risk)
            
            # Train performance model
            self.performance_model = RandomForestRegressor(n_estimators=100, random_state=42)
            self.performance_model.fit(X_scaled, y_performance)
            
            # Train allocation model
            self.allocation_model = RandomForestRegressor(n_estimators=100, random_state=42)
            self.allocation_model.fit(X_scaled, y_allocation)
            
            self.is_trained = True
            logger.info("ML models trained successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error training ML models: {str(e)}")
            return False
    
    def predict_portfolio_metrics(self, portfolio_data: Dict) -> Dict:
        """Predict portfolio metrics using trained models"""
        try:
            if not self.is_trained:
                return {}
            
            features = self.extract_portfolio_features(portfolio_data)
            if not features:
                return {}
            
            X = np.array([list(features.values())])
            X_scaled = self.scaler.transform(X)
            
            predictions = {
                'predicted_risk_score': float(self.risk_model.predict(X_scaled)[0]),
                'predicted_performance_score': float(self.performance_model.predict(X_scaled)[0]),
                'predicted_allocation_score': float(self.allocation_model.predict(X_scaled)[0])
            }
            
            return predictions
            
        except Exception as e:
            logger.error(f"Error predicting portfolio metrics: {str(e)}")
            return {}
    
    def save_models(self, filepath: str):
        """Save trained models to disk"""
        try:
            if self.is_trained:
                model_data = {
                    'risk_model': self.risk_model,
                    'performance_model': self.performance_model,
                    'allocation_model': self.allocation_model,
                    'scaler': self.scaler,
                    'is_trained': self.is_trained
                }
                joblib.dump(model_data, filepath)
                logger.info(f"Models saved to {filepath}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error saving models: {str(e)}")
            return False
    
    def load_models(self, filepath: str):
        """Load trained models from disk"""
        try:
            model_data = joblib.load(filepath)
            self.risk_model = model_data['risk_model']
            self.performance_model = model_data['performance_model']
            self.allocation_model = model_data['allocation_model']
            self.scaler = model_data['scaler']
            self.is_trained = model_data['is_trained']
            logger.info(f"Models loaded from {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error loading models: {str(e)}")
            return False
    
    def _personalize_recommendations(self, recommendations: List[Dict], user_preferences: Dict = None, user_feedback: Dict = None) -> List[Dict]:
        """Personalize recommendations based on user preferences and feedback"""
        try:
            if not user_preferences and not user_feedback:
                return recommendations
            
            personalized_recs = []
            
            for rec in recommendations:
                # Adjust priority based on user preferences
                adjusted_rec = rec.copy()
                
                # Check if user prefers this category
                if user_preferences and user_preferences.get('preferred_categories'):
                    preferred_categories = user_preferences['preferred_categories']
                    if rec.get('type') in preferred_categories:
                        adjusted_rec['priority'] = self._increase_priority(rec.get('priority', 'medium'))
                        adjusted_rec['confidence'] = min(1.0, rec.get('confidence', 0) + 0.1)
                
                # Check if user has historically ignored this type
                if user_feedback and self._has_user_ignored_category(user_feedback, rec.get('type')):
                    adjusted_rec['priority'] = self._decrease_priority(rec.get('priority', 'medium'))
                    adjusted_rec['confidence'] = max(0.1, rec.get('confidence', 0) - 0.2)
                
                # Check if user has successfully implemented similar recommendations
                if user_feedback and self._has_user_implemented_category(user_feedback, rec.get('type')):
                    adjusted_rec['priority'] = self._increase_priority(rec.get('priority', 'medium'))
                    adjusted_rec['confidence'] = min(1.0, rec.get('confidence', 0) + 0.15)
                
                # Adjust detail level based on user preferences
                if user_preferences and user_preferences.get('detail_level'):
                    detail_level = user_preferences['detail_level']
                    if detail_level == 'brief':
                        adjusted_rec['description'] = self._make_description_brief(rec['description'])
                    elif detail_level == 'technical':
                        adjusted_rec['description'] = self._make_description_technical(rec['description'])
                
                personalized_recs.append(adjusted_rec)
            
            return personalized_recs
            
        except Exception as e:
            logger.error(f"Error personalizing recommendations: {str(e)}")
            return recommendations
    
    def _has_user_ignored_category(self, user_feedback: Dict, category: str) -> bool:
        """Check if user has historically ignored recommendations of this category"""
        try:
            ignored_recs = user_feedback.get('ignored_recommendations', [])
            return any(rec.get('type') == category for rec in ignored_recs)
        except Exception as e:
            logger.error(f"Error checking ignored category: {str(e)}")
            return False
    
    def _has_user_implemented_category(self, user_feedback: Dict, category: str) -> bool:
        """Check if user has successfully implemented recommendations of this category"""
        try:
            implemented_recs = user_feedback.get('high_implementation_recommendations', [])
            return any(rec.get('type') == category for rec in implemented_recs)
        except Exception as e:
            logger.error(f"Error checking implemented category: {str(e)}")
            return False
    
    def _increase_priority(self, current_priority: str) -> str:
        """Increase recommendation priority"""
        priority_order = ['low', 'medium', 'high']
        try:
            current_index = priority_order.index(current_priority)
            return priority_order[min(current_index + 1, len(priority_order) - 1)]
        except ValueError:
            return current_priority
    
    def _decrease_priority(self, current_priority: str) -> str:
        """Decrease recommendation priority"""
        priority_order = ['low', 'medium', 'high']
        try:
            current_index = priority_order.index(current_priority)
            return priority_order[max(current_index - 1, 0)]
        except ValueError:
            return current_priority
    
    def _make_description_brief(self, description: str) -> str:
        """Make description more concise"""
        # Simple implementation - could be enhanced with NLP
        if len(description) > 100:
            return description[:97] + "..."
        return description
    
    def _make_description_technical(self, description: str) -> str:
        """Make description more technical"""
        # Simple implementation - could be enhanced with technical terminology
        technical_terms = {
            'volatility': 'standard deviation of returns',
            'diversification': 'portfolio concentration risk',
            'allocation': 'asset class distribution',
            'rebalancing': 'portfolio weight adjustment'
        }
        
        enhanced_description = description
        for term, technical_term in technical_terms.items():
            if term in description.lower():
                enhanced_description = enhanced_description.replace(term, technical_term)
        
        return enhanced_description

