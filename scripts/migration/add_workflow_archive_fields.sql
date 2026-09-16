-- Migration: Add archive fields to workflow table
-- Date: 2025-08-15

-- Add updated_at column with default value
ALTER TABLE workflow 
ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

-- Add archive fields
ALTER TABLE workflow 
ADD COLUMN is_archived BOOLEAN DEFAULT FALSE;

ALTER TABLE workflow 
ADD COLUMN archived_at DATETIME NULL;

ALTER TABLE workflow 
ADD COLUMN archived_reason VARCHAR(200) NULL;

-- Update existing records to have updated_at value
UPDATE workflow 
SET updated_at = created_at 
WHERE updated_at IS NULL;

-- Add index for better performance on archive queries
CREATE INDEX idx_workflow_archived ON workflow(is_archived, archived_at);
CREATE INDEX idx_workflow_stage_archived ON workflow(current_stage, is_archived);

-- Verify the changes
SELECT 'Migration completed successfully' as status;

