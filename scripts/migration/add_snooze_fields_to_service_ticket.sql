-- Add snooze support fields to service_ticket (safe to run multiple times if you manually check columns)
-- MySQL / MariaDB

ALTER TABLE `service_ticket`
  ADD COLUMN `pre_snooze_status` varchar(20) DEFAULT NULL,
  ADD COLUMN `snoozed_until` datetime DEFAULT NULL,
  ADD COLUMN `snoozed_at` datetime DEFAULT NULL,
  ADD COLUMN `snoozed_by` int(11) DEFAULT NULL,
  ADD COLUMN `snooze_reason` text DEFAULT NULL;

ALTER TABLE `service_ticket`
  ADD KEY `snoozed_by` (`snoozed_by`);

ALTER TABLE `service_ticket`
  ADD CONSTRAINT `service_ticket_ibfk_5` FOREIGN KEY (`snoozed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL;





