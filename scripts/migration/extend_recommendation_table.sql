-- Extend the existing recommendation table to support client-specific trade recommendations
ALTER TABLE `recommendation` 
ADD COLUMN `client_id` int(11) DEFAULT NULL AFTER `security_id`,
ADD COLUMN `quantity` decimal(15,4) DEFAULT NULL AFTER `action`,
ADD COLUMN `actual_price` decimal(15,2) DEFAULT NULL AFTER `target_price`,
ADD COLUMN `status` varchar(20) DEFAULT 'pending' AFTER `actual_price`,
ADD COLUMN `executed_at` datetime DEFAULT NULL AFTER `status`,
ADD COLUMN `executed_by` int(11) DEFAULT NULL AFTER `executed_at`,
ADD COLUMN `notes` text DEFAULT NULL AFTER `executed_by`,
ADD KEY `client_id` (`client_id`),
ADD KEY `executed_by` (`executed_by`),
ADD CONSTRAINT `recommendation_ibfk_2` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE,
ADD CONSTRAINT `recommendation_ibfk_3` FOREIGN KEY (`executed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL;

-- Add index for status to improve query performance
ALTER TABLE `recommendation` ADD INDEX `idx_status` (`status`);
ALTER TABLE `recommendation` ADD INDEX `idx_client_status` (`client_id`, `status`); 