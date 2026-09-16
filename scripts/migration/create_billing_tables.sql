-- Billing System Database Schema
-- This file creates all necessary tables for the billing system

-- 1. Add billing fields to existing agreement table
ALTER TABLE agreement ADD COLUMN (
    effective_date DATE NOT NULL DEFAULT '2025-01-01',
    billing_frequency ENUM('quarterly', 'half_yearly', 'yearly') DEFAULT 'yearly',
    billing_status ENUM('active', 'suspended', 'terminated') DEFAULT 'active'
);

-- 2. Create billing schedule table for operational billing data
CREATE TABLE billing_schedule (
    id INT PRIMARY KEY AUTO_INCREMENT,
    agreement_id INT NOT NULL,
    billing_start_date DATE NOT NULL,
    last_billing_date DATE NULL,
    next_billing_date DATE NOT NULL,
    billing_cycle_number INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agreement_id) REFERENCES agreement(id) ON DELETE CASCADE,
    INDEX idx_agreement_next_billing (agreement_id, next_billing_date),
    INDEX idx_next_billing_date (next_billing_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Create billing rate structure table
CREATE TABLE billing_rate_structure (
    id INT PRIMARY KEY AUTO_INCREMENT,
    agreement_id INT NOT NULL,
    asset_class_id INT NOT NULL,
    min_amount DECIMAL(15,2) DEFAULT 0.00,
    max_amount DECIMAL(15,2) DEFAULT NULL,
    rate_percentage DECIMAL(5,4) NOT NULL COMMENT 'Rate as decimal (0.0125 = 1.25%)',
    min_fee DECIMAL(10,2) DEFAULT 0.00,
    max_fee DECIMAL(10,2) DEFAULT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agreement_id) REFERENCES agreement(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_class_id) REFERENCES asset_class(id) ON DELETE CASCADE,
    INDEX idx_agreement_asset (agreement_id, asset_class_id),
    INDEX idx_amount_range (min_amount, max_amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Create invoice table
CREATE TABLE invoice (
    id INT PRIMARY KEY AUTO_INCREMENT,
    agreement_id INT NOT NULL,
    client_id INT NOT NULL,
    invoice_number VARCHAR(50) UNIQUE NOT NULL,
    invoice_date DATE NOT NULL,
    billing_period_start DATE NOT NULL,
    billing_period_end DATE NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    tax_rate DECIMAL(5,4) DEFAULT 0.1800 COMMENT 'Tax rate as decimal (0.18 = 18%)',
    tax_amount DECIMAL(12,2) DEFAULT 0.00,
    net_amount DECIMAL(12,2) NOT NULL,
    status ENUM('draft', 'sent', 'paid', 'overdue', 'cancelled') DEFAULT 'draft',
    due_date DATE NOT NULL,
    paid_date DATE NULL,
    payment_reference VARCHAR(100),
    payment_method VARCHAR(50),
    notes TEXT,
    pdf_path VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by INT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agreement_id) REFERENCES agreement(id),
    FOREIGN KEY (client_id) REFERENCES client(id),
    FOREIGN KEY (created_by) REFERENCES user(id),
    INDEX idx_invoice_number (invoice_number),
    INDEX idx_client_invoice (client_id, invoice_date),
    INDEX idx_agreement_invoice (agreement_id, invoice_date),
    INDEX idx_status_due_date (status, due_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. Create invoice line items table
CREATE TABLE invoice_line_item (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id INT NOT NULL,
    asset_class_id INT NOT NULL,
    asset_class_name VARCHAR(100) NOT NULL,
    portfolio_value DECIMAL(15,2) NOT NULL,
    rate_percentage DECIMAL(5,4) NOT NULL,
    calculated_fee DECIMAL(10,2) NOT NULL,
    min_fee_applied DECIMAL(10,2) DEFAULT 0.00,
    max_fee_applied DECIMAL(10,2) DEFAULT 0.00,
    final_fee DECIMAL(10,2) NOT NULL,
    fee_breakdown TEXT COMMENT 'Explanation of fee calculation',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_class_id) REFERENCES asset_class(id),
    INDEX idx_invoice_line_items (invoice_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. Create billing configuration table for system-wide settings
CREATE TABLE billing_configuration (
    id INT PRIMARY KEY AUTO_INCREMENT,
    config_key VARCHAR(100) UNIQUE NOT NULL,
    config_value TEXT NOT NULL,
    config_type ENUM('string', 'number', 'boolean', 'json') DEFAULT 'string',
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7. Insert default billing configuration
INSERT INTO billing_configuration (config_key, config_value, config_type, description) VALUES
('default_tax_rate', '0.18', 'number', 'Default GST rate (18%)'),
('invoice_due_days', '15', 'number', 'Default invoice due days'),
('invoice_number_prefix', 'INV', 'string', 'Prefix for invoice numbers'),
('invoice_number_format', '{prefix}-{year}-{quarter}-{sequence}', 'string', 'Invoice number format'),
('min_billing_amount', '1000.00', 'number', 'Minimum billing amount'),
('max_billing_amount', '1000000.00', 'number', 'Maximum billing amount'),
('billing_currency', 'INR', 'string', 'Default billing currency'),
('payment_terms', 'Net 15 days', 'string', 'Default payment terms');

-- 8. Create indexes for better performance
CREATE INDEX idx_agreement_billing_status ON agreement(billing_status);
CREATE INDEX idx_agreement_effective_date ON agreement(effective_date);
CREATE INDEX idx_agreement_billing_frequency ON agreement(billing_frequency);

-- 9. Add comments for documentation
ALTER TABLE agreement COMMENT = 'Enhanced with billing fields for effective date, frequency, and status';
ALTER TABLE billing_schedule COMMENT = 'Tracks billing schedule and cycle information for each agreement';
ALTER TABLE billing_rate_structure COMMENT = 'Defines billing rates for different asset classes and amount ranges';
ALTER TABLE invoice COMMENT = 'Stores invoice information and payment status';
ALTER TABLE invoice_line_item COMMENT = 'Detailed line items for each invoice showing asset class-wise billing';
ALTER TABLE billing_configuration COMMENT = 'System-wide billing configuration settings';










