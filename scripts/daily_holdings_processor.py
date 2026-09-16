#!/usr/bin/env python3
"""
Weekly holdings processor — processes **all** clients in the current monthly cycle.
Runs every Sunday (scheduled via Airflow).
"""
import os
import sys
from datetime import datetime, date
import logging

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from main import create_app
from extensions import db
from sqlalchemy import text
from services.forward_holding_calculation_service import audit_holdings_accuracy

# Setup logging
_log_dir = os.path.join(_ROOT, "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_log_dir, "holdings_processor.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def audit_client_holdings(client_id, as_of_date=None):
    """Compare holdings table to forward calculation (transactions + corporate actions)."""
    as_of_date = as_of_date or date.today()
    try:
        discrepancies = audit_holdings_accuracy(client_id, as_of_date)
        mismatches = len(discrepancies)
        for disc in discrepancies:
            logger.warning(
                f"Client {client_id}, {disc.get('security_symbol', disc.get('security_id'))}: "
                f"holdings {disc['holdings_table_qty']:.2f} vs forward {disc['forward_calc_qty']:.2f}"
            )
        return {'status': 'completed', 'mismatches': mismatches}
    except Exception as e:
        logger.error(f"Error auditing client {client_id}: {e}")
        return {'status': 'error', 'error': str(e), 'mismatches': 0}

def process_weekly_clients():
    """Process every client in the current monthly cycle (weekly Sunday run)."""
    app = create_app()
    
    with app.app_context():
        try:
            today = date.today()
            current_month = datetime.now().strftime("%Y-%m")

            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT id, client_id, cycle_id 
                    FROM monthly_holdings_cycle 
                    WHERE cycle_id = :cycle_id
                    ORDER BY id
                """), {"cycle_id": current_month})
                pending_clients = result.fetchall()
            
            if not pending_clients:
                logger.info(f"No clients in cycle {current_month}")
                return
            
            logger.info(f"Weekly processing {len(pending_clients)} clients for cycle {current_month} on {today}")
            
            processed_count = 0
            error_count = 0
            
            for cycle_entry in pending_clients:
                cycle_id_db = cycle_entry[0]
                client_id = cycle_entry[1]
                cycle_id = cycle_entry[2]
                
                try:
                    # Update status to processing
                    with db.engine.connect() as conn:
                        conn.execute(text("""
                            UPDATE monthly_holdings_cycle 
                            SET status = 'processing', processed_at = :now
                            WHERE id = :id
                        """), {"now": datetime.now(), "id": cycle_id_db})
                        conn.commit()
                    
                    # Process the client
                    result = audit_client_holdings(client_id, today)
                    
                    # Update cycle entry
                    with db.engine.connect() as conn:
                        conn.execute(text("""
                            UPDATE monthly_holdings_cycle 
                            SET status = :status, mismatches_found = :mismatches
                            WHERE id = :id
                        """), {
                            "status": 'completed' if result['status'] != 'error' else 'failed',
                            "mismatches": result.get('mismatches', 0),
                            "id": cycle_id_db
                        })
                        conn.commit()
                    
                    if result['status'] == 'error':
                        error_count += 1
                    else:
                        processed_count += 1
                    
                    logger.info(f"✅ Processed client {client_id}: {result['status']}, {result.get('mismatches', 0)} mismatches")
                    
                except Exception as e:
                    logger.error(f"❌ Error processing client {client_id}: {e}")
                    try:
                        with db.engine.connect() as conn:
                            conn.execute(text("""
                                UPDATE monthly_holdings_cycle 
                                SET status = 'failed', error_message = :error
                                WHERE id = :id
                            """), {"error": str(e), "id": cycle_id_db})
                            conn.commit()
                    except:
                        pass
                    error_count += 1
            
            logger.info(f"Weekly processing complete: {processed_count} successful, {error_count} errors")
            
        except Exception as e:
            logger.error(f"Weekly processing failed: {e}")
            import traceback
            traceback.print_exc()
            raise

if __name__ == "__main__":
    process_weekly_clients()

