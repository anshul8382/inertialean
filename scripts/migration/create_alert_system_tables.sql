-- Create Alert System Tables
-- This script creates all necessary tables for the alert system and SLA monitoring

-- Alert table
CREATE TABLE IF NOT EXISTS `alert` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `alert_type` varchar(50) NOT NULL,
  `alert_subtype` varchar(50) NOT NULL,
  `severity` varchar(20) NOT NULL,
  `status` varchar(20) DEFAULT 'active',
  
  -- Related entities
  `client_id` int(11) DEFAULT NULL,
  `workflow_id` int(11) DEFAULT NULL,
  `recommendation_id` int(11) DEFAULT NULL,
  `user_id` int(11) DEFAULT NULL,
  
  -- Alert details
  `title` varchar(200) NOT NULL,
  `description` text NOT NULL,
  `sla_timeline` int(11) DEFAULT NULL,
  `current_delay` int(11) DEFAULT NULL,
  
  -- Timestamps
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `acknowledged_at` datetime DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  `snoozed_until` datetime DEFAULT NULL,
  
  -- Escalation
  `escalated_at` datetime DEFAULT NULL,
  `escalated_to` int(11) DEFAULT NULL,
  
  -- Resolution
  `resolved_by` int(11) DEFAULT NULL,
  `resolution_notes` text DEFAULT NULL,
  
  PRIMARY KEY (`id`),
  KEY `client_id` (`client_id`),
  KEY `workflow_id` (`workflow_id`),
  KEY `recommendation_id` (`recommendation_id`),
  KEY `user_id` (`user_id`),
  KEY `escalated_to` (`escalated_to`),
  KEY `resolved_by` (`resolved_by`),
  KEY `status` (`status`),
  KEY `severity` (`severity`),
  KEY `alert_type` (`alert_type`),
  KEY `created_at` (`created_at`),
  
  CONSTRAINT `alert_ibfk_1` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alert_ibfk_2` FOREIGN KEY (`workflow_id`) REFERENCES `workflow` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alert_ibfk_3` FOREIGN KEY (`recommendation_id`) REFERENCES `recommendation` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alert_ibfk_4` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alert_ibfk_5` FOREIGN KEY (`escalated_to`) REFERENCES `user` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alert_ibfk_6` FOREIGN KEY (`resolved_by`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- SLA Configuration table
CREATE TABLE IF NOT EXISTS `sla_configuration` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `process_name` varchar(100) NOT NULL,
  `process_stage` varchar(100) NOT NULL,
  `sla_hours` int(11) NOT NULL,
  `alert_trigger_percentage` int(11) DEFAULT 80,
  `escalation_hours` int(11) DEFAULT NULL,
  
  -- Notifications
  `email_notification` tinyint(1) DEFAULT 1,
  `sms_notification` tinyint(1) DEFAULT 0,
  `in_app_notification` tinyint(1) DEFAULT 1,
  
  -- Assignment
  `default_assignee_role` varchar(50) DEFAULT NULL,
  `escalation_role` varchar(50) DEFAULT NULL,
  
  -- Customization
  `client_specific` tinyint(1) DEFAULT 0,
  `active` tinyint(1) DEFAULT 1,
  
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  UNIQUE KEY `process_stage_unique` (`process_name`, `process_stage`),
  KEY `active` (`active`),
  KEY `process_name` (`process_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Alert Notification table
CREATE TABLE IF NOT EXISTS `alert_notification` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `alert_id` int(11) NOT NULL,
  `notification_type` varchar(20) NOT NULL,
  `recipient_id` int(11) NOT NULL,
  `sent_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `status` varchar(20) DEFAULT 'sent',
  
  PRIMARY KEY (`id`),
  KEY `alert_id` (`alert_id`),
  KEY `recipient_id` (`recipient_id`),
  KEY `notification_type` (`notification_type`),
  KEY `sent_at` (`sent_at`),
  
  CONSTRAINT `alert_notification_ibfk_1` FOREIGN KEY (`alert_id`) REFERENCES `alert` (`id`) ON DELETE CASCADE,
  CONSTRAINT `alert_notification_ibfk_2` FOREIGN KEY (`recipient_id`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Alert Report table
CREATE TABLE IF NOT EXISTS `alert_report` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `report_date` date NOT NULL,
  `report_type` varchar(20) NOT NULL,
  
  -- Metrics
  `total_alerts` int(11) DEFAULT 0,
  `new_alerts` int(11) DEFAULT 0,
  `resolved_alerts` int(11) DEFAULT 0,
  `escalated_alerts` int(11) DEFAULT 0,
  `sla_compliance_rate` float DEFAULT 0.0,
  
  -- Breakdown by type
  `workflow_alerts` int(11) DEFAULT 0,
  `recommendation_alerts` int(11) DEFAULT 0,
  `communication_alerts` int(11) DEFAULT 0,
  `portfolio_alerts` int(11) DEFAULT 0,
  `system_alerts` int(11) DEFAULT 0,
  
  -- Breakdown by severity
  `critical_alerts` int(11) DEFAULT 0,
  `warning_alerts` int(11) DEFAULT 0,
  `info_alerts` int(11) DEFAULT 0,
  
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  UNIQUE KEY `report_date_type_unique` (`report_date`, `report_type`),
  KEY `report_date` (`report_date`),
  KEY `report_type` (`report_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert default SLA configurations
