#!/usr/bin/env python3
"""
Database migration to add user modification tracking tables for ML training
"""

def create_user_modification_tracking_tables():
    """Create tables for tracking user modifications to recommendations"""
    
    migration_sql = """
    -- Table to track all user modifications to recommendations
    CREATE TABLE IF NOT EXISTS user_modification_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        client_id INT NOT NULL,
        recommendation_id INT NULL,  -- NULL for asset-level modifications
        modification_type ENUM(
            'quantity_change', 
            'action_change', 
            'security_addition', 
            'security_deletion', 
            'asset_allocation_change'
        ) NOT NULL,
        
        -- Quantity modification data
        original_quantity INT NULL,
        new_quantity INT NULL,
        quantity_change_ratio DECIMAL(10,4) NULL,
        
        -- Amount modification data
        original_amount DECIMAL(15,2) NULL,
        new_amount DECIMAL(15,2) NULL,
        amount_change_ratio DECIMAL(10,4) NULL,
        
        -- Action modification data
        original_action ENUM('BUY', 'SELL', 'HOLD') NULL,
        new_action ENUM('BUY', 'SELL', 'HOLD') NULL,
        action_changed TINYINT(1) DEFAULT 0,
        
        -- Asset allocation modification data
        asset_class VARCHAR(50) NULL,
        original_allocation DECIMAL(10,4) NULL,
        new_allocation DECIMAL(10,4) NULL,
        allocation_change_ratio DECIMAL(10,4) NULL,
        
        -- Security addition/deletion data
        added_security_data JSON NULL,
        deleted_security_data JSON NULL,
        
        -- Context and metadata
        context_data JSON NULL,  -- Additional context like time spent, page interactions
        user_session_id VARCHAR(100) NULL,
        time_spent_on_page INT DEFAULT 0,  -- Seconds spent on page before modification
        modification_reason VARCHAR(200) DEFAULT 'user_preference',
        
        -- Timestamps
        modification_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        
        -- Indexes for performance
        INDEX idx_client_id (client_id),
        INDEX idx_recommendation_id (recommendation_id),
        INDEX idx_modification_type (modification_type),
        INDEX idx_modification_timestamp (modification_timestamp),
        INDEX idx_client_modification_type (client_id, modification_type),
        
        -- Foreign key constraints
        FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE CASCADE,
        FOREIGN KEY (recommendation_id) REFERENCES recommendation(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    
    -- Table to store ML training features for user modifications
    CREATE TABLE IF NOT EXISTS user_modification_features (
        id INT AUTO_INCREMENT PRIMARY KEY,
        modification_log_id INT NOT NULL,
        
        -- Original recommendation features
        original_quantity INT NOT NULL,
        original_amount DECIMAL(15,2) NOT NULL,
        original_action ENUM('BUY', 'SELL', 'HOLD') NOT NULL,
        original_confidence DECIMAL(5,2) DEFAULT 50.0,
        
        -- Security characteristics
        security_market_cap DECIMAL(15,2) NULL,
        security_volatility DECIMAL(10,4) NULL,
        security_sector VARCHAR(50) NULL,
        security_current_price DECIMAL(10,2) NULL,
        security_pe_ratio DECIMAL(10,2) NULL,
        
        -- Portfolio context
        portfolio_total_value DECIMAL(15,2) NULL,
        current_weight_in_portfolio DECIMAL(10,4) NULL,
        target_weight_recommended DECIMAL(10,4) NULL,
        portfolio_concentration DECIMAL(10,4) NULL,
        
        -- Client characteristics
        client_risk_profile TINYINT DEFAULT 3,
        client_age TINYINT NULL,
        client_investment_experience TINYINT NULL,
        client_income_level TINYINT NULL,
        
        -- Market conditions
        market_volatility_index DECIMAL(10,4) DEFAULT 0,
        sector_performance DECIMAL(10,4) DEFAULT 0,
        interest_rate_environment DECIMAL(10,4) DEFAULT 5.0,
        
        -- Time-based features
        time_of_month TINYINT NULL,
        day_of_week TINYINT NULL,
        quarter TINYINT NULL,
        
        -- Historical user behavior
        user_follow_rate_history DECIMAL(10,4) DEFAULT 0.5,
        user_modification_frequency DECIMAL(10,4) DEFAULT 0.1,
        user_preferred_sectors TINYINT DEFAULT 0,
        
        -- Modification context
        modification_time_seconds INT DEFAULT 0,
        number_of_modifications INT DEFAULT 1,
        modification_type_encoded DECIMAL(10,4) DEFAULT 0.5,
        
        -- Timestamps
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        
        -- Indexes
        INDEX idx_modification_log_id (modification_log_id),
        INDEX idx_client_risk_profile (client_risk_profile),
        INDEX idx_security_sector (security_sector),
        INDEX idx_created_at (created_at),
        
        -- Foreign key
        FOREIGN KEY (modification_log_id) REFERENCES user_modification_log(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    
    -- Table to store ML model predictions vs actual user behavior
    CREATE TABLE IF NOT EXISTS ml_prediction_accuracy (
        id INT AUTO_INCREMENT PRIMARY KEY,
        client_id INT NOT NULL,
        recommendation_id INT NULL,
        prediction_timestamp DATETIME NOT NULL,
        
        -- Model predictions
        predicted_quantity_ratio DECIMAL(10,4) NULL,
        predicted_amount_ratio DECIMAL(10,4) NULL,
        predicted_action_change_prob DECIMAL(10,4) NULL,
        predicted_modification_confidence DECIMAL(10,4) NULL,
        
        -- Actual user behavior
        actual_quantity_ratio DECIMAL(10,4) NULL,
        actual_amount_ratio DECIMAL(10,4) NULL,
        actual_action_changed TINYINT(1) NULL,
        
        -- Accuracy metrics
        quantity_prediction_error DECIMAL(10,4) NULL,
        amount_prediction_error DECIMAL(10,4) NULL,
        action_prediction_correct TINYINT(1) NULL,
        
        -- Model version and metadata
        model_version VARCHAR(50) DEFAULT '1.0',
        prediction_context JSON NULL,
        
        -- Timestamps
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        
        -- Indexes
        INDEX idx_client_id (client_id),
        INDEX idx_recommendation_id (recommendation_id),
        INDEX idx_prediction_timestamp (prediction_timestamp),
        INDEX idx_model_version (model_version),
        
        -- Foreign keys
        FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE CASCADE,
        FOREIGN KEY (recommendation_id) REFERENCES recommendation(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    
    -- Add indexes to existing recommendation table for better performance
    ALTER TABLE recommendation 
    ADD INDEX IF NOT EXISTS idx_is_user_modified (is_user_modified),
    ADD INDEX IF NOT EXISTS idx_client_created_by (client_id, created_by),
    ADD INDEX IF NOT EXISTS idx_batch_created_at (batch_created_at);
    """
    
    return migration_sql

if __name__ == "__main__":
    print("User modification tracking migration SQL:")
    print(create_user_modification_tracking_tables())
