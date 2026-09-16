-- Migration: Add missing fields to recommendation table
-- Date: 2025-08-15

-- Add sent_at field
ALTER TABLE recommendation 
ADD COLUMN sent_at DATETIME NULL;

-- Add sent_by field
ALTER TABLE recommendation 
ADD COLUMN sent_by INT NULL,
ADD FOREIGN KEY (sent_by) REFERENCES user(id);

-- Add executed_at field
ALTER TABLE recommendation 
ADD COLUMN executed_at DATETIME NULL;

-- Add executed_by field
ALTER TABLE recommendation 
ADD COLUMN executed_by INT NULL,
ADD FOREIGN KEY (executed_by) REFERENCES user(id);

-- Add indexes for better performance
CREATE INDEX idx_recommendation_status ON recommendation(status);
CREATE INDEX idx_recommendation_client_status ON recommendation(client_id, status);
CREATE INDEX idx_recommendation_sent ON recommendation(sent_at);
CREATE INDEX idx_recommendation_executed ON recommendation(executed_at);

-- Verify the changes
SELECT 'Migration completed successfully' as status;

