-- Fix foreign key constraints for lead deletion to include CASCADE DELETE
-- This will automatically delete related records when a lead is deleted

-- Drop existing foreign key constraints
ALTER TABLE lead_call_log DROP FOREIGN KEY lead_call_log_ibfk_1;
ALTER TABLE meeting DROP FOREIGN KEY meeting_ibfk_2;

-- Recreate foreign key constraints with CASCADE DELETE
ALTER TABLE lead_call_log 
ADD CONSTRAINT lead_call_log_ibfk_1 
FOREIGN KEY (lead_id) REFERENCES lead(id) ON DELETE CASCADE;

ALTER TABLE meeting 
ADD CONSTRAINT meeting_ibfk_2 
FOREIGN KEY (lead_id) REFERENCES lead(id) ON DELETE CASCADE; 