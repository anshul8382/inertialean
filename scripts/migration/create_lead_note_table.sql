-- Create lead_note table
CREATE TABLE IF NOT EXISTS `lead_note` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `lead_id` INT NOT NULL,
    `note_text` TEXT NOT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `created_by` INT NOT NULL,
    FOREIGN KEY (`lead_id`) REFERENCES `lead`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    INDEX `idx_lead_note_lead_id` (`lead_id`),
    INDEX `idx_lead_note_created_at` (`created_at`)
);

