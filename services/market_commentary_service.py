"""
Market Commentary Service for Period Analysis V2

This service manages shared market commentary that appears in all Period Analysis V2 emails.
The commentary can be AI-generated or manually edited, and once saved, it's reused for all clients.
"""
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any
from extensions import db

# Try to import MarketCommentary, but make it optional if model doesn't exist
try:
    from models import MarketCommentary, User
    MARKET_COMMENTARY_AVAILABLE = True
except ImportError:
    MarketCommentary = None
    try:
        from models import User
    except ImportError:
        User = None
    MARKET_COMMENTARY_AVAILABLE = False
    logging.warning("MarketCommentary model not found - market commentary features will be limited")

# Try to import AI dependencies, but make optional
try:
    from ai_models.utils.advanced_hybrid_manager import AdvancedHybridManager
    from ai_models.utils.perplexity_client import PerplexityClient
    from ai_models.config import AIConfig
    AI_MODELS_AVAILABLE = True
except ImportError:
    AdvancedHybridManager = None
    PerplexityClient = None
    AIConfig = None
    AI_MODELS_AVAILABLE = False
    logging.warning("AI models not available - AI commentary generation will be disabled")

logger = logging.getLogger(__name__)


class MarketCommentaryService:
    """Service for managing market commentary for Period Analysis V2"""
    
    def __init__(self):
        if AI_MODELS_AVAILABLE:
            self.ai_manager = AdvancedHybridManager()
            # For market outlook, we can use Perplexity directly (external OK) to avoid long multi-step pipelines/timeouts.
            self.perplexity_client = PerplexityClient(AIConfig.get_perplexity_api_key())
        else:
            self.ai_manager = None
            self.perplexity_client = None
    
    def get_active_commentary(self) -> Optional[Any]:
        """Get the currently active market commentary"""
        if not MARKET_COMMENTARY_AVAILABLE or MarketCommentary is None:
            return None
        return MarketCommentary.get_active_commentary()
    
    def generate_ai_commentary(self, period_start: Optional[date] = None, 
                               period_end: Optional[date] = None,
                               user_id: int = None) -> Dict[str, Any]:
        """
        Generate AI-powered market commentary for the given period
        
        Args:
            period_start: Start date of the period (optional)
            period_end: End date of the period (optional)
            user_id: ID of user generating the commentary
            
        Returns:
            Dict with 'success', 'content', and 'error' keys
        """
        if not AI_MODELS_AVAILABLE:
            return {
                'success': False,
                'code': 'ai_unavailable',
                'error': 'AI models not available. Please configure AI models.',
            }
        
        try:
            # Build prompt for market commentary
            prompt = self._build_commentary_prompt(period_start, period_end)
            
            # Generate using Perplexity directly (external web-enabled) to keep latency predictable.
            # Client-specific data is NOT used here, so external generation is acceptable.
            logger.info("Generating market commentary using Perplexity (direct)")
            if self.perplexity_client and hasattr(self.perplexity_client, 'is_available') and self.perplexity_client.is_available():
                result = self.perplexity_client.generate(
                    prompt=prompt,
                    model="sonar-pro",
                    temperature=0.3,
                    max_tokens=2800,
                    top_p=0.9
                )
                # Normalize to AdvancedHybridManager-like shape
                if result.get("status") == "success":
                    result["source"] = result.get("source", "perplexity")
            elif self.ai_manager:
                # Fallback to hybrid manager (will use local/fallback paths)
                logger.info("Perplexity not available - falling back to hybrid manager")
                result = self.ai_manager.generate_business_content(
                    task_type='equity_research',
                    prompt=prompt,
                    context=self._build_context(period_start, period_end),
                    user_id=str(user_id) if user_id else "system",
                    model_id=1
                )
            else:
                return {
                    'success': False,
                    'code': 'ai_unavailable',
                    'error': 'AI models not configured (no Perplexity key and no hybrid manager).',
                }
            
            if result.get('status') == 'success':
                content = result.get('response', '').strip()
                if not content:
                    return {
                        'success': False,
                        'error': 'AI generated empty commentary'
                    }
                
                return {
                    'success': True,
                    'content': content,
                    'is_ai_generated': True,
                    'source': result.get('source', 'unknown')
                }
            else:
                error = result.get('error', 'Unknown error generating commentary')
                logger.error(f"AI generation failed: {error}")
                return {
                    'success': False,
                    'error': f'AI generation failed: {error}'
                }
                
        except Exception as e:
            logger.error(f"Error generating market commentary: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': f'Error generating commentary: {str(e)}'
            }
    
    def save_commentary(self, content: str, is_ai_generated: bool = True,
                       period_start: Optional[date] = None,
                       period_end: Optional[date] = None,
                       user_id: int = None) -> Dict[str, Any]:
        """
        Save market commentary (deactivates previous active commentary)
        
        Args:
            content: The commentary text
            is_ai_generated: Whether this was AI-generated
            period_start: Start date of the period (optional)
            period_end: End date of the period (optional)
            user_id: ID of user saving the commentary
            
        Returns:
            Dict with 'success' and 'commentary' or 'error' keys
        """
        if not MARKET_COMMENTARY_AVAILABLE or MarketCommentary is None:
            return {
                'success': False,
                'error': 'MarketCommentary model not available. Please run database migrations.'
            }
        
        try:
            if not content or not content.strip():
                return {
                    'success': False,
                    'error': 'Commentary content cannot be empty'
                }
            
            # Deactivate all existing commentaries
            MarketCommentary.deactivate_all()
            
            # Get the highest version number
            max_version = db.session.query(db.func.max(MarketCommentary.version)).scalar() or 0
            
            # Create new active commentary
            commentary = MarketCommentary(
                content=content.strip(),
                is_ai_generated=is_ai_generated,
                period_start_date=period_start,
                period_end_date=period_end,
                version=max_version + 1,
                is_active=True,
                created_by=user_id or 1,
                updated_by=user_id or 1
            )
            
            db.session.add(commentary)
            db.session.commit()
            
            logger.info(f"Market commentary saved (ID: {commentary.id}, Version: {commentary.version})")
            
            return {
                'success': True,
                'commentary': {
                    'id': commentary.id,
                    'content': commentary.content,
                    'is_ai_generated': commentary.is_ai_generated,
                    'version': commentary.version,
                    'created_at': commentary.created_at.isoformat(),
                    'updated_at': commentary.updated_at.isoformat()
                }
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving market commentary: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': f'Error saving commentary: {str(e)}'
            }
    
    def update_commentary(self, commentary_id: int, content: str, user_id: int = None) -> Dict[str, Any]:
        """
        Update existing market commentary
        
        Args:
            commentary_id: ID of commentary to update
            content: New commentary content
            user_id: ID of user updating the commentary
            
        Returns:
            Dict with 'success' and 'commentary' or 'error' keys
        """
        if not MARKET_COMMENTARY_AVAILABLE or MarketCommentary is None:
            return {
                'success': False,
                'error': 'MarketCommentary model not available. Please run database migrations.'
            }
        
        try:
            commentary = MarketCommentary.query.get(commentary_id)
            if not commentary:
                return {
                    'success': False,
                    'error': 'Commentary not found'
                }
            
            if not content or not content.strip():
                return {
                    'success': False,
                    'error': 'Commentary content cannot be empty'
                }
            
            commentary.content = content.strip()
            commentary.is_ai_generated = False  # Mark as manually edited
            commentary.updated_by = user_id
            commentary.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Market commentary updated (ID: {commentary.id})")
            
            return {
                'success': True,
                'commentary': {
                    'id': commentary.id,
                    'content': commentary.content,
                    'is_ai_generated': commentary.is_ai_generated,
                    'version': commentary.version,
                    'updated_at': commentary.updated_at.isoformat()
                }
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating market commentary: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': f'Error updating commentary: {str(e)}'
            }
    
    def _build_commentary_prompt(self, period_start: Optional[date], period_end: Optional[date]) -> str:
        """Build the prompt for AI market commentary generation (Perplexity web / sonar-pro)."""
        if period_start and period_end:
            start_fmt = period_start.strftime("%B %d, %Y")
            end_fmt = period_end.strftime("%B %d, %Y")
            period_clause = f"covering {start_fmt} to {end_fmt}"
        elif period_end:
            end_fmt = period_end.strftime("%B %d, %Y")
            period_clause = f"through {end_fmt}"
        else:
            period_clause = (
                "for Indian equities over roughly the last six months "
                "(infer exact window from current date if specific bounds are missing)"
            )

        # Shared commentary: must never ask for client names, ₹ portfolio sizes, or account-specific data.
        return f"""Search the web and write a 3–4 paragraph market commentary for an Indian equity portfolio review {period_clause}.

Cover (integrate into flowing prose — do not use bullet points in the main body):
1) Nifty 50 — approximate index level near the start of the window, key highs/lows or notable swings during the period, approximate level near the end, and approximate total return over the span (use current, source-backed figures).
2) FII flows — net buyers vs sellers across the period; mention DII briefly if it materially offset or amplified FII; call out any months or stretches of heavy buying or selling.
3) India macro — RBI policy decisions, important GDP or inflation prints, Union Budget or major fiscal headlines if they fell inside the window.
4) Global factors — US Fed stance, dollar/DXY strength, tariff or trade developments, and risk-off episodes that clearly spilled into Indian markets.
5) Sector themes — which sectors actually led and lagged the index during this window and why (describe from flows and earnings news; do not assume any default tilt such as IT vs BFSI unless sources support it).

Tone and audience:
- Warm, first-person adviser voice ("we saw…", "our read was…"). Written for a sophisticated retail investor, not a professional desk.
- Prefer specific levels, percentages, months, and events over vague generalities; where sources disagree, state the range briefly and move on.

Hard rules:
- Do NOT mention any individual client name, initials as a stand-in for a person, portfolio size in ₹, or account-specific data — this text may be reused across clients.
- Do NOT describe "search results", APIs, or your process; deliver finished commentary only. No marketing slogans.

End with one short closing sentence (still prose, not a bullet) on why this backdrop matters for disciplined, long-term Indian equity investors who stay diversified.

If you use web material, add 3–6 plain URL citations on separate lines after the prose under the heading "Sources"."""
    
    def _build_context(self, period_start: Optional[date], period_end: Optional[date]) -> str:
        """Build context for AI generation"""
        context_parts = []
        
        if period_start and period_end:
            context_parts.append(f"Analysis period: {period_start} to {period_end}")
        
        context_parts.append("Focus on Indian equity markets (NSE/BSE)")
        context_parts.append("Target audience: Wealth management clients")
        
        return "\n".join(context_parts)









