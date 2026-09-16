-- Create Historical Price Table
-- Run this to add historical price tracking capability

CREATE TABLE IF NOT EXISTS `historical_price` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `security_id` INT NOT NULL,
    `date` DATE NOT NULL,
    
    -- Price data
    `open_price` DECIMAL(15, 4) NULL,
    `high_price` DECIMAL(15, 4) NULL,
    `low_price` DECIMAL(15, 4) NULL,
    `close_price` DECIMAL(15, 4) NOT NULL,
    `volume` BIGINT NULL,
    
    -- Data source tracking
    `source` VARCHAR(50) NOT NULL COMMENT 'cron, transaction, googlefinance, manual',
    `confidence` DECIMAL(3, 2) DEFAULT 1.0 COMMENT 'Confidence level 0.0-1.0',
    
    -- Metadata
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- Foreign keys
    FOREIGN KEY (`security_id`) REFERENCES `security`(`id`) ON DELETE CASCADE,
    
    -- Unique constraint: one price per security per date
    UNIQUE KEY `uq_security_date` (`security_id`, `date`),
    
    -- Indexes for fast lookups
    INDEX `idx_security_date` (`security_id`, `date`),
    INDEX `idx_date` (`date`),
    INDEX `idx_source` (`source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add comment to table
ALTER TABLE `historical_price` COMMENT = 'Historical stock prices for performance analysis and review generation';


