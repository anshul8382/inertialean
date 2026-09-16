-- Check the current structure of the recommendation table
DESCRIBE `recommendation`;

-- Check existing foreign keys
SELECT 
    CONSTRAINT_NAME,
    COLUMN_NAME,
    REFERENCED_TABLE_NAME,
    REFERENCED_COLUMN_NAME
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE 
WHERE TABLE_SCHEMA = 'inertia_app2025' 
AND TABLE_NAME = 'recommendation';

-- Check existing indexes
SHOW INDEX FROM `recommendation`;

-- Verify the recommended_trade table is gone
SHOW TABLES LIKE 'recommended_trade'; 