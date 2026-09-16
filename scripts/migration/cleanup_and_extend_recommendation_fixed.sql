-- Drop the recommended_trade table if it exists
DROP TABLE IF EXISTS `recommended_trade`;

-- First, let's check if the columns already exist to avoid duplicate column errors
-- Extend the existing recommendation table to support client-specific trade recommendations
ALTER TABLE `recommendation` 
ADD COLUMN IF NOT EXISTS `client_id` int(11) DEFAULT NULL AFTER `security_id`,
ADD COLUMN IF NOT EXISTS `quantity` decimal(15,4) DEFAULT NULL AFTER `action`,
ADD COLUMN IF NOT EXISTS `actual_price` decimal(15,2) DEFAULT NULL AFTER `target_price`,
ADD COLUMN IF NOT EXISTS `status` varchar(20) DEFAULT 'pending' AFTER `actual_price`,
ADD COLUMN IF NOT EXISTS `executed_at` datetime DEFAULT NULL AFTER `status`,
ADD COLUMN IF NOT EXISTS `executed_by` int(11) DEFAULT NULL AFTER `executed_at`,
ADD COLUMN IF NOT EXISTS `notes` text DEFAULT NULL AFTER `executed_by`;

-- Add indexes if they don't exist (MySQL doesn't support IF NOT EXISTS for indexes, so we'll handle errors)
-- Add foreign key constraints with unique names
ALTER TABLE `recommendation` 
ADD CONSTRAINT `recommendation_client_fk` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE;

ALTER TABLE `recommendation` 
ADD CONSTRAINT `recommendation_executed_by_fk` FOREIGN KEY (`executed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL;

-- Add indexes for performance (ignore errors if they already exist)
ALTER TABLE `recommendation` ADD INDEX `idx_recommendation_status` (`status`);
ALTER TABLE `recommendation` ADD INDEX `idx_recommendation_client_status` (`client_id`, `status`);

-- Show the updated table structure
DESCRIBE `recommendation`; 