-- Add secondary client emails for recommendation CC (comma-separated in app layer).
-- Safe to re-run: check information_schema first on MySQL 5.7+.

-- SELECT COLUMN_NAME FROM information_schema.COLUMNS
--   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'client' AND COLUMN_NAME = 'secondary_emails';

ALTER TABLE client ADD COLUMN secondary_emails TEXT NULL;
