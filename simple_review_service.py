#!/usr/bin/env python3
"""
Simple Review Service - Basic portfolio review enhancement without ML
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from models import Review, Client, Holding, Security, Transaction
from extensions import db

logger = logging.getLogger(__name__)

class SimpleReviewService:
    """Service for basic portfolio review enhancement without ML"""
    
    def enhance_review_with_basic_insights(self, review_id: int) -> bool:
        """Enhance an existing review with basic portfolio insights"""
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
            
            # Prepare basic portfolio insights
            insights = self._generate_basic_insights(client, review.start_date, review.end_date)
            
            # Enhance review data
            review_data = review.review_data or {}
            
            # Add basic insights
            review_data['basic_insights'] = {
                'generated_at': datetime.utcnow().isoformat(),
                'insights': insights,
                'portfolio_summary': self._generate_portfolio_summary(client),
                'recommendations': self._generate_basic_recommendations(client, insights)
            }
            
            # Update review
            review.review_data = review_data
            db.session.commit()
            
            logger.info(f"Enhanced review {review_id} with basic insights")
            return True
            
        except Exception as e:
            logger.error(f"Error enhancing review with basic insights: {str(e)}")
            return False
    
    def _generate_basic_insights(self, client: Client, start_date, end_date) -> Dict:
        """Generate basic portfolio insights"""
        try:
            insights = {
                'diversification_score': 0,
                'concentration_risk': 'Low',
                'asset_allocation': {},
                'top_holdings': [],
                'performance_summary': {},
                'risk_indicators': []
            }
            
            # Get holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            if not holdings:
                insights['risk_indicators'].append('No holdings found - portfolio may be empty')
                return insights
            
            # Calculate total value
            total_value = 0
            asset_values = {}
            
            for holding in holdings:
                if holding.security.current_price:
                    value = float(holding.quantity * holding.security.current_price)
                    total_value += value
                    
                    # Group by asset class
                    asset_class = holding.security.asset_class.name if holding.security.asset_class else 'Unknown'
                    asset_values[asset_class] = asset_values.get(asset_class, 0) + value
            
            insights['total_value'] = total_value
            
            # Calculate asset allocation
            if total_value > 0:
                for asset_class, value in asset_values.items():
                    insights['asset_allocation'][asset_class] = round((value / total_value) * 100, 2)
            
            # Get top holdings
            holdings_with_values = []
            for holding in holdings:
                if holding.security.current_price:
                    value = float(holding.quantity * holding.security.current_price)
                    holdings_with_values.append({
                        'security': holding.security.name,
                        'symbol': holding.security.symbol,
                        'value': value,
                        'percentage': round((value / total_value) * 100, 2) if total_value > 0 else 0
                    })
            
            # Sort by value and get top 5
            holdings_with_values.sort(key=lambda x: x['value'], reverse=True)
            insights['top_holdings'] = holdings_with_values[:5]
            
            # Calculate diversification score
            num_securities = len(holdings)
            if num_securities >= 20:
                insights['diversification_score'] = 5
            elif num_securities >= 15:
                insights['diversification_score'] = 4
            elif num_securities >= 10:
                insights['diversification_score'] = 3
            elif num_securities >= 5:
                insights['diversification_score'] = 2
            else:
                insights['diversification_score'] = 1
            
            # Check concentration risk
            if insights['top_holdings']:
                top_holding_percentage = insights['top_holdings'][0]['percentage']
                if top_holding_percentage > 20:
                    insights['concentration_risk'] = 'High'
                    insights['risk_indicators'].append(f"Top holding represents {top_holding_percentage:.1f}% of portfolio")
                elif top_holding_percentage > 15:
                    insights['concentration_risk'] = 'Medium'
                else:
                    insights['concentration_risk'] = 'Low'
            
            # Check asset class concentration
            if len(asset_values) < 3:
                insights['risk_indicators'].append('Limited asset class diversification')
            
            # Check for over-concentration in any single asset class
            for asset_class, percentage in insights['asset_allocation'].items():
                if percentage > 60:
                    insights['risk_indicators'].append(f"Over-concentrated in {asset_class} ({percentage:.1f}%)")
            
            return insights
            
        except Exception as e:
            logger.error(f"Error generating basic insights: {str(e)}")
            return {}
    
    def _generate_portfolio_summary(self, client: Client) -> Dict:
        """Generate basic portfolio summary"""
        try:
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            summary = {
                'total_securities': len(holdings),
                'total_value': 0,
                'asset_classes': set(),
                'sectors': set()
            }
            
            for holding in holdings:
                if holding.security.current_price:
                    value = float(holding.quantity * holding.security.current_price)
                    summary['total_value'] += value
                    
                    if holding.security.asset_class:
                        summary['asset_classes'].add(holding.security.asset_class.name)
            
            summary['asset_classes'] = list(summary['asset_classes'])
            summary['num_asset_classes'] = len(summary['asset_classes'])
            
            return summary
            
        except Exception as e:
            logger.error(f"Error generating portfolio summary: {str(e)}")
            return {}
    
    def _generate_basic_recommendations(self, client: Client, insights: Dict) -> List[Dict]:
        """Generate basic recommendations based on insights"""
        recommendations = []
        
        try:
            # Diversification recommendations
            if insights.get('diversification_score', 0) < 3:
                recommendations.append({
                    'type': 'diversification',
                    'title': 'Improve Portfolio Diversification',
                    'description': 'Consider adding more securities to improve diversification',
                    'priority': 'medium'
                })
            
            # Concentration risk recommendations
            if insights.get('concentration_risk') == 'High':
                recommendations.append({
                    'type': 'concentration',
                    'title': 'Reduce Concentration Risk',
                    'description': 'Consider reducing position size in top holdings',
                    'priority': 'high'
                })
            
            # Asset class recommendations
            asset_allocation = insights.get('asset_allocation', {})
            if len(asset_allocation) < 3:
                recommendations.append({
                    'type': 'asset_class',
                    'title': 'Diversify Asset Classes',
                    'description': 'Consider adding exposure to different asset classes',
                    'priority': 'medium'
                })
            
            # Risk indicator recommendations
            risk_indicators = insights.get('risk_indicators', [])
            for indicator in risk_indicators:
                recommendations.append({
                    'type': 'risk',
                    'title': 'Address Risk Indicator',
                    'description': indicator,
                    'priority': 'high'
                })
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error generating basic recommendations: {str(e)}")
            return []







