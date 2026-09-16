-- Create lead_note table for notes log functionality
CREATE TABLE IF NOT EXISTS lead_note (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lead_id INT NOT NULL,
    note_text TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by INT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES lead(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES user(id) ON DELETE CASCADE,
    INDEX idx_lead_id (lead_id),
    INDEX idx_created_at (created_at)
);

-- Add any existing notes from the old notes field to the new table
-- This is optional and can be run if there are existing notes to migrate
-- INSERT INTO lead_note (lead_id, note_text, created_at, created_by)
-- SELECT id, notes, created_at, user_id FROM lead WHERE notes IS NOT NULL AND notes != ''; 