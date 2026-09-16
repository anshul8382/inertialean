#!/usr/bin/env python3
"""
Workflow-Transaction Integration Module

This module handles the automatic creation and management of recommendations
and transactions based on workflow stages.
"""

from datetime import datetime
from models import db, Workflow, WorkflowAction, MonthlyInvestment, Client, Recommendation, Transaction, Security, Portfolio
from flask import Flask
from extensions import db as db_ext

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your-secret-key'
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or (
    f"mysql+pymysql://{os.environ.get('DB_USER', 'inertia_admin')}:"
    f"{os.environ.get('DB_PASSWORD', '')}@"
    f"{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '3306')}/"
    f"{os.environ.get('DB_NAME', 'inertia_app2025')}"
)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db_ext.init_app(app)
    return app

def create_recommendations_for_workflow(workflow_id, amount, user_id):
    """
    Create recommendations when workflow reaches RECOS stage
    
    Args:
        workflow_id (int): ID of the workflow
        amount (float): Total amount for recommendations
        user_id (int): ID of the user creating recommendations
    """
    app = create_app()
    
    with app.app_context():
        try:
            workflow = Workflow.query.get(workflow_id)
            if not workflow or not workflow.monthly_investment:
                print(f"Workflow {workflow_id} not found or no monthly investment")
                return False
            
            client = workflow.monthly_investment.client
            if not client:
                print(f"Client not found for workflow {workflow_id}")
                return False
            
            # Get client's portfolio
            portfolio = Portfolio.query.filter_by(client_id=client.id, status='active').first()
            if not portfolio:
                print(f"No active portfolio found for client {client.name}")
                return False
            
            # Get recommended securities based on portfolio model
            # This is a simplified version - you can enhance this based on your business logic
            recommended_securities = get_recommended_securities(portfolio, amount)
            
            recommendations_created = 0
            for sec_data in recommended_securities:
                recommendation = Recommendation(
                    security_id=sec_data['security_id'],
                    client_id=client.id,
                    action='buy',
                    quantity=sec_data['quantity'],
                    target_price=sec_data['target_price'],
                    status='pending',
                    expiry_date=datetime.utcnow().replace(day=datetime.utcnow().day + 7),  # 7 days expiry
                    created_by=user_id,
                    notes=f"Generated from workflow {workflow_id} - Monthly investment"
                )
                db.session.add(recommendation)
                recommendations_created += 1
            
            db.session.commit()
            print(f"Created {recommendations_created} recommendations for workflow {workflow_id}")
            return True
            
        except Exception as e:
            print(f"Error creating recommendations: {e}")
            db.session.rollback()
            return False

def execute_recommendations_for_workflow(workflow_id, user_id):
    """
    Execute pending recommendations when workflow reaches EXEC stage
    
    Args:
        workflow_id (int): ID of the workflow
        user_id (int): ID of the user executing recommendations
    """
    app = create_app()
    
    with app.app_context():
        try:
            workflow = Workflow.query.get(workflow_id)
            if not workflow or not workflow.monthly_investment:
                print(f"Workflow {workflow_id} not found or no monthly investment")
                return False
            
            client = workflow.monthly_investment.client
            if not client:
                print(f"Client not found for workflow {workflow_id}")
                return False
            
            # Get client's portfolio
            portfolio = Portfolio.query.filter_by(client_id=client.id, status='active').first()
            if not portfolio:
                print(f"No active portfolio found for client {client.name}")
                return False
            
            # Get pending recommendations for this client
            pending_recommendations = Recommendation.query.filter_by(
                client_id=client.id,
                status='pending'
            ).all()
            
            transactions_created = 0
            for recommendation in pending_recommendations:
                # Get current market price (you can integrate with market data API)
                current_price = get_current_market_price(recommendation.security_id)
                
                if current_price:
                    # Create transaction
                    transaction = Transaction(
                        client_id=client.id,
                        security_id=recommendation.security_id,
                        portfolio_id=portfolio.id,
                        transaction_date=datetime.utcnow(),
                        type='buy',
                        quantity=recommendation.quantity,
                        price=current_price,
                        amount=float(recommendation.quantity) * float(current_price),
                        created_at=datetime.utcnow()
                    )
                    db.session.add(transaction)
                    
                    # Update recommendation status
                    recommendation.status = 'executed'
                    recommendation.actual_price = current_price
                    recommendation.executed_at = datetime.utcnow()
                    recommendation.executed_by = user_id
                    
                    transactions_created += 1
            
            db.session.commit()
            print(f"Executed {transactions_created} recommendations for workflow {workflow_id}")
            return True
            
        except Exception as e:
            print(f"Error executing recommendations: {e}")
            db.session.rollback()
            return False

