-- Fix workflow_action amount column to have default value of 0
-- This prevents the "Incorrect decimal value: ''" error

-- First, update existing NULL values to 0
UPDATE workflow_action SET amount = 0 WHERE amount IS NULL;

-- Then modify the column to have a default value of 0
ALTER TABLE workflow_action MODIFY COLUMN amount DECIMAL(15,2) DEFAULT 0;

-- Also fix the generic_workflow_action table if it exists
UPDATE generic_workflow_action SET amount = 0 WHERE amount IS NULL;
ALTER TABLE generic_workflow_action MODIFY COLUMN amount DECIMAL(15,2) DEFAULT 0;

