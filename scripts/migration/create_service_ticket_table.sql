-- Create Service Ticket Table
CREATE TABLE IF NOT EXISTS `service_ticket` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `ticket_number` varchar(50) NOT NULL,
  `client_id` int(11) NOT NULL,
  `title` varchar(200) NOT NULL,
  `description` text NOT NULL,
  `alert_type` varchar(50) NOT NULL,
  `priority` varchar(20) NOT NULL DEFAULT 'medium',
  `status` varchar(20) NOT NULL DEFAULT 'open',
  `sla_hours` int(11) DEFAULT NULL,
  `sla_deadline` datetime DEFAULT NULL,
  `sla_breached` tinyint(1) DEFAULT 0,
  `assigned_to` int(11) DEFAULT NULL,
  `created_by` int(11) NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `resolved_at` datetime DEFAULT NULL,
  `closed_at` datetime DEFAULT NULL,
  `resolution_notes` text DEFAULT NULL,
  `resolved_by` int(11) DEFAULT NULL,
  `pre_snooze_status` varchar(20) DEFAULT NULL,
  `snoozed_until` datetime DEFAULT NULL,
  `snoozed_at` datetime DEFAULT NULL,
  `snoozed_by` int(11) DEFAULT NULL,
  `snooze_reason` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ticket_number` (`ticket_number`),
  KEY `client_id` (`client_id`),
  KEY `assigned_to` (`assigned_to`),
  KEY `created_by` (`created_by`),
  KEY `resolved_by` (`resolved_by`),
  KEY `status` (`status`),
  KEY `priority` (`priority`),
  KEY `snoozed_by` (`snoozed_by`),
  CONSTRAINT `service_ticket_ibfk_1` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE CASCADE,
  CONSTRAINT `service_ticket_ibfk_2` FOREIGN KEY (`assigned_to`) REFERENCES `user` (`id`) ON DELETE SET NULL,
  CONSTRAINT `service_ticket_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`),
  CONSTRAINT `service_ticket_ibfk_4` FOREIGN KEY (`resolved_by`) REFERENCES `user` (`id`) ON DELETE SET NULL,
  CONSTRAINT `service_ticket_ibfk_5` FOREIGN KEY (`snoozed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Add default SLA configurations for service tickets
INSERT IGNORE INTO `sla_configuration` (`process_name`, `process_stage`, `sla_hours`, `alert_trigger_percentage`, `escalation_hours`, `email_notification`, `sms_notification`, `in_app_notification`, `default_assignee_role`, `escalation_role`, `client_specific`, `active`, `created_at`, `updated_at`) VALUES
('service_ticket', 'critical', 4, 75, 6, 1, 0, 1, 'advisor', 'manager', 0, 1, NOW(), NOW()),
('service_ticket', 'high', 24, 75, 48, 1, 0, 1, 'advisor', 'manager', 0, 1, NOW(), NOW()),
('service_ticket', 'medium', 72, 75, 120, 1, 0, 1, 'advisor', 'manager', 0, 1, NOW(), NOW()),
('service_ticket', 'low', 168, 75, 192, 1, 0, 1, 'advisor', 'manager', 0, 1, NOW(), NOW());

