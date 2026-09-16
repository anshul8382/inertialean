"""
Temporary script to process all executed recommendations that haven't been recorded as trades.
This script creates transactions for executed recommendations that are missing transactions.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import Recommendation, Transaction
from services.transaction_orchestrator import TransactionOrchestrator
from datetime import timedelta, datetime
from decimal import Decimal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_executed_recommendations():
    """Process all executed recommendations that don't have corresponding transactions"""
    app = create_app()
    
    with app.app_context():
        # Get all executed recommendations that are trade recommendations
        executed_recommendations = Recommendation.query.filter(
            Recommendation.status == 'executed',
            Recommendation.client_id.isnot(None),
            Recommendation.quantity.isnot(None),
            Recommendation.actual_price.isnot(None)
        ).all()
        
        logger.info(f"Found {len(executed_recommendations)} executed recommendations to process")
        
        orchestrator = TransactionOrchestrator(db.session)
        processed_count = 0
        skipped_count = 0
        error_count = 0
        errors = []
        
        for recommendation in executed_recommendations:
            try:
                # Check if transaction already exists for this recommendation
                execution_date = recommendation.executed_at or recommendation.created_at
                
                existing_transaction = Transaction.query.filter(
                    Transaction.client_id == recommendation.client_id,
                    Transaction.security_id == recommendation.security_id,
                    Transaction.type == recommendation.action.upper(),
                    Transaction.quantity == recommendation.quantity,
                    Transaction.price.between(
                        recommendation.actual_price * Decimal('0.99'), 
                        recommendation.actual_price * Decimal('1.01')
                    ),
                    Transaction.transaction_date >= execution_date - timedelta(days=1),
                    Transaction.transaction_date <= execution_date + timedelta(days=1)
                ).first()
                
                if existing_transaction:
                    logger.info(f"Skipping recommendation {recommendation.id} - transaction already exists (Transaction ID: {existing_transaction.id})")
                    skipped_count += 1
                    continue
                
                # Create transaction using TransactionOrchestrator
                transaction_data = {
                    'client_id': recommendation.client_id,
                    'security_id': recommendation.security_id,
                    'type': recommendation.action.upper(),  # BUY or SELL
                    'quantity': recommendation.quantity,
                    'price': recommendation.actual_price,
                    'transaction_date': execution_date.date() if isinstance(execution_date, datetime) else execution_date
                }
                
                result = orchestrator.create_single_transaction(transaction_data)
                
                if result['success']:
                    logger.info(f"Created transaction for recommendation {recommendation.id} (Transaction ID: {result['transaction_id']})")
                    processed_count += 1
                    db.session.commit()
                else:
                    error_msg = f"Recommendation {recommendation.id}: {result.get('message', 'Unknown error')}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    error_count += 1
                    db.session.rollback()
                    
            except Exception as e:
                error_msg = f"Error processing recommendation {recommendation.id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                error_count += 1
                db.session.rollback()
        
        # Summary
        logger.info("\n" + "="*50)
        logger.info("PROCESSING SUMMARY")
        logger.info("="*50)
        logger.info(f"Total executed recommendations: {len(executed_recommendations)}")
        logger.info(f"Successfully processed: {processed_count}")
        logger.info(f"Skipped (transaction exists): {skipped_count}")
        logger.info(f"Errors: {error_count}")
        
        if errors:
            logger.info("\nErrors encountered:")
            for error in errors[:10]:  # Show first 10 errors
                logger.info(f"  - {error}")
            if len(errors) > 10:
                logger.info(f"  ... and {len(errors) - 10} more errors")

if __name__ == '__main__':
    process_executed_recommendations()