INSERT INTO `sla_configuration` (`process_name`, `process_stage`, `sla_hours`, `alert_trigger_percentage`, `default_assignee_role`) VALUES
('monthly_investment', 'FUNDS', 48, 75, 'advisor'),
('monthly_investment', 'RECOS', 72, 67, 'advisor'),
('monthly_investment', 'NOTIFY', 48, 75, 'advisor'),
('monthly_investment', 'EXEC', 72, 67, 'advisor'),
('monthly_investment', 'UPDATE', 24, 75, 'advisor'),
('monthly_investment', 'COMPLETED', 24, 75, 'advisor'),
('recommendation', 'creation', 24, 75, 'advisor'),
('recommendation', 'client_response', 72, 67, 'advisor'),
('recommendation', 'execution', 48, 75, 'advisor'),
('client_onboarding', 'documentation', 120, 80, 'advisor'),
('client_onboarding', 'kyc_verification', 72, 75, 'advisor'),
('client_onboarding', 'portfolio_setup', 48, 75, 'advisor'),
('portfolio', 'rebalancing', 168, 80, 'advisor'),
('communication', 'client_followup', 72, 75, 'advisor'),
('communication', 'meeting_reminder', 24, 80, 'advisor')
ON DUPLICATE KEY UPDATE
`sla_hours` = VALUES(`sla_hours`),
`alert_trigger_percentage` = VALUES(`alert_trigger_percentage`),
`default_assignee_role` = VALUES(`default_assignee_role`);

-- Create indexes for better performance
CREATE INDEX `idx_alert_status_severity` ON `alert` (`status`, `severity`);
CREATE INDEX `idx_alert_created_at` ON `alert` (`created_at`);
CREATE INDEX `idx_alert_user_status` ON `alert` (`user_id`, `status`);
CREATE INDEX `idx_alert_type_subtype` ON `alert` (`alert_type`, `alert_subtype`);

-- Create views for common queries
CREATE OR REPLACE VIEW `active_alerts_view` AS
SELECT 
    a.*,
    c.name as client_name,
    u.username as assigned_user_name,
    w.current_stage as workflow_stage
FROM `alert` a
LEFT JOIN `client` c ON a.client_id = c.id
LEFT JOIN `user` u ON a.user_id = u.id
LEFT JOIN `workflow` w ON a.workflow_id = w.id
WHERE a.status = 'active'
ORDER BY a.created_at DESC;

CREATE OR REPLACE VIEW `alert_summary_view` AS
SELECT 
    alert_type,
    severity,
    COUNT(*) as count,
    AVG(TIMESTAMPDIFF(HOUR, created_at, NOW())) as avg_age_hours
FROM `alert`
WHERE status = 'active'
GROUP BY alert_type, severity;

-- Create stored procedure for SLA compliance calculation
DELIMITER //
CREATE PROCEDURE `CalculateSLACompliance`(IN report_date DATE)
BEGIN
    DECLARE total_workflows INT;
    DECLARE compliant_workflows INT;
    DECLARE compliance_rate DECIMAL(5,2);
    
    -- Count total workflows for the date
    SELECT COUNT(*) INTO total_workflows
    FROM workflow
    WHERE DATE(created_at) = report_date;
    
    -- Count compliant workflows (completed within SLA)
    SELECT COUNT(*) INTO compliant_workflows
    FROM workflow w
    JOIN sla_configuration s ON s.process_name = 'monthly_investment' 
        AND s.process_stage = w.current_stage
    WHERE DATE(w.created_at) = report_date
        AND w.status = 'completed'
        AND TIMESTAMPDIFF(HOUR, w.created_at, w.actual_completion_date) <= s.sla_hours;
    
    -- Calculate compliance rate
    IF total_workflows > 0 THEN
        SET compliance_rate = (compliant_workflows / total_workflows) * 100;
    ELSE
        SET compliance_rate = 0;
    END IF;
    
    -- Return results
    SELECT 
        report_date as date,
        total_workflows,
        compliant_workflows,
        compliance_rate as sla_compliance_rate;
END //
DELIMITER ;

-- Create event to run daily SLA checks (runs every hour)
CREATE EVENT IF NOT EXISTS `hourly_sla_check`
ON SCHEDULE EVERY 1 HOUR
DO
BEGIN
    -- This would call the AlertService.check_workflow_sla() and AlertService.check_recommendation_sla()
    -- In a real implementation, this would be handled by a background job
    INSERT INTO alert_notification (alert_id, notification_type, recipient_id, status)
    SELECT 
        a.id,
        'system_check',
        1,
        'sent'
    FROM alert a
    WHERE a.status = 'active' 
        AND a.created_at < DATE_SUB(NOW(), INTERVAL 1 HOUR)
        AND NOT EXISTS (
            SELECT 1 FROM alert_notification an 
            WHERE an.alert_id = a.id 
                AND an.notification_type = 'system_check'
                AND an.sent_at > DATE_SUB(NOW(), INTERVAL 1 HOUR)
        );
END;



