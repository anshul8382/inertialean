-- Add role-based access control fields
-- This migration adds user roles and client advisor assignments

-- 1. Add role field to user table
ALTER TABLE `user` ADD COLUMN `role` VARCHAR(20) DEFAULT 'advisor' AFTER `is_admin`;

-- 2. Add advisor_id field to client table for advisor assignments
ALTER TABLE `client` ADD COLUMN `advisor_id` INTEGER NULL AFTER `user_id`;
ALTER TABLE `client` ADD CONSTRAINT `fk_client_advisor` FOREIGN KEY (`advisor_id`) REFERENCES `user`(`id`);

-- 3. Create client_advisor_assignment table for tracking advisor assignments
CREATE TABLE `client_advisor_assignment` (
    `id` INTEGER PRIMARY KEY AUTO_INCREMENT,
    `client_id` INTEGER NOT NULL,
    `advisor_id` INTEGER NOT NULL,
    `assigned_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `assigned_by` INTEGER NOT NULL,
    `is_active` BOOLEAN DEFAULT TRUE,
    `notes` TEXT,
    FOREIGN KEY (`client_id`) REFERENCES `client`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`advisor_id`) REFERENCES `user`(`id`),
    FOREIGN KEY (`assigned_by`) REFERENCES `user`(`id`)
);

-- 4. Create index for better performance
CREATE INDEX `idx_client_advisor` ON `client_advisor_assignment`(`client_id`, `advisor_id`, `is_active`);
CREATE INDEX `idx_client_advisor_id` ON `client`(`advisor_id`);

-- 5. Update existing data
-- Set admin user (id=1) as manager role
UPDATE `user` SET `role` = 'manager' WHERE `id` = 1;

-- Set other users as advisors
UPDATE `user` SET `role` = 'advisor' WHERE `id` > 1;

-- Assign existing clients to their current user_id as advisor_id
UPDATE `client` SET `advisor_id` = `user_id` WHERE `advisor_id` IS NULL;

-- Insert advisor assignments for existing clients
INSERT INTO `client_advisor_assignment` (`client_id`, `advisor_id`, `assigned_by`)
SELECT `id`, `user_id`, 1 FROM `client` WHERE `user_id` IS NOT NULL;

-- 6. Add comments for documentation
COMMENT ON TABLE `client_advisor_assignment` IS 'Tracks advisor assignments to clients with history';
COMMENT ON COLUMN `user.role` IS 'User role: manager, advisor, admin';
COMMENT ON COLUMN `client.advisor_id` IS 'Current assigned advisor for this client'; 