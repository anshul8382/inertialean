#!/usr/bin/env python3
"""
Script to assign default models to all clients who don't have model assignments.

This script:
1. Finds all clients without model assignments
2. Assigns them a default 100% Equity asset allocation model
3. Assigns them "Inertia Core Portfolio" (or most popular equity portfolio) as equity portfolio

Usage:
    python scripts/utilities/assign_default_models_to_clients.py
"""

import sys
sys.path.insert(0, '.')

from app import create_app
from models import (
    Client, ModelAssignment, AssetAllocationModel, AssetAllocation,
    SecurityAllocationModel, AssetClass, User
)
from extensions import db
from datetime import datetime
from sqlalchemy import func
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def assign_default_models_to_all_clients():
    """Assign default models to all clients without model assignments"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get or create admin user
            admin_user = User.query.filter_by(is_active=True).first()
            if not admin_user:
                admin_user = User.query.first()
                if not admin_user:
                    logger.error("No users found in database - cannot assign default models")
                    return False
            
            # Find or create Equity asset class
            equity_asset_class = AssetClass.query.filter_by(name='Equity').first()
            if not equity_asset_class:
                equity_asset_class = AssetClass.query.filter(
                    db.func.lower(AssetClass.name) == 'equity'
                ).first()
            
            if not equity_asset_class:
                logger.error("Equity asset class not found in database - cannot assign default models")
                return False
            
            # Find or create 100% Equity asset allocation model
            asset_model = AssetAllocationModel.query.join(AssetAllocation).filter(
                AssetAllocation.asset_class_id == equity_asset_class.id,
                AssetAllocation.allocation_percentage == 100
            ).first()
            
            if not asset_model:
                logger.info("Creating default 100% Equity asset allocation model")
                asset_model = AssetAllocationModel(
                    name='Default 100% Equity',
                    description='Default asset allocation model with 100% Equity allocation',
                    risk_profile='Moderate',
                    created_by=admin_user.id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.session.add(asset_model)
                db.session.flush()
                
                asset_allocation = AssetAllocation(
                    asset_class_id=equity_asset_class.id,
                    model_id=asset_model.id,
                    allocation_percentage=100,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.session.add(asset_allocation)
                db.session.commit()
                logger.info(f"Created default asset allocation model: {asset_model.name} (ID: {asset_model.id})")
            
            # Find "Inertia Core Portfolio" or most popular equity portfolio
            stock_model = None
            
            # Try multiple variations of the name
            search_variations = [
                'Inertia Core Portfolio',
                'inertia core portfolio',
                'Inertia Core',
                'inertia core'
            ]
            
            for variation in search_variations:
                stock_model = SecurityAllocationModel.query.filter_by(name=variation).first()
                if stock_model:
                    logger.info(f"Found equity portfolio by exact match: {stock_model.name} (ID: {stock_model.id})")
                    break
            
            # If not found, try case-insensitive search
            if not stock_model:
                stock_model = SecurityAllocationModel.query.filter(
                    db.func.lower(SecurityAllocationModel.name) == 'inertia core portfolio'
                ).first()
                if stock_model:
                    logger.info(f"Found equity portfolio by case-insensitive match: {stock_model.name} (ID: {stock_model.id})")
            
            # If still not found, try partial match
            if not stock_model:
                stock_model = SecurityAllocationModel.query.filter(
                    SecurityAllocationModel.name.ilike('%Inertia Core%')
                ).first()
                if stock_model:
                    logger.info(f"Found equity portfolio by partial match: {stock_model.name} (ID: {stock_model.id})")
            
            # If still not found, find the most popular equity portfolio
            if not stock_model:
                logger.info("Inertia Core Portfolio not found, finding most popular equity portfolio")
                most_popular = db.session.query(
                    SecurityAllocationModel.id,
                    SecurityAllocationModel.name,
                    func.count(ModelAssignment.id).label('assignment_count')
                ).outerjoin(
                    ModelAssignment,
                    ModelAssignment.stock_model_id == SecurityAllocationModel.id
                ).filter(
                    SecurityAllocationModel.asset_class_id == equity_asset_class.id
                ).group_by(
                    SecurityAllocationModel.id,
                    SecurityAllocationModel.name
                ).order_by(
                    func.count(ModelAssignment.id).desc()
                ).first()
                
                if most_popular:
                    stock_model = SecurityAllocationModel.query.get(most_popular.id)
                    logger.info(f"Found most popular equity portfolio: {stock_model.name} (ID: {stock_model.id}) with {most_popular.assignment_count} assignments")
                    # Verify if it's Inertia Core
                    if 'inertia core' in stock_model.name.lower():
                        logger.info(f"✅ Confirmed: Most popular portfolio is Inertia Core ({stock_model.name})")
                else:
                    # Last resort: get any equity security model
                    stock_model = SecurityAllocationModel.query.filter_by(
                        asset_class_id=equity_asset_class.id
                    ).first()
                    if stock_model:
                        logger.info(f"Using first available equity portfolio: {stock_model.name} (ID: {stock_model.id})")
            
            if not stock_model:
                logger.error("No equity security allocation model found in database - cannot assign default models")
                return False
            
            # Find all clients without model assignments
            clients_without_models = db.session.query(Client).outerjoin(
                ModelAssignment, Client.id == ModelAssignment.client_id
            ).filter(
                ModelAssignment.id.is_(None)
            ).all()
            
            logger.info(f"Found {len(clients_without_models)} clients without model assignments")
            
            if len(clients_without_models) == 0:
                logger.info("✅ All clients already have model assignments!")
                return True
            
            # Assign default models to each client
            assigned_count = 0
            failed_count = 0
            
            for client in clients_without_models:
                try:
                    # Check if assignment already exists (race condition check)
                    existing = ModelAssignment.query.filter_by(client_id=client.id).first()
                    if existing:
                        logger.debug(f"Client {client.id} ({client.name}) already has assignment, skipping")
                        continue
                    
                    model_assignment = ModelAssignment(
                        client_id=client.id,
                        asset_model_id=asset_model.id,
                        stock_model_id=stock_model.id,
                        assigned_at=datetime.utcnow(),
                        assigned_by=admin_user.id
                    )
                    db.session.add(model_assignment)
                    assigned_count += 1
                    logger.info(f"Assigned default models to client {client.id} ({client.name})")
                    
                except Exception as e:
                    logger.error(f"Error assigning models to client {client.id} ({client.name}): {str(e)}")
                    failed_count += 1
                    continue
            
            # Commit all assignments
            db.session.commit()
            
            logger.info(f"\n=== SUMMARY ===")
            logger.info(f"✅ Successfully assigned default models to {assigned_count} clients")
            if failed_count > 0:
                logger.warning(f"⚠️  Failed to assign models to {failed_count} clients")
            logger.info(f"Asset Model: {asset_model.name} (ID: {asset_model.id})")
            logger.info(f"Stock Model: {stock_model.name} (ID: {stock_model.id})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error assigning default models: {str(e)}", exc_info=True)
            db.session.rollback()
            return False

if __name__ == '__main__':
    success = assign_default_models_to_all_clients()
    sys.exit(0 if success else 1)

