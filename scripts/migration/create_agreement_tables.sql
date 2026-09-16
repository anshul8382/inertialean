-- Create agreement_template table
CREATE TABLE `agreement_template` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(200) NOT NULL,
  `description` text,
  `template_file_path` varchar(500) NOT NULL,
  `template_type` varchar(50) NOT NULL,
  `variables` text,
  `is_active` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_template_created_by` (`created_by`),
  CONSTRAINT `fk_agreement_template_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create agreement table
CREATE TABLE `agreement` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `lead_id` int(11) NOT NULL,
  `template_id` int(11) NOT NULL,
  `agreement_data` text,
  `generated_pdf_path` varchar(500),
  `status` varchar(50) DEFAULT 'draft',
  `sent_date` datetime,
  `signed_date` datetime,
  `notes` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_lead_id` (`lead_id`),
  KEY `fk_agreement_template_id` (`template_id`),
  KEY `fk_agreement_created_by` (`created_by`),
  CONSTRAINT `fk_agreement_lead_id` FOREIGN KEY (`lead_id`) REFERENCES `lead` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_template_id` FOREIGN KEY (`template_id`) REFERENCES `agreement_template` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create agreement_variables table to store variables for each agreement
CREATE TABLE `agreement_variables` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `agreement_id` int(11) NOT NULL,
  `lead_id` int(11) NOT NULL,
  `client_id` int(11) NULL,
  `variable_name` varchar(100) NOT NULL,
  `variable_value` text,
  `variable_type` varchar(50) DEFAULT 'text', -- text, number, date, email, phone, etc.
  `is_required` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_variables_agreement_id` (`agreement_id`),
  KEY `fk_agreement_variables_lead_id` (`lead_id`),
  KEY `fk_agreement_variables_client_id` (`client_id`),
  KEY `idx_agreement_variable` (`agreement_id`, `variable_name`),
  CONSTRAINT `fk_agreement_variables_agreement_id` FOREIGN KEY (`agreement_id`) REFERENCES `agreement` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_variables_lead_id` FOREIGN KEY (`lead_id`) REFERENCES `lead` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_variables_client_id` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add docx_path column to existing agreement table (run this if table already exists)
ALTER TABLE `agreement` ADD COLUMN `docx_path` varchar(500) NULL AFTER `generated_pdf_path`;

-- Create review_schedule table
CREATE TABLE `review_schedule` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `client_id` int(11) NOT NULL,
  `first_review_date` date NOT NULL,
  `frequency` varchar(20) NOT NULL,
  `is_active` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_review_schedule_client_id` (`client_id`),
  KEY `fk_review_schedule_created_by` (`created_by`),
  CONSTRAINT `fk_review_schedule_client_id` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_review_schedule_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create review_workflow table
CREATE TABLE `review_workflow` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `schedule_id` int(11) NOT NULL,
  `client_id` int(11) NOT NULL,
  `review_date` date NOT NULL,
  `status` varchar(20) DEFAULT 'initiated',
  `notes` text,
  `meeting_date` datetime NULL,
  `meeting_notes` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_review_workflow_schedule_id` (`schedule_id`),
  KEY `fk_review_workflow_client_id` (`client_id`),
  KEY `fk_review_workflow_created_by` (`created_by`),
  KEY `idx_review_workflow_status` (`status`),
  KEY `idx_review_workflow_date` (`review_date`),
  CONSTRAINT `fk_review_workflow_schedule_id` FOREIGN KEY (`schedule_id`) REFERENCES `review_schedule` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_review_workflow_client_id` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_review_workflow_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add agreements relationship to lead table (if not already exists)
-- This is just for reference - the foreign key constraint above handles the relationship
