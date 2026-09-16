-- Drop the recommended_trade table if it exists
DROP TABLE IF EXISTS `recommended_trade`;

-- Check and add columns one by one to avoid conflicts
-- Add client_id column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'client_id') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `client_id` int(11) DEFAULT NULL AFTER `security_id`',
    'SELECT "client_id column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add quantity column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'quantity') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `quantity` decimal(15,4) DEFAULT NULL AFTER `action`',
    'SELECT "quantity column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add actual_price column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'actual_price') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `actual_price` decimal(15,2) DEFAULT NULL AFTER `target_price`',
    'SELECT "actual_price column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add status column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'status') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `status` varchar(20) DEFAULT "pending" AFTER `actual_price`',
    'SELECT "status column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add executed_at column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'executed_at') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `executed_at` datetime DEFAULT NULL AFTER `status`',
    'SELECT "executed_at column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add executed_by column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'executed_by') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `executed_by` int(11) DEFAULT NULL AFTER `executed_at`',
    'SELECT "executed_by column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add notes column if it doesn't exist
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'notes') = 0,
    'ALTER TABLE `recommendation` ADD COLUMN `notes` text DEFAULT NULL AFTER `executed_by`',
    'SELECT "notes column already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add foreign key constraints if they don't exist
-- Check for client_id foreign key
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'client_id' 
     AND REFERENCED_TABLE_NAME = 'client') = 0,
    'ALTER TABLE `recommendation` ADD CONSTRAINT `recommendation_client_fk` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE',
    'SELECT "client_id foreign key already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Check for executed_by foreign key
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND COLUMN_NAME = 'executed_by' 
     AND REFERENCED_TABLE_NAME = 'user') = 0,
    'ALTER TABLE `recommendation` ADD CONSTRAINT `recommendation_executed_by_fk` FOREIGN KEY (`executed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL',
    'SELECT "executed_by foreign key already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add indexes if they don't exist
-- Status index
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND INDEX_NAME = 'idx_recommendation_status') = 0,
    'ALTER TABLE `recommendation` ADD INDEX `idx_recommendation_status` (`status`)',
    'SELECT "status index already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Client status composite index
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS 
     WHERE TABLE_SCHEMA = 'inertia_app2025' 
     AND TABLE_NAME = 'recommendation' 
     AND INDEX_NAME = 'idx_recommendation_client_status') = 0,
    'ALTER TABLE `recommendation` ADD INDEX `idx_recommendation_client_status` (`client_id`, `status`)',
    'SELECT "client_status index already exists" as message'
));
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Show the final table structure
DESCRIBE `recommendation`; 