#!/usr/bin/env python3
"""
Migration to add user recommendation feedback tracking tables
"""

from flask import Flask
from extensions import db
from models import Base

def create_feedback_tables():
    """Create tables for tracking user recommendation feedback"""
    
    # Create the migration SQL
    migration_sql = """
    -- User Recommendation Feedback Table
    CREATE TABLE IF NOT EXISTS user_recommendation_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        security_id INTEGER NOT NULL,
        recommendation_type VARCHAR(20) NOT NULL, -- 'SELL', 'BUY', 'HOLD'
        model_recommended BOOLEAN NOT NULL, -- TRUE = model recommended this action
        user_decision BOOLEAN NOT NULL, -- TRUE = user followed, FALSE = user rejected
        amount DECIMAL(15,2) NOT NULL,
        reason TEXT, -- Model reason for recommendation
        ml_confidence DECIMAL(5,2), -- ML predicted confidence (0-100)
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        FOREIGN KEY (client_id) REFERENCES client(id),
        FOREIGN KEY (security_id) REFERENCES security(id),
        
        INDEX idx_client_security_type (client_id, security_id, recommendation_type),
        INDEX idx_created_at (created_at),
        INDEX idx_user_decision (user_decision)
    );
    
    -- ML Model Performance Tracking
    CREATE TABLE IF NOT EXISTS ml_model_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_name VARCHAR(100) NOT NULL,
        client_id INTEGER,
        security_id INTEGER,
        prediction_confidence DECIMAL(5,2) NOT NULL,
        actual_user_decision BOOLEAN NOT NULL,
        prediction_correct BOOLEAN NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        FOREIGN KEY (client_id) REFERENCES client(id),
        FOREIGN KEY (security_id) REFERENCES security(id),
        
        INDEX idx_model_performance (model_name, prediction_correct),
        INDEX idx_client_model (client_id, model_name)
    );
    
    -- User Behavior Patterns (aggregated insights)
    CREATE TABLE IF NOT EXISTS user_behavior_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        security_id INTEGER NOT NULL,
        recommendation_type VARCHAR(20) NOT NULL,
        total_recommendations INTEGER DEFAULT 0,
        user_followed_count INTEGER DEFAULT 0,
        user_rejected_count INTEGER DEFAULT 0,
        follow_rate DECIMAL(5,2) DEFAULT 0.0, -- Percentage user follows this recommendation
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        FOREIGN KEY (client_id) REFERENCES client(id),
        FOREIGN KEY (security_id) REFERENCES security(id),
        
        UNIQUE KEY unique_client_security_type (client_id, security_id, recommendation_type),
        INDEX idx_follow_rate (follow_rate),
        INDEX idx_client_patterns (client_id)
    );
    """
    
    return migration_sql

if __name__ == '__main__':
    print("Migration SQL for user feedback tracking:")
    print(create_feedback_tables())

