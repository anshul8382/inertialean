-- Add risk_profile_updated_at column to client table
ALTER TABLE client ADD COLUMN risk_profile_updated_at DATETIME NULL;

-- Update existing clients with risk profiles to have a default updated timestamp
UPDATE client SET risk_profile_updated_at = created_at WHERE risk_profile IS NOT NULL AND risk_profile != ''; 