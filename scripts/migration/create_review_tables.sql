-- Create Review Tables
-- Run this script to add review functionality to your database

-- Review table
CREATE TABLE IF NOT EXISTS `review` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `client_id` INT NOT NULL,
    `review_type` VARCHAR(50) NOT NULL,
    `start_date` DATE NOT NULL,
    `end_date` DATE NOT NULL,
    `review_data` JSON NULL,
    `status` VARCHAR(20) DEFAULT 'generating',
    `generated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `completed_at` DATETIME NULL,
    `requested_by` INT NOT NULL,
    `error_message` TEXT NULL,
    FOREIGN KEY (`client_id`) REFERENCES `client`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`requested_by`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    INDEX `idx_review_client` (`client_id`),
    INDEX `idx_review_status` (`status`),
    INDEX `idx_review_dates` (`start_date`, `end_date`)
);

-- Review Section table
CREATE TABLE IF NOT EXISTS `review_section` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `review_id` INT NOT NULL,
    `section_type` VARCHAR(50) NOT NULL,
    `section_data` JSON NULL,
    `status` VARCHAR(20) DEFAULT 'pending',
    `generated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `error_message` TEXT NULL,
    FOREIGN KEY (`review_id`) REFERENCES `review`(`id`) ON DELETE CASCADE,
    INDEX `idx_review_section_review` (`review_id`),
    INDEX `idx_review_section_type` (`section_type`)
);

-- Review Share table
CREATE TABLE IF NOT EXISTS `review_share` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `review_id` INT NOT NULL,
    `shared_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `shared_by` INT NOT NULL,
    `shared_data` JSON NULL,
    `delivery_method` VARCHAR(20) DEFAULT 'email',
    `delivery_status` VARCHAR(20) DEFAULT 'pending',
    `access_token` VARCHAR(100) NULL,
    `expires_at` DATETIME NULL,
    FOREIGN KEY (`review_id`) REFERENCES `review`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`shared_by`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    INDEX `idx_review_share_review` (`review_id`),
    INDEX `idx_review_share_token` (`access_token`)
);

-- Review Template table
CREATE TABLE IF NOT EXISTS `review_template` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL,
    `description` TEXT NULL,
    `template_type` VARCHAR(50) NOT NULL,
    `sections` JSON NULL,
    `template_data` JSON NULL,
    `is_active` BOOLEAN DEFAULT TRUE,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `created_by` INT NOT NULL,
    FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    INDEX `idx_review_template_type` (`template_type`),
    INDEX `idx_review_template_active` (`is_active`)
);

-- Insert default review templates
INSERT INTO `review_template` (`name`, `description`, `template_type`, `sections`, `created_by`) VALUES
('Comprehensive Review', 'Full portfolio review with all sections', 'comprehensive', '["performance", "allocation", "transactions", "securities", "recommendations"]', 1),
('Performance Review', 'Focus on performance metrics and returns', 'performance', '["performance", "securities"]', 1),
('Allocation Review', 'Asset allocation analysis and drift', 'allocation', '["allocation", "recommendations"]', 1),
('Transaction Analysis', 'Detailed transaction patterns and changes', 'transactions', '["transactions", "securities"]', 1)
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);