def update_portfolio_holdings_for_workflow(workflow_id):
    """
    Update portfolio holdings when workflow reaches UPDATE stage
    
    Args:
        workflow_id (int): ID of the workflow
    """
    app = create_app()
    
    with app.app_context():
        try:
            workflow = Workflow.query.get(workflow_id)
            if not workflow or not workflow.monthly_investment:
                print(f"Workflow {workflow_id} not found or no monthly investment")
                return False
            
            client = workflow.monthly_investment.client
            if not client:
                print(f"Client not found for workflow {workflow_id}")
                return False
            
            # Get client's portfolio
            portfolio = Portfolio.query.filter_by(client_id=client.id, status='active').first()
            if not portfolio:
                print(f"No active portfolio found for client {client.name}")
                return False
            
            # Get recent transactions for this portfolio
            recent_transactions = Transaction.query.filter_by(
                portfolio_id=portfolio.id
            ).order_by(Transaction.transaction_date.desc()).limit(10).all()
            
            # Update holdings based on transactions
            for transaction in recent_transactions:
                update_holding_from_transaction(transaction, portfolio)
            
            # Update portfolio current value
            update_portfolio_value(portfolio)
            
            db.session.commit()
            print(f"Updated portfolio holdings for workflow {workflow_id}")
            return True
            
        except Exception as e:
            print(f"Error updating portfolio holdings: {e}")
            db.session.rollback()
            return False

def get_recommended_securities(portfolio, total_amount):
    """
    Get recommended securities based on portfolio model and amount
    
    Args:
        portfolio (Portfolio): Client's portfolio
        total_amount (float): Total amount to invest
    
    Returns:
        list: List of recommended securities with quantities and target prices
    """
    # This is a simplified version - you can enhance this based on your business logic
    # For now, we'll create a basic recommendation structure
    
    # Get securities from the portfolio's asset allocation model
    if portfolio.model:
        # Get securities based on asset allocation
        securities = []
        for allocation in portfolio.model.asset_allocations:
            # Get securities in this asset class
            asset_class_securities = Security.query.filter_by(
                asset_class_id=allocation.asset_class_id
            ).limit(3).all()  # Top 3 securities per asset class
            
            for security in asset_class_securities:
                allocation_amount = (float(allocation.allocation_percentage) / 100) * total_amount
                # Distribute amount among securities in this asset class
                per_security_amount = allocation_amount / len(asset_class_securities)
                
                # Get current price (you can integrate with market data API)
                current_price = get_current_market_price(security.id) or 100.0
                quantity = per_security_amount / current_price
                
                securities.append({
                    'security_id': security.id,
                    'quantity': quantity,
                    'target_price': current_price,
                    'asset_class': allocation.asset_class.name
                })
        
        return securities
    else:
        # Fallback: return some default securities
        default_securities = Security.query.limit(5).all()
        securities = []
        per_security_amount = total_amount / len(default_securities)
        
        for security in default_securities:
            current_price = get_current_market_price(security.id) or 100.0
            quantity = per_security_amount / current_price
            
            securities.append({
                'security_id': security.id,
                'quantity': quantity,
                'target_price': current_price,
                'asset_class': 'Default'
            })
        
        return securities

def get_current_market_price(security_id):
    """
    Get current market price for a security
    
    Args:
        security_id (int): ID of the security
    
    Returns:
        float: Current market price
    """
    # This is a placeholder - you should integrate with your market data provider
    # For now, return a default price
    return 100.0

def update_holding_from_transaction(transaction, portfolio):
    """
    Update holding based on a transaction
    
    Args:
        transaction (Transaction): The transaction to process
        portfolio (Portfolio): The portfolio to update
    """
    # Check if holding exists
    existing_holding = db.session.query(Holding).filter_by(
        portfolio_id=portfolio.id,
        security_id=transaction.security_id
    ).first()
    
    if existing_holding:
        # Update existing holding
        if transaction.type == 'buy':
            # Calculate new average price
            total_quantity = float(existing_holding.quantity) + float(transaction.quantity)
            total_value = (float(existing_holding.average_price) * float(existing_holding.quantity)) + float(transaction.amount)
            new_average_price = total_value / total_quantity
            
            existing_holding.quantity = total_quantity
            existing_holding.average_price = new_average_price
            existing_holding.average_buy_price = new_average_price
        else:  # sell
            existing_holding.quantity = float(existing_holding.quantity) - float(transaction.quantity)
            if existing_holding.quantity <= 0:
                db.session.delete(existing_holding)
    else:
        # Create new holding
        if transaction.type == 'buy':
            new_holding = Holding(
                client_id=transaction.client_id,
                security_id=transaction.security_id,
                portfolio_id=portfolio.id,
                quantity=transaction.quantity,
                average_price=transaction.price,
                average_buy_price=transaction.price,
                created_at=datetime.utcnow()
            )
            db.session.add(new_holding)

def update_portfolio_value(portfolio):
    """
    Update portfolio current value based on holdings
    
    Args:
        portfolio (Portfolio): The portfolio to update
    """
    total_value = 0
    
    for holding in portfolio.holdings:
        current_price = get_current_market_price(holding.security_id)
        holding_value = float(holding.quantity) * current_price
        total_value += holding_value
    
    portfolio.current_value = total_value

def process_workflow_stage_change(workflow_id, new_stage, amount=None, user_id=None):
    """
    Process workflow stage change and trigger appropriate actions
    
    Args:
        workflow_id (int): ID of the workflow
        new_stage (str): New stage (RECOS, EXEC, UPDATE)
        amount (float): Amount for recommendations (required for RECOS)
        user_id (int): ID of the user making the change
    """
    if new_stage == 'RECOS' and amount and user_id:
        return create_recommendations_for_workflow(workflow_id, amount, user_id)
    elif new_stage == 'EXEC' and user_id:
        return execute_recommendations_for_workflow(workflow_id, user_id)
    elif new_stage == 'UPDATE':
        return update_portfolio_holdings_for_workflow(workflow_id)
    else:
        print(f"No action required for stage {new_stage}")
        return True

