#!/usr/bin/env python3
"""
Market Commentary Service - Generates market commentary for client reviews
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
import json
from models import Review, Client, Holding, Security
from extensions import db

# AI commentary generator is optional (we are deprecating AI/ML modules)
try:
    from ai_market_commentary_generator import AIMarketCommentaryGenerator
    AI_COMMENTARY_AVAILABLE = True
except Exception:
    AIMarketCommentaryGenerator = None
    AI_COMMENTARY_AVAILABLE = False

logger = logging.getLogger(__name__)

class MarketCommentaryService:
    """Service for generating market commentary and insights"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.ai_generator = AIMarketCommentaryGenerator() if AI_COMMENTARY_AVAILABLE else None
        # In production, these would be real API keys
        self.market_data_api_key = "demo_key"
        self.news_api_key = "demo_key"
    
    def generate_market_commentary(self, review_id: int, custom_insights: str = None) -> Dict:
        """
        Generate comprehensive market commentary for a review
        
        Args:
            review_id: ID of the review
            custom_insights: Optional custom insights from advisor
        
        Returns:
            Dict containing market commentary sections
        """
        try:
            # Use AI generator for personalized commentary (if available)
            if self.ai_generator:
                self.logger.info(f"Generating AI commentary for review {review_id}")
                ai_commentary = self.ai_generator.generate_personalized_commentary(review_id, custom_insights)
                if ai_commentary:
                    self.logger.info(f"AI commentary generated successfully for review {review_id}")
                    # Add period summary and key events
                    review = Review.query.get(review_id)
                    if review:
                        ai_commentary['period_summary'] = self._generate_period_summary(review.start_date, review.end_date)
                        ai_commentary['key_events'] = self._generate_key_events(review.start_date, review.end_date)
                    return ai_commentary
                self.logger.warning(f"AI commentary generation failed for review {review_id}, using fallback")
            
            # Fallback to basic commentary if AI generation fails
            self.logger.info(f"Using fallback commentary generation for review {review_id}")
            review = Review.query.get(review_id)
            if not review:
                self.logger.error(f"Review {review_id} not found")
                return None
            
            client = Client.query.get(review.client_id)
            if not client:
                self.logger.error(f"Client {review.client_id} not found")
                return None
            
            # Get portfolio holdings for client-specific analysis
            holdings = Holding.query.filter_by(client_id=client.id).all()
            self.logger.info(f"Found {len(holdings)} holdings for client {client.id}")
            
            # Generate different sections of market commentary
            commentary = {
                'period_summary': self._generate_period_summary(review.start_date, review.end_date),
                'market_performance': self._generate_market_performance(review.start_date, review.end_date),
                'sector_analysis': self._generate_sector_analysis(holdings, review.start_date, review.end_date),
                'economic_outlook': self._generate_economic_outlook(),
                'portfolio_impact': self._generate_portfolio_impact(holdings, review.start_date, review.end_date),
                'forward_looking': self._generate_forward_looking_insights(),
                'custom_insights': custom_insights,
                'key_events': self._generate_key_events(review.start_date, review.end_date),
                'generated_at': datetime.utcnow().isoformat()
            }
            
            self.logger.info(f"Fallback commentary generated successfully for review {review_id}")
            return commentary
            
        except Exception as e:
            self.logger.error(f"Error generating market commentary: {str(e)}")
            return None
    
    def _generate_period_summary(self, start_date, end_date) -> Dict:
        """Generate summary of the review period"""
        try:
            # In production, this would fetch real market data
            period_days = (end_date - start_date).days
            
            return {
                'period': f"{start_date.strftime('%B %Y')} to {end_date.strftime('%B %Y')}",
                'duration_days': period_days,
                'market_environment': self._get_market_environment(start_date, end_date),
                'volatility_level': self._get_volatility_level(start_date, end_date),
                'trend_direction': self._get_trend_direction(start_date, end_date),
                'summary': f"During this {period_days}-day period, markets experienced {self._get_market_environment(start_date, end_date).lower()} conditions with {self._get_volatility_level(start_date, end_date).lower()} volatility. The overall trend was {self._get_trend_direction(start_date, end_date).lower()}, influenced by various economic and geopolitical factors."
            }
            
        except Exception as e:
            self.logger.error(f"Error generating period summary: {str(e)}")
            return {}
    
    def _generate_market_performance(self, start_date, end_date) -> Dict:
        """Generate market performance analysis"""
        try:
            # Simulate market data - in production, fetch from real APIs
            market_data = {
                'sp500_return': self._simulate_market_return('S&P 500', start_date, end_date),
                'nasdaq_return': self._simulate_market_return('NASDAQ', start_date, end_date),
                'dow_return': self._simulate_market_return('Dow Jones', start_date, end_date),
                'bond_yields': self._simulate_bond_yields(start_date, end_date),
                'currency_movements': self._simulate_currency_movements(start_date, end_date),
                'commodity_prices': self._simulate_commodity_prices(start_date, end_date)
            }
            
            return {
                'major_indices': {
                    'S&P 500': {
                        'return': market_data['sp500_return'],
                        'performance': 'outperformed' if market_data['sp500_return'] > 0 else 'underperformed',
                        'comment': f"The S&P 500 {'gained' if market_data['sp500_return'] > 0 else 'declined'} {abs(market_data['sp500_return']):.1f}% during this period."
                    },
                    'NASDAQ': {
                        'return': market_data['nasdaq_return'],
                        'performance': 'outperformed' if market_data['nasdaq_return'] > 0 else 'underperformed',
                        'comment': f"Technology-heavy NASDAQ {'gained' if market_data['nasdaq_return'] > 0 else 'declined'} {abs(market_data['nasdaq_return']):.1f}%."
                    },
                    'Dow Jones': {
                        'return': market_data['dow_return'],
                        'performance': 'outperformed' if market_data['dow_return'] > 0 else 'underperformed',
                        'comment': f"The Dow Jones Industrial Average {'gained' if market_data['dow_return'] > 0 else 'declined'} {abs(market_data['dow_return']):.1f}%."
                    }
                },
                'fixed_income': {
                    '10_year_treasury': market_data['bond_yields']['10y'],
                    'comment': f"10-year Treasury yields {'increased' if market_data['bond_yields']['10y'] > 0 else 'decreased'} to {market_data['bond_yields']['current_10y']:.2f}%."
                },
                'currencies': market_data['currency_movements'],
                'commodities': market_data['commodity_prices']
            }
            
        except Exception as e:
            self.logger.error(f"Error generating market performance: {str(e)}")
            return {}
    
    def _generate_sector_analysis(self, holdings: List[Holding], start_date, end_date) -> Dict:
        """Generate sector-specific analysis based on client holdings"""
        try:
            # Get unique sectors from holdings
            sectors = {}
            for holding in holdings:
                if holding.security and holding.security.sector:
                    sector = holding.security.sector
                    if sector not in sectors:
                        sectors[sector] = {
                            'holdings': [],
                            'total_value': 0,
                            'weight': 0
                        }
                    sectors[sector]['holdings'].append(holding)
                    sectors[sector]['total_value'] += holding.current_value or 0
            
            # Calculate weights
            total_portfolio_value = sum(s['total_value'] for s in sectors.values())
            for sector in sectors:
                if total_portfolio_value > 0:
                    sectors[sector]['weight'] = (sectors[sector]['total_value'] / total_portfolio_value) * 100
            
            # Generate sector performance and outlook
            sector_analysis = {}
            for sector_name, sector_data in sectors.items():
                sector_performance = self._simulate_sector_performance(sector_name, start_date, end_date)
                sector_analysis[sector_name] = {
                    'portfolio_weight': sector_data['weight'],
                    'market_performance': sector_performance['return'],
                    'outlook': sector_performance['outlook'],
                    'key_drivers': sector_performance['drivers'],
                    'risks': sector_performance['risks'],
                    'opportunities': sector_performance['opportunities']
                }
            
            return sector_analysis
            
        except Exception as e:
            self.logger.error(f"Error generating sector analysis: {str(e)}")
            return {}
    
    def _generate_economic_outlook(self) -> Dict:
        """Generate economic outlook and forecasts"""
        try:
            # In production, this would fetch from economic data providers
            return {
                'gdp_growth': {
                    'current': '2.1%',
                    'forecast': '2.3%',
                    'trend': 'moderate_growth',
                    'comment': 'GDP growth remains steady with moderate expansion expected.'
                },
                'inflation': {
                    'current': '3.2%',
                    'forecast': '2.8%',
                    'trend': 'declining',
                    'comment': 'Inflation continues to moderate, supporting consumer spending.'
                },
                'interest_rates': {
                    'current_fed_rate': '5.25%',
                    'forecast': '4.75%',
                    'trend': 'declining',
                    'comment': 'Federal Reserve expected to gradually reduce rates.'
                },
                'employment': {
                    'unemployment_rate': '3.7%',
                    'trend': 'stable',
                    'comment': 'Labor market remains strong with stable employment.'
                },
                'consumer_sentiment': {
                    'index': '72.5',
                    'trend': 'improving',
                    'comment': 'Consumer confidence showing signs of improvement.'
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error generating economic outlook: {str(e)}")
            return {}
    
    def _generate_portfolio_impact(self, holdings: List[Holding], start_date, end_date) -> Dict:
        """Analyze how market conditions impacted the specific portfolio"""
        try:
            total_value = sum(holding.current_value or 0 for holding in holdings)
            total_return = 0
            sector_impacts = {}
            
            for holding in holdings:
                if holding.security:
                    # Simulate individual security performance
                    security_return = self._simulate_security_performance(holding.security.symbol, start_date, end_date)
                    holding_return = (holding.current_value or 0) * (security_return / 100)
                    total_return += holding_return
                    
                    # Track sector impacts
                    sector = holding.security.sector or 'Unknown'
                    if sector not in sector_impacts:
                        sector_impacts[sector] = {'return': 0, 'weight': 0}
                    sector_impacts[sector]['return'] += holding_return
                    sector_impacts[sector]['weight'] += holding.current_value or 0
            
            portfolio_return_pct = (total_return / total_value * 100) if total_value > 0 else 0
            
            return {
                'total_return': portfolio_return_pct,
                'total_value': total_value,
                'sector_impacts': sector_impacts,
                'top_performers': self._get_top_performers(holdings, start_date, end_date),
                'underperformers': self._get_underperformers(holdings, start_date, end_date),
                'market_correlation': self._calculate_market_correlation(holdings, start_date, end_date),
                'summary': f"The portfolio {'gained' if portfolio_return_pct > 0 else 'declined'} {abs(portfolio_return_pct):.1f}% during this period, {'outperforming' if portfolio_return_pct > 8 else 'underperforming' if portfolio_return_pct < 5 else 'tracking'} the broader market."
            }
            
        except Exception as e:
            self.logger.error(f"Error generating portfolio impact: {str(e)}")
            return {}
    
    def _generate_forward_looking_insights(self) -> Dict:
        """Generate forward-looking market insights and expectations"""
        try:
            return {
                'short_term_outlook': {
                    'timeframe': '3-6 months',
                    'sentiment': 'cautiously_optimistic',
                    'key_factors': [
                        'Federal Reserve policy decisions',
                        'Earnings season performance',
                        'Geopolitical developments',
                        'Inflation trends'
                    ],
                    'comment': 'Markets are expected to remain volatile with a cautiously optimistic bias as inflation moderates and growth remains steady.'
                },
                'medium_term_outlook': {
                    'timeframe': '6-12 months',
                    'sentiment': 'positive',
                    'key_factors': [
                        'Interest rate normalization',
                        'Corporate earnings growth',
                        'Consumer spending trends',
                        'Global economic recovery'
                    ],
                    'comment': 'Medium-term outlook is positive as economic fundamentals improve and monetary policy becomes more accommodative.'
                },
                'long_term_outlook': {
                    'timeframe': '1-3 years',
                    'sentiment': 'bullish',
                    'key_factors': [
                        'Technological innovation',
                        'Demographic trends',
                        'Global economic integration',
                        'Sustainable investing growth'
                    ],
                    'comment': 'Long-term prospects remain strong driven by innovation, demographic shifts, and global economic growth.'
                },
                'risks_to_watch': [
                    'Geopolitical tensions',
                    'Inflation resurgence',
                    'Credit market stress',
                    'Regulatory changes'
                ],
                'opportunities': [
                    'Technology sector growth',
                    'International markets',
                    'Alternative investments',
                    'ESG-focused strategies'
                ]
            }
            
        except Exception as e:
            self.logger.error(f"Error generating forward looking insights: {str(e)}")
            return {}
    
    def _generate_key_events(self, start_date, end_date) -> List[Dict]:
        """Generate key market events during the period"""
        try:
            # In production, this would fetch from news APIs
            events = [
                {
                    'date': (start_date + timedelta(days=30)).strftime('%Y-%m-%d'),
                    'event': 'Federal Reserve Meeting',
                    'impact': 'positive',
                    'description': 'Fed maintained interest rates, signaling potential future cuts.'
                },
                {
                    'date': (start_date + timedelta(days=60)).strftime('%Y-%m-%d'),
                    'event': 'Earnings Season',
                    'impact': 'mixed',
                    'description': 'Mixed corporate earnings with technology sector outperforming.'
                },
                {
                    'date': (start_date + timedelta(days=90)).strftime('%Y-%m-%d'),
                    'event': 'Inflation Report',
                    'impact': 'positive',
                    'description': 'CPI data showed continued moderation in inflation.'
                },
                {
                    'date': (start_date + timedelta(days=120)).strftime('%Y-%m-%d'),
                    'event': 'Jobs Report',
                    'impact': 'positive',
                    'description': 'Strong employment data supporting economic growth.'
                }
            ]
            
            return events
            
        except Exception as e:
            self.logger.error(f"Error generating key events: {str(e)}")
            return []
    
    # Helper methods for simulated data
    def _simulate_market_return(self, index: str, start_date, end_date) -> float:
        """Simulate market returns - in production, fetch real data"""
        import random
        base_returns = {
            'S&P 500': 8.5,
            'NASDAQ': 12.3,
            'Dow Jones': 6.8
        }
        base = base_returns.get(index, 7.0)
        return base + random.uniform(-2, 2)
    
    def _simulate_bond_yields(self, start_date, end_date) -> Dict:
        """Simulate bond yield movements"""
        return {
            '10y': 0.5,
            'current_10y': 4.25,
            'trend': 'declining'
        }
    
    def _simulate_currency_movements(self, start_date, end_date) -> Dict:
        """Simulate currency movements"""
        return {
            'USD_EUR': {'change': -2.1, 'trend': 'strengthening'},
            'USD_JPY': {'change': 1.8, 'trend': 'strengthening'},
            'USD_GBP': {'change': -1.2, 'trend': 'strengthening'}
        }
    
    def _simulate_commodity_prices(self, start_date, end_date) -> Dict:
        """Simulate commodity price movements"""
        return {
            'gold': {'change': 5.2, 'trend': 'increasing'},
            'oil': {'change': -3.1, 'trend': 'declining'},
            'copper': {'change': 2.8, 'trend': 'increasing'}
        }
    
    def _simulate_sector_performance(self, sector: str, start_date, end_date) -> Dict:
        """Simulate sector performance"""
        sector_performances = {
            'Technology': {'return': 15.2, 'outlook': 'positive', 'drivers': ['AI growth', 'Cloud computing'], 'risks': ['Regulation'], 'opportunities': ['Innovation']},
            'Healthcare': {'return': 8.7, 'outlook': 'stable', 'drivers': ['Aging population', 'Innovation'], 'risks': ['Policy changes'], 'opportunities': ['Biotech']},
            'Financial': {'return': 6.3, 'outlook': 'positive', 'drivers': ['Interest rates', 'Economic growth'], 'risks': ['Credit quality'], 'opportunities': ['Digital banking']},
            'Consumer': {'return': 4.8, 'outlook': 'stable', 'drivers': ['Consumer spending', 'Employment'], 'risks': ['Inflation'], 'opportunities': ['E-commerce']},
            'Energy': {'return': -2.1, 'outlook': 'mixed', 'drivers': ['Oil prices', 'Energy transition'], 'risks': ['Price volatility'], 'opportunities': ['Renewables']}
        }
        return sector_performances.get(sector, {'return': 5.0, 'outlook': 'neutral', 'drivers': [], 'risks': [], 'opportunities': []})
    
    def _simulate_security_performance(self, symbol: str, start_date, end_date) -> float:
        """Simulate individual security performance"""
        import random
        return random.uniform(-20, 30)
    
    def _get_market_environment(self, start_date, end_date) -> str:
        """Get market environment description"""
        return "Moderate growth with some volatility"
    
    def _get_volatility_level(self, start_date, end_date) -> str:
        """Get volatility level description"""
        return "Moderate"
    
    def _get_trend_direction(self, start_date, end_date) -> str:
        """Get trend direction"""
        return "Upward"
    
    def _get_top_performers(self, holdings: List[Holding], start_date, end_date) -> List[Dict]:
        """Get top performing holdings"""
        performers = []
        for holding in holdings[:3]:  # Top 3
            if holding.security:
                performers.append({
                    'symbol': holding.security.symbol,
                    'name': holding.security.name,
                    'return': self._simulate_security_performance(holding.security.symbol, start_date, end_date)
                })
        return sorted(performers, key=lambda x: x['return'], reverse=True)
    
    def _get_underperformers(self, holdings: List[Holding], start_date, end_date) -> List[Dict]:
        """Get underperforming holdings"""
        performers = []
        for holding in holdings[:3]:  # Bottom 3
            if holding.security:
                performers.append({
                    'symbol': holding.security.symbol,
                    'name': holding.security.name,
                    'return': self._simulate_security_performance(holding.security.symbol, start_date, end_date)
                })
        return sorted(performers, key=lambda x: x['return'])
    
    def _calculate_market_correlation(self, holdings: List[Holding], start_date, end_date) -> float:
        """Calculate portfolio correlation with market"""
        return 0.75  # Simulated correlation
