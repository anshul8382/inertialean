#!/usr/bin/env python3
"""
ML Review Service - Integrates ML recommendation engine with review system
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np

from ml_recommendation_engine import PortfolioRecommendationEngine
from ml_feedback_service import MLFeedbackService
from models import Review, Client, Holding, Security, Transaction
from extensions import db

logger = logging.getLogger(__name__)

class MLReviewService:
    """Service for integrating ML recommendations with review system"""
    
    def __init__(self):
        self.ml_engine = PortfolioRecommendationEngine()
        self.feedback_service = MLFeedbackService()
        self.model_path = 'ml_models/portfolio_recommendation_models.pkl'
        
    def enhance_review_with_ml_recommendations(self, review_id: int) -> bool:
        """Enhance an existing review with ML-based recommendations"""
        try:
            # Get the review
            review = Review.query.get(review_id)
            if not review:
                logger.error(f"Review {review_id} not found")
                return False
            
            # Get client data
            client = Client.query.get(review.client_id)
            if not client:
                logger.error(f"Client {review.client_id} not found")
                return False
            
            # Prepare portfolio data
            portfolio_data = self._prepare_portfolio_data(client, review.start_date, review.end_date)
            
            # Prepare market data
            market_data = self._prepare_market_data(review.start_date, review.end_date)
            
            # Get user preferences and feedback
            user_preferences = self.feedback_service.get_user_preferences(review.user_id)
            user_feedback = self.feedback_service.get_learning_insights()
            
            # Generate ML recommendations with personalization
            ml_recommendations = self.ml_engine.generate_portfolio_recommendations(
                portfolio_data, 
                market_data,
                user_preferences.__dict__ if user_preferences else None,
                user_feedback
            )
            
            # Get predictions
            predictions = self.ml_engine.predict_portfolio_metrics(portfolio_data)
            
            # Enhance review data
            review_data = review.review_data or {}
            
            # Add ML recommendations
            review_data['ml_recommendations'] = {
                'generated_at': datetime.utcnow().isoformat(),
                'recommendations': ml_recommendations,
                'predictions': predictions,
                'risk_metrics': self.ml_engine.calculate_risk_metrics(portfolio_data),
                'portfolio_features': self.ml_engine.extract_portfolio_features(portfolio_data)
            }
            
            # Update review
            review.review_data = review_data
            db.session.commit()
            
            logger.info(f"Enhanced review {review_id} with ML recommendations")
            return True
            
        except Exception as e:
            logger.error(f"Error enhancing review with ML recommendations: {str(e)}")
            return False
    
    def _prepare_portfolio_data(self, client: Client, start_date, end_date) -> Dict:
        """Prepare portfolio data for ML analysis"""
        try:
            portfolio_data = {
                'client_id': client.id,
                'client_name': client.name,
                'start_date': start_date,
                'end_date': end_date,
                'total_value': 0,
                'securities': [],
                'returns': [],
                'cumulative_returns': [],
                'allocation': {},
                'sector_allocation': {},
                'drift_analysis': {}
            }
            
            # Get holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            # Calculate total value and allocation
            total_value = 0
            equity_value = 0
            debt_value = 0
            cash_value = 0
            international_value = 0
            
            for holding in holdings:
                if holding.security.current_price:
                    security_value = float(holding.quantity * holding.security.current_price)
                    total_value += security_value
                    
                    # Categorize by asset class
                    asset_class = holding.security.asset_class.name.lower() if holding.security.asset_class else 'unknown'
                    
                    if 'equity' in asset_class or 'stock' in asset_class:
                        equity_value += security_value
                    elif 'debt' in asset_class or 'bond' in asset_class:
                        debt_value += security_value
                    elif 'cash' in asset_class or 'money' in asset_class:
                        cash_value += security_value
                    
                    # Check for international exposure
                    if holding.security.symbol.endswith('.NS') or 'international' in asset_class:
                        international_value += security_value
                    
                    # Add security data
                    portfolio_data['securities'].append({
                        'symbol': holding.security.symbol,
                        'name': holding.security.name,
                        'quantity': float(holding.quantity),
                        'current_price': float(holding.security.current_price),
                        'average_buy_price': float(holding.average_buy_price),
                        'current_value': security_value,
                        'unrealized_pnl_percent': float((holding.security.current_price / holding.average_buy_price - 1) * 100),
                        'asset_class': asset_class
                    })
            
            portfolio_data['total_value'] = total_value
            
            # Calculate allocation percentages
            if total_value > 0:
                portfolio_data['allocation'] = {
                    'equity': (equity_value / total_value) * 100,
                    'debt': (debt_value / total_value) * 100,
                    'cash': (cash_value / total_value) * 100,
                    'international': (international_value / total_value) * 100
                }
            
            # Calculate diversification metrics
            portfolio_data['num_securities'] = len(holdings)
            
            # Calculate concentration risk (top 5 holdings)
            if portfolio_data['securities']:
                sorted_securities = sorted(portfolio_data['securities'], key=lambda x: x['current_value'], reverse=True)
                top_5_value = sum(s['current_value'] for s in sorted_securities[:5])
                portfolio_data['concentration_risk'] = top_5_value / total_value if total_value > 0 else 0
            
            # Calculate sector diversification
            sector_values = {}
            for security in portfolio_data['securities']:
                sector = security.get('sector', 'Unknown')
                sector_values[sector] = sector_values.get(sector, 0) + security['current_value']
            
            portfolio_data['sector_allocation'] = {
                sector: (value / total_value) * 100 
                for sector, value in sector_values.items()
            } if total_value > 0 else {}
            
            # Calculate sector diversification score
            if portfolio_data['sector_allocation']:
                sector_weights = list(portfolio_data['sector_allocation'].values())
                portfolio_data['sector_diversification'] = 1 - sum(w**2 for w in sector_weights)  # Herfindahl index
            
            # Calculate returns (simplified - would need historical data)
            portfolio_data['returns'] = self._calculate_historical_returns(client, start_date, end_date)
            
            # Add client-specific features
            portfolio_data['client_age'] = getattr(client, 'age', 45)
            portfolio_data['risk_tolerance'] = getattr(client, 'risk_tolerance', 3)
            portfolio_data['investment_horizon'] = getattr(client, 'investment_horizon', 10)
            portfolio_data['income_level'] = getattr(client, 'income_level', 2)
            
            return portfolio_data
            
        except Exception as e:
            logger.error(f"Error preparing portfolio data: {str(e)}")
            return {}
    
    def _prepare_market_data(self, start_date, end_date) -> Dict:
        """Prepare market data for ML analysis"""
        try:
            # This would typically fetch from external APIs
            # For now, using sample data
            market_data = {
                'sentiment': 'neutral',  # bullish, bearish, neutral
                'volatility': 0.15,  # Market volatility
                'sector_performance': {
                    'Technology': 0.12,
                    'Healthcare': 0.08,
                    'Financial': 0.05,
                    'Consumer': 0.03,
                    'Energy': -0.02
                },
                'market_trend': 'sideways',  # uptrend, downtrend, sideways
                'risk_premium': 0.06,  # Market risk premium
                'interest_rate': 0.05  # Risk-free rate
            }
            
            return market_data
            
        except Exception as e:
            logger.error(f"Error preparing market data: {str(e)}")
            return {}
    
    def _calculate_historical_returns(self, client: Client, start_date, end_date) -> List[float]:
        """Calculate historical portfolio returns"""
        try:
            # This is a simplified calculation
            # In a real implementation, you would calculate daily returns based on historical data
            
            # For now, generate sample returns
            days = (end_date - start_date).days
            if days <= 0:
                return []
            
            # Generate random returns with some correlation
            np.random.seed(client.id)  # For reproducible results
            returns = np.random.normal(0.0005, 0.02, days)  # Daily returns with 0.05% mean, 2% std
            
            return returns.tolist()
            
        except Exception as e:
            logger.error(f"Error calculating historical returns: {str(e)}")
            return []
    
    def train_models_with_historical_data(self) -> bool:
        """Train ML models with historical portfolio data"""
        try:
            logger.info("Starting ML model training with historical data...")
            
            # Collect historical data from reviews
            training_data = []
            
            # Get completed reviews from the last 6 months
            six_months_ago = datetime.utcnow() - timedelta(days=180)
            reviews = Review.query.filter(
                Review.status == 'completed',
                Review.completed_at >= six_months_ago
            ).all()
            
            for review in reviews:
                client = Client.query.get(review.client_id)
                if client and review.review_data:
                    # Prepare portfolio data
                    portfolio_data = self._prepare_portfolio_data(client, review.start_date, review.end_date)
                    
                    # Add performance scores based on review data
                    review_data = review.review_data
                    performance_data = review_data.get('sections', {}).get('performance', {})
                    
                    # Calculate scores
                    risk_score = self._calculate_risk_score(performance_data)
                    performance_score = self._calculate_performance_score(performance_data)
                    allocation_score = self._calculate_allocation_score(review_data.get('sections', {}).get('allocation', {}))
                    
                    # Add to training data
                    training_point = portfolio_data.copy()
                    training_point['risk_score'] = risk_score
                    training_point['performance_score'] = performance_score
                    training_point['allocation_score'] = allocation_score
                    
                    training_data.append(training_point)
            
            if len(training_data) < 10:
                logger.warning(f"Insufficient training data: {len(training_data)} samples")
                return False
            
            # Train models
            success = self.ml_engine.train_models(training_data)
            
            if success:
                # Save models
                import os
                os.makedirs('ml_models', exist_ok=True)
                self.ml_engine.save_models(self.model_path)
                logger.info(f"ML models trained and saved with {len(training_data)} samples")
            
            return success
            
        except Exception as e:
            logger.error(f"Error training ML models: {str(e)}")
            return False
    
    def _calculate_risk_score(self, performance_data: Dict) -> float:
        """Calculate risk score from performance data"""
        try:
            # Lower score = higher risk
            score = 5.0  # Base score
            
            # Adjust based on volatility
            if 'volatility' in performance_data:
                volatility = performance_data['volatility']
                if volatility > 0.25:
                    score -= 2.0
                elif volatility > 0.15:
                    score -= 1.0
            
            # Adjust based on drawdown
            if 'max_drawdown' in performance_data:
                drawdown = abs(performance_data['max_drawdown'])
                if drawdown > 0.20:
                    score -= 2.0
                elif drawdown > 0.10:
                    score -= 1.0
            
            return max(1.0, min(10.0, score))
            
        except Exception as e:
            logger.error(f"Error calculating risk score: {str(e)}")
            return 5.0
    
    def _calculate_performance_score(self, performance_data: Dict) -> float:
        """Calculate performance score from performance data"""
        try:
            score = 5.0  # Base score
            
            # Adjust based on total return
            if 'total_return_percent' in performance_data:
                total_return = performance_data['total_return_percent']
                if total_return > 0.15:
                    score += 2.0
                elif total_return > 0.10:
                    score += 1.0
                elif total_return < -0.10:
                    score -= 1.0
                elif total_return < -0.20:
                    score -= 2.0
            
            # Adjust based on excess return
            if 'excess_return' in performance_data:
                excess_return = performance_data['excess_return']
                if excess_return > 0.05:
                    score += 1.0
                elif excess_return < -0.05:
                    score -= 1.0
            
            return max(1.0, min(10.0, score))
            
        except Exception as e:
            logger.error(f"Error calculating performance score: {str(e)}")
            return 5.0
    
    def _calculate_allocation_score(self, allocation_data: Dict) -> float:
        """Calculate allocation score from allocation data"""
        try:
            score = 5.0  # Base score
            
            # Adjust based on diversification
            if 'sector_diversification' in allocation_data:
                diversification = allocation_data['sector_diversification']
                if diversification > 0.8:
                    score += 1.0
                elif diversification < 0.5:
                    score -= 1.0
            
            # Adjust based on drift
            drift_analysis = allocation_data.get('drift_analysis', {})
            total_drift = sum(abs(drift) for drift in drift_analysis.values())
            if total_drift > 0.20:
                score -= 1.0
            elif total_drift < 0.05:
                score += 1.0
            
            return max(1.0, min(10.0, score))
            
        except Exception as e:
            logger.error(f"Error calculating allocation score: {str(e)}")
            return 5.0
    
    def load_trained_models(self) -> bool:
        """Load pre-trained ML models"""
        try:
            return self.ml_engine.load_models(self.model_path)
        except Exception as e:
            logger.error(f"Error loading trained models: {str(e)}")
            return False

