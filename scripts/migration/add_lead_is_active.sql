-- Add is_active column to lead table to support marking leads as inactive
ALTER TABLE lead ADD COLUMN is_active BOOLEAN DEFAULT TRUE; 