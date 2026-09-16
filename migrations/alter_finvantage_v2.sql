-- FinVantage v2 Migration: health_insurance_coverage, waterfall_order, savings records
-- Run against finvantage DB: mysql -u inertia_admin -p finvantage < migrations/alter_finvantage_v2.sql

-- Add new columns to finvantage_profile (ignore errors if already present)
ALTER TABLE finvantage_profile ADD COLUMN IF NOT EXISTS health_insurance_coverage DECIMAL(15,2) DEFAULT 0;
ALTER TABLE finvantage_profile ADD COLUMN IF NOT EXISTS waterfall_order JSON DEFAULT NULL;

-- MySQL 5.7 doesn't support ADD COLUMN IF NOT EXISTS, so use this instead for older MySQL:
-- ALTER TABLE finvantage_profile ADD COLUMN health_insurance_coverage DECIMAL(15,2) DEFAULT 0;
-- ALTER TABLE finvantage_profile ADD COLUMN waterfall_order JSON DEFAULT NULL;

-- Create savings record table
CREATE TABLE IF NOT EXISTS finvantage_savings_record (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  year_month VARCHAR(7) NOT NULL,
  planned_savings DECIMAL(15,2) DEFAULT 0,
  actual_savings DECIMAL(15,2) DEFAULT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_savings_user FOREIGN KEY (user_id) REFERENCES finvantage_user(id) ON DELETE CASCADE,
  CONSTRAINT uq_user_month UNIQUE (user_id, year_month)
);
