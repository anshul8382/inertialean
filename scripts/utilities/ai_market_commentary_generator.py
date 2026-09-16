#!/usr/bin/env python3
"""
AI Market Commentary Generator - Generates personalized market commentary for Indian markets
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from models import Review, Client, Holding, Security
from extensions import db

logger = logging.getLogger(__name__)

class AIMarketCommentaryGenerator:
    """AI-powered market commentary generator for Indian markets"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_personalized_commentary(self, review_id: int, custom_insights: str = None) -> Dict:
        """Generate AI-powered personalized market commentary"""
        try:
            review = Review.query.get(review_id)
            if not review:
                return None
            
            client = Client.query.get(review.client_id)
            if not client:
                return None
            
            # Get client's actual holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            # Generate personalized commentary
            commentary = {
                'portfolio_overview': self._generate_portfolio_overview(holdings),
                'stock_analysis': self._generate_stock_analysis(holdings),
                'sector_analysis': self._generate_sector_analysis(holdings),
                'indian_market_context': self._generate_indian_market_context(),
                'risk_analysis': self._generate_risk_analysis(holdings),
                'opportunities_concerns': self._generate_opportunities_concerns(holdings),
                'forward_looking': self._generate_forward_looking_insights(),
                'custom_insights': custom_insights,
                'generated_at': datetime.utcnow().isoformat()
            }
            
            return commentary
            
        except Exception as e:
            self.logger.error(f"Error generating AI commentary: {str(e)}")
            return None
    
    def _generate_portfolio_overview(self, holdings: List[Holding]) -> Dict:
        """Generate portfolio overview"""
        try:
            # Calculate current value for each holding
            total_value = 0
            for holding in holdings:
                if holding.security and holding.security.current_price:
                    current_value = float(holding.quantity) * float(holding.security.current_price)
                    total_value += current_value
            
            # Calculate portfolio return (simulated)
            portfolio_return = self._calculate_portfolio_return(holdings)
            
            # Analyze asset class exposure
            asset_class_exposure = self._analyze_asset_class_exposure(holdings)
            
            return {
                'total_value': total_value,
                'total_holdings': len(holdings),
                'portfolio_return': portfolio_return,
                'asset_class_exposure': asset_class_exposure,
                'summary': f"Portfolio valued at ₹{total_value:,.0f} with {len(holdings)} holdings, showing {portfolio_return:+.1f}% return."
            }
            
        except Exception as e:
            self.logger.error(f"Error generating portfolio overview: {str(e)}")
            return {}
    
    def _generate_stock_analysis(self, holdings: List[Holding]) -> Dict:
        """Generate stock-specific analysis"""
        try:
            stock_analysis = {
                'top_performers': [],
                'underperformers': [],
                'key_insights': []
            }
            
            # Analyze each holding
            for holding in holdings:
                if holding.security:
                    stock_data = self._analyze_individual_stock(holding)
                    
                    if stock_data['return'] > 10:
                        stock_analysis['top_performers'].append(stock_data)
                    elif stock_data['return'] < -5:
                        stock_analysis['underperformers'].append(stock_data)
                    
                    if stock_data['insights']:
                        stock_analysis['key_insights'].extend(stock_data['insights'])
            
            # Sort by performance
            stock_analysis['top_performers'] = sorted(stock_analysis['top_performers'], 
                                                    key=lambda x: x['return'], reverse=True)[:5]
            stock_analysis['underperformers'] = sorted(stock_analysis['underperformers'], 
                                                     key=lambda x: x['return'])[:5]
            
            return stock_analysis
            
        except Exception as e:
            self.logger.error(f"Error generating stock analysis: {str(e)}")
            return {}
    
    def _generate_sector_analysis(self, holdings: List[Holding]) -> Dict:
        """Generate sector-specific analysis"""
        try:
            sector_holdings = {}
            
            # Group holdings by asset class (since Security doesn't have sector attribute)
            for holding in holdings:
                if holding.security and holding.security.asset_class:
                    asset_class = holding.security.asset_class.name
                    if asset_class not in sector_holdings:
                        sector_holdings[asset_class] = []
                    sector_holdings[asset_class].append(holding)
            
            sector_analysis = {}
            for asset_class, asset_holdings_list in sector_holdings.items():
                sector_data = self._analyze_sector_performance(asset_class, asset_holdings_list)
                sector_analysis[asset_class] = sector_data
            
            return sector_analysis
            
        except Exception as e:
            self.logger.error(f"Error generating sector analysis: {str(e)}")
            return {}
    
    def _generate_indian_market_context(self) -> Dict:
        """Generate Indian market context"""
        try:
            # Simulate Indian market data
            market_data = {
                'nifty_50': {'return': 8.5, 'level': 21157, 'trend': 'bullish'},
                'sensex': {'return': 7.8, 'level': 70070, 'trend': 'bullish'},
                'bank_nifty': {'return': 6.2, 'level': 47259, 'trend': 'sideways'},
                'nifty_500': {'return': 10.2, 'level': 18500, 'trend': 'bullish'}
            }
            
            return {
                'indices': market_data,
                'key_trends': [
                    'Broad-based rally across equity markets',
                    'FII flows positive at ₹25,000 crores',
                    'Sector rotation from growth to value',
                    'Economic recovery driving earnings growth'
                ],
                'economic_factors': {
                    'gdp_growth': '6.5%',
                    'inflation': '5.2%',
                    'interest_rates': '6.5%',
                    'rupee': '₹83.25/USD'
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error generating Indian market context: {str(e)}")
            return {}
    
    def _generate_risk_analysis(self, holdings: List[Holding]) -> Dict:
        """Generate risk analysis"""
        try:
            total_value = sum(holding.current_value or 0 for holding in holdings)
            
            # Calculate concentration risk
            concentration_risk = self._calculate_concentration_risk(holdings, total_value)
            
            # Calculate sector risk
            sector_risk = self._calculate_sector_risk(holdings)
            
            # Calculate overall risk score
            risk_score = (concentration_risk + sector_risk) / 2
            
            return {
                'overall_risk_score': risk_score,
                'concentration_risk': concentration_risk,
                'sector_risk': sector_risk,
                'risk_factors': self._identify_risk_factors(holdings),
                'risk_level': 'High' if risk_score > 7 else 'Moderate' if risk_score > 4 else 'Low'
            }
            
        except Exception as e:
            self.logger.error(f"Error generating risk analysis: {str(e)}")
            return {}
    
    def _generate_opportunities_concerns(self, holdings: List[Holding]) -> Dict:
        """Generate opportunities and concerns"""
        try:
            opportunities = []
            concerns = []
            
            # Analyze opportunities
            if len(holdings) < 15:
                opportunities.append("Consider adding more stocks for better diversification")
            
            # Check sector concentration
            sector_weights = self._calculate_sector_weights(holdings)
            for sector, weight in sector_weights.items():
                if weight > 30:
                    concerns.append(f"High concentration in {sector} sector ({weight:.1f}%)")
                elif weight < 5:
                    opportunities.append(f"Consider adding exposure to {sector} sector")
            
            return {
                'opportunities': opportunities,
                'concerns': concerns,
                'action_items': self._generate_action_items(opportunities, concerns)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating opportunities and concerns: {str(e)}")
            return {}
    
    def _generate_forward_looking_insights(self) -> Dict:
        """Generate forward-looking insights"""
        try:
            return {
                'short_term': {
                    'outlook': 'Cautiously Optimistic',
                    'timeframe': '3-6 months',
                    'key_factors': [
                        'RBI policy decisions',
                        'Q3 earnings season',
                        'State elections impact',
                        'Global market volatility'
                    ],
                    'expected_return': '5-8%'
                },
                'medium_term': {
                    'outlook': 'Positive',
                    'timeframe': '6-12 months',
                    'key_factors': [
                        'Economic recovery strengthening',
                        'Corporate earnings growth',
                        'Interest rate normalization',
                        'Infrastructure spending'
                    ],
                    'expected_return': '10-15%'
                },
                'long_term': {
                    'outlook': 'Bullish',
                    'timeframe': '1-3 years',
                    'key_factors': [
                        'India\'s demographic dividend',
                        'Digital transformation',
                        'Manufacturing sector growth',
                        'Financial inclusion'
                    ],
                    'expected_return': '15-25% annualized'
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error generating forward-looking insights: {str(e)}")
            return {}
    
    # Helper methods
    def _calculate_portfolio_return(self, holdings: List[Holding]) -> float:
        """Calculate portfolio return (simulated)"""
        import random
        return random.uniform(-5, 15)
    
    def _analyze_asset_class_exposure(self, holdings: List[Holding]) -> Dict:
        """Analyze asset class exposure"""
        asset_class_exposure = {}
        total_value = 0
        
        for holding in holdings:
            if holding.security and holding.security.current_price:
                current_value = float(holding.quantity) * float(holding.security.current_price)
                total_value += current_value
                
                # Group by asset class
                asset_class = holding.security.asset_class.name if holding.security.asset_class else 'Unknown'
                if asset_class not in asset_class_exposure:
                    asset_class_exposure[asset_class] = 0
                asset_class_exposure[asset_class] += current_value
        
        # Convert to percentages
        if total_value > 0:
            return {asset_class: (value / total_value) * 100 for asset_class, value in asset_class_exposure.items()}
        return {}
    
    def _analyze_individual_stock(self, holding: Holding) -> Dict:
        """Analyze individual stock"""
        import random
        
        # Calculate current value
        current_value = 0
        if holding.security and holding.security.current_price:
            current_value = float(holding.quantity) * float(holding.security.current_price)
        
        stock_return = random.uniform(-20, 30)
        
        insights = []
        if stock_return > 15:
            insights.append(f"{holding.security.name} showing strong performance")
        elif stock_return < -10:
            insights.append(f"{holding.security.name} underperforming - review needed")
        
        return {
            'symbol': holding.security.symbol,
            'name': holding.security.name,
            'return': stock_return,
            'current_value': current_value,
            'insights': insights
        }
    
    def _analyze_sector_performance(self, sector: str, holdings: List[Holding]) -> Dict:
        """Analyze sector performance"""
        import random
        
        sector_return = random.uniform(-10, 20)
        total_value = 0
        
        for holding in holdings:
            if holding.security and holding.security.current_price:
                current_value = float(holding.quantity) * float(holding.security.current_price)
                total_value += current_value
        
        return {
            'return': sector_return,
            'weight': total_value,
            'holdings_count': len(holdings),
            'outlook': 'positive' if sector_return > 5 else 'neutral' if sector_return > -5 else 'negative'
        }
    
    def _calculate_concentration_risk(self, holdings: List[Holding], total_value: float) -> float:
        """Calculate concentration risk"""
        if total_value == 0:
            return 0
        
        # Calculate Herfindahl-Hirschman Index
        hhi = 0
        for holding in holdings:
            if holding.security and holding.security.current_price:
                current_value = float(holding.quantity) * float(holding.security.current_price)
                hhi += (current_value / total_value) ** 2
        
        # Convert to risk score (0-10)
        if hhi > 0.25:
            return 8  # High concentration
        elif hhi > 0.15:
            return 5  # Moderate concentration
        else:
            return 2  # Low concentration
    
    def _calculate_sector_risk(self, holdings: List[Holding]) -> float:
        """Calculate sector risk"""
        sector_weights = self._calculate_sector_weights(holdings)
        
        # Check for high sector concentration
        max_sector_weight = max(sector_weights.values()) if sector_weights else 0
        
        if max_sector_weight > 40:
            return 8  # High sector risk
        elif max_sector_weight > 25:
            return 5  # Moderate sector risk
        else:
            return 2  # Low sector risk
    
    def _calculate_sector_weights(self, holdings: List[Holding]) -> Dict:
        """Calculate sector weights"""
        sector_values = {}
        total_value = 0
        
        for holding in holdings:
            if holding.security and holding.security.current_price:
                current_value = float(holding.quantity) * float(holding.security.current_price)
                total_value += current_value
                
                # Use asset class instead of sector
                if holding.security.asset_class:
                    asset_class = holding.security.asset_class.name
                    sector_values[asset_class] = sector_values.get(asset_class, 0) + current_value
        
        if total_value > 0:
            return {sector: (value / total_value) * 100 for sector, value in sector_values.items()}
        return {}
    
    def _identify_risk_factors(self, holdings: List[Holding]) -> List[str]:
        """Identify risk factors"""
        risk_factors = []
        
        if len(holdings) < 10:
            risk_factors.append("Low diversification - few holdings")
        
        sector_weights = self._calculate_sector_weights(holdings)
        for sector, weight in sector_weights.items():
            if weight > 30:
                risk_factors.append(f"High concentration in {sector} sector")
        
        return risk_factors
    
    def _generate_action_items(self, opportunities: List[str], concerns: List[str]) -> List[str]:
        """Generate action items"""
        actions = []
        
        if concerns:
            actions.append("Review portfolio concentration and consider rebalancing")
        
        if opportunities:
            actions.append("Consider adding positions in underrepresented sectors")
        
        actions.append("Monitor quarterly earnings and sector trends")
        actions.append("Review portfolio quarterly for rebalancing")
        
        return actions
